"""Tests for Assistant serializers and pagination."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import ValidationError

from apps.assistant.pagination import AssistantPagination
from apps.assistant.serializers import (
    AskQuestionSerializer,
    ConversationSerializer,
    CreateConversationSerializer,
    EscalateTurnSerializer,
    ProcedureDetailSerializer,
    ReplyTicketSerializer,
    SupportTicketDetailSerializer,
    SupportTicketListSerializer,
    SupportTicketNestedSerializer,
    TurnSerializer,
)


# ---------------------------------------------------------------------------
# Pagination Tests
# ---------------------------------------------------------------------------

def test_assistant_pagination_constants():
    pagination = AssistantPagination()
    assert pagination.page_size == 15
    assert pagination.max_page_size == 20


def test_assistant_pagination_rejects_forbidden_params():
    pagination = AssistantPagination()

    for param in ('all', 'pagination'):
        request = MagicMock()
        request.query_params = {param: 'true'}
        with pytest.raises(ValidationError) as exc:
            pagination.paginate_queryset(queryset=[], request=request)
        assert 'all and pagination parameters are not allowed' in str(exc.value)


# ---------------------------------------------------------------------------
# Input Serializer Strict Validation Tests
# ---------------------------------------------------------------------------

def test_create_conversation_serializer_strict():
    s_valid = CreateConversationSerializer(data={})
    assert s_valid.is_valid(), s_valid.errors
    assert s_valid.validated_data == {}

    s_invalid = CreateConversationSerializer(data={"unknown": 123})
    assert not s_invalid.is_valid()
    assert "unknown" in s_invalid.errors


def test_escalate_turn_serializer_strict():
    s_valid = EscalateTurnSerializer(data={})
    assert s_valid.is_valid(), s_valid.errors
    assert s_valid.validated_data == {}

    s_invalid = EscalateTurnSerializer(data={"extra": "field"})
    assert not s_invalid.is_valid()
    assert "extra" in s_invalid.errors


def test_ask_question_serializer_valid():
    req_id = str(uuid.uuid4())
    data = {
        "client_request_id": req_id,
        "question": "  Tìm cho tôi laptop dell  ",
    }
    s = AskQuestionSerializer(data=data)
    assert s.is_valid(), s.errors
    assert s.validated_data["question"] == "Tìm cho tôi laptop dell"
    assert str(s.validated_data["client_request_id"]) == req_id


def test_ask_question_serializer_whitespace_rejected():
    data = {
        "client_request_id": str(uuid.uuid4()),
        "question": "   \n\t  ",
    }
    s = AskQuestionSerializer(data=data)
    assert not s.is_valid()
    assert "question" in s.errors


def test_ask_question_serializer_length_bounds():
    req_id = str(uuid.uuid4())
    s_empty = AskQuestionSerializer(data={"client_request_id": req_id, "question": ""})
    assert not s_empty.is_valid()

    s_too_long = AskQuestionSerializer(data={"client_request_id": req_id, "question": "a" * 1001})
    assert not s_too_long.is_valid()
    assert "question" in s_too_long.errors

    s_max_valid = AskQuestionSerializer(data={"client_request_id": req_id, "question": "a" * 1000})
    assert s_max_valid.is_valid(), s_max_valid.errors


def test_ask_question_serializer_rejects_extra_fields():
    data = {
        "client_request_id": str(uuid.uuid4()),
        "question": "Laptop",
        "injected": "malicious",
    }
    s = AskQuestionSerializer(data=data)
    assert not s.is_valid()
    assert "injected" in s.errors


def test_reply_ticket_serializer_valid():
    req_id = str(uuid.uuid4())
    data = {
        "client_request_id": req_id,
        "expected_version": 2,
        "reply": "  Đã cấp phát máy Dell Latitude  ",
    }
    s = ReplyTicketSerializer(data=data)
    assert s.is_valid(), s.errors
    assert s.validated_data["reply"] == "Đã cấp phát máy Dell Latitude"
    assert s.validated_data["expected_version"] == 2


def test_reply_ticket_serializer_validation_bounds():
    req_id = str(uuid.uuid4())
    # Whitespace reply
    s_blank = ReplyTicketSerializer(data={"client_request_id": req_id, "expected_version": 1, "reply": "   "})
    assert not s_blank.is_valid()
    assert "reply" in s_blank.errors

    # Reply > 4000
    s_too_long = ReplyTicketSerializer(data={"client_request_id": req_id, "expected_version": 1, "reply": "x" * 4001})
    assert not s_too_long.is_valid()
    assert "reply" in s_too_long.errors

    # Version < 1
    s_bad_ver = ReplyTicketSerializer(data={"client_request_id": req_id, "expected_version": 0, "reply": "ok"})
    assert not s_bad_ver.is_valid()
    assert "expected_version" in s_bad_ver.errors

    # Extra field
    s_extra = ReplyTicketSerializer(data={
        "client_request_id": req_id,
        "expected_version": 1,
        "reply": "ok",
        "extra_field": "val",
    })
    assert not s_extra.is_valid()
    assert "extra_field" in s_extra.errors


# ---------------------------------------------------------------------------
# Output Serializer Tests
# ---------------------------------------------------------------------------

def test_support_ticket_nested_serializer():
    user = SimpleNamespace(full_name="Nguyễn Văn A", username="nguyenvana")
    dept = SimpleNamespace(name="Phòng IT")
    ticket = SimpleNamespace(
        id=uuid.uuid4(),
        target_kind="DEPARTMENT",
        target_department=dept,
        status="ANSWERED",
        version=2,
        reply="Đã giải quyết",
        replied_by=user,
        replied_at="2026-11-05T12:00:00Z",
    )
    s = SupportTicketNestedSerializer(ticket)
    data = s.data
    assert data["target_kind"] == "DEPARTMENT"
    assert data["target_department_name"] == "Phòng IT"
    assert data["replied_by_name"] == "Nguyễn Văn A"
    assert data["reply"] == "Đã giải quyết"

    # With no department and no replied_by
    ticket_admin = SimpleNamespace(
        id=uuid.uuid4(),
        target_kind="ADMIN",
        target_department=None,
        status="OPEN",
        version=1,
        reply="",
        replied_by=None,
        replied_at=None,
    )
    s_admin = SupportTicketNestedSerializer(ticket_admin)
    data_admin = s_admin.data
    assert data_admin["target_department_name"] is None
    assert data_admin["replied_by_name"] is None


def test_turn_serializer():
    conv_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    req_id = uuid.uuid4()
    conv = SimpleNamespace(id=conv_id)
    turn = SimpleNamespace(
        id=turn_id,
        conversation=conv,
        client_request_id=req_id,
        question="Có bao nhiêu laptop?",
        outcome="ANSWERED",
        answer="Có 5 laptop",
        sources=[],
        created_at="2026-11-05T10:00:00Z",
        updated_at="2026-11-05T10:00:01Z",
        ticket=None,
    )
    s = TurnSerializer(turn)
    data = s.data
    assert data["id"] == str(turn_id)
    assert data["conversation_id"] == str(conv_id)
    assert data["client_request_id"] == str(req_id)
    assert data["ticket"] is None


def test_conversation_serializer():
    conv_id = uuid.uuid4()
    conv = SimpleNamespace(
        id=conv_id,
        created_at="2026-11-05T10:00:00Z",
        updated_at="2026-11-05T10:00:01Z",
    )
    s = ConversationSerializer(conv)
    assert s.data["id"] == str(conv_id)
    assert s.data["created_at"] == "2026-11-05T10:00:00Z"
    assert s.data["updated_at"] == "2026-11-05T10:00:01Z"


def test_support_ticket_list_serializer():
    ticket_id = uuid.uuid4()
    dept = SimpleNamespace(name="Phòng Nhân Sự")
    ticket = SimpleNamespace(
        id=ticket_id,
        target_kind="DEPARTMENT",
        target_department=dept,
        status="OPEN",
        version=1,
        created_at="2026-11-05T10:00:00Z",
        updated_at="2026-11-05T10:00:01Z",
    )
    s = SupportTicketListSerializer(ticket)
    data = s.data
    assert data["id"] == str(ticket_id)
    assert data["target_kind"] == "DEPARTMENT"
    assert data["target_department_name"] == "Phòng Nhân Sự"
    assert data["status"] == "OPEN"
    assert data["version"] == 1


def test_support_ticket_detail_serializer():
    ticket_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    turn = SimpleNamespace(id=turn_id)
    dept = SimpleNamespace(name="Phòng IT")
    user = SimpleNamespace(full_name=None, username="manager_user")
    ticket = SimpleNamespace(
        id=ticket_id,
        turn=turn,
        target_kind="DEPARTMENT",
        target_department=dept,
        question_snapshot="Hỏi cấp máy mới",
        status="ANSWERED",
        version=3,
        reply="Đồng ý cấp",
        replied_by=user,
        replied_at="2026-11-05T11:00:00Z",
        created_at="2026-11-05T10:00:00Z",
        updated_at="2026-11-05T11:00:00Z",
    )
    s = SupportTicketDetailSerializer(ticket)
    data = s.data
    assert data["id"] == str(ticket_id)
    assert data["turn_id"] == str(turn_id)
    assert data["target_department_name"] == "Phòng IT"
    assert data["question_snapshot"] == "Hỏi cấp máy mới"
    assert data["replied_by_name"] == "manager_user"
    assert data["reply"] == "Đồng ý cấp"
    assert data["version"] == 3


def test_procedure_detail_serializer():
    payload = {
        "topic": "allocate",
        "version": "2026-11-05",
        "title": "Quy trình cấp phát",
        "body": "Hướng dẫn chi tiết",
        "actions": [{"label": "Tạo phiếu", "href": "/my-requests"}],
    }
    s = ProcedureDetailSerializer(payload)
    assert s.data == payload
