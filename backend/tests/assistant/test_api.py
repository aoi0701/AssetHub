import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from apps.assistant import contracts
from apps.assistant.models import ChatTurn, Conversation, SupportTicket
from apps.assistant.storage import reserve_turn
from apps.assistant.support import escalate_turn
from apps.authentication.models import User


@pytest.fixture(autouse=True)
def enable_assistant_and_clear_cache(settings):
    settings.ASSISTANT_ENABLED = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def other_user(db, dept_hr):
    return User.objects.create_user(
        username="w5_other_user",
        email="other_user@assethub.local",
        password=None,
        role=User.Role.USER,
        department=dept_hr,
        full_name="Other Department User",
    )


# ---------------------------------------------------------------------------
# 1. Authentication & Feature Gate Tests
# ---------------------------------------------------------------------------

def test_anonymous_user_returns_401(db):
    client = APIClient()
    conv_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    ticket_id = uuid.uuid4()

    assert client.get("/api/assistant/conversations/").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.post("/api/assistant/conversations/", {}).status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get(f"/api/assistant/conversations/{conv_id}/turns/").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.post(f"/api/assistant/conversations/{conv_id}/turns/", {}).status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get(f"/api/assistant/turns/{turn_id}/").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.post(f"/api/assistant/turns/{turn_id}/escalate/", {}).status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get("/api/assistant/procedures/allocate/").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get("/api/assistant/support-tickets/").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get(f"/api/assistant/support-tickets/{ticket_id}/").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.post(f"/api/assistant/support-tickets/{ticket_id}/reply/", {}).status_code == status.HTTP_401_UNAUTHORIZED


def test_assistant_disabled_returns_503(db, regular_user, settings):
    settings.ASSISTANT_ENABLED = False
    client = APIClient()
    client.force_authenticate(user=regular_user)

    res = client.get("/api/assistant/conversations/")
    assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert res.data["code"] == "ASSISTANT_DISABLED"
    assert "disabled" in res.data["detail"]


# ---------------------------------------------------------------------------
# 2. Conversation Endpoints
# ---------------------------------------------------------------------------

def test_conversation_list_and_create(db, regular_user):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    # 1. Create conversation with empty dict
    res = client.post("/api/assistant/conversations/", {}, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    conv_id = res.data["id"]
    assert conv_id is not None

    # Strict: extra field rejected
    bad_res = client.post("/api/assistant/conversations/", {"extra": "field"}, format="json")
    assert bad_res.status_code == status.HTTP_400_BAD_REQUEST

    # 2. List conversations
    list_res = client.get("/api/assistant/conversations/")
    assert list_res.status_code == status.HTTP_200_OK
    assert any(item["id"] == conv_id for item in list_res.data["results"])

    # 3. Scope change hides conversation from list
    regular_user.role = User.Role.MANAGER
    regular_user.save()
    list_res2 = client.get("/api/assistant/conversations/")
    assert not any(item["id"] == conv_id for item in list_res2.data["results"])


# ---------------------------------------------------------------------------
# 3. Security: Foreign Conversation & Scope Mismatch
# ---------------------------------------------------------------------------

def test_foreign_conversation_returns_404(db, regular_user, other_user, conversation):
    client = APIClient()
    client.force_authenticate(user=other_user)

    # Cannot list turns
    res = client.get(f"/api/assistant/conversations/{conversation.id}/turns/")
    assert res.status_code == status.HTTP_404_NOT_FOUND

    # Cannot ask in foreign conversation
    req_body = {
        "client_request_id": str(uuid.uuid4()),
        "question": "Hỏi trộm",
    }
    res_post = client.post(f"/api/assistant/conversations/{conversation.id}/turns/", req_body, format="json")
    assert res_post.status_code == status.HTTP_404_NOT_FOUND


def test_scope_mismatch_returns_409(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    # Change role
    regular_user.role = User.Role.MANAGER
    regular_user.save()

    res_get = client.get(f"/api/assistant/conversations/{conversation.id}/turns/")
    assert res_get.status_code == status.HTTP_409_CONFLICT
    assert res_get.data["code"] == "CONVERSATION_SCOPE_CHANGED"

    req_body = {
        "client_request_id": str(uuid.uuid4()),
        "question": "Hỏi sau khi đổi quyền",
    }
    res_post = client.post(f"/api/assistant/conversations/{conversation.id}/turns/", req_body, format="json")
    assert res_post.status_code == status.HTTP_409_CONFLICT
    assert res_post.data["code"] == "CONVERSATION_SCOPE_CHANGED"


def test_turn_detail_scope_mismatch_returns_409(db, regular_user, dept_hr, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    turn = ChatTurn.objects.create(
        conversation=conversation,
        client_request_id=uuid.uuid4(),
        question="Câu hỏi kiểm tra scope turn detail",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Đáp án ban đầu",
        sources=[],
    )

    # 1. Change role to MANAGER -> 409 CONVERSATION_SCOPE_CHANGED
    regular_user.role = User.Role.MANAGER
    regular_user.save()

    res = client.get(f"/api/assistant/turns/{turn.id}/")
    assert res.status_code == status.HTTP_409_CONFLICT
    assert res.data["code"] == "CONVERSATION_SCOPE_CHANGED"

    # 2. Reset role, change department -> 409 CONVERSATION_SCOPE_CHANGED
    regular_user.role = User.Role.USER
    regular_user.department = dept_hr
    regular_user.save()

    res_dept = client.get(f"/api/assistant/turns/{turn.id}/")
    assert res_dept.status_code == status.HTTP_409_CONFLICT
    assert res_dept.data["code"] == "CONVERSATION_SCOPE_CHANGED"


def test_stale_turn_auto_expires_on_read(db, regular_user, conversation):
    import datetime
    from django.utils import timezone

    client = APIClient()
    client.force_authenticate(user=regular_user)

    # Turn created 181s in the past
    stale_turn = ChatTurn.objects.create(
        conversation=conversation,
        client_request_id=uuid.uuid4(),
        question="Câu hỏi bị treo lâu",
        outcome=ChatTurn.Outcome.PROCESSING,
        answer="",
        sources=[],
    )
    past_time = timezone.now() - datetime.timedelta(seconds=181)
    ChatTurn.objects.filter(id=stale_turn.id).update(created_at=past_time)

    # GET /turns/{id}/ expires stale turn to ERROR and returns 200
    res = client.get(f"/api/assistant/turns/{stale_turn.id}/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["outcome"] == "ERROR"
    assert "quá thời gian xử lý" in res.data["answer"]

    # Re-check list turns also reflects ERROR
    list_res = client.get(f"/api/assistant/conversations/{conversation.id}/turns/")
    assert list_res.status_code == status.HTTP_200_OK
    matched = next(t for t in list_res.data["results"] if t["id"] == str(stale_turn.id))
    assert matched["outcome"] == "ERROR"


def test_client_request_id_conflict_code(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    req_id = uuid.uuid4()
    ChatTurn.objects.create(
        conversation=conversation,
        client_request_id=req_id,
        question="Câu hỏi gốc",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Đáp án",
        sources=[],
    )

    # Same request_id, different question -> 409 CLIENT_REQUEST_ID_CONFLICT
    res = client.post(
        f"/api/assistant/conversations/{conversation.id}/turns/",
        {"client_request_id": str(req_id), "question": "Câu hỏi khác với request id cũ"},
        format="json",
    )
    assert res.status_code == status.HTTP_409_CONFLICT
    assert res.data["code"] == "CLIENT_REQUEST_ID_CONFLICT"


# ---------------------------------------------------------------------------
# 4. Turn Endpoints: Ask, Replay, Throttling & Outage
# ---------------------------------------------------------------------------

def test_turn_list_and_ask_success(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    req_id = uuid.uuid4()
    req_body = {
        "client_request_id": str(req_id),
        "question": "Tôi có bao nhiêu máy tính?",
    }

    # Mock ChatService.ask to return a new answered turn
    fake_turn = ChatTurn(
        id=uuid.uuid4(),
        conversation=conversation,
        client_request_id=req_id,
        question=req_body["question"],
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Bạn có 1 máy tính.",
        sources=[],
    )
    fake_turn.is_replay = False

    with patch("apps.assistant.views.ChatService.ask", return_value=fake_turn):
        res = client.post(f"/api/assistant/conversations/{conversation.id}/turns/", req_body, format="json")
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data["answer"] == "Bạn có 1 máy tính."
        assert res.data["outcome"] == "ANSWERED"


def test_replay_returns_200_or_202(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    req_id = uuid.uuid4()
    req_body = {"client_request_id": str(req_id), "question": "Hỏi lại"}

    # Completed replay -> 200
    answered_turn = ChatTurn(
        id=uuid.uuid4(),
        conversation=conversation,
        client_request_id=req_id,
        question=req_body["question"],
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Đã trả lời",
        sources=[],
    )
    answered_turn.is_replay = True
    with patch("apps.assistant.views.ChatService.ask", return_value=answered_turn):
        res = client.post(f"/api/assistant/conversations/{conversation.id}/turns/", req_body, format="json")
        assert res.status_code == status.HTTP_200_OK

    # In-progress replay -> 202
    processing_turn = ChatTurn(
        id=uuid.uuid4(),
        conversation=conversation,
        client_request_id=req_id,
        question=req_body["question"],
        outcome=ChatTurn.Outcome.PROCESSING,
        answer="",
        sources=[],
    )
    processing_turn.is_replay = True
    with patch("apps.assistant.views.ChatService.ask", return_value=processing_turn):
        res2 = client.post(f"/api/assistant/conversations/{conversation.id}/turns/", req_body, format="json")
        assert res2.status_code == status.HTTP_202_ACCEPTED


def test_provider_unavailable_returns_503_with_turn(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    req_id = uuid.uuid4()
    req_body = {"client_request_id": str(req_id), "question": "Hỏi khi AI chết"}

    error_turn = ChatTurn.objects.create(
        conversation=conversation,
        client_request_id=req_id,
        question=req_body["question"],
        outcome=ChatTurn.Outcome.ERROR,
        answer="Dịch vụ trợ lý tạm thời không khả dụng.",
        sources=[],
    )
    exc = contracts.AssistantProviderUnavailable("AI Worker 503")
    exc.turn = error_turn

    with patch("apps.assistant.views.ChatService.ask", side_effect=exc):
        res = client.post(f"/api/assistant/conversations/{conversation.id}/turns/", req_body, format="json")
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert res.data["code"] == "ASSISTANT_UNAVAILABLE"
        assert res.data["turn"]["id"] == str(error_turn.id)
        assert res.data["turn"]["outcome"] == "ERROR"


def test_gateway_planner_protocol_error_maps_to_503_and_turn_error(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    req_id = uuid.uuid4()
    req_body = {
        "client_request_id": str(req_id),
        "question": "Hỏi khi planner protocol lỗi",
    }

    with patch(
        "apps.assistant.gateway.plan_question",
        side_effect=contracts.PlannerProtocolError("Invalid JSON structure"),
    ):
        res = client.post(
            f"/api/assistant/conversations/{conversation.id}/turns/",
            req_body,
            format="json",
        )
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert res.data["code"] == "ASSISTANT_UNAVAILABLE"
        assert res.data["turn"]["outcome"] == "ERROR"

    turn_in_db = ChatTurn.objects.get(client_request_id=req_id)
    assert turn_in_db.outcome == ChatTurn.Outcome.ERROR


def test_execute_tool_planner_protocol_error_maps_to_503_and_turn_error(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    req_id = uuid.uuid4()
    req_body = {
        "client_request_id": str(req_id),
        "question": "Hỏi khi tool execution protocol lỗi",
    }

    valid_tools_decision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"keyword": "laptop"},
            }
        ],
        "clarification": None,
        "reason": None,
    }

    with patch("apps.assistant.gateway.plan_question", return_value=valid_tools_decision), \
         patch("apps.assistant.tools.execute_tool", side_effect=contracts.PlannerProtocolError("Invalid tool arguments")):
        res = client.post(
            f"/api/assistant/conversations/{conversation.id}/turns/",
            req_body,
            format="json",
        )
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert res.data["code"] == "ASSISTANT_UNAVAILABLE"
        assert res.data["turn"]["outcome"] == "ERROR"

    turn_in_db = ChatTurn.objects.get(client_request_id=req_id)
    assert turn_in_db.outcome == ChatTurn.Outcome.ERROR


def test_tool_unsupported_precedence_http_probe_creates_ticket(db, regular_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    req_id = uuid.uuid4()
    req_body = {
        "client_request_id": str(req_id),
        "question": "Hỏi yêu cầu unsupported để tạo ticket",
    }

    valid_tools_decision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"keyword": "laptop"},
            }
        ],
        "clarification": None,
        "reason": None,
    }

    unsupported_tool_result = {
        "tool": "assets.search",
        "status": "unsupported",
        "data": {"message": "Yêu cầu cần hỗ trợ thủ công."},
        "sources": [],
        "scope_label": "HR",
        "observed_at": "2026-11-05T00:00:00Z",
    }

    with patch("apps.assistant.gateway.plan_question", return_value=valid_tools_decision), \
         patch("apps.assistant.tools.execute_tool", return_value=unsupported_tool_result):
        res = client.post(
            f"/api/assistant/conversations/{conversation.id}/turns/",
            req_body,
            format="json",
        )
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data["outcome"] == "ESCALATED"
        assert res.data["ticket"] is not None
        assert res.data["sources"] == []

        # Assert replay returns 200 and only 1 ticket exists in DB
        res_replay = client.post(
            f"/api/assistant/conversations/{conversation.id}/turns/",
            req_body,
            format="json",
        )
        assert res_replay.status_code == status.HTTP_200_OK
        assert res_replay.data["outcome"] == "ESCALATED"
        assert res_replay.data["ticket"] is not None
        assert SupportTicket.objects.filter(turn_id=res.data["id"]).count() == 1


def test_rate_limit_10_per_min_and_replay_exemption(db, regular_user, conversation):
    cache.clear()
    client = APIClient()
    client.force_authenticate(user=regular_user)

    clarify_decision = {
        "kind": "clarify",
        "calls": [],
        "clarification": "Làm rõ",
        "reason": None,
    }

    turn_req_ids = []
    with patch("apps.assistant.gateway.plan_question", return_value=clarify_decision):
        for i in range(10):
            req_id = uuid.uuid4()
            turn_req_ids.append(req_id)
            res = client.post(
                f"/api/assistant/conversations/{conversation.id}/turns/",
                {"client_request_id": str(req_id), "question": f"Câu hỏi {i}"},
                format="json",
            )
            assert res.status_code == status.HTTP_201_CREATED

        # 11th request triggers rate limit 429
        res_11 = client.post(
            f"/api/assistant/conversations/{conversation.id}/turns/",
            {"client_request_id": str(uuid.uuid4()), "question": "Câu hỏi thứ 11"},
            format="json",
        )
        assert res_11.status_code == status.HTTP_429_TOO_MANY_REQUESTS

        # Replay of 1st request still succeeds without rate limit bump
        replay_res = client.post(
            f"/api/assistant/conversations/{conversation.id}/turns/",
            {"client_request_id": str(turn_req_ids[0]), "question": "Câu hỏi 0"},
            format="json",
        )
        assert replay_res.status_code in (status.HTTP_200_OK, status.HTTP_202_ACCEPTED)


# ---------------------------------------------------------------------------
# 5. Turn Detail & Escalation
# ---------------------------------------------------------------------------

def test_turn_detail_and_foreign_protection(db, regular_user, other_user, conversation):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    turn = ChatTurn.objects.create(
        conversation=conversation,
        client_request_id=uuid.uuid4(),
        question="Chi tiết lượt hỏi",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Đáp án chi tiết",
        sources=[],
    )

    # Owner can get
    res = client.get(f"/api/assistant/turns/{turn.id}/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["id"] == str(turn.id)
    assert res.data["answer"] == "Đáp án chi tiết"

    # Foreign user gets 404
    client_other = APIClient()
    client_other.force_authenticate(user=other_user)
    res_other = client_other.get(f"/api/assistant/turns/{turn.id}/")
    assert res_other.status_code == status.HTTP_404_NOT_FOUND


def test_turn_escalate_endpoint(db, assistant_graph):
    user_it = assistant_graph["user_it"]
    other_user = assistant_graph["user_hr"]
    conv = Conversation.objects.create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn = ChatTurn.objects.create(
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Cần chuyển hỗ trợ khẩn cấp",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Câu trả lời tạm",
        sources=[],
    )

    client = APIClient()
    client.force_authenticate(user=user_it)

    # 1. Escalate turn -> 201
    res = client.post(f"/api/assistant/turns/{turn.id}/escalate/", {}, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    assert res.data["status"] == "OPEN"
    assert res.data["target_kind"] == "DEPARTMENT"

    # 2. Idempotent re-escalate -> 200
    res_re = client.post(f"/api/assistant/turns/{turn.id}/escalate/", {}, format="json")
    assert res_re.status_code == status.HTTP_200_OK
    assert res_re.data["id"] == res.data["id"]

    # 3. Foreign user cannot escalate
    client_other = APIClient()
    client_other.force_authenticate(user=other_user)
    assert client_other.post(f"/api/assistant/turns/{turn.id}/escalate/", {}, format="json").status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# 6. Procedure Guidance Endpoints
# ---------------------------------------------------------------------------

def test_procedure_detail_endpoint(db, regular_user):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    # Valid topic
    res = client.get("/api/assistant/procedures/allocate/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["topic"] == "allocate"
    assert "version" in res.data
    assert "title" in res.data
    assert "body" in res.data
    assert isinstance(res.data["actions"], list)

    # Unknown topic -> 404
    res_bad = client.get("/api/assistant/procedures/unknown_xyz/")
    assert res_bad.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# 7. Support Ticket Inbox & Reply Endpoints
# ---------------------------------------------------------------------------

def test_support_ticket_list_and_permissions(db, assistant_graph):
    admin = assistant_graph["admin"]
    mgr_it = assistant_graph["manager_it"]
    mgr_hr = assistant_graph["manager_hr"]
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
        question="Cần đổi laptop IT",
    )
    turn.outcome = ChatTurn.Outcome.ANSWERED
    turn.save()
    ticket = escalate_turn(actor=user_it, turn=turn)

    client = APIClient()

    # 1. User sees under 'mine'
    client.force_authenticate(user=user_it)
    res_mine = client.get("/api/assistant/support-tickets/?view=mine")
    assert res_mine.status_code == status.HTTP_200_OK
    assert any(t["id"] == str(ticket.id) for t in res_mine.data["results"])

    # User cannot view 'inbox'
    res_inbox_user = client.get("/api/assistant/support-tickets/?view=inbox")
    assert res_inbox_user.status_code == status.HTTP_403_FORBIDDEN

    # 2. Manager IT sees IT ticket in inbox
    client.force_authenticate(user=mgr_it)
    res_inbox_mgr = client.get("/api/assistant/support-tickets/?view=inbox")
    assert res_inbox_mgr.status_code == status.HTTP_200_OK
    assert any(t["id"] == str(ticket.id) for t in res_inbox_mgr.data["results"])

    # Manager HR does not see IT ticket in inbox
    client.force_authenticate(user=mgr_hr)
    res_inbox_hr = client.get("/api/assistant/support-tickets/?view=inbox")
    assert res_inbox_hr.status_code == status.HTTP_200_OK
    assert not any(t["id"] == str(ticket.id) for t in res_inbox_hr.data["results"])

    # 3. Admin sees in inbox
    client.force_authenticate(user=admin)
    res_inbox_admin = client.get("/api/assistant/support-tickets/?view=inbox")
    assert res_inbox_admin.status_code == status.HTTP_200_OK
    assert any(t["id"] == str(ticket.id) for t in res_inbox_admin.data["results"])


def test_support_ticket_reply_and_occ(db, assistant_graph):
    mgr_it = assistant_graph["manager_it"]
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
        question="Xin cấp chuột mới",
    )
    turn.outcome = ChatTurn.Outcome.ANSWERED
    turn.save()
    ticket = escalate_turn(actor=user_it, turn=turn)

    client = APIClient()
    client.force_authenticate(user=mgr_it)

    # 1. OCC clash: ticket is OPEN but at version 2, client supplies expected_version=1
    SupportTicket.objects.filter(id=ticket.id).update(version=2, status=SupportTicket.Status.OPEN)
    res_conflict = client.post(
        f"/api/assistant/support-tickets/{ticket.id}/reply/",
        {"client_request_id": str(uuid.uuid4()), "expected_version": 1, "reply": "Phản hồi với version cũ"},
        format="json",
    )
    assert res_conflict.status_code == status.HTTP_409_CONFLICT
    assert res_conflict.data["code"] == "STALE_VERSION_CONFLICT"

    # 2. Reply succeeds with correct expected_version=2 -> 200, version becomes 3
    req_id = uuid.uuid4()
    reply_payload = {
        "client_request_id": str(req_id),
        "expected_version": 2,
        "reply": "Đã duyệt cấp chuột mới.",
    }
    res = client.post(f"/api/assistant/support-tickets/{ticket.id}/reply/", reply_payload, format="json")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["status"] == "ANSWERED"
    assert res.data["version"] == 3
    assert res.data["reply"] == "Đã duyệt cấp chuột mới."

    # 3. Idempotent replay with same client_request_id returns 200
    res_replay = client.post(
        f"/api/assistant/support-tickets/{ticket.id}/reply/",
        reply_payload,
        format="json",
    )
    assert res_replay.status_code == status.HTTP_200_OK
    assert res_replay.data["id"] == str(ticket.id)

    # 4. Replay with different reply text returns 409 REPLY_REQUEST_ID_CONFLICT
    bad_replay_payload = {
        "client_request_id": str(req_id),
        "expected_version": 2,
        "reply": "Nội dung đổi khác",
    }
    res_bad = client.post(
        f"/api/assistant/support-tickets/{ticket.id}/reply/",
        bad_replay_payload,
        format="json",
    )
    assert res_bad.status_code == status.HTTP_409_CONFLICT
    assert res_bad.data["code"] == "REPLY_REQUEST_ID_CONFLICT"

    # 5. Subsequent reply on already answered ticket with different request_id returns 409 TICKET_ALREADY_ANSWERED
    res_answered = client.post(
        f"/api/assistant/support-tickets/{ticket.id}/reply/",
        {"client_request_id": str(uuid.uuid4()), "expected_version": 3, "reply": "Gửi thêm phản hồi nữa"},
        format="json",
    )
    assert res_answered.status_code == status.HTTP_409_CONFLICT
    assert res_answered.data["code"] == "TICKET_ALREADY_ANSWERED"


# ---------------------------------------------------------------------------
# 8. Pagination Strictness
# ---------------------------------------------------------------------------

def test_pagination_strictness_rejects_bypass_params(db, regular_user):
    client = APIClient()
    client.force_authenticate(user=regular_user)

    # all=true forbidden
    res1 = client.get("/api/assistant/conversations/?all=true")
    assert res1.status_code == status.HTTP_400_BAD_REQUEST

    # pagination=false forbidden
    res2 = client.get("/api/assistant/conversations/?pagination=false")
    assert res2.status_code == status.HTTP_400_BAD_REQUEST
