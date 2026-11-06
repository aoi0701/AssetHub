"""Gate 3-L evaluator: compares a planner's decisions with the 12-case oracle by meaning.

A run makes one planner call per case, never retries, and never writes a pass status: a live run
that matches everything still needs its clarifications reviewed by a person.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from time import perf_counter
from pathlib import Path
from urllib.parse import urlsplit

from apps.assets.models import Asset
from apps.assistant.contracts import (
    PLAN_SCHEMA_ID,
    PLAN_SCHEMA_PATH,
    AssistantProviderUnavailable,
    PlannerProtocolError,
    validate_decision,
)
from apps.assistant.scoping import normalize_text, visible_assets
from apps.assistant.tools import execute_tool
from apps.authentication.models import User
from apps.departments.models import Department

MAX_LIVE_CALLS = 12
CASE_IDS = {"E1", "E2", "E3", "F1", "F2", "A1", "A2", "O1", "O2", "P1", "P2", "H1"}
_CASES_PATH = Path(__file__).with_name("planner_eval_cases.json")
# A separate robustness set; its results never decide Gate 3-L.
EXTENDED_CASE_IDS = {f"X{number}" for number in range(1, 13)}
_EXTENDED_CASES_PATH = Path(__file__).with_name("planner_eval_extended_cases.json")
EXTENDED_LIVE_FLAG = "RUN_ASSISTANT_LLM_EVAL_EXTENDED"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKER_PROMPT_FILES = ("cloudflare-worker/src/assistant.js", "cloudflare-worker/assistant.schema.json")
_PLACEHOLDER = re.compile(r"\{(\w+)\.(\w+)\}")
_ALLOWED_PLACEHOLDERS = {"asset_code", "full_name"}
_INVALID_DECISION_PREFIX = "AI Worker returned invalid planner decision"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_MAX_ERROR_CHARS = 300


class CaseFixtureError(ValueError):
    """The oracle does not fit the graph; no planner call may be spent on it."""


def load_cases() -> list[dict]:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def load_extended_cases() -> list[dict]:
    return json.loads(_EXTENDED_CASES_PATH.read_text(encoding="utf-8"))


def _fill(value, graph):
    if isinstance(value, str):
        def replace(match):
            key, attribute = match.groups()
            if key not in graph or attribute not in _ALLOWED_PLACEHOLDERS or not hasattr(graph[key], attribute):
                raise CaseFixtureError(f"Unknown placeholder {match.group(0)}")
            return str(getattr(graph[key], attribute))
        return _PLACEHOLDER.sub(replace, value)
    if isinstance(value, list):
        return [_fill(item, graph) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, graph) for key, item in value.items()}
    return value


def prepare_case(case: dict, graph: dict) -> dict:
    """Fills placeholders from the graph and proves the actor may see every context ref."""
    if case["actor_key"] not in graph:
        raise CaseFixtureError(f"{case['id']}: unknown actor {case['actor_key']}")
    prepared = _fill(copy.deepcopy(case), graph)
    actor = graph[prepared["actor_key"]]
    for turn in prepared["context"]:
        if set(turn) != {"question", "refs"} or any(set(ref) != {"asset_code"} for ref in turn["refs"]):
            raise CaseFixtureError(f"{case['id']}: context must be question and asset_code refs only")
        for ref in turn["refs"]:
            if not visible_assets(actor).filter(asset_code=ref["asset_code"]).exists():
                raise CaseFixtureError(f"{case['id']}: ref {ref['asset_code']} is not visible to the actor")
    return prepared


def build_scope(actor: User) -> dict:
    """The wire scope agreed for API and eval; the gateway adds its own clock fields."""
    department = actor.department if actor.department_id else None
    return {
        "role": actor.role,
        "department_id": department.id if department else None,
        "department_name": department.name if department else None,
    }


# --- normalization ------------------------------------------------------------------------

def _canonical_department(value: str, actor: User) -> str | None:
    wanted = normalize_text(value)
    # For a manager their own department is the default scope, so naming it adds no meaning.
    # An admin has no default scope, so the filter stays and E3 cannot drop it.
    if actor.role == User.Role.MANAGER and actor.department_id:
        own = actor.department
        if wanted in {normalize_text(own.code), normalize_text(own.name)}:
            return None
    for code, name in Department.objects.values_list("code", "name"):
        if wanted in {normalize_text(code), normalize_text(name)}:
            return code
    return wanted


def _canonical_holder(value: str) -> str:
    wanted = normalize_text(value)
    for username, full_name in User.objects.values_list("username", "full_name"):
        if wanted in {normalize_text(username), normalize_text(full_name)}:
            return f"user:{username}"
    return wanted


def normalize_call(call: dict, actor: User) -> dict:
    arguments = dict(call["arguments"])
    if arguments.get("limit") == 20:
        del arguments["limit"]
    if arguments.get("group_by", "") is None:
        del arguments["group_by"]
    for key in ("statuses", "actions"):
        if key in arguments:
            arguments[key] = sorted(arguments[key])
    for key in ("query", "category"):
        if key in arguments:
            arguments[key] = normalize_text(arguments[key])
    if "asset_code" in arguments:
        arguments["asset_code"] = arguments["asset_code"].strip().upper()
    if "holder" in arguments:
        arguments["holder"] = _canonical_holder(arguments["holder"])
    if "department" in arguments:
        canonical = _canonical_department(arguments["department"], actor)
        if canonical is None:
            del arguments["department"]
        else:
            arguments["department"] = canonical
    return {"name": call["name"], "arguments": dict(sorted(arguments.items()))}


def _normalize_decision(decision: dict, actor: User) -> dict:
    return {
        "kind": decision["kind"],
        "calls": [normalize_call(call, actor) for call in decision["calls"]],
        "reason": decision["reason"],
    }


# --- evaluation ---------------------------------------------------------------------------

def _echo_form(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", normalize_text(text)).split())


def _clarification_problems(text, case: dict) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return ["clarification is blank"]
    # Only an exact echo is caught; whether a clarification really helps stays a manual review.
    if _echo_form(text) == _echo_form(case["question"]):
        return ["clarification repeats the question"]
    allowed = {ref["asset_code"] for turn in case["context"] for ref in turn["refs"]}
    folded = text.upper()
    named = [code for code in Asset.objects.values_list("asset_code", flat=True) if code.upper() in folded]
    unlisted = sorted(code for code in named if code not in allowed)
    return [f"clarification names assets outside the context: {unlisted}"] if unlisted else []


def _run_tools(decision: dict, case: dict, actor: User) -> tuple[list[dict], list[str]]:
    summaries, problems = [], []
    expected_status = case["expected_tool_result_status"]
    for call in decision["calls"]:
        try:
            result = execute_tool(actor=actor, call=call)
        except PlannerProtocolError as error:
            summaries.append({"tool": call["name"], "status": "rejected", "total": None})
            problems.append(f"tool rejected the call: {error}")
            continue
        data = result["data"]
        summaries.append({"tool": result["tool"], "status": result["status"], "total": data.get("total")})
        if result["status"] != expected_status:
            problems.append(f"tool status {result['status']} instead of {expected_status}")
        if result["status"] == "restricted" and (set(data) != {"message"} or result["sources"]):
            problems.append("restricted result leaks data")
        problems.extend(_result_value_problems(data, case.get("expected_result")))
    return summaries, problems


def _result_value_problems(data: dict, expected: dict | None) -> list[str]:
    if not expected:
        return []
    problems = []
    if "total" in expected and data.get("total") != expected["total"]:
        problems.append(f"total {data.get('total')} instead of {expected['total']}")
    if "asset_code" in expected:
        items = data.get("items") or [{}]
        if items[0].get("asset_code") != expected["asset_code"]:
            problems.append("result is a different asset")
        if items[0].get("holder") != expected["holder"]:
            problems.append("result has a different holder")
    return problems


def _redact(text: str, secrets) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:_MAX_ERROR_CHARS]


def _empty_row(case: dict, verdict: str, planner_calls: int) -> dict:
    return {
        "case_id": case["id"],
        "actor_key": case["actor_key"],
        "question": case["question"],
        "expected": None,
        "actual": None,
        "tool_results": [],
        "authorization": None,
        "mismatches": [],
        "error": None,
        "clarification_for_review": None,
        "verdict": verdict,
        "planner_calls": planner_calls,
        "planner_latency_ms": None,
    }


def evaluate_case(case: dict, graph: dict, planner, *, redact=()) -> dict:
    """Spends exactly one planner call. Unexpected exceptions propagate: they are evaluator bugs."""
    actor = graph[case["actor_key"]]
    row = _empty_row(case, "mismatch", 1)
    row["expected"] = _normalize_decision(case["expected_decision"], actor)

    scope = build_scope(actor)
    try:
        # Timed: the planner call alone, whether it answers or raises; not scope, validation or tools.
        started = perf_counter()
        try:
            raw = planner(question=case["question"], context=case["context"], scope=scope)
        finally:
            row["planner_latency_ms"] = round((perf_counter() - started) * 1000, 1)
        decision = validate_decision(raw)
    except PlannerProtocolError as error:
        row.update(verdict="protocol_error", error=_redact(str(error), redact))
        return row
    except AssistantProviderUnavailable as error:
        message = _redact(str(error), redact)
        # The Worker refusing the model's plan is the planner's failure, not an outage: the run goes on.
        refused = getattr(error, "http_status", None) == 502 and getattr(error, "worker_code", None) == "INVALID_PLAN"
        kind = "protocol_error" if refused or str(error).startswith(_INVALID_DECISION_PREFIX) else "provider_error"
        row.update(verdict=kind, error=message)
        return row

    row["actual"] = _normalize_decision(decision, actor)
    mismatches = [
        f"{field} differs" for field in ("kind", "calls", "reason") if row["actual"][field] != row["expected"][field]
    ]
    if decision["kind"] == "clarify":
        row["clarification_for_review"] = decision["clarification"]
        mismatches.extend(_clarification_problems(decision["clarification"], case))
    # A tool is never run for a case that should not use one, even if the planner asked for it.
    if decision["kind"] == "tools" and case["expected_decision"]["kind"] == "tools":
        row["tool_results"], tool_problems = _run_tools(decision, case, actor)
        mismatches.extend(tool_problems)
        statuses = {summary["status"] for summary in row["tool_results"]}
        row["authorization"] = "restricted" if "restricted" in statuses else "ok"
    row["mismatches"] = mismatches
    row["verdict"] = "mismatch" if mismatches else "match"
    return row


def _gate_status(rows: list[dict], live: bool) -> str:
    if not live:
        return "LLM_EVAL_PENDING"
    verdicts = {row["verdict"] for row in rows}
    if "provider_error" in verdicts:
        return "LIVE_BLOCKED"
    if verdicts != {"match"}:
        return "LIVE_FAILED"
    return "LIVE_MATCHED_PENDING_REVIEW"


def run_suite(cases: list[dict], graph: dict, planner, *, live: bool, meta: dict | None = None, redact=()) -> dict:
    """Checks every fixture first, then spends at most one call per case; a provider failure stops the run."""
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or set(ids) != CASE_IDS or len(ids) != MAX_LIVE_CALLS:
        raise CaseFixtureError("A run needs exactly the 12 plan cases")
    rows = _run_cases([prepare_case(case, graph) for case in cases], graph, planner, redact)
    return {
        "gate_status": _gate_status(rows, live),
        "planner_calls": sum(row["planner_calls"] for row in rows),
        "meta": meta or {},
        "cases": rows,
    }


def _run_cases(prepared: list[dict], graph: dict, planner, redact) -> list[dict]:
    """One call per prepared case, in order; a provider failure leaves the rest not_run."""
    rows, stopped = [], False
    for case in prepared:
        if stopped:
            rows.append(_empty_row(case, "not_run", 0))
            continue
        row = evaluate_case(case, graph, planner, redact=redact)
        rows.append(row)
        stopped = row["verdict"] == "provider_error"
    return rows


_EXTENDED_STATUS = {
    "LLM_EVAL_PENDING": "EXTENDED_PENDING",
    "LIVE_BLOCKED": "EXTENDED_BLOCKED",
    "LIVE_FAILED": "EXTENDED_FAILED",
    "LIVE_MATCHED_PENDING_REVIEW": "EXTENDED_MATCHED_PENDING_REVIEW",
}


def run_extended_suite(cases: list[dict], graph: dict, planner, *, live: bool, meta: dict | None = None,
                       redact=()) -> dict:
    """The 12 extended cases, highest risk first, under the same one-call, halt and redaction rules."""
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or set(ids) != EXTENDED_CASE_IDS or len(ids) != MAX_LIVE_CALLS:
        raise CaseFixtureError("An extended run needs exactly the 12 extended cases")
    ranks = [case.get("risk_rank") for case in cases]
    # type() not isinstance(): True would pass as 1.
    if any(type(rank) is not int for rank in ranks) or sorted(ranks) != list(range(1, MAX_LIVE_CALLS + 1)):
        raise CaseFixtureError("Extended cases need unique risk ranks 1..12")
    prepared = [prepare_case(case, graph) for case in sorted(cases, key=lambda case: case["risk_rank"])]
    rows = _run_cases(prepared, graph, planner, redact)
    return {
        "suite": "extended",
        "extended_status": _EXTENDED_STATUS[_gate_status(rows, live)],
        "planner_calls": sum(row["planner_calls"] for row in rows),
        "meta": meta or {},
        "cases": rows,
    }


# --- live run support ---------------------------------------------------------------------

def live_gate_problem(env) -> str | None:
    """Why a live run may not start, read only from non-secret settings; None when it may."""
    if env.get("RUN_ASSISTANT_LLM_EVAL") != "1" or env.get("ASSETHUB_WEEK5_LIVE_AI_ALLOWED") != "1":
        return "LLM_EVAL_PENDING: live evaluation needs the opt-in and the runner's allow marker"
    if not env.get("ASSETHUB_WEEK5_PG_RUN_ID"):
        return "BLOCKED: no PostgreSQL instance owned by the runner"
    endpoint = env.get("ASSISTANT_EVAL_WORKER_URL")
    if not endpoint or env.get("AI_WORKER_URL") != endpoint:
        return "BLOCKED: the approved planner endpoint is missing or differs from AI_WORKER_URL"
    parsed = urlsplit(endpoint)
    if parsed.hostname in _LOOPBACK and parsed.port == 8787:
        return "BLOCKED: loopback port 8787 is the week 4 default, not an approved planner endpoint"
    if "suggest-category" in parsed.path:
        return "BLOCKED: the category endpoint is not the planner"
    if not env.get("ASSISTANT_EVAL_MODEL"):
        return "BLOCKED: name the planner model in ASSISTANT_EVAL_MODEL so the record can state it"
    artifact_dir = env.get("ASSISTANT_EVAL_ARTIFACT_DIR")
    if not artifact_dir or not os.path.isdir(artifact_dir) or not os.access(artifact_dir, os.W_OK):
        return "BLOCKED: ASSISTANT_EVAL_ARTIFACT_DIR must be an existing writable directory"
    return None


def extended_live_problem(env) -> str | None:
    """The base live conditions plus an explicit extended opt-in; None when an extended run may start."""
    if env.get(EXTENDED_LIVE_FLAG) != "1":
        return f"EXTENDED_PENDING: an extended run needs {EXTENDED_LIVE_FLAG}=1"
    return live_gate_problem(env)


def _sha256(*paths) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def run_metadata(*, endpoint: str, model: str) -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True)
    return {
        "model": model,
        "endpoint": endpoint,
        "schema_id": PLAN_SCHEMA_ID,
        "schema_sha256": _sha256(PLAN_SCHEMA_PATH),
        "worker_prompt_sha256": _sha256(*(_REPO_ROOT / path for path in _WORKER_PROMPT_FILES)),
        "commit": head.stdout.strip(),
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
