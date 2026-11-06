import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.ai.exceptions import ServiceUnavailable

from apps.assistant.models import ChatTurn, Conversation, SupportTicket
from apps.assistant.storage import reserve_turn
from apps.assistant.support import (
    build_question_snapshot,
    escalate_turn,
    get_visible_ticket,
    reply_ticket,
    resolve_support_target,
    visible_tickets,
)
from apps.authentication.models import User
from apps.common.exceptions import ConflictError


@pytest.fixture
def open_ticket(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn, _ = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Tôi cần xin cấp máy tính mới",
    )
    turn.outcome = ChatTurn.Outcome.ANSWERED
    turn.answer = "Vui lòng liên hệ IT để cấp phát."
    turn.save()
    return escalate_turn(actor=user_it, turn=turn)


def test_department_queue_and_admin_fallback(db, assistant_graph):
    admin = assistant_graph["admin"]
    manager_it = assistant_graph["manager_it"]
    user_it = assistant_graph["user_it"]
    it = assistant_graph["it"]

    # 1. User IT -> routes to DEPARTMENT IT (manager_it is active in IT)
    target_user = resolve_support_target(user_it)
    assert target_user["kind"] == "DEPARTMENT"
    assert target_user["department_id"] == it.id

    # 2. User without department -> fallback to ADMIN
    target_admin = resolve_support_target(admin)
    assert target_admin["kind"] == "ADMIN"
    assert target_admin["department_id"] is None

    # 3. Manager IT alone in IT -> fallback to ADMIN (exclude actor herself)
    target_mgr_alone = resolve_support_target(manager_it)
    assert target_mgr_alone["kind"] == "ADMIN"
    assert target_mgr_alone["department_id"] is None

    # 4. If another manager exists in IT -> routes to DEPARTMENT IT
    second_mgr = User.objects.create_user(
        username="w5_second_mgr_it",
        email="second_mgr@assethub.local",
        password=None,
        role=User.Role.MANAGER,
        department=it,
        full_name="Quản lý IT 2",
        is_active=True,
    )
    target_with_peer = resolve_support_target(manager_it)
    assert target_with_peer["kind"] == "DEPARTMENT"
    assert target_with_peer["department_id"] == it.id

    # 5. Inactive manager does not count -> fallback ADMIN
    second_mgr.is_active = False
    second_mgr.save()
    assert resolve_support_target(manager_it)["kind"] == "ADMIN"


def test_ticket_does_not_grant_conversation_access(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]
    # Manager can see the ticket via get_visible_ticket
    ticket = get_visible_ticket(actor=manager_it, ticket_id=open_ticket.id)
    assert ticket.id == open_ticket.id

    # But staff cannot reserve or mutate turns in the owner's conversation
    with pytest.raises(PermissionDenied):
        reserve_turn(
            actor=manager_it,
            conversation=open_ticket.turn.conversation,
            client_request_id=uuid.uuid4(),
            question="Nhân viên IT xâm nhập cuộc trò chuyện",
        )


def test_duplicate_turn_and_manual_escalation_are_idempotent(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    req_id = uuid.uuid4()
    turn1, is_new1 = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=req_id,
        question="Cần hỗ trợ thiết bị",
    )
    assert is_new1 is True

    # Duplicate turn reservation
    turn2, is_new2 = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=req_id,
        question="Cần hỗ trợ thiết bị",
    )
    assert is_new2 is False
    assert turn2.id == turn1.id

    # Finalize turn before escalation per Point 3
    turn1.outcome = ChatTurn.Outcome.ANSWERED
    turn1.save()

    # Duplicate escalation
    ticket1 = escalate_turn(actor=user_it, turn=turn1)
    ticket2 = escalate_turn(actor=user_it, turn=turn1)
    assert ticket1.id == ticket2.id
    assert SupportTicket.objects.filter(turn=turn1).count() == 1


def test_escalate_turn_wrong_owner_raises_permission_denied(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]
    with pytest.raises(PermissionDenied):
        escalate_turn(actor=manager_it, turn=open_ticket.turn)


def test_escalate_turn_question_snapshot_context(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    t1, _ = reserve_turn(actor=user_it, conversation=conv, client_request_id=uuid.uuid4(), question="Q1: Laptop bị chậm")
    t1.outcome = ChatTurn.Outcome.ANSWERED
    t1.save()

    t2, _ = reserve_turn(actor=user_it, conversation=conv, client_request_id=uuid.uuid4(), question="Q2: Pin có chai không")
    t2.outcome = ChatTurn.Outcome.ANSWERED
    t2.save()

    t3, _ = reserve_turn(actor=user_it, conversation=conv, client_request_id=uuid.uuid4(), question="Q3: Đổi máy được không")
    t3.outcome = ChatTurn.Outcome.ANSWERED
    t3.save()

    t4, _ = reserve_turn(actor=user_it, conversation=conv, client_request_id=uuid.uuid4(), question="Q4: Cần hỗ trợ khẩn cấp")

    snapshot = build_question_snapshot(t4)
    # Should include last 2 prior questions (Q2, Q3) and current question (Q4)
    assert "Q2: Pin có chai không" in snapshot
    assert "Q3: Đổi máy được không" in snapshot
    assert "Q4: Cần hỗ trợ khẩn cấp" in snapshot
    assert "Q1: Laptop bị chậm" not in snapshot


def test_escalate_turn_snapshot_truncation(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    t1, _ = reserve_turn(actor=user_it, conversation=conv, client_request_id=uuid.uuid4(), question="A" * 1000)
    t1.outcome = ChatTurn.Outcome.ANSWERED
    t1.save()

    t2, _ = reserve_turn(actor=user_it, conversation=conv, client_request_id=uuid.uuid4(), question="B" * 1000)
    t2.outcome = ChatTurn.Outcome.ANSWERED
    t2.save()

    t3, _ = reserve_turn(actor=user_it, conversation=conv, client_request_id=uuid.uuid4(), question="C" * 1000)

    # 1000 + 2 + 1000 + 2 + 1000 = 3004 chars, truncated to 3000
    snapshot = build_question_snapshot(t3)
    assert len(snapshot) == 3000
    assert snapshot.startswith("A" * 1000)


def test_failed_or_unknown_commit_never_claims_handoff(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn, _ = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Kiểm tra lỗi commit",
    )
    turn.outcome = ChatTurn.Outcome.ERROR
    turn.save()

    # 1. D1 batch error without confirmed commit in readback -> raises ServiceUnavailable
    with patch("apps.assistant.support._is_d1", return_value=True), \
         patch("assethub_api.backends.d1.d1_batch", side_effect=Exception("D1 bridge dropped connection")):
        with pytest.raises(ServiceUnavailable):
            escalate_turn(actor=user_it, turn=turn)

        # Ticket must NOT exist
        assert not SupportTicket.objects.filter(turn=turn).exists()

    # 2. D1 batch error but readback confirms commit occurred -> successfully recovers
    conv2 = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn2, _ = reserve_turn(
        actor=user_it,
        conversation=conv2,
        client_request_id=uuid.uuid4(),
        question="Lượt hỏi thứ 2 test recovery",
    )
    turn2.outcome = ChatTurn.Outcome.ANSWERED
    turn2.save()
    ticket_id = uuid.uuid5(uuid.NAMESPACE_URL, f"assethub-assistant-ticket:{turn2.id}")
    def side_effect_with_commit(*args, **kwargs):
        # Simulate background commit landed before timeout
        ChatTurn.objects.filter(id=turn2.id).update(outcome=ChatTurn.Outcome.ESCALATED)
        SupportTicket.objects.create(
            id=ticket_id,
            turn=turn2,
            target_kind="DEPARTMENT",
            target_department_id=user_it.department_id,
            question_snapshot=turn2.question,
            status=SupportTicket.Status.OPEN,
            version=1,
        )
        raise Exception("Timeout after commit")

    with patch("apps.assistant.support._is_d1", return_value=True), \
         patch("assethub_api.backends.d1.d1_batch", side_effect=side_effect_with_commit):
        recovered_ticket = escalate_turn(actor=user_it, turn=turn2)
        assert recovered_ticket.id == ticket_id


def test_reply_occ_and_replay(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]
    reply_id = uuid.uuid4()

    # 1. Initial reply succeeds (version 1 -> 2)
    ticket = reply_ticket(
        actor=manager_it,
        ticket_id=open_ticket.id,
        reply="Đã tiếp nhận yêu cầu.",
        expected_version=1,
        client_request_id=reply_id,
    )
    assert ticket.status == SupportTicket.Status.ANSWERED
    assert ticket.version == 2
    assert ticket.reply == "Đã tiếp nhận yêu cầu."
    assert ticket.replied_by == manager_it

    # 2. Concurrent reply using old expected_version=1 fails with ConflictError
    other_mgr = User.objects.create_user(
        username="w5_concurrent_mgr",
        email="concurrent_mgr@assethub.local",
        password=None,
        role=User.Role.MANAGER,
        department=assistant_graph["it"],
        full_name="Quản lý đồng thời",
    )
    with pytest.raises(ConflictError) as exc:
        reply_ticket(
            actor=other_mgr,
            ticket_id=open_ticket.id,
            reply="Tôi cũng muốn trả lời.",
            expected_version=1,
            client_request_id=uuid.uuid4(),
        )
    assert "TICKET_ALREADY_ANSWERED" in str(exc.value) or "STALE_VERSION_CONFLICT" in str(exc.value)

    # 3. Idempotent replay with same client_request_id, same actor, same text succeeds
    replay = reply_ticket(
        actor=manager_it,
        ticket_id=open_ticket.id,
        reply="Đã tiếp nhận yêu cầu.",
        expected_version=1,
        client_request_id=reply_id,
    )
    assert replay.id == ticket.id
    assert replay.reply == "Đã tiếp nhận yêu cầu."

    # 4. Replay with same client_request_id but different text raises ConflictError
    with pytest.raises(ConflictError) as exc:
        reply_ticket(
            actor=manager_it,
            ticket_id=open_ticket.id,
            reply="Nội dung khác với lần gửi trước",
            expected_version=1,
            client_request_id=reply_id,
        )
    assert "REPLY_REQUEST_ID_CONFLICT" in str(exc.value)

    # 5. Replay with same client_request_id but different actor raises ConflictError
    with pytest.raises(ConflictError) as exc:
        reply_ticket(
            actor=other_mgr,
            ticket_id=open_ticket.id,
            reply="Đã tiếp nhận yêu cầu.",
            expected_version=1,
            client_request_id=reply_id,
        )
    assert "REPLY_REQUEST_ID_CONFLICT" in str(exc.value)


def test_reply_permissions(db, assistant_graph, open_ticket):
    admin = assistant_graph["admin"]
    manager_hr = assistant_graph["manager_hr"]
    user_it = assistant_graph["user_it"]

    # Regular user cannot reply
    with pytest.raises(PermissionDenied):
        reply_ticket(
            actor=user_it,
            ticket_id=open_ticket.id,
            reply="Nhân viên tự trả lời",
            expected_version=1,
            client_request_id=uuid.uuid4(),
        )

    # Manager from other department (HR) cannot reply to IT ticket
    with pytest.raises(PermissionDenied):
        reply_ticket(
            actor=manager_hr,
            ticket_id=open_ticket.id,
            reply="Quản lý HR can thiệp IT",
            expected_version=1,
            client_request_id=uuid.uuid4(),
        )

    # Admin can reply to department ticket
    reply_id = uuid.uuid4()
    admin_ticket = reply_ticket(
        actor=admin,
        ticket_id=open_ticket.id,
        reply="Admin duyệt hỗ trợ.",
        expected_version=1,
        client_request_id=reply_id,
    )
    assert admin_ticket.status == SupportTicket.Status.ANSWERED
    assert admin_ticket.replied_by == admin


def test_reply_validation(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]

    # Empty reply
    with pytest.raises(ValidationError):
        reply_ticket(
            actor=manager_it,
            ticket_id=open_ticket.id,
            reply="   ",
            expected_version=1,
            client_request_id=uuid.uuid4(),
        )

    # Too long reply (> 4000)
    with pytest.raises(ValidationError):
        reply_ticket(
            actor=manager_it,
            ticket_id=open_ticket.id,
            reply="X" * 4001,
            expected_version=1,
            client_request_id=uuid.uuid4(),
        )


def test_visible_tickets_mine_and_inbox(db, assistant_graph, open_ticket):
    admin = assistant_graph["admin"]
    manager_it = assistant_graph["manager_it"]
    manager_hr = assistant_graph["manager_hr"]
    user_it = assistant_graph["user_it"]

    # 1. User sees their own tickets under 'mine'
    user_mine = visible_tickets(actor=user_it, view="mine")
    assert user_mine.filter(id=open_ticket.id).exists()

    # User inbox is empty
    assert visible_tickets(actor=user_it, view="inbox").count() == 0

    # 2. Manager IT sees IT ticket in inbox
    mgr_inbox = visible_tickets(actor=manager_it, view="inbox")
    assert mgr_inbox.filter(id=open_ticket.id).exists()

    # 3. Manager HR does NOT see IT ticket in inbox
    hr_inbox = visible_tickets(actor=manager_hr, view="inbox")
    assert not hr_inbox.filter(id=open_ticket.id).exists()

    # 4. Admin sees all tickets in inbox
    admin_inbox = visible_tickets(actor=admin, view="inbox")
    assert admin_inbox.filter(id=open_ticket.id).exists()

    # 5. User changes department -> old tickets excluded from 'mine'
    user_it.department = assistant_graph["hr"]
    user_it.save()
    assert visible_tickets(actor=user_it, view="mine").count() == 0


def test_get_visible_ticket_access_control(db, assistant_graph, open_ticket):
    admin = assistant_graph["admin"]
    manager_it = assistant_graph["manager_it"]
    user_hr = assistant_graph["user_hr"]
    user_it = assistant_graph["user_it"]

    # Owner can view
    assert get_visible_ticket(actor=user_it, ticket_id=open_ticket.id).id == open_ticket.id

    # Department manager can view
    assert get_visible_ticket(actor=manager_it, ticket_id=open_ticket.id).id == open_ticket.id

    # Admin can view
    assert get_visible_ticket(actor=admin, ticket_id=open_ticket.id).id == open_ticket.id

    # Unrelated user gets 404 NotFound (not exposing ticket existence)
    with pytest.raises(NotFound):
        get_visible_ticket(actor=user_hr, ticket_id=open_ticket.id)


def test_escalate_turn_processing_outcome_raises_conflict(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn, _ = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Hỏi câu hỏi",
    )
    # Point 3: PROCESSING turns cannot be escalated directly
    assert turn.outcome == ChatTurn.Outcome.PROCESSING
    with pytest.raises(ConflictError) as exc:
        escalate_turn(actor=user_it, turn=turn)
    assert "TURN_STATE_CONFLICT" in str(exc.value)


def test_escalate_turn_out_of_scope_outcome_raises_conflict(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn, _ = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Hỏi câu hỏi ngoài phạm vi",
    )
    turn.outcome = ChatTurn.Outcome.OUT_OF_SCOPE
    turn.save()
    # Point 3: OUT_OF_SCOPE turns cannot be escalated
    with pytest.raises(ConflictError) as exc:
        escalate_turn(actor=user_it, turn=turn)
    assert "TURN_STATE_CONFLICT" in str(exc.value)


def test_escalate_turn_scope_change_raises_conflict(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn, _ = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Câu hỏi test đổi role",
    )
    turn.outcome = ChatTurn.Outcome.ERROR
    turn.save()

    # Point 4: User changes role from USER to MANAGER after turn ERROR
    user_it.role = User.Role.MANAGER
    user_it.save()
    with pytest.raises(ConflictError) as exc:
        escalate_turn(actor=user_it, turn=turn)
    assert "CONVERSATION_SCOPE_CHANGED" in str(exc.value)


def test_reply_ticket_d1_outage_before_commit_raises_service_unavailable(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]
    # Point 2: Outage before commit (readback shows still OPEN, version 1) raises ServiceUnavailable
    with patch("apps.assistant.support._is_d1", return_value=True), \
         patch("assethub_api.backends.d1.d1_batch", side_effect=Exception("D1 connection lost before execute")):
        with pytest.raises(ServiceUnavailable):
            reply_ticket(
                actor=manager_it,
                ticket_id=open_ticket.id,
                reply="Phản hồi thử nghiệm",
                expected_version=1,
                client_request_id=uuid.uuid4(),
            )
    open_ticket.refresh_from_db()
    assert open_ticket.status == SupportTicket.Status.OPEN
    assert open_ticket.version == 1


def test_reply_ticket_d1_commit_landed_response_lost_recovers(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]
    req_id = uuid.uuid4()
    # Point 2: Commit landed in D1 but response timed out -> successfully recovered
    def side_effect_landed(*args, **kwargs):
        SupportTicket.objects.filter(id=open_ticket.id).update(
            status=SupportTicket.Status.ANSWERED,
            version=2,
            reply="Phản hồi đã ghi thành công",
            replied_by=manager_it,
            replied_at=timezone.now(),
            reply_request_id=req_id,
        )
        raise Exception("Timeout reading HTTP response from Cloudflare")

    with patch("apps.assistant.support._is_d1", return_value=True), \
         patch("assethub_api.backends.d1.d1_batch", side_effect=side_effect_landed):
        recovered = reply_ticket(
            actor=manager_it,
            ticket_id=open_ticket.id,
            reply="Phản hồi đã ghi thành công",
            expected_version=1,
            client_request_id=req_id,
        )
        assert recovered.status == SupportTicket.Status.ANSWERED
        assert recovered.reply == "Phản hồi đã ghi thành công"


def test_reply_ticket_d1_failed_readback_raises_service_unavailable(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]
    # Point 2: D1 batch throws and readback refresh_from_db also fails -> ServiceUnavailable
    with patch("apps.assistant.support._is_d1", return_value=True), \
         patch("assethub_api.backends.d1.d1_batch", side_effect=Exception("D1 total outage")), \
         patch.object(SupportTicket, "refresh_from_db", side_effect=Exception("Cannot read from DB")):
        with pytest.raises(ServiceUnavailable):
            reply_ticket(
                actor=manager_it,
                ticket_id=open_ticket.id,
                reply="Phản hồi thử nghiệm",
                expected_version=1,
                client_request_id=uuid.uuid4(),
            )


def test_reply_ticket_d1_guard_trips_real_occ_loser_raises_stale_version_conflict(db, assistant_graph, open_ticket):
    manager_it = assistant_graph["manager_it"]
    # Point 2: Real OCC loser: guard trips with rows_affected CHECK constraint failed
    with patch("apps.assistant.support._is_d1", return_value=True), \
         patch("assethub_api.backends.d1.d1_batch", side_effect=Exception("CHECK constraint failed on _assert_guard rows_affected")):
        with pytest.raises(ConflictError) as exc:
            reply_ticket(
                actor=manager_it,
                ticket_id=open_ticket.id,
                reply="Phản hồi thử nghiệm",
                expected_version=1,
                client_request_id=uuid.uuid4(),
            )
        assert "STALE_VERSION_CONFLICT" in str(exc.value)
