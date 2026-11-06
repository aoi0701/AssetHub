"""Extended live run: 12 robustness cases, at most 12 real planner calls, only with an explicit extra opt-in.

Skips before any fixture, network or secret access unless the base live conditions hold AND
RUN_ASSISTANT_LLM_EVAL_EXTENDED=1. Its record never decides Gate 3-L. No mock, no retry.
"""
import json
import os
from pathlib import Path

import pytest

from .planner_eval import extended_live_problem, load_extended_cases, run_extended_suite, run_metadata

_problem = extended_live_problem(os.environ)
pytestmark = pytest.mark.skipif(_problem is not None, reason=_problem or "")


def test_twelve_extended_cases_against_the_real_planner(assistant_graph):
    from django.conf import settings

    from apps.assistant.gateway import plan_question

    meta = {**run_metadata(endpoint=os.environ["ASSISTANT_EVAL_WORKER_URL"], model=os.environ["ASSISTANT_EVAL_MODEL"]),
            "suite": "extended"}

    # Never the base artifact name; proven writable before anything is spent.
    artifact = Path(os.environ["ASSISTANT_EVAL_ARTIFACT_DIR"]) / f"gate3l-extended-{meta['started_at'].replace(':', '')}.json"
    artifact.write_text(json.dumps({"suite": "extended", "extended_status": "EXTENDED_PENDING", "meta": meta}),
                        encoding="utf-8")

    record = run_extended_suite(
        load_extended_cases(), assistant_graph, plan_question, live=True, meta=meta, redact=[settings.AI_SERVICE_TOKEN],
    )

    artifact.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Extended record: {artifact}")

    assert record["planner_calls"] <= 12
    assert record["extended_status"] == "EXTENDED_MATCHED_PENDING_REVIEW", {
        row["case_id"]: row["verdict"] for row in record["cases"]
    }
