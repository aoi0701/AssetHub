import pytest
from rest_framework.test import APIClient

from apps.assets.models import Asset, AssetHistoryLog
from apps.assets.services import AssetService
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


@pytest.mark.django_db
def test_personal_history_survives_return_and_transfer(
    admin_user, regular_user, user_hr, dept_it, dept_hr, cat_laptop
):
    """
    Spec §7.1: User A receives asset, returns it. Admin transfers to Dept B, assigns to User B.
    User A still sees their own history events but cannot see User B's events or subsequent logs.
    Also verifies safe serialization: no raw reasons, other users' names, or full timeline.
    """
    # 1. Admin creates asset in Dept IT
    admin_client = auth_client_for(admin_user)
    create_resp = admin_client.post(
        "/api/assets/",
        {
            "name": "ThinkPad T14",
            "category_id": cat_laptop.id,
            "department_id": dept_it.id,
            "specification": {"cpu": "AMD Ryzen 7"},
        },
        format="json",
    )
    assert create_resp.status_code == 201, create_resp.data
    asset_id = create_resp.data["id"]
    asset = Asset.objects.get(id=asset_id)

    # 2. Allocate to User A (regular_user)
    alloc_resp = admin_client.post(
        f"/api/assets/{asset_id}/allocate/",
        {
            "assigned_to_user_id": regular_user.id,
            "reason": "Internal secret allocation reason for user A",
        },
        format="json",
    )
    assert alloc_resp.status_code == 200, alloc_resp.data

    # 3. User A returns asset
    revoke_resp = admin_client.post(
        f"/api/assets/{asset_id}/revoke/",
        {
            "to_status": "AVAILABLE",
            "reason": "Internal private return reason from user A",
        },
        format="json",
    )
    assert revoke_resp.status_code == 200, revoke_resp.data

    # 4. Admin transfers asset to Dept HR
    transfer_resp = admin_client.post(
        f"/api/assets/{asset_id}/transfer/",
        {
            "target_department_id": dept_hr.id,
            "reason": "Internal transfer to HR",
        },
        format="json",
    )
    assert transfer_resp.status_code == 200, transfer_resp.data

    # 5. Admin allocates asset to User B (user_hr)
    alloc_b_resp = admin_client.post(
        f"/api/assets/{asset_id}/allocate/",
        {
            "assigned_to_user_id": user_hr.id,
            "reason": "Confidential allocation for user B",
        },
        format="json",
    )
    assert alloc_b_resp.status_code == 200, alloc_b_resp.data

    # 6. User A checks personal asset history: GET /api/me/asset-history/
    user_a_client = auth_client_for(regular_user)
    history_resp = user_a_client.get("/api/me/asset-history/")
    assert history_resp.status_code == 200, history_resp.data

    results = history_resp.data.get("results", history_resp.data)
    assert len(results) == 2, f"User A should see exactly 2 events (allocate & return), got {len(results)}"

    # Check safe serialization
    allowed_fields = {
        "id",
        "asset_id",
        "asset_code_snapshot",
        "asset_name_snapshot",
        "action_type",
        "from_status",
        "to_status",
        "created_at",
        "message",
    }
    for item in results:
        assert set(item.keys()) == allowed_fields, f"Unexpected fields in safe serialization: {set(item.keys()) - allowed_fields}"
        # Ensure raw internal reasons, other users' names, actor info are NOT present
        assert "reason" not in item
        assert "actor" not in item
        assert "actor_name" not in item
        assert "to_user_name" not in item
        assert "from_user_name" not in item
        assert "user_hr" not in str(item)
        assert "Confidential" not in str(item)
        assert "Internal" not in str(item)

    # Check actions and messages
    action_types = [item["action_type"] for item in results]
    assert "ALLOCATE" in action_types
    assert "RETURN" in action_types
    assert "TRANSFER" not in action_types

    # Allocate message should indicate allocation to user
    alloc_item = next(item for item in results if item["action_type"] == "ALLOCATE")
    assert alloc_item["message"] == "Tài sản được cấp phát cho bạn"
    assert alloc_item["asset_id"] == asset_id

    # Return message should indicate user returned asset
    return_item = next(item for item in results if item["action_type"] == "RETURN")
    assert return_item["message"] == "Bạn đã hoàn trả tài sản"


@pytest.mark.django_db
def test_user_cannot_access_full_asset_history(regular_user, dept_it, cat_laptop):
    """
    Spec §7.1: User cannot access full history (/api/assets/{id}/history/) -> 403 Forbidden.
    """
    asset = Asset.objects.create(
        asset_code="AST-2026-901",
        name="Dell Latitude",
        category=cat_laptop,
        department=dept_it,
        assigned_to_user=regular_user,
        status=Asset.Status.IN_USE,
    )
    user_client = auth_client_for(regular_user)
    resp = user_client.get(f"/api/assets/{asset.id}/history/")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_user_cannot_query_personal_history_of_other_users(regular_user, user_hr):
    """
    Spec §7.1: User cannot query personal history of other users. Passing other user parameter returns 403.
    """
    user_client = auth_client_for(regular_user)
    resp_id = user_client.get(f"/api/me/asset-history/?user_id={user_hr.id}")
    assert resp_id.status_code in [400, 403]

    resp_user = user_client.get(f"/api/me/asset-history/?user={user_hr.id}")
    assert resp_user.status_code in [400, 403]


@pytest.mark.django_db
def test_personal_history_unauthenticated(client):
    """Anonymous user must receive 401 Unauthorized."""
    resp = client.get("/api/me/asset-history/")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_personal_history_pagination_and_strict_scoping(
    regular_user, user_hr, admin_user, dept_it, dept_hr, cat_laptop
):
    """
    Verifies backend database-level scoping across pagination:
    - 25 history events created for regular_user (User A)
    - 10 history events created for user_hr (User B)
    - regular_user querying page 1 gets 20 events, count == 25 (NOT 35)
    - regular_user querying page 2 gets 5 events, count == 25
    - NEITHER page 1 nor page 2 leaks any asset code or log from user_hr
    - user_hr querying gets count == 10, zero events from regular_user
    """
    # 1. Create 25 logs for regular_user (User A)
    user_a_codes = []
    for i in range(25):
        asset = Asset.objects.create(
            asset_code=f"AST-USER-A-{i:03d}",
            name=f"Device User A {i}",
            category=cat_laptop,
            department=dept_it,
            status=Asset.Status.AVAILABLE,
        )
        user_a_codes.append(asset.asset_code)
        AssetHistoryLog.objects.create(
            asset=asset,
            actor=admin_user,
            action_type=AssetHistoryLog.ActionType.ALLOCATE,
            from_status=Asset.Status.AVAILABLE,
            to_status=Asset.Status.IN_USE,
            to_user=regular_user,
            from_department=dept_it,
            to_department=dept_it,
            reason="Bulk testing allocation User A",
            asset_code_snapshot=asset.asset_code,
            asset_name_snapshot=asset.name,
        )

    # 2. Create 10 logs for user_hr (User B)
    user_b_codes = []
    for j in range(10):
        asset_b = Asset.objects.create(
            asset_code=f"AST-SECRET-B-{j:03d}",
            name=f"Device User B {j}",
            category=cat_laptop,
            department=dept_hr,
            status=Asset.Status.AVAILABLE,
        )
        user_b_codes.append(asset_b.asset_code)
        AssetHistoryLog.objects.create(
            asset=asset_b,
            actor=admin_user,
            action_type=AssetHistoryLog.ActionType.ALLOCATE,
            from_status=Asset.Status.AVAILABLE,
            to_status=Asset.Status.IN_USE,
            to_user=user_hr,
            from_department=dept_hr,
            to_department=dept_hr,
            reason="Secret allocation User B",
            asset_code_snapshot=asset_b.asset_code,
            asset_name_snapshot=asset_b.name,
        )

    # 3. Query as User A - Page 1
    client_a = auth_client_for(regular_user)
    resp_p1 = client_a.get("/api/me/asset-history/?page=1")
    assert resp_p1.status_code == 200
    assert resp_p1.data["count"] == 25, f"Expected 25 events for User A, got {resp_p1.data['count']}"
    assert len(resp_p1.data["results"]) == 20
    assert resp_p1.data["next"] is not None

    page1_codes = [item["asset_code_snapshot"] for item in resp_p1.data["results"]]
    for code in page1_codes:
        assert code in user_a_codes
        assert code not in user_b_codes

    # 4. Query as User A - Page 2
    resp_p2 = client_a.get("/api/me/asset-history/?page=2")
    assert resp_p2.status_code == 200
    assert resp_p2.data["count"] == 25
    assert len(resp_p2.data["results"]) == 5
    assert resp_p2.data["next"] is None

    page2_codes = [item["asset_code_snapshot"] for item in resp_p2.data["results"]]
    for code in page2_codes:
        assert code in user_a_codes
        assert code not in user_b_codes

    # Ensure no duplicates across page 1 and page 2, and exact union is 25
    combined_codes = set(page1_codes) | set(page2_codes)
    assert len(combined_codes) == 25
    assert combined_codes == set(user_a_codes)

    # 5. Query as User B - Verify User B only sees their 10 events
    client_b = auth_client_for(user_hr)
    resp_b = client_b.get("/api/me/asset-history/")
    assert resp_b.status_code == 200
    assert resp_b.data["count"] == 10
    assert len(resp_b.data["results"]) == 10
    b_codes = [item["asset_code_snapshot"] for item in resp_b.data["results"]]
    for code in b_codes:
        assert code in user_b_codes
        assert code not in user_a_codes


@pytest.mark.django_db
def test_personal_history_survives_asset_disposal(admin_user, regular_user, dept_it, cat_laptop):
    """
    Spec §7.1: Personal history survives disposal.
    Even after asset is disposed, user still sees past allocation event.
    """
    asset = Asset.objects.create(
        asset_code="AST-2026-DSP",
        name="Server 1U",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    # 1. Allocate to user
    AssetService.allocate(asset.id, regular_user.id, admin_user, "Given to user")
    # 2. Revoke from user
    AssetService.revoke(asset.id, Asset.Status.AVAILABLE, admin_user, "Returned by user")
    # 3. Propose disposal
    AssetService.propose_disposal(asset.id, admin_user, "Obsolete")
    # 4. Confirm disposal
    AssetService.confirm_disposal(asset.id, admin_user, "Confirmed scrap")

    asset.refresh_from_db()
    assert asset.status == Asset.Status.DISPOSED

    user_client = auth_client_for(regular_user)
    resp = user_client.get("/api/me/asset-history/")
    assert resp.status_code == 200
    results = resp.data["results"]
    assert len(results) == 2
    # Only ALLOCATE and RETURN are visible, not DISPOSAL_PROPOSE or DISPOSAL_CONFIRM
    types = [r["action_type"] for r in results]
    assert "ALLOCATE" in types
    assert "RETURN" in types
    assert "DISPOSAL_PROPOSE" not in types
    assert "DISPOSAL_CONFIRM" not in types
