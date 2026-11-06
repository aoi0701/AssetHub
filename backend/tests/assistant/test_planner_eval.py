"""Gate 3-L oracle and evaluator with fake planners. Proves the evaluator only, never model quality."""
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from apps.assets.models import Asset
from apps.assistant.contracts import AssistantProviderUnavailable, PlannerProtocolError, validate_decision
from apps.assistant.tools import execute_tool

from . import planner_eval
from .planner_eval import (
    MAX_LIVE_CALLS,
    CaseFixtureError,
    build_scope,
    evaluate_case,
    live_gate_problem,
    load_cases,
    prepare_case,
    run_metadata,
    run_suite,
)

CASE_IDS = {"E1", "E2", "E3", "F1", "F2", "A1", "A2", "O1", "O2", "P1", "P2", "H1"}
HELD = "W5-IT-L24"
OTHER = "W5-IT-L25"


class OracleCopy:
    """A fake planner that answers from a table, recording every call it receives."""

    def __init__(self, decisions):
        self.decisions = decisions
        self.calls = []

    def __call__(self, *, question, context, scope):
        self.calls.append({"question": question, "context": context, "scope": scope})
        answer = self.decisions[question]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


@pytest.fixture
def prepared(assistant_graph):
    return {case["id"]: prepare_case(case, assistant_graph) for case in load_cases()}


@pytest.fixture
def perfect(prepared):
    return OracleCopy({case["question"]: case["expected_decision"] for case in prepared.values()})


def tools(name, **arguments):
    return {"kind": "tools", "calls": [{"name": name, "arguments": arguments}], "clarification": None, "reason": None}


def clarify(text="Bạn muốn hỏi máy nào?"):
    return {"kind": "clarify", "calls": [], "clarification": text, "reason": None}


def out_of_scope():
    return {"kind": "out_of_scope", "calls": [], "clarification": None, "reason": None}


def handoff(reason="missing_knowledge"):
    return {"kind": "handoff", "calls": [], "clarification": None, "reason": reason}


def evaluate(prepared, assistant_graph, case_id, decision):
    case = prepared[case_id]
    return evaluate_case(case, assistant_graph, OracleCopy({case["question"]: decision}))


# --- L1: the oracle against the real graph ------------------------------------------------

def test_exactly_the_twelve_plan_cases():
    cases = load_cases()
    assert {case["id"] for case in cases} == CASE_IDS
    assert len(cases) == 12


def test_placeholders_are_replaced_from_graph_and_recorded(prepared):
    text = json.dumps(prepared, ensure_ascii=False)
    assert not re.search(r"\{\w+\.\w+\}", text)
    assert HELD in prepared["F1"]["question"]
    assert prepared["P1"]["question"] == "Liệt kê tài sản Nhân viên IT B đang giữ."
    assert [ref["asset_code"] for ref in prepared["A2"]["context"][0]["refs"]] == [HELD, OTHER]


@pytest.mark.parametrize("case_id", sorted(CASE_IDS))
def test_expected_decision_passes_the_shared_contract(prepared, case_id):
    decision = prepared[case_id]["expected_decision"]
    assert validate_decision(decision) == decision


@pytest.mark.parametrize("case_id, total", [("E1", 23), ("E2", 23), ("E3", 23)])
def test_expected_counts_run_through_real_tools(assistant_graph, prepared, case_id, total):
    case = prepared[case_id]
    result = execute_tool(actor=assistant_graph[case["actor_key"]], call=case["expected_decision"]["calls"][0])
    assert (result["status"], result["data"]["total"]) == ("ok", total)


@pytest.mark.parametrize("case_id", ["F1", "F2"])
def test_expected_lookups_find_the_holder(assistant_graph, prepared, case_id):
    case = prepared[case_id]
    result = execute_tool(actor=assistant_graph[case["actor_key"]], call=case["expected_decision"]["calls"][0])
    assert result["status"] == "ok"
    assert result["data"]["items"][0]["asset_code"] == HELD
    assert result["data"]["items"][0]["holder"] == assistant_graph["user_it"].full_name


@pytest.mark.parametrize("case_id", ["P1", "P2"])
def test_expected_restricted_calls_reveal_nothing(assistant_graph, prepared, case_id):
    case = prepared[case_id]
    result = execute_tool(actor=assistant_graph[case["actor_key"]], call=case["expected_decision"]["calls"][0])
    assert result["status"] == "restricted"
    assert set(result["data"]) == {"message"}
    assert result["sources"] == []
    assert OTHER not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("case_id", ["A1", "A2", "O1", "O2", "H1"])
def test_non_tool_cases_expect_no_tool_result(prepared, case_id):
    assert prepared[case_id]["expected_tool_result_status"] is None
    assert prepared[case_id]["expected_decision"]["calls"] == []


def test_every_context_ref_is_visible_to_the_actor(assistant_graph, prepared):
    for case in prepared.values():
        actor = assistant_graph[case["actor_key"]]
        for turn in case["context"]:
            assert set(turn) == {"question", "refs"}
            for ref in turn["refs"]:
                assert set(ref) == {"asset_code"}
                assert planner_eval.visible_assets(actor).filter(asset_code=ref["asset_code"]).exists()


@pytest.mark.parametrize("mutate, message", [
    (lambda case: case.update(actor_key="nobody"), "actor"),
    (lambda case: case.update(question="{nobody.full_name}"), "placeholder"),
    (lambda case: case.update(question="{held_asset.password}"), "placeholder"),
    (lambda case: case.update(context=[{"question": "q", "refs": [{"asset_code": "NO-SUCH"}]}]), "visible"),
    (lambda case: case.update(context=[{"question": "q", "refs": [{"asset_code": "W5-HR-L01"}]}]), "visible"),
    (lambda case: case.update(context=[{"question": "q", "refs": [{"code": "W5-IT-L24"}]}]), "context"),
    (lambda case: case.update(context=[{"question": "q", "answer": "cũ", "refs": []}]), "context"),
])
def test_bad_fixture_is_refused_before_any_planner_call(assistant_graph, mutate, message):
    case = next(item for item in load_cases() if item["id"] == "F1")
    mutate(case)
    with pytest.raises(CaseFixtureError, match=message):
        prepare_case(case, assistant_graph)


# --- evaluator: full run with a fake planner ----------------------------------------------

def test_perfect_fake_matches_all_cases_but_the_gate_stays_pending(assistant_graph, perfect):
    record = run_suite(load_cases(), assistant_graph, perfect, live=False)

    assert record["gate_status"] == "LLM_EVAL_PENDING"
    assert record["planner_calls"] == 12
    assert len(perfect.calls) == 12
    assert {row["case_id"] for row in record["cases"]} == CASE_IDS
    assert all(row["verdict"] == "match" for row in record["cases"]), record
    assert all(row["planner_calls"] == 1 for row in record["cases"])
    assert "PASS" not in json.dumps(record)


def test_live_record_with_all_matches_still_needs_clarification_review(assistant_graph, perfect):
    record = run_suite(load_cases(), assistant_graph, perfect, live=True)

    assert record["gate_status"] == "LIVE_MATCHED_PENDING_REVIEW"
    review = {row["case_id"]: row["clarification_for_review"] for row in record["cases"] if row["clarification_for_review"]}
    assert set(review) == {"A1", "A2"}
    assert "PASS" not in json.dumps(record)


def test_non_tool_decisions_run_no_tool(assistant_graph, perfect, monkeypatch):
    executed = []
    real = planner_eval.execute_tool
    monkeypatch.setattr(planner_eval, "execute_tool", lambda **kw: executed.append(kw["call"]["name"]) or real(**kw))

    record = run_suite(load_cases(), assistant_graph, perfect, live=False)

    assert len(executed) == 7
    for row in record["cases"]:
        if row["case_id"] in {"A1", "A2", "O1", "O2", "H1"}:
            assert row["tool_results"] == []


def test_planner_receives_actor_scope_and_task5_context_shape(assistant_graph, perfect):
    run_suite(load_cases(), assistant_graph, perfect, live=False)

    f2 = next(call for call in perfect.calls if call["question"] == "Máy đó ai đang giữ?")
    assert f2["scope"] == {"role": "MANAGER", "department_id": assistant_graph["it"].id, "department_name": assistant_graph["it"].name}
    assert f2["context"] == [{"question": f"Cho tôi xem máy {HELD}.", "refs": [{"asset_code": HELD}]}]
    a1 = next(call for call in perfect.calls if call["question"] == "Máy này ai giữ?")
    assert a1["context"] == []
    assert a1["scope"]["role"] == "USER"


def test_scope_uses_current_actor_and_admin_has_no_department(assistant_graph):
    assert build_scope(assistant_graph["admin"]) == {"role": "ADMIN", "department_id": None, "department_name": None}
    manager = assistant_graph["manager_it"]
    manager.department = assistant_graph["hr"]
    manager.save()
    assert build_scope(manager)["department_name"] == assistant_graph["hr"].name


# --- equivalences the plan allows ---------------------------------------------------------

@pytest.mark.parametrize("case_id, decision", [
    ("E1", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"])),
    ("E1", tools("assets.summary", statuses=["AVAILABLE"], category="laptop", group_by=None)),
    ("E1", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"], department="IT")),
    ("E2", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"])),
    ("E2", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"], department="Phòng Công nghệ thông tin")),
    ("E3", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"], department="it")),
    ("E3", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"], department="phong cong nghe thong tin")),
    ("F1", tools("assets.search", asset_code=HELD, limit=20)),
    ("F1", tools("assets.search", asset_code=HELD.lower())),
    ("P1", tools("assets.search", holder="nhân viên it b")),
    ("P1", tools("assets.search", holder="w5_other_it")),
    ("P2", tools("assets.summary", department="hr", category="Laptop")),
    ("P2", tools("assets.summary", department="Phòng Nhân sự", category="LAPTOP")),
    ("A1", clarify("Bạn đang nhắc đến thiết bị nào?")),
    ("A2", clarify(f"Máy nào: {HELD} hay {OTHER}?")),
])
def test_same_meaning_matches(assistant_graph, prepared, case_id, decision):
    row = evaluate(prepared, assistant_graph, case_id, decision)
    assert row["verdict"] == "match", row["mismatches"]


# --- mismatches must be caught ------------------------------------------------------------

@pytest.mark.parametrize("case_id, decision", [
    ("E1", tools("assets.summary", category="Laptop", statuses=["IN_USE"])),
    ("E1", tools("assets.summary", category="Laptop")),
    ("E1", tools("assets.summary", statuses=["AVAILABLE"])),
    ("E1", tools("assets.summary", category="Màn hình", statuses=["AVAILABLE"])),
    ("E1", tools("assets.summary", category="Laptop", statuses=["AVAILABLE", "IN_USE"])),
    ("E1", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"], group_by="status")),
    ("E1", tools("assets.search", category="Laptop", statuses=["AVAILABLE"])),
    ("E2", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"], department="HR")),
    ("E3", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"])),
    ("E3", tools("assets.summary", category="Laptop", statuses=["AVAILABLE"], department="HR")),
    ("F1", tools("assets.search", asset_code=OTHER)),
    ("F1", tools("assets.search", query=HELD)),
    ("F1", tools("assets.search", asset_code=HELD, limit=5)),
    ("F2", tools("assets.search", asset_code=OTHER)),
    ("F2", clarify()),
    ("A1", tools("assets.search", asset_code=HELD)),
    ("A1", out_of_scope()),
    ("A1", clarify(f"Có phải máy {HELD} không?")),
    ("A2", tools("assets.search", asset_code=HELD)),
    ("A2", clarify("Máy nào, W5-HR-L01 hay W5-HR-L02?")),
    ("O1", handoff()),
    ("O1", clarify()),
    ("O2", tools("procedures.lookup", topic="roles")),
    ("P1", tools("assets.search", holder="Nhân viên IT A")),
    ("P1", tools("assets.search", holder="Nhân viên")),
    ("P1", tools("assets.search")),
    ("P1", handoff("unsupported_asset_question")),
    ("P2", tools("assets.summary", department="HR", category="Laptop", statuses=["AVAILABLE"])),
    ("P2", tools("assets.summary", category="Laptop")),
    ("P2", tools("assets.summary", department="IT", category="Laptop")),
    ("P2", handoff("unsupported_asset_question")),
    ("H1", out_of_scope()),
    ("H1", handoff("unsupported_asset_question")),
    ("H1", tools("procedures.lookup", topic="return")),
])
def test_wrong_meaning_is_a_mismatch(assistant_graph, prepared, case_id, decision):
    row = evaluate(prepared, assistant_graph, case_id, decision)
    assert row["verdict"] == "mismatch", decision
    assert row["mismatches"]
    if prepared[case_id]["expected_decision"]["kind"] != "tools":
        assert row["tool_results"] == []


def test_extra_call_is_a_mismatch(assistant_graph, prepared):
    decision = tools("assets.search", asset_code=HELD)
    decision["calls"].append({"name": "assets.search", "arguments": {"asset_code": OTHER}})
    row = evaluate(prepared, assistant_graph, "F1", decision)
    assert row["verdict"] == "mismatch"
    assert len(row["actual"]["calls"]) == 2


@pytest.mark.parametrize("decision", [
    {"kind": "tools", "calls": [{"name": "sql.execute", "arguments": {"sql": "SELECT 1"}}], "clarification": None, "reason": None},
    {"kind": "tools", "calls": [{"name": "assets.summary", "arguments": {"holder__email": "x"}}], "clarification": None, "reason": None},
    {"kind": "tools", "calls": [{"name": "assets.search", "arguments": {"limit": True}}], "clarification": None, "reason": None},
    {"kind": "tools", "calls": [], "clarification": None, "reason": None},
    {"kind": "answer", "text": "Có 23 laptop."},
    "23 laptop",
    None,
])
def test_wrong_schema_is_a_protocol_error_and_runs_no_tool(assistant_graph, prepared, decision, monkeypatch):
    monkeypatch.setattr(planner_eval, "execute_tool", lambda **kw: pytest.fail("tool must not run"))
    row = evaluate(prepared, assistant_graph, "E1", decision)
    assert row["verdict"] == "protocol_error"
    assert row["tool_results"] == []


@pytest.mark.parametrize("text", ["   ", "\n"])
def test_blank_clarification_is_a_mismatch(assistant_graph, prepared, text):
    assert evaluate(prepared, assistant_graph, "A1", clarify(text))["verdict"] == "mismatch"


def test_restricted_result_with_data_is_flagged(assistant_graph, prepared, monkeypatch):
    def leaky(**kwargs):
        return {"tool": "assets.search", "status": "restricted", "data": {"message": "x", "total": 1},
                "sources": [], "scope_label": "s", "observed_at": "t"}

    monkeypatch.setattr(planner_eval, "execute_tool", leaky)
    row = evaluate(prepared, assistant_graph, "P1", prepared["P1"]["expected_decision"])
    assert row["verdict"] == "mismatch"
    assert any("leak" in item for item in row["mismatches"])


def test_tool_status_or_value_different_from_oracle_is_flagged(assistant_graph, prepared):
    Asset.objects.filter(asset_code="W5-IT-L01").update(status="IN_USE", assigned_to_user=assistant_graph["user_it"])
    row = evaluate(prepared, assistant_graph, "E1", prepared["E1"]["expected_decision"])
    assert row["verdict"] == "mismatch"
    assert any("total" in item for item in row["mismatches"])


# --- provider failures: no fallback, no retry, nothing spent past the failure --------------

@pytest.mark.parametrize("message", ["AI Worker returned HTTP 429: Too Many Requests", "AI Worker request timed out: x"])
def test_provider_failure_stops_the_run_without_retry_or_fallback(assistant_graph, perfect, prepared, message):
    perfect.decisions[prepared["E2"]["question"]] = AssistantProviderUnavailable(message)

    record = run_suite(load_cases(), assistant_graph, perfect, live=True)

    assert record["gate_status"] == "LIVE_BLOCKED"
    assert len(perfect.calls) == 2
    assert record["planner_calls"] == 2
    rows = {row["case_id"]: row for row in record["cases"]}
    assert rows["E2"]["verdict"] == "provider_error"
    assert rows["E2"]["planner_calls"] == 1
    assert rows["E3"]["verdict"] == "not_run"
    assert rows["E3"]["planner_calls"] == 0


def test_invalid_decision_from_gateway_counts_as_protocol_error_and_continues(assistant_graph, perfect, prepared):
    perfect.decisions[prepared["E2"]["question"]] = AssistantProviderUnavailable(
        "AI Worker returned invalid planner decision: Schema validation error")

    record = run_suite(load_cases(), assistant_graph, perfect, live=True)

    assert record["gate_status"] == "LIVE_FAILED"
    assert record["planner_calls"] == 12
    assert {row["case_id"]: row["verdict"] for row in record["cases"]}["E2"] == "protocol_error"


def _worker_error(status, code, message="AI Worker returned HTTP 502: Bad Gateway (worker code INVALID_PLAN)"):
    error = AssistantProviderUnavailable(message)
    error.http_status, error.worker_code, error.worker_reason = status, code, None
    return error


def test_worker_invalid_plan_is_a_protocol_error_the_run_continues_and_fails(assistant_graph, perfect, prepared):
    perfect.decisions[prepared["P1"]["question"]] = _worker_error(502, "INVALID_PLAN")

    record = run_suite(load_cases(), assistant_graph, perfect, live=True)

    rows = {row["case_id"]: row for row in record["cases"]}
    assert rows["P1"]["verdict"] == "protocol_error"
    assert rows["P1"]["actual"] is None and rows["P1"]["tool_results"] == []
    assert all(row["verdict"] == "match" for case_id, row in rows.items() if case_id != "P1")
    assert record["gate_status"] == "LIVE_FAILED"
    assert record["planner_calls"] == 12 and len(perfect.calls) == 12


def test_every_case_invalid_plan_still_spends_at_most_twelve_calls(assistant_graph, perfect, prepared):
    for case in prepared.values():
        perfect.decisions[case["question"]] = _worker_error(502, "INVALID_PLAN")

    record = run_suite(load_cases(), assistant_graph, perfect, live=True)

    assert {row["verdict"] for row in record["cases"]} == {"protocol_error"}
    assert record["planner_calls"] == 12 and len(perfect.calls) == 12
    assert record["gate_status"] == "LIVE_FAILED"


@pytest.mark.parametrize("error", [
    _worker_error(503, "UNAVAILABLE", "AI Worker returned HTTP 503: Service Unavailable (worker code UNAVAILABLE)"),
    _worker_error(429, "3036", "AI Worker returned HTTP 429: Too Many Requests (worker code 3036)"),
    _worker_error(502, None, "AI Worker returned HTTP 502: Bad Gateway"),
    _worker_error(503, "INVALID_PLAN", "AI Worker returned HTTP 503: x (worker code INVALID_PLAN)"),
    AssistantProviderUnavailable("AI Worker returned HTTP 502: (worker code INVALID_PLAN)"),
    AssistantProviderUnavailable("AI Worker connection failed: refused"),
], ids=["503", "quota", "502-no-code", "invalid-plan-wrong-status", "message-only", "network"])
def test_other_worker_and_network_errors_still_stop_the_run(assistant_graph, perfect, prepared, error):
    perfect.decisions[prepared["E2"]["question"]] = error

    record = run_suite(load_cases(), assistant_graph, perfect, live=True)

    assert record["gate_status"] == "LIVE_BLOCKED"
    assert record["planner_calls"] == 2
    rows = {row["case_id"]: row["verdict"] for row in record["cases"]}
    assert rows["E2"] == "provider_error" and rows["E3"] == "not_run"


@pytest.mark.parametrize("echo", ["Máy này ai giữ?", "  máy NÀY ai giu  ", "May nay, ai giu!", "MÁY NÀY AI GIỮ"])
def test_clarification_that_echoes_the_question_is_a_mismatch(assistant_graph, prepared, echo):
    row = evaluate(prepared, assistant_graph, "A1", clarify(echo))
    assert row["verdict"] == "mismatch"
    assert any("repeats the question" in item for item in row["mismatches"])
    assert row["clarification_for_review"] == echo


def test_echo_check_applies_to_every_clarify_case(assistant_graph, prepared):
    case = prepared["A2"]
    row = evaluate(prepared, assistant_graph, "A2", clarify(case["question"].upper()))
    assert any("repeats the question" in item for item in row["mismatches"])


def test_real_clarification_that_reuses_question_words_still_matches(assistant_graph, prepared):
    row = evaluate(prepared, assistant_graph, "A1", clarify("Máy này là máy nào? Bạn cho tôi mã tài sản nhé."))
    assert row["verdict"] == "match"

def test_planner_protocol_error_is_recorded(assistant_graph, prepared):
    case = prepared["E1"]
    row = evaluate_case(case, assistant_graph, OracleCopy({case["question"]: PlannerProtocolError("bad")}))
    assert row["verdict"] == "protocol_error"
    assert row["planner_calls"] == 1


def test_programming_errors_are_not_hidden_as_provider_failures(assistant_graph, prepared):
    case = prepared["E1"]
    with pytest.raises(KeyError):
        evaluate_case(case, assistant_graph, OracleCopy({}))


def test_mismatch_makes_live_run_failed_not_pending(assistant_graph, perfect, prepared):
    perfect.decisions[prepared["E1"]["question"]] = tools("assets.summary", category="Laptop")
    assert run_suite(load_cases(), assistant_graph, perfect, live=True)["gate_status"] == "LIVE_FAILED"
    assert run_suite(load_cases(), assistant_graph, perfect, live=False)["gate_status"] == "LLM_EVAL_PENDING"


def test_suite_refuses_anything_but_the_twelve_cases(assistant_graph, perfect):
    cases = load_cases()
    with pytest.raises(CaseFixtureError):
        run_suite(cases[:11], assistant_graph, perfect, live=False)
    with pytest.raises(CaseFixtureError):
        run_suite(cases + [cases[0]], assistant_graph, perfect, live=False)
    assert perfect.calls == []
    assert MAX_LIVE_CALLS == 12


def test_all_fixtures_are_checked_before_the_first_planner_call(assistant_graph, perfect):
    cases = load_cases()
    cases[-1]["actor_key"] = "nobody"
    with pytest.raises(CaseFixtureError):
        run_suite(cases, assistant_graph, perfect, live=True)
    assert perfect.calls == []


# --- record -------------------------------------------------------------------------------

def test_record_is_json_with_normalized_args_and_no_credentials(assistant_graph, perfect, prepared):
    secret = "service-token-value-123"
    perfect.decisions[prepared["H1"]["question"]] = AssistantProviderUnavailable(f"AI Worker failed: Bearer {secret}")
    meta = {"model": "m"}

    record = run_suite(load_cases(), assistant_graph, perfect, live=True, meta=meta, redact=[secret])
    text = json.dumps(record, ensure_ascii=False)

    assert secret not in text
    assert "Authorization" not in text
    assert record["meta"] == meta
    row = {item["case_id"]: item for item in record["cases"]}["E1"]
    assert row["expected"]["calls"] == row["actual"]["calls"]
    assert row["actual"]["calls"] == [{"name": "assets.summary", "arguments": {"category": "laptop", "statuses": ["AVAILABLE"]}}]
    assert row["tool_results"] == [{"tool": "assets.summary", "status": "ok", "total": 23}]
    assert row["authorization"] == "ok"
    assert {item["case_id"]: item for item in record["cases"]}["P1"]["authorization"] == "restricted"
    assert "Lý do" not in text


def test_run_metadata_records_versions_without_credentials():
    meta = run_metadata(endpoint="https://planner.example/api/assistant/plan", model="@cf/test/model")

    assert meta["model"] == "@cf/test/model"
    assert meta["endpoint"] == "https://planner.example/api/assistant/plan"
    assert meta["schema_id"] == "urn:assethub:assistant-plan:v1"
    assert len(meta["schema_sha256"]) == 64
    assert len(meta["worker_prompt_sha256"]) == 64
    assert len(meta["commit"]) == 40
    assert meta["started_at"].endswith("Z")


# --- live gate: decided from the environment alone ----------------------------------------

READY = {
    "RUN_ASSISTANT_LLM_EVAL": "1",
    "ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "1",
    "ASSETHUB_WEEK5_PG_RUN_ID": "w5_abc",
    "ASSISTANT_EVAL_WORKER_URL": "https://planner.example",
    "AI_WORKER_URL": "https://planner.example",
    "ASSISTANT_EVAL_MODEL": "@cf/test/model",
    "ASSISTANT_EVAL_ARTIFACT_DIR": tempfile.gettempdir(),
}


def test_live_gate_accepts_only_a_complete_environment():
    assert live_gate_problem(READY) is None


@pytest.mark.parametrize("change, expected", [
    ({"RUN_ASSISTANT_LLM_EVAL": "0"}, "LLM_EVAL_PENDING"),
    ({"RUN_ASSISTANT_LLM_EVAL": None}, "LLM_EVAL_PENDING"),
    ({"ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "0"}, "LLM_EVAL_PENDING"),
    ({"ASSETHUB_WEEK5_LIVE_AI_ALLOWED": None}, "LLM_EVAL_PENDING"),
    ({"ASSETHUB_WEEK5_PG_RUN_ID": ""}, "PostgreSQL"),
    ({"ASSISTANT_EVAL_WORKER_URL": ""}, "endpoint"),
    ({"AI_WORKER_URL": "https://other.example"}, "endpoint"),
    ({"ASSISTANT_EVAL_WORKER_URL": "http://127.0.0.1:8787", "AI_WORKER_URL": "http://127.0.0.1:8787"}, "8787"),
    ({"ASSISTANT_EVAL_WORKER_URL": "http://localhost:8787", "AI_WORKER_URL": "http://localhost:8787"}, "8787"),
    ({"ASSISTANT_EVAL_WORKER_URL": "https://x.example/api/suggest-category", "AI_WORKER_URL": "https://x.example/api/suggest-category"}, "category"),
    ({"ASSISTANT_EVAL_MODEL": ""}, "model"),
    ({"ASSISTANT_EVAL_ARTIFACT_DIR": ""}, "ARTIFACT"),
    ({"ASSISTANT_EVAL_ARTIFACT_DIR": "/nonexistent-gate3l-artifacts"}, "ARTIFACT"),
])
def test_live_gate_refuses_incomplete_environment(change, expected):
    env = {key: value for key, value in {**READY, **change}.items() if value is not None}
    assert expected in live_gate_problem(env)


def test_live_gate_refuses_unwritable_artifact_dir(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    if os.access(locked, os.W_OK):
        pytest.skip("Running as a user that ignores directory permissions")
    assert "ARTIFACT" in live_gate_problem({**READY, "ASSISTANT_EVAL_ARTIFACT_DIR": str(locked)})


def test_live_gate_never_needs_the_service_token():
    assert live_gate_problem({**READY, "AI_SERVICE_TOKEN": ""}) is None


@pytest.mark.parametrize("change, reason", [
    ({}, "LLM_EVAL_PENDING"),
    ({"RUN_ASSISTANT_LLM_EVAL": "1"}, "LLM_EVAL_PENDING"),
    ({"ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "1"}, "LLM_EVAL_PENDING"),
    ({"RUN_ASSISTANT_LLM_EVAL": "1", "ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "1"}, "BLOCKED"),
    ({"RUN_ASSISTANT_LLM_EVAL": "1", "ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "1", "ASSETHUB_WEEK5_PG_RUN_ID": "w5_x"}, "BLOCKED"),
], ids=["default", "opt-in-only", "marker-only", "no-pg-owner", "no-endpoint"])
def test_live_module_skips_cleanly_before_fixtures_network_and_secrets(change, reason):
    """Runs the real module: an unreachable database and no token prove nothing past the guard ran."""
    env = {
        **os.environ,
        "RUN_ASSISTANT_LLM_EVAL": "0",
        "ASSETHUB_WEEK5_LIVE_AI_ALLOWED": "0",
        "ASSETHUB_WEEK5_PG_RUN_ID": "",
        "ASSISTANT_EVAL_WORKER_URL": "",
        "ASSISTANT_EVAL_MODEL": "",
        "AI_WORKER_URL": "",
        "AI_SERVICE_TOKEN": "",
        "USE_D1_DATABASE": "False",
        "DB_HOST": "/nonexistent-gate3l-socket-dir",
        **change,
    }
    module = Path(__file__).with_name("test_planner_live_eval.py")

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-rs", "-p", "no:cacheprovider"],
        cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True, timeout=60,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 skipped" in completed.stdout
    assert reason in completed.stdout


# --- planner latency: only the planner call is timed ---------------------------------------

class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TimedPlanner(OracleCopy):
    """Advances the fake clock by a fixed planner time before answering or raising."""

    def __init__(self, decisions, clock, seconds):
        super().__init__(decisions)
        self.clock, self.seconds = clock, seconds

    def __call__(self, **kwargs):
        self.clock.now += self.seconds
        return super().__call__(**kwargs)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(planner_eval, "perf_counter", fake)
    return fake


def test_latency_times_the_planner_call_but_not_tools_or_validation(assistant_graph, prepared, clock, monkeypatch):
    real_tool, real_validate = planner_eval.execute_tool, planner_eval.validate_decision

    def slow_tool(**kwargs):
        clock.now += 10.0
        return real_tool(**kwargs)

    def slow_validate(raw):
        clock.now += 5.0
        return real_validate(raw)

    def slow_scope(actor):
        clock.now += 7.0
        return {"role": actor.role, "department_id": actor.department_id, "department_name": None}

    monkeypatch.setattr(planner_eval, "execute_tool", slow_tool)
    monkeypatch.setattr(planner_eval, "validate_decision", slow_validate)
    monkeypatch.setattr(planner_eval, "build_scope", slow_scope)
    case = prepared["E1"]

    row = evaluate_case(case, assistant_graph, TimedPlanner({case["question"]: case["expected_decision"]}, clock, 1.5))

    assert row["verdict"] == "match" and row["tool_results"]
    assert row["planner_latency_ms"] == 1500.0


@pytest.mark.parametrize("error, verdict", [
    (AssistantProviderUnavailable("AI Worker request timed out: x"), "provider_error"),
    (AssistantProviderUnavailable("AI Worker returned invalid planner decision: bad"), "protocol_error"),
    (PlannerProtocolError("bad"), "protocol_error"),
], ids=["provider-timeout", "invalid-decision", "protocol"])
def test_latency_is_recorded_when_the_planner_fails(assistant_graph, prepared, clock, error, verdict):
    case = prepared["A2"]
    row = evaluate_case(case, assistant_graph, TimedPlanner({case["question"]: error}, clock, 30.0))
    assert row["verdict"] == verdict
    assert row["planner_latency_ms"] == 30000.0


def test_schema_invalid_answer_keeps_the_planner_latency(assistant_graph, prepared, clock):
    case = prepared["E1"]
    row = evaluate_case(case, assistant_graph, TimedPlanner({case["question"]: "23 laptop"}, clock, 0.25))
    assert row["verdict"] == "protocol_error"
    assert row["planner_latency_ms"] == 250.0


def test_not_run_cases_have_no_latency(assistant_graph, perfect, prepared, clock):
    timed = TimedPlanner(perfect.decisions, clock, 2.0)
    timed.decisions[prepared["E2"]["question"]] = AssistantProviderUnavailable("AI Worker request timed out: x")

    record = run_suite(load_cases(), assistant_graph, timed, live=True)

    latencies = {row["case_id"]: row["planner_latency_ms"] for row in record["cases"]}
    assert latencies["E1"] == 2000.0 and latencies["E2"] == 2000.0
    assert {latencies[case_id] for case_id in latencies if case_id not in {"E1", "E2"}} == {None}
    assert record["gate_status"] == "LIVE_BLOCKED" and record["planner_calls"] == 2
