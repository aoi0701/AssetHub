"""Gate 3-L live run: real planner calls, at most 12, only with explicit permission.

The marker skips the test before any fixture, network or secret access unless the runner granted
permission, so a default run collects one skipped test and exits 0. The result is a record for
review, never a pass by itself. Provider failures stay failures: no mock, no retry.
"""
import json
import os
from pathlib import Path

import pytest

from .planner_eval import live_gate_problem, load_cases, run_metadata, run_suite

_problem = live_gate_problem(os.environ)
pytestmark = pytest.mark.skipif(_problem is not None, reason=_problem or "")


def test_twelve_cases_against_the_real_planner(assistant_graph, tmp_path):
    # Imported here: settings hold the service token and the gateway is the path to the real model.
    from django.conf import settings

    from apps.assistant.gateway import plan_question

    meta = run_metadata(endpoint=os.environ["ASSISTANT_EVAL_WORKER_URL"], model=os.environ["ASSISTANT_EVAL_MODEL"])

    record = run_suite(
        load_cases(), assistant_graph, plan_question, live=True, meta=meta, redact=[settings.AI_SERVICE_TOKEN],
    )

    directory = Path(os.environ.get("ASSISTANT_EVAL_ARTIFACT_DIR") or tmp_path)
    artifact = directory / f"gate3l-{meta['started_at'].replace(':', '')}.json"
    artifact.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gate 3-L record: {artifact}")

    assert record["planner_calls"] <= 12
    # A match still needs a person to read the clarifications before the gate can be decided.
    assert record["gate_status"] == "LIVE_MATCHED_PENDING_REVIEW", {
        row["case_id"]: row["verdict"] for row in record["cases"]
    }
