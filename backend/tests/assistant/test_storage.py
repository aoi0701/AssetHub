import datetime
import uuid
import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.assistant.models import ChatTurn, Conversation, SupportTicket
from apps.assistant.storage import expire_stale_turn, finalize_turn, reserve_turn
from apps.authentication.models import User
from apps.common.exceptions import ConflictError


@pytest.fixture
def other_user(db, dept_hr):
    return User.objects.create_user(
        username="other_employee",
        email="other@assethub.local",
        password=None,
        role=User.Role.USER,
        department=dept_hr,
        full_name="Other Employee",
    )


def test_reserve_turn_creates_processing_turn(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn, is_new = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Tài sản của tôi là gì?",
    )
    assert is_new is True
    assert turn.outcome == ChatTurn.Outcome.PROCESSING
    assert turn.question == "Tài sản của tôi là gì?"
    assert turn.client_request_id == req_id
    assert turn.conversation == conversation


def test_reserve_turn_idempotent_replay(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn1, is_new1 = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Tôi đang giữ bao nhiêu máy tính?",
    )
    assert is_new1 is True

    turn2, is_new2 = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Tôi đang giữ bao nhiêu máy tính?",
    )
    assert is_new2 is False
    assert turn2.id == turn1.id


def test_reserve_turn_duplicate_request_id_different_question_raises_conflict(db, regular_user, conversation):
    req_id = uuid.uuid4()
    reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Câu hỏi 1",
    )
    with pytest.raises(ConflictError) as exc:
        reserve_turn(
            actor=regular_user,
            conversation=conversation,
            client_request_id=req_id,
            question="Câu hỏi 2 khác nội dung",
        )
    assert "CLIENT_REQUEST_ID_CONFLICT" in str(exc.value)


def test_reserve_turn_same_request_id_in_different_conversations_is_permitted(db, regular_user, conversation, other_user):
    conv2 = Conversation.objects.create(
        owner=other_user,
        role_snapshot=other_user.role,
        department_id_snapshot=other_user.department_id,
    )
    req_id = uuid.uuid4()
    turn1, is_new1 = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Câu hỏi ở conv 1",
    )
    assert is_new1 is True

    # Same request_id in different conversation is valid per (conversation, client_request_id) constraint
    turn2, is_new2 = reserve_turn(
        actor=other_user,
        conversation=conv2,
        client_request_id=req_id,
        question="Câu hỏi ở conv 2",
    )
    assert is_new2 is True
    assert turn2.conversation == conv2
    assert turn2.client_request_id == req_id
    assert turn1.id != turn2.id


def test_reserve_turn_wrong_owner_raises_permission_denied(db, conversation, other_user):
    req_id = uuid.uuid4()
    with pytest.raises(PermissionDenied):
        reserve_turn(
            actor=other_user,
            conversation=conversation,
            client_request_id=req_id,
            question="Câu hỏi trái phép",
        )


def test_reserve_turn_scope_change_raises_conflict(db, regular_user, conversation, dept_hr):
    regular_user.role = User.Role.MANAGER
    regular_user.save()
    with pytest.raises(ConflictError) as exc:
        reserve_turn(
            actor=regular_user,
            conversation=conversation,
            client_request_id=uuid.uuid4(),
            question="Câu hỏi sau khi đổi role",
        )
    assert "CONVERSATION_SCOPE_CHANGED" in str(exc.value)


def test_reserve_turn_conversation_busy_when_processing_turn_active(db, regular_user, conversation):
    req_id1 = uuid.uuid4()
    reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id1,
        question="Đang xử lý câu hỏi này",
    )
    req_id2 = uuid.uuid4()
    with pytest.raises(ConflictError) as exc:
        reserve_turn(
            actor=regular_user,
            conversation=conversation,
            client_request_id=req_id2,
            question="Gửi câu hỏi tiếp khi câu trước chưa xong",
        )
    assert "CONVERSATION_BUSY" in str(exc.value)


def test_reserve_turn_auto_expires_stale_turn_and_succeeds(db, regular_user, conversation):
    req_id1 = uuid.uuid4()
    turn1, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id1,
        question="Câu hỏi bị treo lâu",
    )
    # Simulate past creation > 180s ago
    past_time = timezone.now() - datetime.timedelta(seconds=200)
    ChatTurn.objects.filter(id=turn1.id).update(created_at=past_time)

    # Next reserve should clean up stale turn and succeed
    req_id2 = uuid.uuid4()
    turn2, is_new2 = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id2,
        question="Câu hỏi mới sau khi cũ hết hạn",
    )
    assert is_new2 is True
    turn1.refresh_from_db()
    assert turn1.outcome == ChatTurn.Outcome.ERROR
    assert turn2.outcome == ChatTurn.Outcome.PROCESSING


def test_expire_stale_turn_logic(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Kiểm tra expire_stale_turn",
    )

    now = timezone.now()
    # Within 180s: still PROCESSING
    turn = expire_stale_turn(turn=turn, now=now + datetime.timedelta(seconds=179))
    assert turn.outcome == ChatTurn.Outcome.PROCESSING

    # Over 180s: becomes ERROR
    turn = expire_stale_turn(turn=turn, now=now + datetime.timedelta(seconds=181))
    assert turn.outcome == ChatTurn.Outcome.ERROR
    assert "quá thời gian xử lý" in turn.answer


def test_finalize_turn_success(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Bạn có máy in không?",
    )
    evidence = [
        {
            "id": "asset:1",
            "kind": "asset",
            "label": "Máy in IT",
            "href": None,
            "observed_at": timezone.now().isoformat(),
            "scope_label": "Phòng IT",
            "facts": {"name": "Máy in Canon"},
        }
    ]
    finalized = finalize_turn(
        actor=regular_user,
        turn=turn,
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Có máy in Canon tại phòng IT.",
        sources=evidence,
    )
    assert finalized.outcome == ChatTurn.Outcome.ANSWERED
    assert finalized.answer == "Có máy in Canon tại phòng IT."
    assert len(finalized.sources) == 1
    assert finalized.sources[0]["id"] == "asset:1"


def test_finalize_turn_wrong_owner_raises_permission_denied(db, conversation, other_user, regular_user):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Hỏi câu hỏi",
    )
    with pytest.raises(PermissionDenied):
        finalize_turn(
            actor=other_user,
            turn=turn,
            outcome=ChatTurn.Outcome.ANSWERED,
            answer="Answer",
            sources=[],
        )


def test_finalize_turn_non_processing_raises_conflict(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Hỏi lần đầu",
    )
    finalize_turn(
        actor=regular_user,
        turn=turn,
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Đã trả lời xong.",
        sources=[],
    )
    # Attempting to finalize an already finalized turn raises ConflictError
    with pytest.raises(ConflictError) as exc:
        finalize_turn(
            actor=regular_user,
            turn=turn,
            outcome=ChatTurn.Outcome.ANSWERED,
            answer="Cố ghi đè",
            sources=[],
        )
    assert "TURN_STATE_CONFLICT" in str(exc.value)


def test_pending_turn_expires_without_late_overwrite(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Câu hỏi bị quá hạn",
    )
    # Clock passes 181s
    future_time = timezone.now() + datetime.timedelta(seconds=181)
    expire_stale_turn(turn=turn, now=future_time)
    assert turn.outcome == ChatTurn.Outcome.ERROR

    # Late worker tries to finalize
    with pytest.raises(ConflictError) as exc:
        finalize_turn(
            actor=regular_user,
            turn=turn,
            outcome=ChatTurn.Outcome.ANSWERED,
            answer="Kết quả về muộn không được ghi đè",
            sources=[],
        )
    assert "TURN_STATE_CONFLICT" in str(exc.value)


def test_finalize_turn_truncates_long_answer_to_6000(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Hỏi văn bản dài",
    )
    very_long_answer = "A" * 7000
    finalized = finalize_turn(
        actor=regular_user,
        turn=turn,
        outcome=ChatTurn.Outcome.ANSWERED,
        answer=very_long_answer,
        sources=[],
    )
    assert len(finalized.answer) == 6000
    assert "[Nội dung đã được rút gọn do vượt quá giới hạn độ dài]" in finalized.answer


def test_finalize_turn_with_escalated_creates_ticket(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=req_id,
        question="Cần bàn giao tài sản đặc biệt không có trong danh mục",
    )
    finalized = finalize_turn(
        actor=user_it,
        turn=turn,
        outcome=ChatTurn.Outcome.ESCALATED,
        answer="Yêu cầu được chuyển đến quản lý.",
        sources=[],
    )
    assert finalized.outcome == ChatTurn.Outcome.ESCALATED
    ticket = SupportTicket.objects.get(turn=finalized)
    assert ticket.status == SupportTicket.Status.OPEN
    assert ticket.target_kind == SupportTicket.TargetKind.DEPARTMENT
    assert ticket.target_department_id == user_it.department_id
    assert "Cần bàn giao" in ticket.question_snapshot


def test_finalize_turn_rejects_processing_outcome(db, regular_user, conversation):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Hỏi câu hỏi",
    )
    # Point 5: finalize_turn only accepts terminal outcomes; PROCESSING must be rejected
    with pytest.raises(ValidationError) as exc:
        finalize_turn(
            actor=regular_user,
            turn=turn,
            outcome=ChatTurn.Outcome.PROCESSING,
            answer="Answer",
            sources=[],
        )
    assert "terminal outcome" in str(exc.value).lower() or "không hợp lệ" in str(exc.value).lower()


def test_finalize_turn_scope_change_raises_conflict(db, regular_user, conversation, dept_hr):
    req_id = uuid.uuid4()
    turn, _ = reserve_turn(
        actor=regular_user,
        conversation=conversation,
        client_request_id=req_id,
        question="Hỏi câu hỏi",
    )
    # Point 4: Scope change before finalize raises ConflictError("CONVERSATION_SCOPE_CHANGED")
    regular_user.role = User.Role.MANAGER
    regular_user.save()
    with pytest.raises(ConflictError) as exc:
        finalize_turn(
            actor=regular_user,
            turn=turn,
            outcome=ChatTurn.Outcome.ANSWERED,
            answer="Answer",
            sources=[],
        )
    assert "CONVERSATION_SCOPE_CHANGED" in str(exc.value)
