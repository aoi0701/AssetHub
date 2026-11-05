import datetime
import socket
import urllib.error
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import NotFound, Throttled

from apps.assets.models import Asset
from apps.assistant import contracts
from apps.assistant.contracts import AssistantProviderUnavailable, PlannerProtocolError
from apps.assistant.models import ChatTurn, Conversation, SupportTicket
from apps.assistant.services import ChatService, build_planner_context, check_rate_limit
from apps.authentication.models import User
from apps.common.exceptions import ConflictError


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def manager_conv(assistant_graph):
    mgr = assistant_graph["manager_it"]
    return Conversation.objects.create(
        owner=mgr,
        role_snapshot=mgr.role,
        department_id_snapshot=mgr.department_id,
    )


@pytest.fixture
def user_conv(assistant_graph):
    user = assistant_graph["user_it"]
    return Conversation.objects.create(
        owner=user,
        role_snapshot=user.role,
        department_id_snapshot=user.department_id,
    )


def test_answer_uses_current_database_and_backend_sources(assistant_graph, manager_conv):
    """Dynamic facts: changing database state between turns produces fresh counts, not stale cached numbers."""
    mgr = assistant_graph["manager_it"]
    laptop_summary_decision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.summary",
                "arguments": {
                    "category": "Laptop",
                    "statuses": ["AVAILABLE"],
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }

    with patch("apps.assistant.gateway.plan_question", return_value=laptop_summary_decision):
        # Turn 1: 23 available laptops in IT
        turn1 = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=uuid.uuid4(),
            question="Phòng tôi còn bao nhiêu laptop chưa cấp?",
        )
        assert turn1.outcome == ChatTurn.Outcome.ANSWERED
        assert "23" in turn1.answer

        # Mutate database: change 1 available laptop to DAMAGED_PENDING_REPAIR
        first_laptop = Asset.objects.filter(department=mgr.department, status=Asset.Status.AVAILABLE).first()
        assert first_laptop is not None
        first_laptop.status = Asset.Status.DAMAGED_PENDING_REPAIR
        first_laptop.save(update_fields=["status"])

        # Turn 2: dynamically reflects 22 available laptops
        turn2 = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=uuid.uuid4(),
            question="Bây giờ còn mấy laptop trống?",
        )
        assert turn2.outcome == ChatTurn.Outcome.ANSWERED
        assert "22" in turn2.answer
        assert len(turn2.sources) > 0


def test_followup_revalidates_refs_and_scope(assistant_graph, user_conv):
    """Invisible refs are purged from context; changed role or department raises 409 Conflict."""
    user = assistant_graph["user_it"]

    # Seed turn with one visible asset and one foreign invisible asset
    ChatTurn.objects.create(
        conversation=user_conv,
        client_request_id=uuid.uuid4(),
        question="Máy của tôi là máy nào?",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Máy W5-IT-L24",
        sources=[
            {"kind": "asset", "key": "W5-IT-L24", "facts": {"asset_code": "W5-IT-L24"}},
            {"kind": "asset", "key": "W5-HR-L01", "facts": {"asset_code": "W5-HR-L01"}},
        ],
    )

    # Revalidation checks against visible_assets(actor)
    context = build_planner_context(actor=user, conversation=user_conv)
    assert len(context) == 1
    refs = context[0]["refs"]
    ref_codes = [r["asset_code"] for r in refs]
    assert "W5-IT-L24" in ref_codes
    assert "W5-HR-L01" not in ref_codes

    # Scope snapshot validation: changing actor role or department raises 409 CONVERSATION_SCOPE_CHANGED
    user.role = User.Role.MANAGER
    user.save(update_fields=["role"])

    with pytest.raises(ConflictError) as exc_info:
        ChatService.ask(
            actor=user,
            conversation_id=user_conv.id,
            client_request_id=uuid.uuid4(),
            question="Máy của tôi thế nào?",
        )
    assert "CONVERSATION_SCOPE_CHANGED" in str(exc_info.value)


def test_followup_context_matches_early_eval_fixture(assistant_graph, manager_conv):
    """Context matches Gate 3-L structure: [{question, refs: [{asset_code}]}], excluding PROCESSING turns."""
    mgr = assistant_graph["manager_it"]

    # Completed turn with 2 asset codes
    ChatTurn.objects.create(
        conversation=manager_conv,
        client_request_id=uuid.uuid4(),
        question="Danh sách máy kiểm tra",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Đã tìm thấy",
        sources=[
            {"kind": "asset", "key": "W5-IT-L01", "facts": {"asset_code": "W5-IT-L01"}},
            {"kind": "asset", "key": "W5-IT-L02", "facts": {"asset_code": "W5-IT-L02"}},
        ],
    )

    # In-progress PROCESSING turn must NOT be included in context
    ChatTurn.objects.create(
        conversation=manager_conv,
        client_request_id=uuid.uuid4(),
        question="Câu hỏi đang xử lý",
        outcome=ChatTurn.Outcome.PROCESSING,
    )

    context = build_planner_context(actor=mgr, conversation=manager_conv)
    assert len(context) == 1
    assert context[0]["question"] == "Danh sách máy kiểm tra"
    assert context[0]["refs"] == [
        {"asset_code": "W5-IT-L01"},
        {"asset_code": "W5-IT-L02"},
    ]


def test_planner_cannot_insert_facts_sql_or_external_citations(assistant_graph, manager_conv):
    """Malformed decision with unknown root keys is rejected by validate_decision, raising AssistantProviderUnavailable in ERROR outcome."""
    mgr = assistant_graph["manager_it"]

    malicious_decision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.summary",
                "arguments": {
                    "category": "Laptop",
                    "statuses": ["AVAILABLE"],
                },
            }
        ],
        "clarification": None,
        "reason": None,
        # Malicious root keys violating additionalProperties: false
        "injected_sources": [{"title": "Fake Citation", "url": "http://evil.com"}],
        "sql": "DROP TABLE assistant_chatturn;",
    }

    with patch("apps.assistant.gateway.plan_question", return_value=malicious_decision), \
         patch("apps.assistant.tools.execute_tool") as mock_execute:
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            ChatService.ask(
                actor=mgr,
                conversation_id=manager_conv.id,
                client_request_id=uuid.uuid4(),
                question="Laptop có bao nhiêu chiếc?",
            )
        assert hasattr(exc_info.value, "turn")
        assert exc_info.value.turn.outcome == ChatTurn.Outcome.ERROR
        # Tools are never executed when decision validation fails
        mock_execute.assert_not_called()


def test_ambiguous_zero_missing_knowledge_and_outage_are_distinct(assistant_graph, manager_conv):
    """Total 0 -> ANSWERED, clarify -> CLARIFY, handoff -> ESCALATED + ticket, outage -> ERROR + 503."""
    mgr = assistant_graph["manager_it"]

    # 1. Total 0: query yields no results, outcome is ANSWERED
    zero_result_decision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"query": "Máy in màu 3D công nghiệp siêu cấp"},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with patch("apps.assistant.gateway.plan_question", return_value=zero_result_decision):
        t_zero = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=uuid.uuid4(),
            question="Tìm máy in màu 3D",
        )
        assert t_zero.outcome == ChatTurn.Outcome.ANSWERED
        assert not hasattr(t_zero, "ticket")

    # 2. Clarify: planner requests clarification, outcome is CLARIFY
    clarify_decision = {
        "kind": "clarify",
        "calls": [],
        "clarification": "Bạn muốn tìm máy theo mã tài sản hay theo người sử dụng?",
        "reason": None,
    }
    with patch("apps.assistant.gateway.plan_question", return_value=clarify_decision):
        t_clarify = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=uuid.uuid4(),
            question="Tìm máy",
        )
        assert t_clarify.outcome == ChatTurn.Outcome.CLARIFY
        assert "mã tài sản" in t_clarify.answer
        assert not hasattr(t_clarify, "ticket")

    # 3. Missing knowledge: handoff triggers ESCALATED and creates a SupportTicket with Vietnamese message
    handoff_decision = {
        "kind": "handoff",
        "calls": [],
        "clarification": None,
        "reason": "missing_knowledge",
    }
    with patch("apps.assistant.gateway.plan_question", return_value=handoff_decision):
        t_handoff = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=uuid.uuid4(),
            question="Chính sách khấu hao tài sản công năm nay thế nào?",
        )
        assert t_handoff.outcome == ChatTurn.Outcome.ESCALATED
        assert t_handoff.answer == "Yêu cầu của bạn đã được chuyển tiếp tới bộ phận hỗ trợ chuyên môn."
        assert SupportTicket.objects.filter(turn=t_handoff).exists()

    # 4. Outage: AI provider unavailable raises AssistantProviderUnavailable with attached turn
    with patch(
        "apps.assistant.gateway.plan_question",
        side_effect=AssistantProviderUnavailable("AI Worker upstream 503"),
    ):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            ChatService.ask(
                actor=mgr,
                conversation_id=manager_conv.id,
                client_request_id=uuid.uuid4(),
                question="Câu hỏi khi provider chết",
            )
        assert hasattr(exc_info.value, "turn")
        assert exc_info.value.turn.outcome == ChatTurn.Outcome.ERROR


def test_scope_restriction_does_not_auto_escalate(assistant_graph, user_conv):
    """Scope restriction returns RESTRICTED with empty sources and does NOT create a SupportTicket."""
    user = assistant_graph["user_it"]

    restricted_tool_decision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {
                    "asset_code": "W5-HR-L01",
                    "department": "HR",
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }

    with patch("apps.assistant.gateway.plan_question", return_value=restricted_tool_decision):
        turn = ChatService.ask(
            actor=user,
            conversation_id=user_conv.id,
            client_request_id=uuid.uuid4(),
            question="Cho tôi xem thông tin máy W5-HR-L01 của phòng HR",
        )
        assert turn.outcome == ChatTurn.Outcome.RESTRICTED
        assert turn.sources == []
        assert not SupportTicket.objects.filter(turn=turn).exists()


def test_new_turn_rate_limit_and_replay(assistant_graph, manager_conv):
    """10 allowed within 1 minute, 11th Throttled 429; replay does not throttle."""
    mgr = assistant_graph["manager_it"]

    simple_clarify = {
        "kind": "clarify",
        "calls": [],
        "clarification": "Vui lòng làm rõ",
        "reason": None,
    }

    turn_ids = []
    with patch("apps.assistant.gateway.plan_question", return_value=simple_clarify):
        # 10 successful new turns
        for i in range(10):
            req_id = uuid.uuid4()
            turn = ChatService.ask(
                actor=mgr,
                conversation_id=manager_conv.id,
                client_request_id=req_id,
                question=f"Câu hỏi số {i}",
            )
            assert turn.outcome == ChatTurn.Outcome.CLARIFY
            assert turn.is_replay is False
            turn_ids.append((req_id, f"Câu hỏi số {i}"))

        # 11th new turn must raise Throttled
        with pytest.raises(Throttled) as exc_info:
            ChatService.ask(
                actor=mgr,
                conversation_id=manager_conv.id,
                client_request_id=uuid.uuid4(),
                question="Câu hỏi số 11",
            )
        assert exc_info.value.status_code == 429

        # Replay of existing turn with same request ID and question succeeds without throttle
        first_req_id, first_question = turn_ids[0]
        replay_turn = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=first_req_id,
            question=first_question,
        )
        assert replay_turn.is_replay is True
        assert replay_turn.question == first_question

        # Reusing existing request ID with different question raises ConflictError
        with pytest.raises(ConflictError) as exc_info:
            ChatService.ask(
                actor=mgr,
                conversation_id=manager_conv.id,
                client_request_id=first_req_id,
                question="Câu hỏi khác với cùng request_id",
            )
        assert "CLIENT_REQUEST_ID_CONFLICT" in str(exc_info.value)


def test_stale_processing_turn_expires_on_replay(assistant_graph, manager_conv):
    """PROCESSING turn older than 180s expires to ERROR when replayed, returning ERROR without calling AI."""
    mgr = assistant_graph["manager_it"]
    req_id = uuid.uuid4()
    question = "Câu hỏi bị treo"

    # Create stale turn created 200 seconds ago
    stale_time = timezone.now() - datetime.timedelta(seconds=200)
    stale_turn = ChatTurn.objects.create(
        conversation=manager_conv,
        client_request_id=req_id,
        question=question,
        outcome=ChatTurn.Outcome.PROCESSING,
    )
    # Manually update created_at to simulate aging
    ChatTurn.objects.filter(id=stale_turn.id).update(created_at=stale_time)

    with patch("apps.assistant.gateway.plan_question") as mock_plan:
        res = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=req_id,
            question=question,
        )
        assert res.is_replay is True
        assert res.outcome == ChatTurn.Outcome.ERROR
        mock_plan.assert_not_called()


def test_context_slices_latest_six_turns_and_handles_null_department(assistant_graph, manager_conv):
    """build_planner_context slices only the 6 latest turns in stable order; scope wire department_name is None when no department."""
    mgr = assistant_graph["manager_it"]

    # Create 8 completed turns
    for idx in range(8):
        ChatTurn.objects.create(
            conversation=manager_conv,
            client_request_id=uuid.uuid4(),
            question=f"Câu hỏi số {idx}",
            outcome=ChatTurn.Outcome.ANSWERED,
            answer=f"Trả lời {idx}",
            sources=[],
        )

    context = build_planner_context(actor=mgr, conversation=manager_conv)
    assert len(context) == 6
    # Must be latest 6 in chronological order: Turn 2 to Turn 7
    expected_questions = [f"Câu hỏi số {i}" for i in range(2, 8)]
    assert [c["question"] for c in context] == expected_questions

    # Scope wire check: admin has no department -> department_name must be None
    admin = assistant_graph["admin"]
    assert admin.department is None
    admin_conv = Conversation.objects.create(
        owner=admin,
        role_snapshot=admin.role,
        department_id_snapshot=None,
    )

    clarify_decision = {
        "kind": "clarify",
        "calls": [],
        "clarification": "Cần làm rõ",
        "reason": None,
    }
    with patch("apps.assistant.gateway.plan_question", return_value=clarify_decision) as mock_plan:
        ChatService.ask(
            actor=admin,
            conversation_id=admin_conv.id,
            client_request_id=uuid.uuid4(),
            question="Thống kê thiết bị",
        )
        mock_plan.assert_called_once()
        scope_arg = mock_plan.call_args.kwargs["scope"]
        assert scope_arg["department_name"] is None
        assert scope_arg["department_id"] is None
        assert scope_arg["role"] == User.Role.ADMIN


def test_gateway_planner_protocol_error_finalizes_turn_error_and_raises_503(assistant_graph, manager_conv):
    """PlannerProtocolError from gateway finalizes turn as ERROR and raises AssistantProviderUnavailable with .turn attached."""
    mgr = assistant_graph["manager_it"]

    with patch(
        "apps.assistant.gateway.plan_question",
        side_effect=PlannerProtocolError("Invalid response from gateway"),
    ):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            ChatService.ask(
                actor=mgr,
                conversation_id=manager_conv.id,
                client_request_id=uuid.uuid4(),
                question="Câu hỏi lỗi protocol gateway",
            )
        assert hasattr(exc_info.value, "turn")
        turn = exc_info.value.turn
        assert turn.outcome == ChatTurn.Outcome.ERROR
        assert turn.answer == "Đã có lỗi xử lý yêu cầu."
        assert turn.sources == []


def test_execute_tool_planner_protocol_error_finalizes_turn_error_and_raises_503(assistant_graph, manager_conv):
    """PlannerProtocolError from execute_tool finalizes turn as ERROR and raises AssistantProviderUnavailable with .turn attached."""
    mgr = assistant_graph["manager_it"]
    tool_decision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"query": "Laptop"},
            }
        ],
        "clarification": None,
        "reason": None,
    }

    with patch("apps.assistant.gateway.plan_question", return_value=tool_decision), \
         patch("apps.assistant.tools.execute_tool", side_effect=PlannerProtocolError("Tool validation failed")):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            ChatService.ask(
                actor=mgr,
                conversation_id=manager_conv.id,
                client_request_id=uuid.uuid4(),
                question="Tìm laptop",
            )
        assert hasattr(exc_info.value, "turn")
        turn = exc_info.value.turn
        assert turn.outcome == ChatTurn.Outcome.ERROR
        assert turn.answer == "Đã có lỗi xử lý yêu cầu."
        assert turn.sources == []


def test_tool_unsupported_precedence_triggers_escalated_and_atomic_ticket(assistant_graph, manager_conv):
    """Tool execution returning unsupported triggers ESCALATED, empty sources, and atomic SupportTicket."""
    mgr = assistant_graph["manager_it"]
    req_id = uuid.uuid4()
    question = "Tra cứu tài sản bằng cách thức chưa hỗ trợ"
    tool_decision = {
        "kind": "tools",
        "calls": [{"name": "assets.search", "arguments": {"query": "XYZ"}}],
        "clarification": None,
        "reason": None,
    }
    unsupported_result = {
        "tool": "assets.search",
        "status": "unsupported",
        "data": {"message": "Hệ thống chưa hỗ trợ tìm kiếm này."},
        "sources": [],
        "label": "Tài sản",
        "observed_at": "2026-11-05T00:00:00Z",
    }

    with patch("apps.assistant.gateway.plan_question", return_value=tool_decision), \
         patch("apps.assistant.tools.execute_tool", return_value=unsupported_result):
        turn = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=req_id,
            question=question,
        )
        assert turn.outcome == ChatTurn.Outcome.ESCALATED
        assert turn.sources == []
        assert SupportTicket.objects.filter(turn=turn).count() == 1

        # Replay same request -> is_replay is True, still only 1 ticket in DB
        replay = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=req_id,
            question=question,
        )
        assert replay.is_replay is True
        assert SupportTicket.objects.filter(turn=turn).count() == 1


def test_tool_mixed_precedence_restricted_over_unsupported(assistant_graph, manager_conv):
    """Mixed tool results: restricted takes precedence over unsupported; no ticket created."""
    mgr = assistant_graph["manager_it"]
    tool_decision = {
        "kind": "tools",
        "calls": [
            {"name": "assets.search", "arguments": {"query": "A"}},
            {"name": "assets.summary", "arguments": {"category": "Laptop"}},
        ],
        "clarification": None,
        "reason": None,
    }
    restricted_res = {
        "tool": "assets.search",
        "status": "restricted",
        "data": {"message": "Không có quyền xem tài sản."},
        "sources": [],
        "label": "Tài sản",
        "observed_at": "2026-11-05T00:00:00Z",
    }
    unsupported_res = {
        "tool": "assets.summary",
        "status": "unsupported",
        "data": {"message": "Chưa hỗ trợ thống kê này."},
        "sources": [],
        "label": "Thống kê",
        "observed_at": "2026-11-05T00:00:00Z",
    }

    with patch("apps.assistant.gateway.plan_question", return_value=tool_decision), \
         patch("apps.assistant.tools.execute_tool", side_effect=[restricted_res, unsupported_res]):
        turn = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=uuid.uuid4(),
            question="Hỏi kết hợp",
        )
        assert turn.outcome == ChatTurn.Outcome.RESTRICTED
        assert not SupportTicket.objects.filter(turn=turn).exists()


def test_tool_mixed_precedence_clarify_over_unsupported(assistant_graph, manager_conv):
    """Mixed tool results: clarify takes precedence over unsupported; no ticket created."""
    mgr = assistant_graph["manager_it"]
    tool_decision = {
        "kind": "tools",
        "calls": [
            {"name": "assets.search", "arguments": {"query": "A"}},
            {"name": "assets.summary", "arguments": {"category": "Laptop"}},
        ],
        "clarification": None,
        "reason": None,
    }
    clarify_res = {
        "tool": "assets.search",
        "status": "clarify",
        "data": {"clarification": "Nhiều kết quả, vui lòng làm rõ.", "items": []},
        "sources": [],
        "label": "Tài sản",
        "observed_at": "2026-11-05T00:00:00Z",
    }
    unsupported_res = {
        "tool": "assets.summary",
        "status": "unsupported",
        "data": {"message": "Chưa hỗ trợ thống kê này."},
        "sources": [],
        "label": "Thống kê",
        "observed_at": "2026-11-05T00:00:00Z",
    }

    with patch("apps.assistant.gateway.plan_question", return_value=tool_decision), \
         patch("apps.assistant.tools.execute_tool", side_effect=[clarify_res, unsupported_res]):
        turn = ChatService.ask(
            actor=mgr,
            conversation_id=manager_conv.id,
            client_request_id=uuid.uuid4(),
            question="Hỏi kết hợp clarify",
        )
        assert turn.outcome == ChatTurn.Outcome.CLARIFY
        assert not SupportTicket.objects.filter(turn=turn).exists()
