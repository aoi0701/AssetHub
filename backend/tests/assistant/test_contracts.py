from __future__ import annotations

import inspect
from pathlib import Path
import pytest

from apps.assistant.contracts import (
    ALLOWED_ACTION_TYPES,
    ALLOWED_ASSET_STATUSES,
    ALLOWED_DECISION_KINDS,
    ALLOWED_EVIDENCE_KINDS,
    ALLOWED_HANDOFF_REASONS,
    ALLOWED_PROCEDURE_TOPICS,
    ALLOWED_REQUEST_STATUSES,
    ALLOWED_REQUEST_TYPES,
    ALLOWED_ROUTING_KINDS,
    ALLOWED_SUMMARY_GROUP_BYS,
    ALLOWED_TOOL_NAMES,
    ALLOWED_TOOL_RESULT_STATUSES,
    PLAN_SCHEMA_ID,
    TOOL_RESULT_STATUS_PRECEDENCE,
    AssistantProviderUnavailable,
    Evidence,
    ExecuteToolCallable,
    FollowUpRef,
    FollowUpTurnContext,
    LookupProcedureCallable,
    PlanQuestionCallable,
    PlannerDecision,
    PlannerProtocolError,
    RenderToolResultsCallable,
    RoutingTarget,
    ToolCall,
    ToolResult,
    VisibleAssetsCallable,
    VisibleHistoryCallable,
    VisibleRequestsCallable,
    load_plan_schema,
    validate_decision,
)


# -----------------------------------------------------------------------------
# 1. Schema metadata and constants integrity
# -----------------------------------------------------------------------------

def test_schema_metadata():
    schema = load_plan_schema()
    assert schema["$id"] == PLAN_SCHEMA_ID
    assert schema["$id"] == "urn:assethub:assistant-plan:v1"
    assert schema["title"] == "PlannerDecision"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"kind", "calls", "clarification", "reason"}


def test_constants_spec_alignment():
    assert set(ALLOWED_TOOL_NAMES) == {
        "assets.search",
        "assets.summary",
        "assets.history",
        "requests.search",
        "procedures.lookup",
    }
    assert set(ALLOWED_DECISION_KINDS) == {"tools", "clarify", "handoff", "out_of_scope"}
    assert set(ALLOWED_HANDOFF_REASONS) == {
        "missing_knowledge",
        "unsupported_asset_question",
    }
    assert set(ALLOWED_ASSET_STATUSES) == {
        "AVAILABLE",
        "IN_USE",
        "DAMAGED_PENDING_REPAIR",
        "UNDER_MAINTENANCE",
        "PENDING_DISPOSAL",
        "DISPOSED",
    }
    assert set(ALLOWED_ACTION_TYPES) == {
        "CREATE",
        "ALLOCATE",
        "RETURN",
        "DAMAGE",
        "MAINTENANCE_START",
        "MAINTENANCE_END",
        "TRANSFER",
        "DISPOSAL_PROPOSE",
        "DISPOSAL_CONFIRM",
    }
    assert set(ALLOWED_REQUEST_TYPES) == {"ALLOCATE", "RETURN", "DAMAGE_REPORT"}
    assert set(ALLOWED_REQUEST_STATUSES) == {"PENDING", "APPROVED", "REJECTED"}
    assert set(ALLOWED_PROCEDURE_TOPICS) == {
        "allocate",
        "return",
        "damage_report",
        "roles",
        "asset_statuses",
    }
    assert set(ALLOWED_SUMMARY_GROUP_BYS) == {"status", "category", "department"}
    assert set(ALLOWED_ROUTING_KINDS) == {"DEPARTMENT", "ADMIN"}
    assert set(ALLOWED_TOOL_RESULT_STATUSES) == {"ok", "clarify", "restricted", "unsupported"}
    assert set(ALLOWED_EVIDENCE_KINDS) == {
        "asset",
        "aggregate",
        "history",
        "request",
        "procedure",
    }
    assert TOOL_RESULT_STATUS_PRECEDENCE == ("restricted", "clarify", "unsupported", "ok")


# -----------------------------------------------------------------------------
# 2. Valid PlannerDecision cases
# -----------------------------------------------------------------------------

def test_valid_tools_assets_search():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {
                    "query": "ThinkPad",
                    "category": "Laptop",
                    "statuses": ["AVAILABLE", "IN_USE"],
                    "limit": 10,
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"
    assert len(decision["calls"]) == 1


def test_valid_tools_assets_summary():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.summary",
                "arguments": {
                    "category": "Laptop",
                    "statuses": ["AVAILABLE"],
                    "group_by": "department",
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"
    assert decision["calls"][0]["name"] == "assets.summary"


def test_valid_tools_assets_summary_null_group_by():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.summary",
                "arguments": {
                    "category": "Laptop",
                    "group_by": None,
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"


def test_valid_tools_assets_history_with_asset_code():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {
                    "asset_code": "AST-2026-0001",
                    "actions": ["ALLOCATE", "RETURN"],
                    "since": "2026-01-01T00:00:00Z",
                    "until": "2026-11-05T23:59:59Z",
                    "limit": 20,
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"


def test_valid_tools_assets_history_with_mine():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {
                    "mine": True,
                    "since": "2026-01-01",
                    "until": "2026-11-01",
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"


def test_valid_history_date_only_until_covers_whole_day_utc():
    # Feedback probe: since afternoon UTC and until date-only of the same day must be valid
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {
                    "mine": True,
                    "since": "2026-11-05T12:00:00Z",
                    "until": "2026-11-05",
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"


def test_valid_history_max_date_9999_no_overflow():
    # Feedback probe: 9999-12-31 handled safely without OverflowError
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {
                    "mine": True,
                    "since": "2026-01-01",
                    "until": "9999-12-31",
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"


def test_valid_tools_requests_search():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "requests.search",
                "arguments": {
                    "query": "keyboard",
                    "request_type": "ALLOCATE",
                    "status": "PENDING",
                    "limit": 5,
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "tools"


def test_valid_tools_procedures_lookup():
    for topic in ALLOWED_PROCEDURE_TOPICS:
        payload = {
            "kind": "tools",
            "calls": [
                {
                    "name": "procedures.lookup",
                    "arguments": {"topic": topic},
                }
            ],
            "clarification": None,
            "reason": None,
        }
        decision = validate_decision(payload)
        assert decision["kind"] == "tools"
        assert decision["calls"][0]["arguments"]["topic"] == topic


def test_valid_tools_multiple_calls_up_to_three():
    payload = {
        "kind": "tools",
        "calls": [
            {"name": "assets.search", "arguments": {"query": "MacBook"}},
            {"name": "assets.summary", "arguments": {"group_by": "status"}},
            {"name": "procedures.lookup", "arguments": {"topic": "allocate"}},
        ],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert len(decision["calls"]) == 3


def test_valid_clarify():
    payload = {
        "kind": "clarify",
        "calls": [],
        "clarification": "Bạn muốn kiểm tra tài sản của phòng IT hay cá nhân?",
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "clarify"
    assert decision["clarification"] is not None


def test_valid_handoff_reasons():
    for reason in ALLOWED_HANDOFF_REASONS:
        payload = {
            "kind": "handoff",
            "calls": [],
            "clarification": None,
            "reason": reason,
        }
        decision = validate_decision(payload)
        assert decision["kind"] == "handoff"
        assert decision["reason"] == reason


def test_valid_out_of_scope():
    payload = {
        "kind": "out_of_scope",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    decision = validate_decision(payload)
    assert decision["kind"] == "out_of_scope"


# -----------------------------------------------------------------------------
# 3. Negative validation cases (Must raise PlannerProtocolError)
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_payload, error_match",
    [
        (None, "Decision payload must be a JSON object / dict"),
        ([], "Decision payload must be a JSON object / dict"),
        ("string", "Decision payload must be a JSON object / dict"),
        (123, "Decision payload must be a JSON object / dict"),
    ],
)
def test_reject_non_dict_payload(bad_payload, error_match):
    with pytest.raises(PlannerProtocolError, match=error_match):
        validate_decision(bad_payload)


def test_reject_unknown_root_keys():
    payload = {
        "kind": "out_of_scope",
        "calls": [],
        "clarification": None,
        "reason": None,
        "version": "v1",  # Spec forbids version field in decision
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_missing_required_root_keys():
    payload = {
        "kind": "out_of_scope",
        "calls": [],
        "clarification": None,
        # missing "reason"
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_unknown_kind():
    payload = {
        "kind": "search",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_tools_with_zero_calls():
    payload = {
        "kind": "tools",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_tools_with_more_than_three_calls():
    payload = {
        "kind": "tools",
        "calls": [
            {"name": "procedures.lookup", "arguments": {"topic": "allocate"}},
            {"name": "procedures.lookup", "arguments": {"topic": "return"}},
            {"name": "procedures.lookup", "arguments": {"topic": "roles"}},
            {"name": "procedures.lookup", "arguments": {"topic": "asset_statuses"}},
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_tools_with_non_null_clarification():
    payload = {
        "kind": "tools",
        "calls": [{"name": "procedures.lookup", "arguments": {"topic": "allocate"}}],
        "clarification": "Hỏi thêm câu này",
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_tools_with_non_null_reason():
    payload = {
        "kind": "tools",
        "calls": [{"name": "procedures.lookup", "arguments": {"topic": "allocate"}}],
        "clarification": None,
        "reason": "missing_knowledge",
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_clarify_with_calls():
    payload = {
        "kind": "clarify",
        "calls": [{"name": "procedures.lookup", "arguments": {"topic": "allocate"}}],
        "clarification": "Hỏi lại",
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_clarify_with_null_or_empty_clarification():
    payload_null = {
        "kind": "clarify",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload_null)

    payload_empty = {
        "kind": "clarify",
        "calls": [],
        "clarification": "",
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload_empty)


def test_reject_clarify_with_oversized_clarification():
    payload = {
        "kind": "clarify",
        "calls": [],
        "clarification": "x" * 301,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_handoff_with_calls():
    payload = {
        "kind": "handoff",
        "calls": [{"name": "procedures.lookup", "arguments": {"topic": "allocate"}}],
        "clarification": None,
        "reason": "missing_knowledge",
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_handoff_with_invalid_reason():
    payload = {
        "kind": "handoff",
        "calls": [],
        "clarification": None,
        "reason": "other_reason",
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_handoff_with_null_reason():
    payload = {
        "kind": "handoff",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_out_of_scope_with_calls_or_reason():
    payload1 = {
        "kind": "out_of_scope",
        "calls": [{"name": "procedures.lookup", "arguments": {"topic": "allocate"}}],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload1)

    payload2 = {
        "kind": "out_of_scope",
        "calls": [],
        "clarification": None,
        "reason": "missing_knowledge",
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload2)


def test_reject_unknown_tool_name():
    payload = {
        "kind": "tools",
        "calls": [{"name": "database.execute_sql", "arguments": {"query": "SELECT 1"}}],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_tool_with_extra_arguments():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"query": "laptop", "sql_injection": "DROP TABLE"},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_assets_summary_with_limit():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.summary",
                "arguments": {"category": "Laptop", "limit": 10},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


@pytest.mark.parametrize("bad_limit", [True, False])
def test_reject_boolean_limit(bad_limit):
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"limit": bad_limit},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


@pytest.mark.parametrize("bad_limit", [0, 21, -1, "10"])
def test_reject_out_of_bounds_limit(bad_limit):
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"limit": bad_limit},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


@pytest.mark.parametrize(
    "dunder_field, dunder_val",
    [
        ("query", "user__email"),
        ("category", "category__name"),
        ("department", "dept__id"),
        ("holder", "holder__username"),
        ("asset_code", "asset__code"),
    ],
)
def test_reject_orm_dunder_lookup(dunder_field, dunder_val):
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {dunder_field: dunder_val},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


# -----------------------------------------------------------------------------
# 4. Codex Review Feedback Conformance Cases
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("blank_query", ["", "   ", "\t\n"])
def test_reject_empty_or_blank_query(blank_query):
    # Feedback probe: assets.search {query: ""} must be rejected
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"query": blank_query},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_empty_statuses_array():
    # Feedback probe: assets.search {statuses: []} must be rejected
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"statuses": []},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_empty_actions_array():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {"mine": True, "actions": []},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_null_filter_keys():
    # Feedback probe: null filter not allowed except group_by (omit optional keys instead)
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"query": None},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_history_both_selectors():
    # Feedback probe: assets.history {asset_code: "...", mine: true} must be rejected
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {"asset_code": "W5-IT-L24", "mine": True},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


@pytest.mark.parametrize(
    "bad_history_args",
    [
        {"asset_code": "W5-IT-L24", "mine": False},
        {"mine": False},
    ],
)
def test_reject_history_mine_false(bad_history_args):
    # Feedback probe: assets.history {mine: false} must be rejected
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": bad_history_args,
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_invalid_statuses_in_search():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"statuses": ["INVALID_STATUS"]},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_invalid_actions_in_history():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {"asset_code": "AST-001", "actions": ["DESTROY"]},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_procedures_lookup_missing_topic():
    payload = {
        "kind": "tools",
        "calls": [{"name": "procedures.lookup", "arguments": {}}],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_procedures_lookup_invalid_topic():
    payload = {
        "kind": "tools",
        "calls": [{"name": "procedures.lookup", "arguments": {"topic": "vacation"}}],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_history_without_code_or_mine():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {"actions": ["ALLOCATE"]},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_history_inverted_dates():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {
                    "asset_code": "AST-001",
                    "since": "2026-11-10T00:00:00Z",
                    "until": "2026-11-01T00:00:00Z",
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError, match="must be <= 'until'"):
        validate_decision(payload)


def test_reject_history_naive_datetime():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {
                    "asset_code": "AST-001",
                    "since": "2026-11-01 12:00:00",  # space and no timezone
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


def test_reject_history_invalid_date_format():
    payload = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.history",
                "arguments": {
                    "asset_code": "AST-001",
                    "since": "not-a-date",
                },
            }
        ],
        "clarification": None,
        "reason": None,
    }
    with pytest.raises(PlannerProtocolError):
        validate_decision(payload)


# -----------------------------------------------------------------------------
# 5. Interface Protocols type checks (Lean hygiene)
# -----------------------------------------------------------------------------

def test_interface_protocols_defined():
    # Protocols must be defined and importable
    assert VisibleAssetsCallable is not None
    assert VisibleHistoryCallable is not None
    assert VisibleRequestsCallable is not None
    assert ExecuteToolCallable is not None
    assert LookupProcedureCallable is not None
    assert RenderToolResultsCallable is not None
    assert PlanQuestionCallable is not None


# -----------------------------------------------------------------------------
# 6. DTO TypedDict instantiation
# -----------------------------------------------------------------------------

def test_dto_typed_dicts():
    target: RoutingTarget = {"kind": "DEPARTMENT", "department_id": 10}
    assert target["kind"] == "DEPARTMENT"
    assert target["department_id"] == 10

    evidence: Evidence = {
        "id": "ev-01",
        "kind": "asset",
        "label": "ThinkPad T14",
        "href": "/assets/1",
        "observed_at": "2026-11-05T12:00:00Z",
        "scope_label": "IT Department",
        "facts": {"total": 1},
    }
    assert evidence["kind"] == "asset"

    tool_call: ToolCall = {
        "name": "assets.search",
        "arguments": {"query": "ThinkPad"},
    }
    assert tool_call["name"] == "assets.search"

    tool_result: ToolResult = {
        "tool": "assets.search",
        "status": "ok",
        "data": {"total": 1, "items": []},
        "sources": [evidence],
        "scope_label": "IT Department",
        "observed_at": "2026-11-05T12:00:00Z",
    }
    assert tool_result["status"] == "ok"

    decision: PlannerDecision = {
        "kind": "tools",
        "calls": [tool_call],
        "clarification": None,
        "reason": None,
    }
    assert decision["kind"] == "tools"

    follow_up: FollowUpTurnContext = {
        "question": "Con nay cua ai?",
        "refs": [{"asset_code": "AST-001"}],
    }
    assert follow_up["refs"][0]["asset_code"] == "AST-001"
