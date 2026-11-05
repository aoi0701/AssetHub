from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

import jsonschema

PLAN_SCHEMA_PATH = Path(__file__).resolve().parent / "plan.schema.json"
PLAN_SCHEMA_ID = "urn:assethub:assistant-plan:v1"

ALLOWED_TOOL_NAMES = (
    "assets.search",
    "assets.summary",
    "assets.history",
    "requests.search",
    "procedures.lookup",
)
ALLOWED_DECISION_KINDS = ("tools", "clarify", "handoff", "out_of_scope")
ALLOWED_HANDOFF_REASONS = ("missing_knowledge", "unsupported_asset_question")
ALLOWED_ASSET_STATUSES = (
    "AVAILABLE",
    "IN_USE",
    "DAMAGED_PENDING_REPAIR",
    "UNDER_MAINTENANCE",
    "PENDING_DISPOSAL",
    "DISPOSED",
)
ALLOWED_ACTION_TYPES = (
    "CREATE",
    "ALLOCATE",
    "RETURN",
    "DAMAGE",
    "MAINTENANCE_START",
    "MAINTENANCE_END",
    "TRANSFER",
    "DISPOSAL_PROPOSE",
    "DISPOSAL_CONFIRM",
)
ALLOWED_REQUEST_TYPES = ("ALLOCATE", "RETURN", "DAMAGE_REPORT")
ALLOWED_REQUEST_STATUSES = ("PENDING", "APPROVED", "REJECTED")
ALLOWED_PROCEDURE_TOPICS = (
    "allocate",
    "return",
    "damage_report",
    "roles",
    "asset_statuses",
)
ALLOWED_SUMMARY_GROUP_BYS = ("status", "category", "department")
ALLOWED_ROUTING_KINDS = ("DEPARTMENT", "ADMIN")
ALLOWED_TOOL_RESULT_STATUSES = ("ok", "clarify", "restricted", "unsupported")
ALLOWED_EVIDENCE_KINDS = ("asset", "aggregate", "history", "request", "procedure")

# ToolResult status precedence: restricted > clarify > unsupported > ok
TOOL_RESULT_STATUS_PRECEDENCE = ("restricted", "clarify", "unsupported", "ok")


class PlannerProtocolError(Exception):
    """Raised when planner decision violates schema or semantic contract."""


class AssistantProviderUnavailable(Exception):
    """Raised when Workers AI or upstream assistant provider cannot fulfill request."""


# DTO TypedDict definitions

class RoutingTarget(TypedDict):
    kind: Literal["DEPARTMENT", "ADMIN"]
    department_id: int | None


class Evidence(TypedDict):
    id: str
    kind: Literal["asset", "aggregate", "history", "request", "procedure"]
    label: str
    href: str | None
    observed_at: str
    scope_label: str
    facts: dict[str, Any]


class ToolCall(TypedDict):
    name: Literal[
        "assets.search",
        "assets.summary",
        "assets.history",
        "requests.search",
        "procedures.lookup",
    ]
    arguments: dict[str, Any]


class ToolResult(TypedDict):
    tool: str
    status: Literal["ok", "clarify", "restricted", "unsupported"]
    data: dict[str, Any]
    sources: list[Evidence]
    scope_label: str
    observed_at: str


class PlannerDecision(TypedDict):
    kind: Literal["tools", "clarify", "handoff", "out_of_scope"]
    calls: list[ToolCall]
    clarification: str | None
    reason: Literal["missing_knowledge", "unsupported_asset_question"] | None


class FollowUpRef(TypedDict):
    asset_code: str


class FollowUpTurnContext(TypedDict):
    question: str
    refs: list[FollowUpRef]


# Callable Protocols defining cross-task interfaces without runtime bloat

@runtime_checkable
class VisibleAssetsCallable(Protocol):
    def __call__(self, actor: Any) -> Any: ...


@runtime_checkable
class VisibleHistoryCallable(Protocol):
    def __call__(self, actor: Any, asset_code: str | None = None) -> Any: ...


@runtime_checkable
class VisibleRequestsCallable(Protocol):
    def __call__(self, actor: Any) -> Any: ...


@runtime_checkable
class ExecuteToolCallable(Protocol):
    def __call__(self, *, actor: Any, call: ToolCall) -> ToolResult: ...


@runtime_checkable
class LookupProcedureCallable(Protocol):
    def __call__(self, *, actor: Any, topic: str) -> ToolResult: ...


@runtime_checkable
class RenderToolResultsCallable(Protocol):
    def __call__(self, results: list[ToolResult]) -> tuple[str, list[Evidence]]: ...


@runtime_checkable
class PlanQuestionCallable(Protocol):
    def __call__(
        self, *, question: str, context: list[dict[str, Any]], scope: dict[str, Any]
    ) -> PlannerDecision: ...


# Schema loading and caching

_cached_plan_schema: dict[str, Any] | None = None
_cached_validator: jsonschema.Draft7Validator | None = None


def load_plan_schema() -> dict[str, Any]:
    """Load and cache plan.schema.json."""
    global _cached_plan_schema
    if _cached_plan_schema is None:
        with open(PLAN_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _cached_plan_schema = json.load(f)
    return _cached_plan_schema


def get_plan_validator() -> jsonschema.Draft7Validator:
    """Return cached jsonschema Draft7Validator instance."""
    global _cached_validator
    if _cached_validator is None:
        schema = load_plan_schema()
        _cached_validator = jsonschema.Draft7Validator(schema)
    return _cached_validator


def _parse_iso_datetime(value: str, is_until: bool = False) -> datetime:
    """Parse date or datetime string with UTC/timezone check.

    - Date-only YYYY-MM-DD:
      * since (is_until=False): start-of-day UTC (00:00:00.000000)
      * until (is_until=True): end-of-day UTC (23:59:59.999999, safe up to 9999-12-31 without overflow)
    - Datetime with timezone:
      * converted to UTC instant
    - Naive datetime without timezone: rejected
    """
    try:
        if len(value) == 10:  # YYYY-MM-DD
            base_dt = datetime.fromisoformat(value)
            if is_until:
                return base_dt.replace(
                    hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
                )
            return base_dt.replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )

        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError("Naive datetime without timezone is not permitted")
        return dt.astimezone(timezone.utc)
    except Exception as exc:
        raise PlannerProtocolError(f"Invalid date/datetime format: {value}") from exc


def validate_decision(payload: object) -> PlannerDecision:
    """Validate raw payload against plan.schema.json and semantic contract rules.

    Raises PlannerProtocolError if payload violates schema, contains ORM lookups,
    blank filters, empty arrays, boolean limits, invalid date order, or bad selectors.
    Returns payload typed as PlannerDecision.
    """
    if not isinstance(payload, dict):
        raise PlannerProtocolError("Decision payload must be a JSON object / dict")

    validator = get_plan_validator()
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
    if errors:
        msg = errors[0].message
        path = ".".join(str(p) for p in errors[0].path)
        err_detail = f"[{path}] {msg}" if path else msg
        raise PlannerProtocolError(f"Schema validation error: {err_detail}")

    kind = payload.get("kind")
    calls = payload.get("calls", [])

    if kind == "tools":
        for call in calls:
            args = call.get("arguments", {})
            name = call.get("name")

            # Reject boolean limit explicitly (isinstance(True, int) is True in Python)
            if "limit" in args:
                limit_val = args["limit"]
                if isinstance(limit_val, bool) or not isinstance(limit_val, int):
                    raise PlannerProtocolError("limit must be integer, boolean not allowed")

            # Check non-blank strings, no dunder ORM lookups, non-empty arrays
            for arg_key, arg_val in args.items():
                if isinstance(arg_val, str):
                    if not arg_val.strip():
                        raise PlannerProtocolError(
                            f"Argument '{arg_key}' cannot be empty or blank string"
                        )
                    if "__" in arg_val:
                        raise PlannerProtocolError(
                            f"ORM lookup pattern '__' is forbidden in argument '{arg_key}': {arg_val}"
                        )
                elif isinstance(arg_val, list):
                    if len(arg_val) == 0:
                        raise PlannerProtocolError(
                            f"Argument '{arg_key}' cannot be empty array"
                        )

            if name == "assets.history":
                has_code = bool(args.get("asset_code") and str(args["asset_code"]).strip())
                has_mine = args.get("mine") is True

                if "mine" in args and args["mine"] is not True:
                    raise PlannerProtocolError("assets.history does not permit 'mine: false'")

                if has_code and "mine" in args:
                    raise PlannerProtocolError(
                        "assets.history selector must be either 'asset_code' or 'mine: true', not both"
                    )

                if not has_code and not has_mine:
                    raise PlannerProtocolError(
                        "assets.history requires either 'asset_code' or 'mine: true'"
                    )

                since_str = args.get("since")
                until_str = args.get("until")
                since_dt = _parse_iso_datetime(since_str, is_until=False) if since_str else None
                until_dt = _parse_iso_datetime(until_str, is_until=True) if until_str else None

                if since_dt and until_dt and since_dt > until_dt:
                    raise PlannerProtocolError(
                        f"assets.history 'since' ({since_str}) must be <= 'until' ({until_str})"
                    )

            elif name == "assets.summary":
                if "limit" in args:
                    raise PlannerProtocolError("assets.summary does not permit 'limit'")

    return payload  # type: ignore[return-value]


__all__ = [
    "PLAN_SCHEMA_PATH",
    "PLAN_SCHEMA_ID",
    "ALLOWED_TOOL_NAMES",
    "ALLOWED_DECISION_KINDS",
    "ALLOWED_HANDOFF_REASONS",
    "ALLOWED_ASSET_STATUSES",
    "ALLOWED_ACTION_TYPES",
    "ALLOWED_REQUEST_TYPES",
    "ALLOWED_REQUEST_STATUSES",
    "ALLOWED_PROCEDURE_TOPICS",
    "ALLOWED_SUMMARY_GROUP_BYS",
    "ALLOWED_ROUTING_KINDS",
    "ALLOWED_TOOL_RESULT_STATUSES",
    "ALLOWED_EVIDENCE_KINDS",
    "TOOL_RESULT_STATUS_PRECEDENCE",
    "PlannerProtocolError",
    "AssistantProviderUnavailable",
    "RoutingTarget",
    "Evidence",
    "ToolCall",
    "ToolResult",
    "PlannerDecision",
    "FollowUpRef",
    "FollowUpTurnContext",
    "VisibleAssetsCallable",
    "VisibleHistoryCallable",
    "VisibleRequestsCallable",
    "ExecuteToolCallable",
    "LookupProcedureCallable",
    "RenderToolResultsCallable",
    "PlanQuestionCallable",
    "load_plan_schema",
    "get_plan_validator",
    "validate_decision",
]
