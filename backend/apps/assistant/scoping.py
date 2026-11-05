"""Query scopes for the assistant; each mirrors the running API for the same role."""
import unicodedata

from django.db.models import Q

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.requests.models import AssetRequest

PERSONAL_HISTORY_LABEL = "Lịch sử của chính bạn"


def normalize_text(value: str) -> str:
    """Accent, case and whitespace insensitive form; 'đ' has no Unicode decomposition."""
    folded = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", folded)
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).split())


def visible_assets(actor: User):
    """Same rows as AssetViewSet.get_queryset() without query parameters."""
    if actor.role == User.Role.ADMIN:
        queryset = Asset.objects.all()
    elif actor.role == User.Role.MANAGER and actor.department_id:
        queryset = Asset.objects.filter(department_id=actor.department_id)
    elif actor.role == User.Role.USER:
        queryset = Asset.objects.filter(assigned_to_user_id=actor.id)
    else:
        queryset = Asset.objects.none()
    return queryset.select_related("category", "department", "assigned_to_user").order_by("-created_at", "-id")


def personal_history(actor: User):
    """Same rows as PersonalAssetHistoryView: events the actor received or returned."""
    return AssetHistoryLog.objects.filter(Q(from_user=actor) | Q(to_user=actor)).order_by("-created_at", "-id")


def visible_history(actor: User, asset_code: str | None = None):
    """USER: personal events only. MANAGER/ADMIN: events of assets the role can open."""
    if actor.role == User.Role.USER:
        queryset = personal_history(actor)
    elif actor.role == User.Role.ADMIN:
        queryset = AssetHistoryLog.objects.order_by("-created_at", "-id")
    elif actor.role == User.Role.MANAGER and actor.department_id:
        queryset = AssetHistoryLog.objects.filter(asset__department_id=actor.department_id).order_by("-created_at", "-id")
    else:
        queryset = AssetHistoryLog.objects.none()
    if asset_code:
        queryset = queryset.filter(asset__asset_code__iexact=asset_code)
    return queryset


def visible_requests(actor: User):
    """Same rows as RequestListCreateView.get_queryset() without query parameters."""
    if actor.role == User.Role.ADMIN:
        queryset = AssetRequest.objects.all()
    elif actor.role == User.Role.MANAGER and actor.department_id:
        queryset = AssetRequest.objects.filter(department_id=actor.department_id)
    elif actor.role == User.Role.USER:
        queryset = AssetRequest.objects.filter(requester_id=actor.id)
    else:
        queryset = AssetRequest.objects.none()
    return queryset.select_related(
        "requester", "department", "requested_category", "asset",
    ).order_by("-created_at", "-id")


def scope_label(actor: User, subject: str) -> str:
    """Backend-owned label attached to every count; subject is assets, history or requests."""
    if actor.role == User.Role.ADMIN:
        return "Toàn hệ thống"
    if actor.role == User.Role.MANAGER:
        return f"Phòng ban: {actor.department.name}" if actor.department_id else "Chưa được gán phòng ban"
    return {
        "assets": "Tài sản bạn đang giữ",
        "history": PERSONAL_HISTORY_LABEL,
        "requests": "Phiếu của chính bạn",
    }[subject]
