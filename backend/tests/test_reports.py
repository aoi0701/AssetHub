import datetime
from zoneinfo import ZoneInfo
import pytest
from rest_framework.test import APIClient

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


@pytest.fixture
def cat_laptop(db):
    return Category.objects.create(name="Laptop", description="Portable computers")


@pytest.fixture
def cat_monitor(db):
    return Category.objects.create(name="Monitor", description="Display screens")


@pytest.fixture
def manager_hr(db, dept_hr):
    return User.objects.create_user(
        username="manager_hr",
        email="manager.hr@assethub.local",
        password="ManagerPassword123!",
        role=User.Role.MANAGER,
        full_name="HR Manager",
        department=dept_hr,
        is_active=True,
    )


# ===========================================================================
# 1. Permission tests
# ===========================================================================

@pytest.mark.django_db
def test_reports_user_forbidden(client, regular_user):
    """User receives 403 Forbidden on reports endpoints. Unauthenticated gets 401."""
    # Unauthenticated
    assert client.get("/api/reports/kpis/").status_code == 401
    assert client.get("/api/reports/charts/").status_code == 401

    # Authenticated standard user -> 403
    user_client = auth_client_for(regular_user)
    assert user_client.get("/api/reports/kpis/").status_code == 403
    assert user_client.get("/api/reports/charts/").status_code == 403


@pytest.mark.django_db
def test_manager_scoping_and_forbidden_on_other_department(manager_user, dept_hr):
    """
    Manager is strictly locked to their department.
    Passing other department_id or 'unallocated' returns 403 Forbidden.
    """
    mgr_client = auth_client_for(manager_user)

    # Passing HR department while manager is in IT -> 403
    resp_other_dept = mgr_client.get(f"/api/reports/kpis/?department_id={dept_hr.id}")
    assert resp_other_dept.status_code == 403

    resp_other_chart = mgr_client.get(f"/api/reports/charts/?department_id={dept_hr.id}")
    assert resp_other_chart.status_code == 403

    # Passing 'unallocated' -> 403
    resp_unalloc = mgr_client.get("/api/reports/kpis/?department_id=unallocated")
    assert resp_unalloc.status_code == 403

    # Passing own department_id or omitting -> 200 OK
    resp_own = mgr_client.get(f"/api/reports/kpis/?department_id={manager_user.department_id}")
    assert resp_own.status_code == 200
    resp_default = mgr_client.get("/api/reports/kpis/")
    assert resp_default.status_code == 200


@pytest.mark.django_db
def test_manager_department_distribution_is_empty(manager_user):
    """
    Spec §7.2: Manager does not receive company-wide department distribution.
    Returns empty list [] for department_distribution.
    """
    mgr_client = auth_client_for(manager_user)
    resp = mgr_client.get("/api/reports/charts/")
    assert resp.status_code == 200
    assert resp.data["department_distribution"] == []


# ===========================================================================
# 2. KPI counts and single asset counting
# ===========================================================================

@pytest.mark.django_db
def test_kpi_counts_and_exclusion_of_disposed(admin_user, dept_it, dept_hr, cat_laptop, regular_user):
    """
    Six KPIs:
    - total_active (status != DISPOSED)
    - available
    - in_use
    - damaged_under_maintenance
    - pending_disposal
    - pending_requests
    Assets counted once by ID despite multiple logs. DISPOSED assets excluded.
    """
    # 1. Available asset
    a1 = Asset.objects.create(
        asset_code="AST-2026-001",
        name="Laptop 1",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    # 2. In use asset
    a2 = Asset.objects.create(
        asset_code="AST-2026-002",
        name="Laptop 2",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.IN_USE,
        assigned_to_user=regular_user,
    )
    # 3. Damaged pending repair asset
    a3 = Asset.objects.create(
        asset_code="AST-2026-003",
        name="Laptop 3",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.DAMAGED_PENDING_REPAIR,
    )
    # 4. Under maintenance asset
    a4 = Asset.objects.create(
        asset_code="AST-2026-004",
        name="Laptop 4",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.UNDER_MAINTENANCE,
    )
    # 5. Pending disposal asset
    a5 = Asset.objects.create(
        asset_code="AST-2026-005",
        name="Laptop 5",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.PENDING_DISPOSAL,
    )
    # 6. Disposed asset (must be excluded from total_active and all other active counts)
    a6 = Asset.objects.create(
        asset_code="AST-2026-006",
        name="Laptop 6",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.DISPOSED,
    )
    # Create multiple history logs for a2 to ensure it is not double counted
    for _ in range(5):
        AssetHistoryLog.objects.create(
            asset=a2,
            actor=admin_user,
            action_type=AssetHistoryLog.ActionType.ALLOCATE,
            from_status=Asset.Status.AVAILABLE,
            to_status=Asset.Status.IN_USE,
            to_user=regular_user,
            from_department=dept_it,
            to_department=dept_it,
            reason="Repeated allocation log",
            asset_code_snapshot=a2.asset_code,
            asset_name_snapshot=a2.name,
        )

    # 7. Pending requests
    req1 = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Need laptop",
        status=AssetRequest.Status.PENDING,
    )
    req2 = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.RETURN,
        requester=regular_user,
        department=dept_it,
        asset=a2,
        reason="Returning",
        status=AssetRequest.Status.PENDING,
    )
    # Approved request should not count in pending_requests
    AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Already approved",
        status=AssetRequest.Status.APPROVED,
    )

    admin_client = auth_client_for(admin_user)
    resp = admin_client.get("/api/reports/kpis/")
    assert resp.status_code == 200
    data = resp.data

    assert data["total_active"] == 5
    assert data["available"] == 1
    assert data["in_use"] == 1
    assert data["damaged_under_maintenance"] == 2  # a3 + a4
    assert data["pending_disposal"] == 1
    assert data["pending_requests"] == 2
    assert "last_updated" in data


@pytest.mark.django_db
def test_admin_reports_department_filter_and_unallocated(admin_user, dept_it, dept_hr, cat_laptop, regular_user, manager_hr):
    """
    Admin can filter by department_id or 'unallocated'.
    Unallocated department has 0 pending requests.
    """
    # IT asset
    Asset.objects.create(
        asset_code="AST-IT-1",
        name="IT Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    # HR asset
    Asset.objects.create(
        asset_code="AST-HR-1",
        name="HR Laptop",
        category=cat_laptop,
        department=dept_hr,
        status=Asset.Status.IN_USE,
        assigned_to_user=manager_hr,
    )
    # Unallocated asset
    Asset.objects.create(
        asset_code="AST-UNALLOC-1",
        name="Company Stock Laptop",
        category=cat_laptop,
        department=None,
        status=Asset.Status.AVAILABLE,
    )

    # IT Request
    AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="IT request",
        status=AssetRequest.Status.PENDING,
    )

    admin_client = auth_client_for(admin_user)

    # Filter IT
    resp_it = admin_client.get(f"/api/reports/kpis/?department_id={dept_it.id}")
    assert resp_it.status_code == 200
    assert resp_it.data["total_active"] == 1
    assert resp_it.data["available"] == 1
    assert resp_it.data["pending_requests"] == 1

    # Filter Unallocated
    resp_unalloc = admin_client.get("/api/reports/kpis/?department_id=unallocated")
    assert resp_unalloc.status_code == 200
    assert resp_unalloc.data["total_active"] == 1
    assert resp_unalloc.data["available"] == 1
    assert resp_unalloc.data["pending_requests"] == 0


@pytest.mark.django_db
def test_reports_category_filter(admin_user, dept_it, cat_laptop, cat_monitor, regular_user):
    """
    Category filter scopes assets and pending requests.
    ALLOCATE request matches requested_category_id.
    RETURN / DAMAGE_REPORT matches asset__category_id.
    """
    laptop = Asset.objects.create(
        asset_code="AST-CAT-L",
        name="Laptop Cat",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.IN_USE,
        assigned_to_user=regular_user,
    )
    monitor = Asset.objects.create(
        asset_code="AST-CAT-M",
        name="Monitor Cat",
        category=cat_monitor,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )

    # ALLOCATE for Laptop
    AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Need laptop",
        status=AssetRequest.Status.PENDING,
    )
    # RETURN for Laptop
    AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.RETURN,
        requester=regular_user,
        department=dept_it,
        asset=laptop,
        reason="Return laptop",
        status=AssetRequest.Status.PENDING,
    )
    # ALLOCATE for Monitor
    AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_monitor,
        reason="Need monitor",
        status=AssetRequest.Status.PENDING,
    )

    admin_client = auth_client_for(admin_user)

    # Filter by Laptop category
    resp_laptop = admin_client.get(f"/api/reports/kpis/?category_id={cat_laptop.id}")
    assert resp_laptop.status_code == 200
    assert resp_laptop.data["total_active"] == 1
    assert resp_laptop.data["in_use"] == 1
    assert resp_laptop.data["available"] == 0
    assert resp_laptop.data["pending_requests"] == 2  # 1 ALLOCATE + 1 RETURN

    # Filter by Monitor category
    resp_monitor = admin_client.get(f"/api/reports/kpis/?category_id={cat_monitor.id}")
    assert resp_monitor.status_code == 200
    assert resp_monitor.data["total_active"] == 1
    assert resp_monitor.data["available"] == 1
    assert resp_monitor.data["in_use"] == 0
    assert resp_monitor.data["pending_requests"] == 1


# ===========================================================================
# 3. Charts: Distributions and Monthly Trend
# ===========================================================================

@pytest.mark.django_db
def test_admin_charts_distributions(admin_user, dept_it, dept_hr, cat_laptop, cat_monitor):
    """
    status_distribution: list of {status, label, count} excluding DISPOSED.
    category_distribution: list of {category_id, category_name, count} excluding DISPOSED.
    department_distribution: Admin sees all departments + unallocated.
    """
    Asset.objects.create(
        asset_code="AST-D1",
        name="D1",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    Asset.objects.create(
        asset_code="AST-D2",
        name="D2",
        category=cat_monitor,
        department=dept_hr,
        status=Asset.Status.AVAILABLE,
    )
    Asset.objects.create(
        asset_code="AST-D3",
        name="D3",
        category=cat_laptop,
        department=None,
        status=Asset.Status.AVAILABLE,
    )
    Asset.objects.create(
        asset_code="AST-D4",
        name="D4",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.DISPOSED,
    )

    admin_client = auth_client_for(admin_user)
    resp = admin_client.get("/api/reports/charts/")
    assert resp.status_code == 200
    data = resp.data

    # 1. status_distribution
    statuses = [s["status"] for s in data["status_distribution"]]
    assert "DISPOSED" not in statuses
    avail = next(s for s in data["status_distribution"] if s["status"] == "AVAILABLE")
    assert avail["count"] == 3

    # 2. category_distribution
    cat_dist = {c["category_id"]: c["count"] for c in data["category_distribution"]}
    assert cat_dist.get(cat_laptop.id) == 2  # AST-D1, AST-D3 (AST-D4 disposed is excluded)
    assert cat_dist.get(cat_monitor.id) == 1

    # 3. department_distribution
    dept_dist = data["department_distribution"]
    unallocated_entry = next((d for d in dept_dist if d["department_id"] is None), None)
    assert unallocated_entry is not None
    assert unallocated_entry["department_name"] == "Chưa phân bổ"
    assert unallocated_entry["count"] == 1  # AST-D3

    it_entry = next((d for d in dept_dist if d["department_id"] == dept_it.id), None)
    assert it_entry is not None
    assert it_entry["count"] == 1  # AST-D1


@pytest.mark.django_db
def test_monthly_trend_zero_padding_and_validation(admin_user):
    """
    monthly_trend fills zero months for range.
    start_month and end_month max 12 consecutive months.
    Invalid format or span > 12 months returns 400 Bad Request.
    """
    admin_client = auth_client_for(admin_user)

    # Valid 6 consecutive months
    resp = admin_client.get("/api/reports/charts/?start_month=2026-01&end_month=2026-06")
    assert resp.status_code == 200
    trend = resp.data["monthly_trend"]
    assert len(trend) == 6
    expected_months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert [item["month"] for item in trend] == expected_months
    for item in trend:
        assert item["new_assets"] == 0
        assert item["disposed_assets"] == 0

    # > 12 consecutive months -> 400
    resp_over = admin_client.get("/api/reports/charts/?start_month=2025-01&end_month=2026-02")
    assert resp_over.status_code == 400

    # start > end -> 400
    resp_reversed = admin_client.get("/api/reports/charts/?start_month=2026-06&end_month=2026-01")
    assert resp_reversed.status_code == 400

    # Invalid format -> 400
    resp_bad = admin_client.get("/api/reports/charts/?start_month=2026-1&end_month=2026-06")
    assert resp_bad.status_code == 400


@pytest.mark.django_db
def test_monthly_trend_asia_bangkok_boundary_and_snapshots(admin_user, dept_it, dept_hr, cat_laptop):
    """
    Spec §7.2:
    - Timezone: Asia/Bangkok (+07:00). Event at 2026-04-30 20:00:00 UTC is 2026-05-01 03:00:00 BKK -> month 2026-05.
    - Department snapshot: CREATE and DISPOSAL_CONFIRM use to_department snapshot, not current department.
    """
    bkk_tz = ZoneInfo("Asia/Bangkok")
    utc_tz = ZoneInfo("UTC")

    # Asset 1 created in Dept IT, later transferred to Dept HR
    a1 = Asset.objects.create(
        asset_code="AST-SNAP-1",
        name="Asset 1",
        category=cat_laptop,
        department=dept_hr,  # Currently in HR
        status=Asset.Status.AVAILABLE,
    )
    # Log CREATE snapshot has to_department=dept_it, created at 2026-04-30 20:00:00 UTC (May 1 in BKK)
    dt_bkk_boundary = datetime.datetime(2026, 4, 30, 20, 0, 0, tzinfo=utc_tz)
    log_create = AssetHistoryLog.objects.create(
        asset=a1,
        actor=admin_user,
        action_type=AssetHistoryLog.ActionType.CREATE,
        from_status="",
        to_status=Asset.Status.AVAILABLE,
        to_department=dept_it,  # Snapshot is IT!
        reason="Created",
        asset_code_snapshot=a1.asset_code,
        asset_name_snapshot=a1.name,
    )
    AssetHistoryLog.objects.filter(pk=log_create.pk).update(created_at=dt_bkk_boundary)

    # Asset 2 disposed in Dept IT
    a2 = Asset.objects.create(
        asset_code="AST-SNAP-2",
        name="Asset 2",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.DISPOSED,
    )
    dt_may = datetime.datetime(2026, 5, 10, 10, 0, 0, tzinfo=utc_tz)
    log_dispose = AssetHistoryLog.objects.create(
        asset=a2,
        actor=admin_user,
        action_type=AssetHistoryLog.ActionType.DISPOSAL_CONFIRM,
        from_status=Asset.Status.PENDING_DISPOSAL,
        to_status=Asset.Status.DISPOSED,
        to_department=dept_it,
        reason="Disposed",
        asset_code_snapshot=a2.asset_code,
        asset_name_snapshot=a2.name,
    )
    AssetHistoryLog.objects.filter(pk=log_dispose.pk).update(created_at=dt_may)

    admin_client = auth_client_for(admin_user)

    # 1. Company-wide monthly trend
    resp = admin_client.get("/api/reports/charts/?start_month=2026-04&end_month=2026-05")
    assert resp.status_code == 200
    trend = resp.data["monthly_trend"]
    april = next(m for m in trend if m["month"] == "2026-04")
    may = next(m for m in trend if m["month"] == "2026-05")

    # Boundary check: 2026-04-30 20:00 UTC belongs to May in Asia/Bangkok
    assert april["new_assets"] == 0
    assert may["new_assets"] == 1
    assert may["disposed_assets"] == 1

    # 2. Filter by IT department:
    # a1's CREATE log had to_department=dept_it snapshot, so IT should see it!
    resp_it = admin_client.get(f"/api/reports/charts/?department_id={dept_it.id}&start_month=2026-04&end_month=2026-05")
    assert resp_it.status_code == 200
    it_may = next(m for m in resp_it.data["monthly_trend"] if m["month"] == "2026-05")
    assert it_may["new_assets"] == 1
    assert it_may["disposed_assets"] == 1

    # 3. Filter by HR department:
    # a1 is currently in HR, but its CREATE snapshot was IT, so HR should NOT see new_assets in May!
    resp_hr = admin_client.get(f"/api/reports/charts/?department_id={dept_hr.id}&start_month=2026-04&end_month=2026-05")
    assert resp_hr.status_code == 200
    hr_may = next(m for m in resp_hr.data["monthly_trend"] if m["month"] == "2026-05")
    assert hr_may["new_assets"] == 0
    assert hr_may["disposed_assets"] == 0
