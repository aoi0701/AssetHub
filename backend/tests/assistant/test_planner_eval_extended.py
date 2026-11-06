"""Extended robustness oracle: checked offline against the real graph and tools, never against a model.

A fake planner that copies the oracle only proves the evaluator can score a case, not that any AI passes.
"""
import copy
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from apps.assistant.contracts import AssistantProviderUnavailable, validate_decision
from apps.assistant.tools import execute_tool

from . import planner_eval
from .planner_eval import (
    EXTENDED_CASE_IDS,
    EXTENDED_LIVE_FLAG,
    CaseFixtureError,
    evaluate_case,
    extended_live_problem,
    load_cases,
    load_extended_cases,
    prepare_case,
    run_extended_suite,
    run_suite,
)

READY = {
    "RUN_ASSISTANT_LLM_EVAL": "1",
    "ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "1",
    "ASSETHUB_WEEK5_PG_RUN_ID": "w5_abc",
    "ASSISTANT_EVAL_WORKER_URL": "https://planner.example",
    "AI_WORKER_URL": "https://planner.example",
    "ASSISTANT_EVAL_MODEL": "@cf/test/model",
    "ASSISTANT_EVAL_ARTIFACT_DIR": tempfile.gettempdir(),
}


class Planner:
    """Answers from a table keyed by question and records every call."""

    def __init__(self, decisions):
        self.decisions, self.calls = decisions, []

    def __call__(self, *, question, context, scope):
        self.calls.append(question)
        answer = self.decisions[question]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


@pytest.fixture
def cases():
    return load_extended_cases()


@pytest.fixture
def prepared(assistant_graph, cases):
    return {case["id"]: prepare_case(case, assistant_graph) for case in cases}


@pytest.fixture
def oracle(prepared):
    return Planner({case["question"]: case["expected_decision"] for case in prepared.values()})


def by_id(record):
    return {row["case_id"]: row for row in record["cases"]}


# --- the dataset ------------------------------------------------------------------------

def test_twelve_new_ranked_cases_with_a_basis(cases):
    assert {case["id"] for case in cases} == EXTENDED_CASE_IDS and len(cases) == 12
    assert sorted(case["risk_rank"] for case in cases) == list(range(1, 13))
    assert all(case["basis"].strip() for case in cases)
    assert not {case["question"] for case in load_cases()} & {case["question"] for case in cases}


@pytest.mark.parametrize("case_id", sorted(EXTENDED_CASE_IDS))
def test_expected_decision_runs_through_the_real_tools(assistant_graph, prepared, case_id):
    case = prepared[case_id]
    decision = validate_decision(case["expected_decision"])
    if decision["kind"] != "tools":
        assert case["expected_tool_result_status"] is None
        return
    for call in decision["calls"]:
        result = execute_tool(actor=assistant_graph[case["actor_key"]], call=call)
        assert result["status"] == case["expected_tool_result_status"], result["data"]
        if result["status"] == "restricted":
            assert set(result["data"]) == {"message"} and result["sources"] == []
        assert planner_eval._result_value_problems(result["data"], case.get("expected_result")) == []


def test_clarify_case_rejects_a_guessed_code(assistant_graph, prepared):
    case = prepared["X8"]
    guess = {"kind": "clarify", "calls": [], "clarification": "Bạn hỏi máy W5-IT-L24 phải không?", "reason": None}
    assert evaluate_case(case, assistant_graph, Planner({case["question"]: guess}))["verdict"] == "mismatch"


@pytest.mark.parametrize("case_id, statuses", [("X4", ["AVAILABLE"]), ("X9", ["IN_USE"])])
def test_restricted_cases_still_score_the_intent(assistant_graph, prepared, case_id, statuses):
    """Same restricted outcome, wrong filters: still a mismatch, the oracle is not loosened to the status."""
    case = prepared[case_id]
    wrong = copy.deepcopy(case["expected_decision"])
    wrong["calls"][0]["arguments"]["statuses"] = statuses
    row = evaluate_case(case, assistant_graph, Planner({case["question"]: wrong}))
    assert row["verdict"] == "mismatch" and row["authorization"] == "restricted"


# --- the extended runner ----------------------------------------------------------------

def test_oracle_copy_scores_every_case_but_stays_pending_offline(assistant_graph, cases, oracle):
    record = run_extended_suite(cases, assistant_graph, oracle, live=False)
    assert record["suite"] == "extended" and "gate_status" not in record
    assert record["extended_status"] == "EXTENDED_PENDING"
    assert {row["verdict"] for row in record["cases"]} == {"match"}
    assert record["planner_calls"] == 12 and len(oracle.calls) == 12
    assert all(row["planner_latency_ms"] is not None for row in record["cases"])


def test_live_record_with_all_matches_still_needs_review(assistant_graph, cases, oracle):
    record = run_extended_suite(cases, assistant_graph, oracle, live=True)
    assert record["extended_status"] == "EXTENDED_MATCHED_PENDING_REVIEW"


def test_cases_run_highest_risk_first(assistant_graph, cases, oracle):
    record = run_extended_suite(list(reversed(cases)), assistant_graph, oracle, live=False)
    ranks = {case["id"]: case["risk_rank"] for case in cases}
    assert [ranks[row["case_id"]] for row in record["cases"]] == list(range(1, 13))


def test_provider_failure_halts_and_leaves_the_rest_not_run(assistant_graph, cases, oracle, prepared):
    second = next(case for case in prepared.values() if case["risk_rank"] == 2)
    oracle.decisions[second["question"]] = AssistantProviderUnavailable("AI Worker request timed out: x")

    record = run_extended_suite(cases, assistant_graph, oracle, live=True)

    rows = record["cases"]
    assert record["extended_status"] == "EXTENDED_BLOCKED"
    assert record["planner_calls"] == 2 and len(oracle.calls) == 2
    assert rows[1]["verdict"] == "provider_error"
    assert all(row["verdict"] == "not_run" and row["planner_calls"] == 0 and row["planner_latency_ms"] is None
               for row in rows[2:])


def test_refused_plan_is_a_protocol_error_and_fails_within_twelve_calls(assistant_graph, cases, oracle, prepared):
    refused = AssistantProviderUnavailable("AI Worker returned HTTP 502: Bad Gateway (worker code INVALID_PLAN)")
    refused.http_status, refused.worker_code, refused.worker_reason = 502, "INVALID_PLAN", None
    oracle.decisions[prepared["X5"]["question"]] = refused

    record = run_extended_suite(cases, assistant_graph, oracle, live=True)

    assert by_id(record)["X5"]["verdict"] == "protocol_error"
    assert record["extended_status"] == "EXTENDED_FAILED"
    assert record["planner_calls"] == 12 and len(oracle.calls) == 12


def test_errors_are_redacted(assistant_graph, cases, oracle, prepared):
    oracle.decisions[prepared["X3"]["question"]] = AssistantProviderUnavailable("failed with secret-token-xyz")
    record = run_extended_suite(cases, assistant_graph, oracle, live=True, redact=["secret-token-xyz"])
    assert by_id(record)["X3"]["error"] == "failed with [redacted]"


def _with(cases, change):
    changed = copy.deepcopy(cases)
    change(changed)
    return changed


@pytest.mark.parametrize("change", [
    lambda cases: cases.pop(),
    lambda cases: cases.append(copy.deepcopy(cases[0])),
    lambda cases: cases.append({**copy.deepcopy(cases[0]), "id": "X13", "risk_rank": 13}),
    lambda cases: cases[1].update(id=cases[0]["id"]),
    lambda cases: cases[1].update(risk_rank=cases[0]["risk_rank"]),
    lambda cases: cases[1].pop("risk_rank"),
    lambda cases: cases[2].update(id="E1"),
    lambda cases: cases[-1]["context"].append({"question": "q", "refs": [{"asset_code": "W5-HR-L03"}]}),
], ids=["missing", "duplicate", "thirteen", "duplicate-id", "duplicate-rank", "no-rank", "base-id", "invisible-ref"])
def test_bad_extended_set_is_refused_before_any_call(assistant_graph, cases, oracle, change):
    with pytest.raises(CaseFixtureError):
        run_extended_suite(_with(cases, change), assistant_graph, oracle, live=True)
    assert oracle.calls == []


@pytest.mark.parametrize("rank", [True, False, 1.0, "1", None], ids=["true", "false", "float", "string", "none"])
def test_non_integer_rank_is_refused_before_any_call(assistant_graph, cases, oracle, rank):
    first = next(case for case in cases if case["risk_rank"] == 1)
    changed = _with(cases, lambda items: next(case for case in items if case["id"] == first["id"]).update(risk_rank=rank))
    with pytest.raises(CaseFixtureError):
        run_extended_suite(changed, assistant_graph, oracle, live=True)
    assert oracle.calls == []


def test_base_suite_still_refuses_extended_cases(assistant_graph, cases, oracle):
    mixed = load_cases()[:11] + cases[:1]
    for attempt in (cases, mixed):
        with pytest.raises(CaseFixtureError):
            run_suite(attempt, assistant_graph, oracle, live=True)
    assert oracle.calls == []


# --- live gate ------------------------------------------------------------------------------

def test_extended_live_needs_its_own_flag_on_top_of_the_base_gate():
    assert "EXTENDED_PENDING" in extended_live_problem(READY)
    assert extended_live_problem({**READY, EXTENDED_LIVE_FLAG: "1"}) is None
    assert "LLM_EVAL_PENDING" in extended_live_problem({**READY, EXTENDED_LIVE_FLAG: "1", "RUN_ASSISTANT_LLM_EVAL": "0"})
    assert "8787" in extended_live_problem({**READY, EXTENDED_LIVE_FLAG: "1",
                                            "ASSISTANT_EVAL_WORKER_URL": "http://127.0.0.1:8787",
                                            "AI_WORKER_URL": "http://127.0.0.1:8787"})


def _run_module(name, change):
    env = {
        **os.environ,
        "RUN_ASSISTANT_LLM_EVAL": "0",
        "ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "0",
        EXTENDED_LIVE_FLAG: "0",
        "ASSETHUB_WEEK5_PG_RUN_ID": "",
        "ASSISTANT_EVAL_WORKER_URL": "",
        "ASSISTANT_EVAL_MODEL": "",
        "AI_WORKER_URL": "",
        "AI_SERVICE_TOKEN": "",
        "USE_D1_DATABASE": "False",
        "DB_HOST": "/nonexistent-gate3l-socket-dir",
        **change,
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(Path(__file__).with_name(name)), "-q", "-rs", "-p", "no:cacheprovider"],
        cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True, timeout=60,
    )


BASE_OPT_IN = {"RUN_ASSISTANT_LLM_EVAL": "1", "ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "1"}


@pytest.mark.parametrize("change, reason", [
    ({}, "EXTENDED_PENDING"),
    (BASE_OPT_IN, "EXTENDED_PENDING"),
    ({EXTENDED_LIVE_FLAG: "1"}, "LLM_EVAL_PENDING"),
    ({**BASE_OPT_IN, EXTENDED_LIVE_FLAG: "1"}, "BLOCKED"),
], ids=["default", "base-opt-in-only", "extended-flag-only", "no-pg-owner"])
def test_extended_live_module_skips_before_fixtures_network_and_secrets(change, reason):
    completed = _run_module("test_planner_live_extended_eval.py", change)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 skipped" in completed.stdout and reason in completed.stdout


def test_base_live_module_spends_nothing_when_an_extended_run_is_requested():
    """A complete base environment would run the base test; the extended flag alone must stop it."""
    completed = _run_module("test_planner_live_eval.py", {**READY, EXTENDED_LIVE_FLAG: "1"})
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 skipped" in completed.stdout and "extended run requested" in completed.stdout
