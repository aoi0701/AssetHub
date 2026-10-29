import pytest
from rest_framework.test import APIClient

from apps.assets.models import Asset
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category, Department


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


@pytest.fixture
def cat_laptop(db):
    return Category.objects.create(name="Laptop", description="Portable computers")


@pytest.fixture
def user_hr(db, dept_hr):
    return User.objects.create_user(
        username="user_hr",
        email="hr.emp@assethub.local",
        password="Password123!",
        role=User.Role.USER,
        department=dept_hr,
        is_active=True,
    )


# ===========================================================================
# 1. User cannot call actions or view full asset history (403 Forbidden)
# ===========================================================================

@pytest.mark.django_db
def test_user_cannot_call_any_action_or_view_history(
    regular_user, dept_it, cat_laptop
):
    """
    Standard Employee (USER role) receives 403 Forbidden for all action endpoints
    and the administrative asset history endpoint.
    """
    client = auth_client_for(regular_user)
    asset = Asset.objects.create(
        asset_code="AST-USER-FORBIDDEN-01",
        name="User Target Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )

    action_payloads = [
        ("allocate", {"assigned_to_user_id": regular_user.id, "reason": "Self allocate"}),
        ("revoke", {"received_confirmed": True, "condition": "GOOD", "reason": "Self revoke"}),
        ("mark-damaged", {"reason": "Self report damaged"}),
        ("send-maintenance", {"reason": "Self send maintenance"}),
        ("complete-maintenance", {"result": "RETURN_TO_STOCK", "reason": "Self complete"}),
        ("transfer", {"to_department_id": None, "reason": "Self transfer"}),
        ("propose-disposal", {"reason": "Self propose disposal"}),
        ("confirm-disposal", {"reason": "Self confirm disposal"}),
    ]

    for endpoint, payload in action_payloads:
        resp = client.post(f"/api/assets/{asset.id}/{endpoint}/", data=payload, format="json")
        assert resp.status_code == 403, f"Action '{endpoint}' should return 403 for USER role"

    # GET /api/assets/{id}/history/ returns 403 for USER
    hist_resp = client.get(f"/api/assets/{asset.id}/history/")
    assert hist_resp.status_code == 403


# ===========================================================================
# 2. Manager cannot transfer or confirm-disposal (403 Forbidden)
# ===========================================================================

@pytest.mark.django_db
def test_manager_cannot_transfer_or_confirm_disposal(
    manager_user, dept_it, dept_hr, cat_laptop
):
    """
    Department Manager receives 403 Forbidden when attempting Admin-only actions:
    transfer and confirm-disposal, even on assets within their own department.
    """
    client = auth_client_for(manager_user)

    # 1. Transfer attempt on own department asset -> 403
    asset_avail = Asset.objects.create(
        asset_code="AST-MGR-OWN-01",
        name="IT Available Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    res_transfer = client.post(
        f"/api/assets/{asset_avail.id}/transfer/",
        data={"to_department_id": dept_hr.id, "reason": "Manager trying to transfer"},
        format="json",
    )
    assert res_transfer.status_code == 403

    # 2. Confirm-disposal attempt on own department asset -> 403
    asset_pending = Asset.objects.create(
        asset_code="AST-MGR-OWN-02",
        name="IT Pending Disposal Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.PENDING_DISPOSAL,
    )
    res_confirm = client.post(
        f"/api/assets/{asset_pending.id}/confirm-disposal/",
        data={"reason": "Manager trying to confirm disposal"},
        format="json",
    )
    assert res_confirm.status_code == 403


# ===========================================================================
# 3. Manager calling other department assets returns 404 for all 8 actions
# ===========================================================================

@pytest.mark.django_db
def test_manager_accessing_other_department_asset_returns_404_for_all_actions_and_history(
    manager_user, dept_hr, cat_laptop, user_hr
):
    """
    Manager (IT) accessing an asset belonging to another department (HR)
    returns 404 Not Found for ALL 8 action endpoints and the history endpoint.
    """
    client = auth_client_for(manager_user)
    asset_hr = Asset.objects.create(
        asset_code="AST-HR-001",
        name="HR Secret Asset",
        category=cat_laptop,
        department=dept_hr,
        status=Asset.Status.AVAILABLE,
    )

    action_payloads = [
        ("allocate", {"assigned_to_user_id": user_hr.id, "reason": "Cross-dept allocate"}),
        ("revoke", {"received_confirmed": True, "condition": "GOOD", "reason": "Cross-dept revoke"}),
        ("mark-damaged", {"reason": "Cross-dept mark damaged"}),
        ("send-maintenance", {"reason": "Cross-dept maintenance"}),
        ("complete-maintenance", {"result": "RETURN_TO_STOCK", "reason": "Cross-dept complete"}),
        ("transfer", {"to_department_id": None, "reason": "Cross-dept transfer"}),
        ("propose-disposal", {"reason": "Cross-dept propose disposal"}),
        ("confirm-disposal", {"reason": "Cross-dept confirm disposal"}),
    ]

    for endpoint, payload in action_payloads:
        resp = client.post(f"/api/assets/{asset_hr.id}/{endpoint}/", data=payload, format="json")
        assert resp.status_code == 404, f"Action '{endpoint}' on other dept asset should return 404"

    # History on other department asset returns 404
    hist_resp = client.get(f"/api/assets/{asset_hr.id}/history/")
    assert hist_resp.status_code == 404


# ===========================================================================
# 4. Company Pool (department=None): Admin manages, Manager returns 404
# ===========================================================================

@pytest.mark.django_db
def test_company_pool_assets_admin_access_manager_404(
    admin_user, manager_user, dept_it, cat_laptop
):
    """
    Company pool assets (department=None) are strictly managed by Admin.
    Manager has no access and receives 404 Not Found.
    """
    asset_pool = Asset.objects.create(
        asset_code="AST-POOL-001",
        name="Company Central Pool Laptop",
        category=cat_laptop,
        department=None,
        status=Asset.Status.AVAILABLE,
    )

    # Manager attempting GET detail or history -> 404
    mgr_client = auth_client_for(manager_user)
    assert mgr_client.get(f"/api/assets/{asset_pool.id}/").status_code == 404
    assert mgr_client.get(f"/api/assets/{asset_pool.id}/history/").status_code == 404
    assert mgr_client.post(
        f"/api/assets/{asset_pool.id}/mark-damaged/",
        data={"reason": "Manager pool damage"},
        format="json",
    ).status_code == 404

    # Admin can view history and transfer pool asset to a department
    admin_client = auth_client_for(admin_user)
    assert admin_client.get(f"/api/assets/{asset_pool.id}/").status_code == 200
    assert admin_client.get(f"/api/assets/{asset_pool.id}/history/").status_code == 200

    transfer_resp = admin_client.post(
        f"/api/assets/{asset_pool.id}/transfer/",
        data={
            "to_department_id": dept_it.id,
            "reason": "Phân bổ từ kho công ty về phòng IT",
        },
        format="json",
    )
    assert transfer_resp.status_code == 200
    assert transfer_resp.data["department_id"] == dept_it.id

    asset_pool.refresh_from_db()
    assert asset_pool.department == dept_it


# ===========================================================================
# 5. Manager can perform permitted actions on own department assets
# ===========================================================================

@pytest.mark.django_db
def test_manager_can_perform_permitted_actions_in_own_department(
    manager_user, dept_it, regular_user, cat_laptop
):
    """Manager can allocate, revoke, mark-damaged, propose-disposal in own department."""
    client = auth_client_for(manager_user)

    # 1. Allocate
    asset = Asset.objects.create(
        asset_code="AST-MGR-PERM-01",
        name="IT Asset for Allocate",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    alloc_resp = client.post(
        f"/api/assets/{asset.id}/allocate/",
        data={"assigned_to_user_id": regular_user.id, "reason": "Manager allocate"},
        format="json",
    )
    assert alloc_resp.status_code == 200
    assert alloc_resp.data["status"] == "IN_USE"

    # 2. Revoke
    rev_resp = client.post(
        f"/api/assets/{asset.id}/revoke/",
        data={"received_confirmed": True, "condition": "GOOD", "reason": "Manager revoke"},
        format="json",
    )
    assert rev_resp.status_code == 200
    assert rev_resp.data["status"] == "AVAILABLE"

    # 3. Mark damaged
    dmg_resp = client.post(
        f"/api/assets/{asset.id}/mark-damaged/",
        data={"reason": "Manager mark damaged"},
        format="json",
    )
    assert dmg_resp.status_code == 200
    assert dmg_resp.data["status"] == "DAMAGED_PENDING_REPAIR"

    # 4. Propose disposal
    prop_resp = client.post(
        f"/api/assets/{asset.id}/propose-disposal/",
        data={"reason": "Manager propose disposal"},
        format="json",
    )
    assert prop_resp.status_code == 200
    assert prop_resp.data["status"] == "PENDING_DISPOSAL"

    # 5. GET history succeeds for Manager on own department asset
    hist_resp = client.get(f"/api/assets/{asset.id}/history/")
    assert hist_resp.status_code == 200
    assert len(hist_resp.data) == 4
