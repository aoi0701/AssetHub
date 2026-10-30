import io
import os
import pytest
from django.core.management import call_command, CommandError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.authentication.models import User
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest


VIETNAMESE_CHARS = set(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"
)

EXPECTED_DEPT_CODES = {'BGD', 'NSHC', 'TCKT', 'CNTT', 'KD', 'MKT', 'VH', 'KV'}


@pytest.mark.django_db
def test_seed_command_requires_flags(monkeypatch):
    """
    1. test_seed_command_requires_flags:
       Calling call_command('seed_vietnam_demo') without --confirm or --dry-run
       must raise CommandError.
    """
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")
    out = io.StringIO()
    with pytest.raises(CommandError, match=r"(--confirm|--dry-run)"):
        call_command("seed_vietnam_demo", stdout=out)


@pytest.mark.django_db
def test_seed_command_dry_run_does_not_modify_database(monkeypatch):
    """
    2. test_seed_command_dry_run_does_not_modify_database:
       Running with --dry-run succeeds without writing to DB.
       Counts in database remain 0. Output includes planned statistics:
       8 departments, 18 categories, 35 users, 80 assets, 30 requests.
    """
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")
    out = io.StringIO()
    call_command("seed_vietnam_demo", "--dry-run", stdout=out)
    output = out.getvalue()

    # Database must remain completely unmodified
    assert Department.objects.count() == 0
    assert Category.objects.count() == 0
    assert User.objects.count() == 0
    assert Asset.objects.count() == 0
    assert AssetRequest.objects.count() == 0
    assert AssetHistoryLog.objects.count() == 0

    # Output prints expected planned counts
    assert "8" in output  # 8 departments
    assert "18" in output  # 18 categories
    assert "35" in output  # 35 users
    assert "80" in output  # 80 assets
    assert "30" in output  # 30 requests


@pytest.mark.django_db
def test_seed_command_safety_guards(monkeypatch, settings):
    """
    3. test_seed_command_safety_guards:
       If ALLOW_DEMO_SEED is not set or false and DEBUG is False, command must abort.
    """
    settings.DEBUG = False
    monkeypatch.delenv("ALLOW_DEMO_SEED", raising=False)
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("seed_vietnam_demo", "--confirm", stdout=out)

    monkeypatch.setenv("ALLOW_DEMO_SEED", "false")
    with pytest.raises(CommandError):
        call_command("seed_vietnam_demo", "--confirm", stdout=out)


@pytest.mark.django_db
def test_seed_command_creates_accurate_vietnamese_dataset(monkeypatch):
    """
    4. test_seed_command_creates_accurate_vietnamese_dataset:
       Run with --confirm:
       - 8 departments created correctly (BGD, NSHC, TCKT, CNTT, KD, MKT, VH, KV).
       - 18 categories created correctly.
       - 35 users created correctly (2 Admin, 8 Manager, 25 User).
       - Full Vietnamese names with diacritics, roles, simulated @gmail.com emails.
       - 80 assets with full status coverage (IN_USE, AVAILABLE, UNDER_MAINTENANCE,
         DAMAGED_PENDING_REPAIR, PENDING_DISPOSAL, DISPOSED).
       - Assets IN_USE have assigned user in matching department.
       - Assets AVAILABLE / DISPOSED have assigned_to_user is None.
       - 30 requests with PENDING, APPROVED, REJECTED.
       - IdentifierSequence counters AST >= 80 and REQ >= 30.
    """
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")
    monkeypatch.setenv("DEMO_SEED_PASSWORD", "DemoVietnamesePass2026!")

    out = io.StringIO()
    call_command("seed_vietnam_demo", "--confirm", stdout=out)

    # 1. Departments (8)
    assert Department.objects.count() == 8
    actual_dept_codes = set(Department.objects.values_list('code', flat=True))
    assert actual_dept_codes == EXPECTED_DEPT_CODES

    # 2. Categories (18)
    assert Category.objects.count() == 18

    # 3. Users (35: 2 Admin, 8 Manager, 25 User)
    assert User.objects.count() == 35
    assert User.objects.filter(role=User.Role.ADMIN).count() == 2
    assert User.objects.filter(role=User.Role.MANAGER).count() == 8
    assert User.objects.filter(role=User.Role.USER).count() == 25

    # Check Vietnamese names with diacritics
    vietnamese_users = [
        u for u in User.objects.all()
        if any(c in u.full_name for c in VIETNAMESE_CHARS)
    ]
    assert len(vietnamese_users) >= 30, "Most users must have Vietnamese names with diacritics"

    # Check simulated email format (@gmail.com) for demo accounts
    demo_users = User.objects.exclude(username__in=['admin', 'truong'])
    for u in demo_users:
        assert u.email.endswith('@gmail.com'), f"User {u.username} email {u.email} does not end with @gmail.com"

    # Exactly 1 Manager per department
    for dept_code in EXPECTED_DEPT_CODES:
        manager_count = User.objects.filter(department__code=dept_code, role=User.Role.MANAGER).count()
        assert manager_count == 1, f"Department {dept_code} must have exactly 1 Manager"

    # 4. Assets (80)
    assert Asset.objects.count() == 80
    statuses = set(Asset.objects.values_list('status', flat=True))
    expected_statuses = {
        Asset.Status.IN_USE,
        Asset.Status.AVAILABLE,
        Asset.Status.UNDER_MAINTENANCE,
        Asset.Status.DAMAGED_PENDING_REPAIR,
        Asset.Status.PENDING_DISPOSAL,
        Asset.Status.DISPOSED,
    }
    assert statuses == expected_statuses

    # IN_USE assets must have assigned user in matching department
    in_use_assets = Asset.objects.filter(status=Asset.Status.IN_USE)
    assert in_use_assets.count() > 0
    for asset in in_use_assets:
        assert asset.assigned_to_user is not None
        assert asset.department is not None
        assert asset.assigned_to_user.department_id == asset.department_id

    # AVAILABLE & DISPOSED must not have assigned user
    available_assets = Asset.objects.filter(status=Asset.Status.AVAILABLE)
    assert available_assets.count() > 0
    for asset in available_assets:
        assert asset.assigned_to_user is None

    disposed_assets = Asset.objects.filter(status=Asset.Status.DISPOSED)
    assert disposed_assets.count() > 0
    for asset in disposed_assets:
        assert asset.assigned_to_user is None

    # Serial numbers and purchase dates
    today = timezone.now().date()
    for asset in Asset.objects.all():
        assert bool(asset.serial_number), f"Asset {asset.asset_code} has empty serial number"
        assert asset.purchase_date is not None, f"Asset {asset.asset_code} missing purchase date"
        assert asset.purchase_date <= today, f"Asset {asset.asset_code} purchase date is in future"

    # 5. Asset Requests (30)
    assert AssetRequest.objects.count() == 30
    req_statuses = set(AssetRequest.objects.values_list('status', flat=True))
    assert req_statuses == {
        AssetRequest.Status.PENDING,
        AssetRequest.Status.APPROVED,
        AssetRequest.Status.REJECTED,
    }

    # 6. IdentifierSequence AST >= 80, REQ >= 30
    current_year = timezone.now().year
    ast_seq = IdentifierSequence.objects.get(kind=IdentifierSequence.Kind.AST, year=current_year)
    assert ast_seq.value >= 80

    req_seq = IdentifierSequence.objects.get(kind=IdentifierSequence.Kind.REQ, year=current_year)
    assert req_seq.value >= 30

    # 7. AssetHistoryLog
    assert AssetHistoryLog.objects.count() >= 80


@pytest.mark.django_db
def test_seed_command_preserves_existing_admin_and_truong_accounts(monkeypatch):
    """
    5. test_seed_command_preserves_existing_admin_and_truong_accounts:
       Pre-existing admin and truong accounts must retain their passwords and identity.
    """
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")

    # Pre-create admin account
    admin = User.objects.create(
        username="admin",
        email="admin@assethub.local",
        role=User.Role.ADMIN,
        full_name="Pre-existing Administrator",
        is_staff=True,
        is_superuser=True,
    )
    admin_secret = "CustomSecretAdminPass#2026!"
    admin.set_password(admin_secret)
    admin.save()

    # Pre-create truong account
    truong = User.objects.create(
        username="truong",
        email="hnhoa494@gmail.comhnhoa",
        role=User.Role.USER,
        full_name="Nguyễn Phan Ngọc Trưởng",
    )
    truong_secret = "TruongCustomSecret#2026!"
    truong.set_password(truong_secret)
    truong.save()

    out = io.StringIO()
    call_command("seed_vietnam_demo", "--confirm", stdout=out)

    # Re-fetch from database
    admin.refresh_from_db()
    truong.refresh_from_db()

    # Passwords must NOT be reset or overwritten
    assert admin.check_password(admin_secret) is True
    assert truong.check_password(truong_secret) is True

    # User profile integrity
    assert admin.role == User.Role.ADMIN
    assert truong.email == "hnhoa494@gmail.comhnhoa"
    assert truong.department is not None
    assert truong.department.code == "CNTT"


@pytest.mark.django_db
def test_seed_command_is_idempotent(monkeypatch):
    """
    6. test_seed_command_is_idempotent:
       Running seed_vietnam_demo --confirm twice succeeds without duplicating
       records or breaking unique constraints.
    """
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")

    out1 = io.StringIO()
    call_command("seed_vietnam_demo", "--confirm", stdout=out1)

    d_count = Department.objects.count()
    c_count = Category.objects.count()
    u_count = User.objects.count()
    a_count = Asset.objects.count()
    r_count = AssetRequest.objects.count()
    l_count = AssetHistoryLog.objects.count()

    assert d_count == 8
    assert c_count == 18
    assert u_count == 35
    assert a_count == 80
    assert r_count == 30

    out2 = io.StringIO()
    call_command("seed_vietnam_demo", "--confirm", stdout=out2)

    assert Department.objects.count() == d_count == 8
    assert Category.objects.count() == c_count == 18
    assert User.objects.count() == u_count == 35
    assert Asset.objects.count() == a_count == 80
    assert AssetRequest.objects.count() == r_count == 30
    assert AssetHistoryLog.objects.count() == l_count


@pytest.mark.django_db(transaction=True)
def test_seed_command_rollback_on_failure(monkeypatch):
    """
    7. test_seed_command_rollback_on_failure:
       A simulated failure mid-process rolls back the transaction completely,
       leaving no partial data behind.
    """
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")

    # Pre-clean DB in isolated transaction test
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM requests_assetrequest")
        cursor.execute("DELETE FROM assets_assethistorylog")
        cursor.execute("DELETE FROM assets_asset")
    User.objects.all().delete()
    Department.objects.all().delete()
    Category.objects.all().delete()

    # Monkeypatch Asset.save to fail on a specific asset code
    original_save = Asset.save

    def failing_save(self, *args, **kwargs):
        if self.asset_code == "AST-2026-015":
            raise RuntimeError("Simulated mid-seed failure during Asset insertion")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Asset, "save", failing_save)

    out = io.StringIO()
    with pytest.raises(RuntimeError, match="Simulated mid-seed failure"):
        call_command("seed_vietnam_demo", "--confirm", stdout=out)

    # Rollback must leave 0 assets, 0 requests, and clean state
    assert Asset.objects.count() == 0
    assert AssetRequest.objects.count() == 0
    assert Department.objects.count() == 0
    assert Category.objects.count() == 0
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_dataset_supports_pagination_and_filtering(monkeypatch):
    """
    8. test_dataset_supports_pagination_and_filtering:
       - 80 assets with page_size=15 yields exactly 6 pages (5 pages of 15, 6th of 5).
       - Filtering by status and department returns accurate results.
       - DRF API endpoint /api/assets/ validates pagination and filtering integration.
    """
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")
    call_command("seed_vietnam_demo", "--confirm", stdout=io.StringIO())

    # 1. ORM / Paginator validation
    assets_qs = Asset.objects.all().order_by("id")
    assert assets_qs.count() == 80

    paginator = Paginator(assets_qs, 15)
    assert paginator.num_pages == 6
    assert len(paginator.page(1).object_list) == 15
    assert len(paginator.page(2).object_list) == 15
    assert len(paginator.page(3).object_list) == 15
    assert len(paginator.page(4).object_list) == 15
    assert len(paginator.page(5).object_list) == 15
    assert len(paginator.page(6).object_list) == 5

    # 2. Filtering by Status
    in_use_count = Asset.objects.filter(status=Asset.Status.IN_USE).count()
    available_count = Asset.objects.filter(status=Asset.Status.AVAILABLE).count()
    assert in_use_count > 0
    assert available_count > 0

    # 3. Filtering by Department
    dept_cntt = Department.objects.get(code="CNTT")
    cntt_assets = Asset.objects.filter(department=dept_cntt)
    assert cntt_assets.count() > 0
    for a in cntt_assets:
        assert a.department_id == dept_cntt.id

    # 4. Combined Filtering (Department + Status)
    cntt_in_use = Asset.objects.filter(department=dept_cntt, status=Asset.Status.IN_USE)
    assert cntt_in_use.count() > 0
    for a in cntt_in_use:
        assert a.department_id == dept_cntt.id
        assert a.status == Asset.Status.IN_USE
        assert a.assigned_to_user is not None
        assert a.assigned_to_user.department_id == dept_cntt.id

    # 5. REST API Integration Test
    client = APIClient()
    admin_user = User.objects.get(username="admin")
    client.force_authenticate(user=admin_user)

    # Page 1 (default page_size = 15)
    resp_p1 = client.get("/api/assets/?page=1")
    assert resp_p1.status_code == 200
    data_p1 = resp_p1.json()
    assert data_p1["count"] == 80
    assert data_p1["total_pages"] == 6
    assert data_p1["current_page"] == 1
    assert len(data_p1["results"]) == 15

    # Page 6 (last page)
    resp_p6 = client.get("/api/assets/?page=6")
    assert resp_p6.status_code == 200
    data_p6 = resp_p6.json()
    assert data_p6["count"] == 80
    assert data_p6["total_pages"] == 6
    assert data_p6["current_page"] == 6
    assert len(data_p6["results"]) == 5

    # Filter API by status=IN_USE
    resp_status = client.get("/api/assets/?status=IN_USE&page_size=100")
    assert resp_status.status_code == 200
    data_status = resp_status.json()
    assert data_status["count"] == in_use_count
    for item in data_status["results"]:
        assert item["status"] == Asset.Status.IN_USE

    # Filter API by department
    resp_dept = client.get(f"/api/assets/?department_id={dept_cntt.id}&page_size=100")
    assert resp_dept.status_code == 200
    data_dept = resp_dept.json()
    assert data_dept["count"] == cntt_assets.count()
    for item in data_dept["results"]:
        assert item["department"] == dept_cntt.id
