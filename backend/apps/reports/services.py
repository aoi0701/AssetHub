import datetime
import re
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest

BKK_TZ = ZoneInfo("Asia/Bangkok")
MONTH_REGEX = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

ACTIVE_STATUS_TUPLES = [
    (Asset.Status.AVAILABLE, Asset.Status.AVAILABLE.label),
    (Asset.Status.IN_USE, Asset.Status.IN_USE.label),
    (Asset.Status.DAMAGED_PENDING_REPAIR, Asset.Status.DAMAGED_PENDING_REPAIR.label),
    (Asset.Status.UNDER_MAINTENANCE, Asset.Status.UNDER_MAINTENANCE.label),
    (Asset.Status.PENDING_DISPOSAL, Asset.Status.PENDING_DISPOSAL.label),
]


class ReportService:
    """
    Business service aggregating KPIs, distribution metrics, and monthly trends.
    Enforces strict role-based scoping (Manager locked to department, Admin configurable).
    """

    @classmethod
    def _resolve_scope(cls, user: User, department_param: Optional[Any]) -> tuple[str, Optional[int]]:
        """
        Resolves scope into (scope_type, scope_id).
        scope_type is one of: 'dept', 'unallocated', 'all'.
        """
        if not user or not user.is_authenticated:
            raise PermissionDenied("Yêu cầu xác thực.")

        if user.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền truy cập báo cáo thống kê.")

        if user.role == User.Role.MANAGER:
            if not user.department_id:
                raise PermissionDenied("Tài khoản quản lý chưa được liên kết với phòng ban nào.")

            if department_param is not None and str(department_param).strip():
                dept_str = str(department_param).strip().lower()
                if dept_str == 'unallocated' or dept_str != str(user.department_id):
                    raise PermissionDenied("Quản lý chỉ có quyền xem báo cáo của phòng ban mình phụ trách.")

            return ('dept', user.department_id)

        # Admin
        if department_param is not None and str(department_param).strip():
            dept_str = str(department_param).strip().lower()
            if dept_str in ('unallocated', 'null', 'none'):
                return ('unallocated', None)
            if dept_str.isdigit():
                return ('dept', int(dept_str))
            raise ValidationError({"department_id": "Mã phòng ban không hợp lệ."})

        return ('all', None)

    @classmethod
    def _resolve_category(cls, category_param: Optional[Any]) -> Optional[int]:
        if category_param is None or str(category_param).strip() == '':
            return None
        cat_str = str(category_param).strip()
        if not cat_str.isdigit():
            raise ValidationError({"category_id": "Mã danh mục không hợp lệ."})
        return int(cat_str)

    @classmethod
    def _resolve_month_range(
        cls, start_month_param: Optional[str], end_month_param: Optional[str]
    ) -> List[str]:
        if not start_month_param and not end_month_param:
            now_bkk = timezone.now().astimezone(BKK_TZ)
            cur_y, cur_m = now_bkk.year, now_bkk.month
            months: List[str] = []
            for _ in range(12):
                months.append(f"{cur_y:04d}-{cur_m:02d}")
                cur_m -= 1
                if cur_m == 0:
                    cur_m = 12
                    cur_y -= 1
            months.reverse()
            return months

        if not start_month_param or not end_month_param:
            raise ValidationError("Cả start_month và end_month phải được cung cấp cùng nhau.")

        start_str = str(start_month_param).strip()
        end_str = str(end_month_param).strip()

        if not MONTH_REGEX.match(start_str) or not MONTH_REGEX.match(end_str):
            raise ValidationError("Định dạng tháng phải là YYYY-MM (ví dụ: 2026-01).")

        y1, m1 = map(int, start_str.split('-'))
        y2, m2 = map(int, end_str.split('-'))

        total_months = (y2 - y1) * 12 + (m2 - m1) + 1

        if total_months <= 0:
            raise ValidationError("start_month phải nhỏ hơn hoặc bằng end_month.")

        if total_months > 12:
            raise ValidationError("Khoảng thời gian tối đa là 12 tháng liên tiếp.")

        months = []
        cur_y, cur_m = y1, m1
        for _ in range(total_months):
            months.append(f"{cur_y:04d}-{cur_m:02d}")
            cur_m += 1
            if cur_m > 12:
                cur_m = 1
                cur_y += 1

        return months

    @classmethod
    def get_kpis(
        cls,
        user: User,
        department_param: Optional[Any] = None,
        category_param: Optional[Any] = None,
    ) -> Dict[str, Any]:
        scope_type, scope_id = cls._resolve_scope(user, department_param)
        category_id = cls._resolve_category(category_param)

        # Base active assets (exclude DISPOSED)
        assets_qs = Asset.objects.exclude(status=Asset.Status.DISPOSED)

        if scope_type == 'dept':
            assets_qs = assets_qs.filter(department_id=scope_id)
        elif scope_type == 'unallocated':
            assets_qs = assets_qs.filter(department__isnull=True)

        if category_id is not None:
            assets_qs = assets_qs.filter(category_id=category_id)

        agg = assets_qs.aggregate(
            total_active=Count('id'),
            available=Count('id', filter=Q(status=Asset.Status.AVAILABLE)),
            in_use=Count('id', filter=Q(status=Asset.Status.IN_USE)),
            damaged_under_maintenance=Count(
                'id',
                filter=Q(
                    status__in=[
                        Asset.Status.DAMAGED_PENDING_REPAIR,
                        Asset.Status.UNDER_MAINTENANCE,
                    ]
                ),
            ),
            pending_disposal=Count('id', filter=Q(status=Asset.Status.PENDING_DISPOSAL)),
        )

        # Pending requests
        if scope_type == 'unallocated':
            pending_requests_count = 0
        else:
            req_qs = AssetRequest.objects.filter(status=AssetRequest.Status.PENDING)
            if scope_type == 'dept':
                req_qs = req_qs.filter(department_id=scope_id)

            if category_id is not None:
                req_qs = req_qs.filter(
                    Q(
                        request_type=AssetRequest.RequestType.ALLOCATE,
                        requested_category_id=category_id,
                    )
                    | Q(
                        request_type__in=[
                            AssetRequest.RequestType.RETURN,
                            AssetRequest.RequestType.DAMAGE_REPORT,
                        ],
                        asset__category_id=category_id,
                    )
                )

            pending_requests_count = req_qs.count()

        return {
            "total_active": agg['total_active'] or 0,
            "available": agg['available'] or 0,
            "in_use": agg['in_use'] or 0,
            "damaged_under_maintenance": agg['damaged_under_maintenance'] or 0,
            "pending_disposal": agg['pending_disposal'] or 0,
            "pending_requests": pending_requests_count,
            "last_updated": timezone.now().isoformat(),
        }

    @classmethod
    def get_charts(
        cls,
        user: User,
        department_param: Optional[Any] = None,
        category_param: Optional[Any] = None,
        start_month: Optional[str] = None,
        end_month: Optional[str] = None,
    ) -> Dict[str, Any]:
        scope_type, scope_id = cls._resolve_scope(user, department_param)
        category_id = cls._resolve_category(category_param)
        months = cls._resolve_month_range(start_month, end_month)

        # 1. Base active assets for scoped distributions
        assets_qs = Asset.objects.exclude(status=Asset.Status.DISPOSED)

        if scope_type == 'dept':
            assets_qs = assets_qs.filter(department_id=scope_id)
        elif scope_type == 'unallocated':
            assets_qs = assets_qs.filter(department__isnull=True)

        if category_id is not None:
            assets_qs = assets_qs.filter(category_id=category_id)

        # Status distribution
        status_counts = dict(
            assets_qs.values_list('status').annotate(cnt=Count('id'))
        )
        status_distribution = [
            {
                "status": s_code,
                "label": s_label,
                "count": status_counts.get(s_code, 0),
            }
            for s_code, s_label in ACTIVE_STATUS_TUPLES
        ]

        # Category distribution
        cat_counts = dict(
            assets_qs.values_list('category_id').annotate(cnt=Count('id'))
        )
        category_distribution = []
        for cat in Category.objects.all().order_by('id'):
            category_distribution.append({
                "category_id": cat.id,
                "category_name": cat.name,
                "count": cat_counts.get(cat.id, 0),
            })

        # Department distribution
        if user.role == User.Role.MANAGER:
            department_distribution = []
        else:
            # Admin sees all departments + unallocated
            counts_by_dept = dict(
                assets_qs.values_list('department_id').annotate(cnt=Count('id'))
            )
            department_distribution = []
            for dept in Department.objects.all().order_by('name', 'id'):
                department_distribution.append({
                    "department_id": dept.id,
                    "department_name": dept.name,
                    "count": counts_by_dept.get(dept.id, 0),
                })
            department_distribution.append({
                "department_id": None,
                "department_name": "Chưa phân bổ",
                "count": counts_by_dept.get(None, 0),
            })

        # 2. Monthly trend
        first_m = months[0]
        last_m = months[-1]
        y1, m1 = map(int, first_m.split('-'))
        y2, m2 = map(int, last_m.split('-'))

        start_dt = datetime.datetime(y1, m1, 1, 0, 0, 0, tzinfo=BKK_TZ)
        if m2 == 12:
            end_dt = datetime.datetime(y2 + 1, 1, 1, 0, 0, 0, tzinfo=BKK_TZ)
        else:
            end_dt = datetime.datetime(y2, m2 + 1, 1, 0, 0, 0, tzinfo=BKK_TZ)

        history_qs = AssetHistoryLog.objects.filter(
            action_type__in=[
                AssetHistoryLog.ActionType.CREATE,
                AssetHistoryLog.ActionType.DISPOSAL_CONFIRM,
            ],
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )

        if scope_type == 'dept':
            history_qs = history_qs.filter(to_department_id=scope_id)
        elif scope_type == 'unallocated':
            history_qs = history_qs.filter(to_department__isnull=True)

        if category_id is not None:
            history_qs = history_qs.filter(asset__category_id=category_id)

        trend_dict = {
            m: {"month": m, "new_assets": 0, "disposed_assets": 0}
            for m in months
        }

        for log_created_at, action_type in history_qs.values_list('created_at', 'action_type'):
            m_str = log_created_at.astimezone(BKK_TZ).strftime('%Y-%m')
            if m_str in trend_dict:
                if action_type == AssetHistoryLog.ActionType.CREATE:
                    trend_dict[m_str]['new_assets'] += 1
                elif action_type == AssetHistoryLog.ActionType.DISPOSAL_CONFIRM:
                    trend_dict[m_str]['disposed_assets'] += 1

        monthly_trend = [trend_dict[m] for m in months]

        return {
            "status_distribution": status_distribution,
            "category_distribution": category_distribution,
            "department_distribution": department_distribution,
            "monthly_trend": monthly_trend,
        }
