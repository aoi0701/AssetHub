"""The five assistant tools. Arguments are validated before any query; scope applies before filters."""
from collections import Counter
from datetime import timezone as datetime_timezone
from types import SimpleNamespace

from django.db.models import Count, Q

from apps.assets.history_api import PersonalAssetHistorySerializer
from apps.assets.serializers import AssetHistoryLogSerializer
from apps.authentication.models import User
from apps.departments.models import Department

from .contracts import ToolCall, ToolResult, _parse_iso_datetime, validate_decision
from .knowledge import lookup_procedure
from .rendering import build_evidence, filters_key, utc_now_iso
from .scoping import (
    normalize_text,
    scope_label,
    visible_assets,
    visible_history,
    visible_requests,
)

MAX_GROUPS = 20
MAX_LIMIT = 20  # Default page size; the shared schema caps limit at the same value.
RESTRICTED_DEPARTMENT = "Bạn không có quyền xem số liệu của phòng ban này."
RESTRICTED_PERSONAL_DEPARTMENT = (
    "Bạn chỉ xem được tài sản mình đang giữ, nên không thể cung cấp số liệu theo phòng ban."
)
RESTRICTED_HOLDER = "Bạn không có quyền xem tài sản của người khác."
STAFF_HISTORY_FIELDS = (
    "id", "asset_id", "asset_code_snapshot", "asset_name_snapshot", "action_type", "from_status", "to_status",
    "created_at", "actor_name", "from_user_name", "to_user_name", "from_department_name", "to_department_name",
)
GROUP_FIELDS = {"status": "status", "category": "category__name", "department": "department__name"}
UNALLOCATED_LABEL = "Chưa phân bổ"


class _Restricted(Exception):
    pass


class _Clarify(Exception):
    def __init__(self, question, items):
        super().__init__(question)
        self.question = question
        self.items = items


# --- results and evidence ---------------------------------------------------------------

def _result(tool, status, data, sources, label, observed_at):
    return {"tool": tool, "status": status, "data": data, "sources": sources, "scope_label": label,
            "observed_at": observed_at}


def _guarded(tool, subject, handler):
    def run(actor, arguments, observed_at):
        label = scope_label(actor, subject)
        try:
            return handler(actor, arguments, label, observed_at)
        except _Restricted as blocked:
            return _result(tool, "restricted", {"message": str(blocked)}, [], label, observed_at)
        except _Clarify as ambiguous:
            data = {"clarification": ambiguous.question, "items": ambiguous.items}
            return _result(tool, "clarify", data, [], label, observed_at)
    return run


def _aggregate_source(tool, filters, total, href, label, observed_at, **facts):
    return build_evidence(
        kind="aggregate",
        key=filters_key(tool, {**filters, "group_by": facts.get("group_by")}),
        label=f"Tổng {total} ({label})",
        href=href,
        observed_at=observed_at,
        scope_label=label,
        facts={"filters": filters, "total": total, **facts},
    )


# --- asset filters ----------------------------------------------------------------------

def _match(rows, term):
    """rows: (id, label, *names). Exact normalized name beats containment; never picks the first."""
    wanted = normalize_text(term)
    exact, partial = {}, {}
    for row_id, label, *names in rows:
        normalized = [normalize_text(name) for name in names if name]
        if wanted in normalized:
            exact[row_id] = label
        elif any(wanted in name for name in normalized):
            partial[row_id] = label
    return exact or partial


def _resolve(rows, term, noun):
    matches = _match(rows, term)
    if len(matches) > 1:
        items = [{"label": label} for label in list(matches.values())[:MAX_LIMIT]]
        raise _Clarify(f"Có nhiều {noun} khớp với “{term}”, bạn muốn chọn mục nào?", items)
    return next(iter(matches), None)


def _narrow(queryset, field, resolved_id):
    # A failed lookup must match nothing; filter(field=None) would mean IS NULL.
    return queryset.none() if resolved_id is None else queryset.filter(**{field: resolved_id})


def _is_self(actor, holder):
    return normalize_text(holder) in {normalize_text(actor.full_name), normalize_text(actor.username)}


def _check_asset_permissions(actor, arguments):
    """Runs before any lookup, so a refusal reveals nothing about whether the target exists."""
    department, holder = arguments.get("department"), arguments.get("holder")
    if actor.role == User.Role.USER:
        if department or arguments.get("group_by") == "department":
            raise _Restricted(RESTRICTED_PERSONAL_DEPARTMENT)
        if holder and not _is_self(actor, holder):
            raise _Restricted(RESTRICTED_HOLDER)
    if actor.role == User.Role.MANAGER and department:
        own = actor.department
        if own is None or normalize_text(department) not in {normalize_text(own.code), normalize_text(own.name)}:
            raise _Restricted(RESTRICTED_DEPARTMENT)


def _filtered_assets(actor, arguments):
    _check_asset_permissions(actor, arguments)
    queryset = visible_assets(actor)
    if "asset_code" in arguments:
        queryset = queryset.filter(asset_code__iexact=arguments["asset_code"])
    if "statuses" in arguments:
        queryset = queryset.filter(status__in=arguments["statuses"])
    if "department" in arguments and actor.role == User.Role.ADMIN:
        rows = Department.objects.values_list("id", "name", "name", "code")
        queryset = _narrow(queryset, "department_id", _resolve(rows, arguments["department"], "phòng ban"))
    if "category" in arguments:
        rows = queryset.order_by().values_list("category_id", "category__name", "category__name").distinct()
        queryset = _narrow(queryset, "category_id", _resolve(rows, arguments["category"], "danh mục"))
    if "holder" in arguments and actor.role != User.Role.USER:
        rows = (
            (row_id, f"{full_name or username} ({username})", full_name, username)
            for row_id, full_name, username in queryset.filter(assigned_to_user__isnull=False).order_by().values_list(
                "assigned_to_user_id", "assigned_to_user__full_name", "assigned_to_user__username").distinct()
        )
        queryset = _narrow(queryset, "assigned_to_user_id", _resolve(rows, arguments["holder"], "người giữ"))
    return queryset


def _matching_asset_ids(queryset, query):
    """Exact code or serial first; otherwise names in Python, as DB LIKE is unreliable for Vietnamese."""
    exact = list(queryset.filter(Q(asset_code__iexact=query) | Q(serial_number__iexact=query)).values_list("id", flat=True))
    if exact:
        return exact
    return list(_match(queryset.values_list("id", "name", "name", "asset_code"), query))


def _asset_item(asset):
    holder = asset.assigned_to_user
    return {
        "id": asset.id,
        "asset_code": asset.asset_code,
        "name": asset.name,
        "category": asset.category.name,
        "status": asset.status,
        "department": asset.department.name if asset.department else None,
        "holder": (holder.full_name or holder.username) if holder else None,
    }


def _assets_href(actor):
    return "/my-assets" if actor.role == User.Role.USER else "/assets"


def _asset_href(actor, asset_id):
    return "/my-assets" if actor.role == User.Role.USER else f"/assets/{asset_id}"


# --- tools ------------------------------------------------------------------------------

def _assets_search(actor, arguments, label, observed_at):
    queryset = _filtered_assets(actor, arguments)
    limit = arguments.get("limit", MAX_LIMIT)
    if "query" in arguments:
        ids = _matching_asset_ids(queryset, arguments["query"])
        total = len(ids)
        # Slice before the IN clause: D1 caps bound parameters per statement.
        rows = list(queryset.filter(pk__in=ids[:limit]))
        if total > 1:
            items = [{"asset_code": asset.asset_code, "name": asset.name} for asset in rows]
            raise _Clarify(f"Có {total} tài sản khớp với “{arguments['query']}”, bạn muốn hỏi tài sản nào?", items)
    else:
        total = queryset.count()
        rows = list(queryset[:limit])

    items = [_asset_item(asset) for asset in rows]
    sources = [_aggregate_source("assets.search", arguments, total, _assets_href(actor), label, observed_at)]
    sources += [
        build_evidence(
            kind="asset", key=item["asset_code"], label=f"{item['asset_code']} — {item['name']}",
            href=_asset_href(actor, item["id"]), observed_at=observed_at, scope_label=label,
            facts={"asset_code": item["asset_code"], "status": item["status"]},
        )
        for item in items
    ]
    data = {"items": items, "total": total, "has_more": total > len(items)}
    return _result("assets.search", "ok", data, sources, label, observed_at)


def _group_counts(queryset, query, group_by):
    """Returns (total, Counter by group key); the total always covers the whole filtered set."""
    field = GROUP_FIELDS.get(group_by)
    if query:
        ids = set(_matching_asset_ids(queryset, query))
        if not field:
            return len(ids), Counter()
        keys = queryset.order_by().values_list("id", field)
        return len(ids), Counter(key for row_id, key in keys if row_id in ids)
    if not field:
        return queryset.count(), Counter()
    counts = Counter({row[field]: row["count"] for row in queryset.order_by().values(field).annotate(count=Count("id"))})
    return sum(counts.values()), counts


def _assets_summary(actor, arguments, label, observed_at):
    queryset = _filtered_assets(actor, arguments)
    group_by = arguments.get("group_by")
    total, counts = _group_counts(queryset, arguments.get("query"), group_by)
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], str(pair[0])))
    groups = [{"key": key if key is not None else UNALLOCATED_LABEL, "count": count} for key, count in ranked[:MAX_GROUPS]]
    data = {"total": total, "groups": groups, "has_more": len(ranked) > MAX_GROUPS}
    filters = {key: value for key, value in arguments.items() if key != "group_by"}
    source = _aggregate_source("assets.summary", filters, total, _assets_href(actor), label, observed_at,
                               group_by=group_by, groups=groups)
    return _result("assets.summary", "ok", data, [source], label, observed_at)


def _assets_history(actor, arguments, label, observed_at):
    mine = arguments.get("mine", False)
    personal = mine or actor.role == User.Role.USER
    if mine:
        # Role scope first: a manager's own events on assets outside the department stay hidden.
        queryset = visible_history(actor).filter(Q(from_user=actor) | Q(to_user=actor))
        if actor.role != User.Role.USER:
            label = f"{label}, sự kiện của chính bạn"
    else:
        queryset = visible_history(actor, arguments["asset_code"])
    if "actions" in arguments:
        queryset = queryset.filter(action_type__in=arguments["actions"])
    if "since" in arguments:
        queryset = queryset.filter(created_at__gte=_parse_iso_datetime(arguments["since"]))
    if "until" in arguments:
        queryset = queryset.filter(created_at__lte=_parse_iso_datetime(arguments["until"], is_until=True))

    total = queryset.count()
    logs = list(queryset.select_related(
        "asset", "actor", "from_user", "to_user", "from_department", "to_department",
    )[:arguments.get("limit", MAX_LIMIT)])
    if personal:
        context = {"request": SimpleNamespace(user=actor)}
        items = [dict(row) for row in PersonalAssetHistorySerializer(logs, many=True, context=context).data]
    else:
        serialized = AssetHistoryLogSerializer(logs, many=True).data
        items = [{key: row.get(key) for key in STAFF_HISTORY_FIELDS} for row in serialized]

    is_user = actor.role == User.Role.USER
    list_href = "/my-history" if is_user else "/assets"
    sources = [_aggregate_source("assets.history", arguments, total, list_href, label, observed_at)]
    sources += [
        build_evidence(
            kind="history", key=item["id"], label=f"{item['asset_code_snapshot']} — {item['action_type']}",
            href=list_href if is_user else f"/assets/{log.asset_id}", observed_at=observed_at, scope_label=label,
            facts={key: item[key] for key in ("action_type", "from_status", "to_status", "created_at")},
        )
        for log, item in zip(logs, items)
    ]
    data = {"items": items, "total": total, "has_more": total > len(items)}
    return _result("assets.history", "ok", data, sources, label, observed_at)


def _request_item(actor, request):
    item = {
        "id": request.id,
        "request_code": request.request_code,
        "request_type": request.request_type,
        "status": request.status,
        "asset_code": request.asset.asset_code if request.asset else None,
        "asset_name": request.asset.name if request.asset else None,
        "category": request.requested_category.name if request.requested_category else None,
        "department": request.department.name,
        "created_at": _iso(request.created_at),
        "reviewed_at": _iso(request.reviewed_at),
    }
    if actor.role != User.Role.USER:
        item["requester"] = request.requester.full_name or request.requester.username
    return item


def _iso(value):
    if value is None:
        return None
    return value.astimezone(datetime_timezone.utc).isoformat().replace("+00:00", "Z")


def _matching_request_ids(queryset, query):
    exact = list(queryset.filter(request_code__iexact=query).values_list("id", flat=True))
    if exact:
        return exact
    rows = queryset.values_list(
        "id", "request_code", "request_code", "asset__asset_code", "asset__name", "requested_category__name",
        "requester__full_name", "requester__username",
    )
    return list(_match(rows, query))


def _requests_search(actor, arguments, label, observed_at):
    queryset = visible_requests(actor)
    if "status" in arguments:
        queryset = queryset.filter(status=arguments["status"])
    if "request_type" in arguments:
        queryset = queryset.filter(request_type=arguments["request_type"])
    limit = arguments.get("limit", MAX_LIMIT)
    if "query" in arguments:
        ids = _matching_request_ids(queryset, arguments["query"])
        total = len(ids)
        rows = list(queryset.filter(pk__in=ids[:limit]))
    else:
        total = queryset.count()
        rows = list(queryset[:limit])

    items = [_request_item(actor, request) for request in rows]
    href = "/my-requests" if actor.role == User.Role.USER else "/requests"
    sources = [_aggregate_source("requests.search", arguments, total, href, label, observed_at)]
    sources += [
        build_evidence(
            kind="request", key=item["request_code"], label=item["request_code"], href=href,
            observed_at=observed_at, scope_label=label,
            facts={key: item[key] for key in ("request_code", "request_type", "status", "created_at")},
        )
        for item in items
    ]
    data = {"items": items, "total": total, "has_more": total > len(items)}
    return _result("requests.search", "ok", data, sources, label, observed_at)


def _procedures_lookup(actor, arguments, observed_at):
    return lookup_procedure(actor=actor, topic=arguments["topic"])


TOOLS = {
    "assets.search": _guarded("assets.search", "assets", _assets_search),
    "assets.summary": _guarded("assets.summary", "assets", _assets_summary),
    "assets.history": _guarded("assets.history", "history", _assets_history),
    "requests.search": _guarded("requests.search", "requests", _requests_search),
    "procedures.lookup": _procedures_lookup,
}


def execute_tool(*, actor: User, call: ToolCall) -> ToolResult:
    """Validates through the shared planner contract before any query; raises PlannerProtocolError."""
    validate_decision({"kind": "tools", "calls": [call], "clarification": None, "reason": None})
    arguments = {key: value.strip() if isinstance(value, str) else value for key, value in call["arguments"].items()}
    return TOOLS[call["name"]](actor, arguments, utc_now_iso())
