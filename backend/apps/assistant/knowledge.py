"""Versioned procedure guidance, read from a data file and filtered by the actor's role."""
import json
from functools import lru_cache
from pathlib import Path

from apps.authentication.models import User

from .contracts import ALLOWED_PROCEDURE_TOPICS, PlannerProtocolError, ToolResult
from .rendering import build_evidence, utc_now_iso

_PROCEDURES_PATH = Path(__file__).with_name("knowledge") / "procedures.json"


@lru_cache(maxsize=1)
def _procedures() -> dict:
    return json.loads(_PROCEDURES_PATH.read_text(encoding="utf-8"))["topics"]


def lookup_procedure(*, actor: User, topic: str) -> ToolResult:
    if topic not in ALLOWED_PROCEDURE_TOPICS:
        raise PlannerProtocolError("Unknown procedure topic")
    entry = _procedures()[topic]
    role_entry = entry["roles"][actor.role]
    actions = role_entry["actions"]
    data = {
        "topic": topic,
        "version": entry["version"],
        "title": entry["title"],
        "body": role_entry["body"],
        "actions": actions,
    }
    observed_at = utc_now_iso()
    label = f"Hướng dẫn cho vai trò {actor.role}"
    source = build_evidence(
        kind="procedure",
        key=f"{topic}:{entry['version']}",
        label=entry["title"],
        href=actions[0]["href"] if actions else None,
        observed_at=observed_at,
        scope_label=label,
        facts={"topic": topic, "version": entry["version"], "title": entry["title"], "body": role_entry["body"]},
    )
    return {"tool": "procedures.lookup", "status": "ok", "data": data, "sources": [source],
            "scope_label": label, "observed_at": observed_at}
