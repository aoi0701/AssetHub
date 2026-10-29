import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category, Department


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


def extract_items(response_data):
    if isinstance(response_data, dict):
        return response_data.get("results", [])
    return response_data


@pytest.fixture
def cat_laptop(db):
    return Category.objects.create(name="Laptop", description="Portable computers")


@pytest.fixture
def cat_monitor(db):
    return Category.objects.create(name="Monitor", description="Displays")


# ---------------------------------------------------------------------------
# Model and Constraint Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_asset_model_constraints_no_holder_when_available_or_disposed(dept_it, regular_user, cat_laptop):
    """Verify assets in AVAILABLE, PENDING_DISPOSAL, DISPOSED cannot have assigned user."""
    # AVAILABLE with user -> ValidationError
    with pytest.raises(ValidationError):
        asset = Asset(
            asset_code="AST-TEST-001",
            name="Test Laptop",
            category=cat_laptop,
            department=dept_it,
            assigned_to_user=regular_user,
            status=Asset.Status.AVAILABLE,
        )
        asset.clean()

    # PENDING_DISPOSAL with user -> ValidationError
    with pytest.raises(ValidationError):
        asset = Asset(
            asset_code="AST-TEST-002",
            name="Test Laptop",
            category=cat_laptop,
            department=dept_it,
            assigned_to_user=regular_user,
            status=Asset.Status.PENDING_DISPOSAL,
        )
        asset.clean()

    # DISPOSED with user -> ValidationError
    with pytest.raises(ValidationError):
        asset = Asset(
            asset_code="AST-TEST-003",
            name="Test Laptop",
            category=cat_laptop,
            department=dept_it,
            assigned_to_user=regular_user,
            status=Asset.Status.DISPOSED,
        )
        asset.clean()


@pytest.mark.django_db
def test_asset_model_constraints_unallocated_cannot_have_holder(regular_user, cat_laptop):
    """Verify company pool / unallocated asset cannot have an assigned user."""
    with pytest.raises(ValidationError):
        asset = Asset(
            asset_code="AST-TEST-004",
            name="Pool Laptop",
            category=cat_laptop,
            department=None,
            assigned_to_user=regular_user,
            status=Asset.Status.IN_USE,
        )
        asset.clean()


@pytest.mark.django_db
def test_asset_model_in_use_requires_holder_in_same_department(dept_it, dept_hr, regular_user, cat_laptop):
    """Verify IN_USE status requires holder from the same department."""
    # IN_USE without holder -> ValidationError
    with pytest.raises(ValidationError):
        asset = Asset(
            asset_code="AST-TEST-005",
            name="In-Use Laptop",
            category=cat_laptop,
            department=dept_it,
            assigned_to_user=None,
            status=Asset.Status.IN_USE,
        )
        asset.clean()

    # IN_USE with holder from different department -> ValidationError
    user_hr = User.objects.create_user(
        username="hr_emp",
        email="hr.emp@assethub.local",
        password="Password123!",
        role=User.Role.USER,
        department=dept_hr,
    )
    with pytest.raises(ValidationError):
        asset = Asset(
            asset_code="AST-TEST-006",
            name="In-Use Laptop",
            category=cat_laptop,
            department=dept_it,
            assigned_to_user=user_hr,
            status=Asset.Status.IN_USE,
        )
        asset.clean()


@pytest.mark.django_db
def test_asset_history_log_append_only(admin_user, cat_laptop):
    """Verify AssetHistoryLog cannot be modified or deleted."""
    asset = Asset.objects.create(
        asset_code="AST-TEST-007",
        name="Log Test Laptop",
        category=cat_laptop,
        status=Asset.Status.AVAILABLE,
    )
    log = AssetHistoryLog.objects.create(
        asset=asset,
        actor=admin_user,
        action_type=AssetHistoryLog.ActionType.CREATE,
        from_status='',
        to_status=Asset.Status.AVAILABLE,
        reason='Initial creation',
        asset_code_snapshot=asset.asset_code,
        asset_name_snapshot=asset.name,
    )

    # Modification rejected
    with pytest.raises(ValueError, match="append-only and cannot be updated"):
        log.reason = "Modified reason"
        log.save()

    # Deletion rejected
    with pytest.raises(ValueError, match="append-only and cannot be deleted"):
        log.delete()


# ---------------------------------------------------------------------------
# Asset Creation (POST /api/assets/)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_create_asset_with_auto_code_and_single_create_log(admin_user, dept_it, cat_laptop):
    """Admin can create asset: code auto-generated, status=AVAILABLE, single CREATE log created."""
    client = auth_client_for(admin_user)
    payload = {
        "name": "ThinkPad T14s",
        "category_id": cat_laptop.id,
        "department_id": dept_it.id,
        "serial_number": "SN-TP-12345",
        "specification": {"cpu": "AMD Ryzen 7", "ram": "32GB"},
        "purchase_date": "2026-03-15",
        "reason": "Mua mới đợt Q1",
    }
    response = client.post("/api/assets/", data=payload, format="json")
    assert response.status_code == 201

    data = response.data
    assert data["asset_code"].startswith("AST-")
    assert data["name"] == "ThinkPad T14s"
    assert data["status"] == "AVAILABLE"
    assert data["department_id"] == dept_it.id
    assert data["category_id"] == cat_laptop.id
    assert data["assigned_to_user_id"] is None
    assert data["specification"] == {"cpu": "AMD Ryzen 7", "ram": "32GB"}

    asset = Asset.objects.get(id=data["id"])
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user is None

    # Exactly one CREATE log created with snapshot
    logs = list(asset.history_logs.all())
    assert len(logs) == 1
    create_log = logs[0]
    assert create_log.action_type == AssetHistoryLog.ActionType.CREATE
    assert create_log.actor == admin_user
    assert create_log.to_status == Asset.Status.AVAILABLE
    assert create_log.asset_code_snapshot == asset.asset_code
    assert create_log.asset_name_snapshot == asset.name
    assert create_log.reason == "Mua mới đợt Q1"


@pytest.mark.django_db
def test_create_asset_rejects_client_status_holder_or_code(admin_user, cat_laptop, regular_user):
    """Client attempting to set status, holder, or asset_code in POST gets 400 Bad Request."""
    client = auth_client_for(admin_user)

    # 1. Reject client status
    resp1 = client.post("/api/assets/", data={
        "name": "Hacked Asset 1",
        "category_id": cat_laptop.id,
        "status": "IN_USE",
    }, format="json")
    assert resp1.status_code == 400
    assert "status" in resp1.data

    # 2. Reject client assigned_to_user
    resp2 = client.post("/api/assets/", data={
        "name": "Hacked Asset 2",
        "category_id": cat_laptop.id,
        "assigned_to_user_id": regular_user.id,
    }, format="json")
    assert resp2.status_code == 400
    assert "assigned_to_user" in resp2.data or "assigned_to_user_id" in resp2.data

    # 3. Reject client asset_code
    resp3 = client.post("/api/assets/", data={
        "name": "Hacked Asset 3",
        "category_id": cat_laptop.id,
        "asset_code": "AST-MANUAL-999",
    }, format="json")
    assert resp3.status_code == 400
    assert "asset_code" in resp3.data


@pytest.mark.django_db
def test_manager_and_user_cannot_create_asset(manager_user, regular_user, cat_laptop):
    """Manager and User receive 403 Forbidden when attempting to create assets."""
    payload = {
        "name": "Unauthorized Laptop",
        "category_id": cat_laptop.id,
    }

    # Manager -> 403
    mgr_client = auth_client_for(manager_user)
    mgr_resp = mgr_client.post("/api/assets/", data=payload, format="json")
    assert mgr_resp.status_code == 403

    # User -> 403
    user_client = auth_client_for(regular_user)
    user_resp = user_client.post("/api/assets/", data=payload, format="json")
    assert user_resp.status_code == 403


# ---------------------------------------------------------------------------
# Scoping and Filtering (GET /api/assets/)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_assets(db, dept_it, dept_hr, manager_user, regular_user, cat_laptop, cat_monitor):
    """Create a set of sample assets across departments and unallocated pool."""
    # IT assets
    ast_it_1 = Asset.objects.create(
        asset_code="AST-2026-101",
        name="Dell IT Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
        serial_number="SN-IT-101",
    )
    ast_it_2 = Asset.objects.create(
        asset_code="AST-2026-102",
        name="MacBook IT InUse",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.IN_USE,
        assigned_to_user=regular_user,
        serial_number="SN-IT-102",
    )
    # HR asset
    ast_hr_1 = Asset.objects.create(
        asset_code="AST-2026-201",
        name="Dell HR Monitor",
        category=cat_monitor,
        department=dept_hr,
        status=Asset.Status.AVAILABLE,
        serial_number="SN-HR-201",
    )
    # Unallocated asset
    ast_pool = Asset.objects.create(
        asset_code="AST-2026-301",
        name="Company Pool Laptop",
        category=cat_laptop,
        department=None,
        status=Asset.Status.AVAILABLE,
        serial_number="SN-POOL-301",
    )
    return {
        "it_1": ast_it_1,
        "it_2": ast_it_2,
        "hr_1": ast_hr_1,
        "pool": ast_pool,
    }


@pytest.mark.django_db
def test_admin_can_list_all_assets_and_filter_by_department(admin_user, sample_assets, dept_it, dept_hr):
    """Admin has full company visibility and can filter by department_id or unallocated."""
    client = auth_client_for(admin_user)

    # All assets
    all_resp = client.get("/api/assets/")
    assert all_resp.status_code == 200
    items = extract_items(all_resp.data)
    assert len(items) == 4

    # Filter department IT
    it_resp = client.get(f"/api/assets/?department_id={dept_it.id}")
    assert it_resp.status_code == 200
    it_items = extract_items(it_resp.data)
    assert len(it_items) == 2
    assert {i["asset_code"] for i in it_items} == {"AST-2026-101", "AST-2026-102"}

    # Filter unallocated
    pool_resp = client.get("/api/assets/?department_id=unallocated")
    assert pool_resp.status_code == 200
    pool_items = extract_items(pool_resp.data)
    assert len(pool_items) == 1
    assert pool_items[0]["asset_code"] == "AST-2026-301"


@pytest.mark.django_db
def test_admin_filter_and_search_assets(admin_user, sample_assets, cat_monitor):
    """Admin can search by code/name/serial and filter by category or status."""
    client = auth_client_for(admin_user)

    # Search by serial number
    search_resp = client.get("/api/assets/?search=SN-HR-201")
    assert search_resp.status_code == 200
    items = extract_items(search_resp.data)
    assert len(items) == 1
    assert items[0]["asset_code"] == "AST-2026-201"

    # Filter by category
    cat_resp = client.get(f"/api/assets/?category_id={cat_monitor.id}")
    assert cat_resp.status_code == 200
    cat_items = extract_items(cat_resp.data)
    assert len(cat_items) == 1
    assert cat_items[0]["name"] == "Dell HR Monitor"

    # Filter by status
    status_resp = client.get("/api/assets/?status=IN_USE")
    assert status_resp.status_code == 200
    status_items = extract_items(status_resp.data)
    assert len(status_items) == 1
    assert status_items[0]["asset_code"] == "AST-2026-102"


@pytest.mark.django_db
def test_manager_scoping_restricted_to_own_department(manager_user, sample_assets, dept_hr):
    """Manager only sees assets within their own department; other departments and pool are hidden."""
    client = auth_client_for(manager_user)

    response = client.get("/api/assets/")
    assert response.status_code == 200
    items = extract_items(response.data)
    codes = [i["asset_code"] for i in items]

    # Manager is in dept_it -> only sees IT assets
    assert "AST-2026-101" in codes
    assert "AST-2026-102" in codes
    assert "AST-2026-201" not in codes
    assert "AST-2026-301" not in codes

    # Attempting to filter by another department does NOT reveal other department assets
    leak_resp = client.get(f"/api/assets/?department_id={dept_hr.id}")
    assert leak_resp.status_code == 200
    leak_items = extract_items(leak_resp.data)
    leak_codes = [i["asset_code"] for i in leak_items]
    assert "AST-2026-201" not in leak_codes


@pytest.mark.django_db
def test_user_scoping_restricted_to_currently_assigned_assets(regular_user, sample_assets):
    """User only sees assets currently assigned to them."""
    client = auth_client_for(regular_user)

    response = client.get("/api/assets/")
    assert response.status_code == 200
    items = extract_items(response.data)
    assert len(items) == 1
    assert items[0]["asset_code"] == "AST-2026-102"


# ---------------------------------------------------------------------------
# Asset Detail (GET /api/assets/{id}/)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_asset_detail_access_controls(admin_user, manager_user, regular_user, sample_assets):
    """Detail view enforces role scoping; out-of-scope assets return 404."""
    it_asset = sample_assets["it_1"]
    in_use_asset = sample_assets["it_2"]
    hr_asset = sample_assets["hr_1"]

    # Admin accesses any asset -> 200
    admin_client = auth_client_for(admin_user)
    assert admin_client.get(f"/api/assets/{hr_asset.id}/").status_code == 200

    # Manager accesses own department asset -> 200
    mgr_client = auth_client_for(manager_user)
    assert mgr_client.get(f"/api/assets/{it_asset.id}/").status_code == 200

    # Manager accesses other department asset -> 404
    assert mgr_client.get(f"/api/assets/{hr_asset.id}/").status_code == 404

    # User accesses assigned asset -> 200
    user_client = auth_client_for(regular_user)
    assert user_client.get(f"/api/assets/{in_use_asset.id}/").status_code == 200

    # User accesses unassigned asset in same department -> 404
    assert user_client.get(f"/api/assets/{it_asset.id}/").status_code == 404


# ---------------------------------------------------------------------------
# Asset PATCH & Strict Allowlist
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_patch_allowlisted_fields(admin_user, sample_assets, cat_monitor):
    """Admin can update allowlisted fields: name, category_id, serial_number, specification, purchase_date."""
    asset = sample_assets["it_1"]
    client = auth_client_for(admin_user)

    payload = {
        "name": "Updated Laptop Name",
        "category_id": cat_monitor.id,
        "serial_number": "SN-NEW-999",
        "specification": {"ram": "64GB"},
        "purchase_date": "2026-05-01",
    }
    response = client.patch(f"/api/assets/{asset.id}/", data=payload, format="json")
    assert response.status_code == 200
    assert response.data["name"] == "Updated Laptop Name"
    assert response.data["category_id"] == cat_monitor.id
    assert response.data["serial_number"] == "SN-NEW-999"
    assert response.data["specification"] == {"ram": "64GB"}
    assert response.data["purchase_date"] == "2026-05-01"

    asset.refresh_from_db()
    assert asset.name == "Updated Laptop Name"
    assert asset.category == cat_monitor


@pytest.mark.django_db
def test_patch_rejects_workflow_and_protected_fields_with_400(admin_user, sample_assets, dept_hr, regular_user):
    """PATCH with status, department, assigned_to_user, asset_code returns 400 Bad Request."""
    asset = sample_assets["it_1"]
    client = auth_client_for(admin_user)

    # 1. Reject status
    res1 = client.patch(f"/api/assets/{asset.id}/", data={"status": "IN_USE"}, format="json")
    assert res1.status_code == 400
    assert "status" in res1.data

    # 2. Reject department
    res2 = client.patch(f"/api/assets/{asset.id}/", data={"department_id": dept_hr.id}, format="json")
    assert res2.status_code == 400
    assert "department" in res2.data or "department_id" in res2.data

    # 3. Reject assigned_to_user
    res3 = client.patch(f"/api/assets/{asset.id}/", data={"assigned_to_user_id": regular_user.id}, format="json")
    assert res3.status_code == 400
    assert "assigned_to_user" in res3.data or "assigned_to_user_id" in res3.data

    # 4. Reject asset_code
    res4 = client.patch(f"/api/assets/{asset.id}/", data={"asset_code": "AST-NEW-CODE"}, format="json")
    assert res4.status_code == 400
    assert "asset_code" in res4.data


@pytest.mark.django_db
def test_manager_and_user_cannot_patch_assets(manager_user, regular_user, sample_assets):
    """Manager and User cannot PATCH assets (403 Forbidden)."""
    asset = sample_assets["it_2"]

    mgr_client = auth_client_for(manager_user)
    assert mgr_client.patch(f"/api/assets/{asset.id}/", data={"name": "Hacked"}, format="json").status_code == 403

    user_client = auth_client_for(regular_user)
    assert user_client.patch(f"/api/assets/{asset.id}/", data={"name": "Hacked"}, format="json").status_code == 403


# ---------------------------------------------------------------------------
# Strictly NO DELETE
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_delete_asset_strictly_forbidden_returns_405(admin_user, manager_user, regular_user, sample_assets):
    """DELETE method on assets endpoint returns 405 Method Not Allowed."""
    asset = sample_assets["it_1"]

    admin_client = auth_client_for(admin_user)
    assert admin_client.delete(f"/api/assets/{asset.id}/").status_code == 405
    assert admin_client.delete("/api/assets/").status_code == 405

    mgr_client = auth_client_for(manager_user)
    assert mgr_client.delete(f"/api/assets/{asset.id}/").status_code in (403, 405)

    user_client = auth_client_for(regular_user)
    assert user_client.delete(f"/api/assets/{asset.id}/").status_code in (403, 405)
