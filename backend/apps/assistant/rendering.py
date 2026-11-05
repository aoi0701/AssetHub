"""Builds evidence and Vietnamese answers only from structured tool results, never model text."""
import hashlib
import json
import re
from datetime import timezone as datetime_timezone

from django.utils import timezone

from .contracts import TOOL_RESULT_STATUS_PRECEDENCE, Evidence, ToolResult

MAX_ANSWER_CHARS = 6000
EVIDENCE_KEYS = ("id", "kind", "label", "href", "observed_at", "scope_label", "facts")
_ALLOWED_HREF = re.compile(r"/(my-assets|my-history|my-requests|requests|assets(/[1-9][0-9]*)?)")
TRUNCATION_NOTE = "Đang hiển thị {shown}/{total} dòng chi tiết; tổng số và căn cứ vẫn đầy đủ."

STATUS_LABELS = {
    "AVAILABLE": "Có sẵn",
    "IN_USE": "Đang sử dụng",
    "DAMAGED_PENDING_REPAIR": "Hỏng, chờ sửa chữa",
    "UNDER_MAINTENANCE": "Đang bảo trì",
    "PENDING_DISPOSAL": "Chờ thanh lý",
    "DISPOSED": "Đã thanh lý",
}
ACTION_LABELS = {
    "CREATE": "Tạo mới",
    "ALLOCATE": "Cấp phát",
    "RETURN": "Hoàn trả",
    "DAMAGE": "Báo hỏng",
    "MAINTENANCE_START": "Bắt đầu bảo trì",
    "MAINTENANCE_END": "Kết thúc bảo trì",
    "TRANSFER": "Điều chuyển",
    "DISPOSAL_PROPOSE": "Đề xuất thanh lý",
    "DISPOSAL_CONFIRM": "Xác nhận thanh lý",
}
REQUEST_TYPE_LABELS = {"ALLOCATE": "Cấp phát", "RETURN": "Trả tài sản", "DAMAGE_REPORT": "Báo hỏng"}
REQUEST_STATUS_LABELS = {"PENDING": "Chờ duyệt", "APPROVED": "Đã duyệt", "REJECTED": "Từ chối"}


def utc_now_iso() -> str:
    return timezone.now().astimezone(datetime_timezone.utc).isoformat().replace("+00:00", "Z")


def is_allowed_href(href: object) -> bool:
    return isinstance(href, str) and _ALLOWED_HREF.fullmatch(href) is not None


def build_evidence(*, kind, key, label, href, observed_at, scope_label, facts) -> Evidence:
    return {
        "id": f"{kind}:{key}",
        "kind": kind,
        "label": label,
        "href": href if is_allowed_href(href) else None,
        "observed_at": observed_at,
        "scope_label": scope_label,
        "facts": facts,
    }


def filters_key(tool: str, filters: dict) -> str:
    """Stable aggregate ID, so identical calls deduplicate and different filters do not collide."""
    digest = hashlib.sha256(json.dumps(filters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return f"{tool}:{digest[:12]}"


def _sanitize_sources(results) -> list[Evidence]:
    """Rebuilds each source from known keys and re-checks its link, whatever the result dict holds."""
    sources = {}
    for result in results:
        for source in result["sources"]:
            clean = {key: source.get(key) for key in EVIDENCE_KEYS}
            clean["href"] = source["href"] if is_allowed_href(source.get("href")) else None
            sources.setdefault(clean["id"], clean)
    return list(sources.values())


def _asset_line(item) -> str:
    parts = [f"{item['asset_code']} — {item['name']}", STATUS_LABELS.get(item["status"], item["status"])]
    if item.get("category"):
        parts.append(item["category"])
    if item.get("department"):
        parts.append(item["department"])
    if item.get("holder"):
        parts.append(f"người giữ: {item['holder']}")
    return "- " + "; ".join(parts)


def _history_line(item) -> str:
    line = (f"- {item['created_at']}: {item['asset_code_snapshot']} — "
            f"{ACTION_LABELS.get(item['action_type'], item['action_type'])} "
            f"({STATUS_LABELS.get(item['from_status'], item['from_status']) or 'không có'} → "
            f"{STATUS_LABELS.get(item['to_status'], item['to_status'])})")
    return f"{line}; thực hiện: {item['actor_name']}" if item.get("actor_name") else line


def _request_line(item) -> str:
    parts = [
        item["request_code"],
        REQUEST_TYPE_LABELS.get(item["request_type"], item["request_type"]),
        REQUEST_STATUS_LABELS.get(item["status"], item["status"]),
    ]
    if item.get("asset_code"):
        parts.append(item["asset_code"])
    if item.get("requester"):
        parts.append(f"người gửi: {item['requester']}")
    return "- " + "; ".join(parts)


def _group_line(tool_filter, group) -> str:
    key = STATUS_LABELS.get(group["key"], group["key"]) if tool_filter == "status" else group["key"]
    return f"- {key}: {group['count']}"


def _list_block(result, noun, line_for) -> tuple[list[str], list[str]]:
    data, scope = result["data"], result["scope_label"]
    if data["total"] == 0:
        return [f"Không tìm thấy {noun} trong phạm vi được phép xem: 0 ({scope})."], []
    heads = [f"Có {data['total']} {noun} ({scope})."]
    if data["has_more"]:
        heads.append(f"Đang hiển thị {len(data['items'])}/{data['total']} {noun}.")
    return heads, [line_for(item) for item in data["items"]]


def _summary_block(result) -> tuple[list[str], list[str]]:
    data, scope = result["data"], result["scope_label"]
    if data["total"] == 0:
        return [f"Không tìm thấy tài sản trong phạm vi được phép xem: 0 ({scope})."], []
    heads = [f"Tổng cộng {data['total']} tài sản ({scope})."]
    group_by = result["sources"][0]["facts"].get("group_by")
    if data["has_more"]:
        heads.append(f"Đang hiển thị {len(data['groups'])} nhóm lớn nhất; tổng vẫn tính trên toàn bộ tập đã lọc.")
    return heads, [_group_line(group_by, group) for group in data["groups"]]


def _procedure_block(result) -> tuple[list[str], list[str]]:
    data = result["data"]
    return [f"{data['title']} (phiên bản {data['version']})"], data["body"].splitlines()


_BLOCKS = {
    "assets.search": lambda result: _list_block(result, "tài sản", _asset_line),
    "assets.summary": _summary_block,
    "assets.history": lambda result: _list_block(result, "sự kiện lịch sử", _history_line),
    "requests.search": lambda result: _list_block(result, "phiếu yêu cầu", _request_line),
    "procedures.lookup": _procedure_block,
}


def _clarify_text(result) -> str:
    lines = [result["data"]["clarification"]]
    for item in result["data"]["items"]:
        if "asset_code" in item:
            lines.append(f"- {item['asset_code']} — {item['name']}")
        else:
            lines.append(f"- {item['label']}")
    return _fit(lines[:1], lines[1:])


def _fit(heads: list[str], details: list[str]) -> str:
    """Drops detail lines from the end to fit; headline totals are never cut."""
    text = "\n".join([*heads, *details])
    if len(text) <= MAX_ANSWER_CHARS:
        return text
    kept = list(details)
    while kept:
        kept.pop()
        note = TRUNCATION_NOTE.format(shown=len(kept), total=len(details))
        text = "\n".join([*heads, note, *kept])
        if len(text) <= MAX_ANSWER_CHARS:
            return text
    return text


def render_tool_results(results: list[ToolResult]) -> tuple[str, list[Evidence]]:
    """A restricted, clarify or unsupported result replaces the whole answer: no partial totals."""
    for status in TOOL_RESULT_STATUS_PRECEDENCE:
        if status == "ok":
            break
        for result in results:
            if result["status"] == status:
                return (_clarify_text(result) if status == "clarify" else result["data"]["message"]), []

    heads, details = [], []
    for result in results:
        block_heads, block_details = _BLOCKS[result["tool"]](result)
        heads.extend(block_heads)
        details.extend(block_details)
    return _fit(heads, details), _sanitize_sources(results)
