import pytest
from unittest.mock import patch
from rest_framework.test import APIClient
from rest_framework.exceptions import APIException
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user


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
def pending_user(db):
    """Pending registered user awaiting admin activation."""
    return User.objects.create_user(
        username="pending_emp",
        email="pending.emp@assethub.local",
        password="PendingPassword123!",
        role=User.Role.USER,
        full_name="Pending Employee",
        phone="0911223344",
        department=None,
        is_active=False,
    )


@pytest.fixture
def hr_user(db, dept_hr):
    """Active user in HR department."""
    return User.objects.create_user(
        username="user_hr",
        email="user.hr@assethub.local",
        password="UserPassword123!",
        role=User.Role.USER,
        full_name="HR Specialist",
        phone="0933445566",
        department=dept_hr,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Admin User List & Filters
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_list_all_users(admin_user, manager_user, regular_user, hr_user, pending_user):
    client = auth_client_for(admin_user)
    response = client.get("/api/users/")
    assert response.status_code == 200
    items = extract_items(response.data)
    usernames = [u["username"] for u in items]
    assert admin_user.username in usernames
    assert manager_user.username in usernames
    assert regular_user.username in usernames
    assert hr_user.username in usernames
    assert pending_user.username in usernames


@pytest.mark.django_db
def test_admin_can_filter_and_search_users(admin_user, manager_user, regular_user, hr_user, pending_user, dept_it):
    client = auth_client_for(admin_user)

    # Filter by role
    res_role = client.get("/api/users/?role=MANAGER")
    assert res_role.status_code == 200
    items = extract_items(res_role.data)
    assert len(items) == 1
    assert items[0]["username"] == manager_user.username

    # Filter by is_active
    res_active = client.get("/api/users/?is_active=false")
    assert res_active.status_code == 200
    items = extract_items(res_active.data)
    assert len(items) == 1
    assert items[0]["username"] == pending_user.username

    # Filter by department_id
    res_dept = client.get(f"/api/users/?department_id={dept_it.id}")
    assert res_dept.status_code == 200
    items = extract_items(res_dept.data)
    assert len(items) == 2
    dept_usernames = {item["username"] for item in items}
    assert dept_usernames == {manager_user.username, regular_user.username}

    # Search by full_name or email
    res_search = client.get("/api/users/?search=Specialist")
    assert res_search.status_code == 200
    items = extract_items(res_search.data)
    assert len(items) == 1
    assert items[0]["username"] == hr_user.username


# ---------------------------------------------------------------------------
# Admin User Creation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_create_user_with_department(admin_user, dept_it):
    client = auth_client_for(admin_user)
    payload = {
        "username": "created_user",
        "email": "created.user@assethub.local",
        "password": "Password123!",
        "full_name": "Created Employee",
        "phone": "0988776655",
        "role": "USER",
        "department_id": dept_it.id,
        "is_active": True,
    }
    response = client.post("/api/users/", data=payload, format="json")
    assert response.status_code == 201
    assert response.data["username"] == "created_user"
    assert response.data["role"] == "USER"
    assert response.data["department_id"] == dept_it.id
    assert response.data["is_active"] is True

    # Ensure password and internal auth_version are never exposed
    assert "password" not in response.data
    assert "password_hash" not in response.data
    assert "auth_version" not in response.data

    user = User.objects.get(username="created_user")
    assert user.check_password("Password123!") is True
    assert user.department == dept_it


@pytest.mark.django_db
def test_admin_create_active_user_or_manager_without_department_fails(admin_user):
    client = auth_client_for(admin_user)

    # Active USER without department -> 400
    payload_user = {
        "username": "no_dept_user",
        "email": "no.dept.user@assethub.local",
        "password": "Password123!",
        "role": "USER",
        "department_id": None,
        "is_active": True,
    }
    resp1 = client.post("/api/users/", data=payload_user, format="json")
    assert resp1.status_code == 400

    # Active MANAGER without department -> 400
    payload_manager = {
        "username": "no_dept_manager",
        "email": "no.dept.manager@assethub.local",
        "password": "Password123!",
        "role": "MANAGER",
        "department_id": None,
        "is_active": True,
    }
    resp2 = client.post("/api/users/", data=payload_manager, format="json")
    assert resp2.status_code == 400


@pytest.mark.django_db
def test_admin_can_create_active_admin_without_department(admin_user):
    client = auth_client_for(admin_user)
    payload = {
        "username": "second_admin",
        "email": "second.admin@assethub.local",
        "password": "AdminPassword123!",
        "role": "ADMIN",
        "department_id": None,
        "is_active": True,
    }
    response = client.post("/api/users/", data=payload, format="json")
    assert response.status_code == 201
    assert response.data["username"] == "second_admin"
    assert response.data["role"] == "ADMIN"
    assert response.data["department_id"] is None


# ---------------------------------------------------------------------------
# Admin User PATCH & Department / Status Validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_activate_pending_user_and_assign_department(admin_user, pending_user, dept_hr):
    client = auth_client_for(admin_user)
    payload = {
        "is_active": True,
        "department_id": dept_hr.id,
    }
    response = client.patch(f"/api/users/{pending_user.id}/", data=payload, format="json")
    assert response.status_code == 200
    pending_user.refresh_from_db()
    assert pending_user.is_active is True
    assert pending_user.department == dept_hr


@pytest.mark.django_db
def test_admin_cannot_activate_user_without_department(admin_user, pending_user):
    client = auth_client_for(admin_user)
    payload = {"is_active": True}
    response = client.patch(f"/api/users/{pending_user.id}/", data=payload, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_cannot_remove_department_from_active_user(admin_user, regular_user):
    client = auth_client_for(admin_user)
    payload = {"department_id": None}
    response = client.patch(f"/api/users/{regular_user.id}/", data=payload, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_cannot_deactivate_or_demote_last_active_admin(admin_user):
    client = auth_client_for(admin_user)

    # Deactivating last active Admin fails
    resp_deact = client.patch(
        f"/api/users/{admin_user.id}/",
        data={"is_active": False},
        format="json",
    )
    assert resp_deact.status_code == 400

    # Demoting last active Admin fails
    resp_demote = client.patch(
        f"/api/users/{admin_user.id}/",
        data={"role": "MANAGER"},
        format="json",
    )
    assert resp_demote.status_code == 400


@pytest.mark.django_db
def test_admin_can_deactivate_admin_if_another_active_admin_exists(admin_user, dept_it):
    second_admin = User.objects.create_user(
        username="admin_two",
        email="admin.two@assethub.local",
        password="AdminPassword123!",
        role=User.Role.ADMIN,
        is_active=True,
    )
    client = auth_client_for(admin_user)

    # Deactivate second admin succeeds
    resp = client.patch(
        f"/api/users/{second_admin.id}/",
        data={"is_active": False},
        format="json",
    )
    assert resp.status_code == 200
    second_admin.refresh_from_db()
    assert second_admin.is_active is False


# ---------------------------------------------------------------------------
# Session Revocation on Role/Department/Status Change
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_role_or_department_or_status_change_revokes_tokens(admin_user, regular_user, dept_hr):
    # 1. Generate active token for regular_user
    tokens = generate_tokens_for_user(regular_user)
    user_client = APIClient()
    user_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    # Check profile works
    assert user_client.get("/api/auth/profile/").status_code == 200

    initial_version = regular_user.auth_version

    # 2. Admin changes regular_user's department to HR
    admin_client = auth_client_for(admin_user)
    resp = admin_client.patch(
        f"/api/users/{regular_user.id}/",
        data={"department_id": dept_hr.id},
        format="json",
    )
    assert resp.status_code == 200

    # 3. Check regular_user auth_version incremented
    regular_user.refresh_from_db()
    assert regular_user.auth_version > initial_version

    # 4. Old access token is immediately revoked (401)
    revoked_resp = user_client.get("/api/auth/profile/")
    assert revoked_resp.status_code == 401


# ---------------------------------------------------------------------------
# Response Never Exposes Sensitive Fields
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_user_admin_endpoints_never_expose_password_or_auth_version(admin_user, regular_user):
    client = auth_client_for(admin_user)

    list_resp = client.get("/api/users/")
    assert list_resp.status_code == 200
    for user_data in extract_items(list_resp.data):
        assert "password" not in user_data
        assert "password_hash" not in user_data
        assert "auth_version" not in user_data

    detail_resp = client.get(f"/api/users/{regular_user.id}/")
    assert detail_resp.status_code == 200
    assert "password" not in detail_resp.data
    assert "password_hash" not in detail_resp.data
    assert "auth_version" not in detail_resp.data


# ---------------------------------------------------------------------------
# Manager Scoping & Permissions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_manager_only_sees_users_in_own_department(manager_user, regular_user, hr_user, admin_user):
    client = auth_client_for(manager_user)

    response = client.get("/api/users/")
    assert response.status_code == 200
    usernames = [u["username"] for u in extract_items(response.data)]

    # Manager and regular_user are both in dept_it
    assert manager_user.username in usernames
    assert regular_user.username in usernames
    # hr_user (in dept_hr) and admin_user (None) must NOT be visible
    assert hr_user.username not in usernames
    assert admin_user.username not in usernames


@pytest.mark.django_db
def test_manager_can_get_user_in_own_department_detail(manager_user, regular_user):
    client = auth_client_for(manager_user)
    response = client.get(f"/api/users/{regular_user.id}/")
    assert response.status_code == 200
    assert response.data["id"] == regular_user.id


@pytest.mark.django_db
def test_manager_cannot_get_user_in_other_department_detail(manager_user, hr_user):
    client = auth_client_for(manager_user)
    response = client.get(f"/api/users/{hr_user.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_manager_cannot_create_or_patch_or_delete_user(manager_user, regular_user, dept_it):
    client = auth_client_for(manager_user)

    # POST returns 403
    create_resp = client.post(
        "/api/users/",
        data={
            "username": "mgr_created",
            "email": "mgr.created@assethub.local",
            "password": "Password123!",
            "role": "USER",
            "department_id": dept_it.id,
        },
        format="json",
    )
    assert create_resp.status_code == 403

    # PATCH returns 403
    patch_resp = client.patch(
        f"/api/users/{regular_user.id}/",
        data={"full_name": "Hacked Name"},
        format="json",
    )
    assert patch_resp.status_code == 403

    # DELETE returns 403 (forbidden for Manager)
    del_resp = client.delete(f"/api/users/{regular_user.id}/")
    assert del_resp.status_code in (403, 405)


@pytest.mark.django_db
def test_delete_user_not_allowed(admin_user, regular_user):
    # Admin has permissions, but DELETE method is disabled on endpoint (405)
    client = auth_client_for(admin_user)
    response = client.delete(f"/api/users/{regular_user.id}/")
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# Regular User Forbidden
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regular_user_cannot_access_user_admin(regular_user, dept_it):
    client = auth_client_for(regular_user)
    assert client.get("/api/users/").status_code == 403
    assert client.get(f"/api/users/{regular_user.id}/").status_code == 403
    assert client.post("/api/users/", data={}).status_code == 403
    assert client.patch(f"/api/users/{regular_user.id}/", data={}).status_code == 403
    assert client.delete(f"/api/users/{regular_user.id}/").status_code in (403, 405)


# ---------------------------------------------------------------------------
# Service Guard for Asset / Pending Request Protection
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_service_guard_blocks_user_department_or_status_change_when_holding_assets(admin_user, regular_user, dept_hr):
    client = auth_client_for(admin_user)

    # Mock service guard to simulate user currently holding assets or pending requests
    with patch(
        "apps.authentication.services.check_can_modify_user_department_or_status",
        return_value=False,
    ):
        # Department change blocked -> 409 Conflict
        resp1 = client.patch(
            f"/api/users/{regular_user.id}/",
            data={"department_id": dept_hr.id},
            format="json",
        )
        assert resp1.status_code == 409

        # Deactivation blocked -> 409 Conflict
        resp2 = client.patch(
            f"/api/users/{regular_user.id}/",
            data={"is_active": False},
            format="json",
        )
        assert resp2.status_code == 409

        # Changing only full_name/phone does not trigger the guard
        resp3 = client.patch(
            f"/api/users/{regular_user.id}/",
            data={"full_name": "Safe Name Update"},
            format="json",
        )
        assert resp3.status_code == 200
        regular_user.refresh_from_db()
        assert regular_user.full_name == "Safe Name Update"


@pytest.mark.django_db
def test_real_service_guard_with_assigned_asset_blocks_department_or_deactivation(admin_user, regular_user, dept_it, dept_hr):
    """
    Real integration test (unmocked):
    If user holds an active asset (IN_USE or DAMAGED_PENDING_REPAIR),
    admin PATCH department or is_active returns 409 Conflict.
    Once asset is DISPOSED, modification succeeds.
    """
    from apps.assets.models import Asset
    from apps.departments.models import Category

    client = auth_client_for(admin_user)
    cat = Category.objects.create(name="Guard Test Category")

    asset = Asset.objects.create(
        asset_code="AST-GUARD-001",
        name="Guard Test Asset",
        category=cat,
        department=dept_it,
        assigned_to_user=regular_user,
        status=Asset.Status.IN_USE,
    )

    # 1. Department change blocked by real guard -> 409 Conflict
    resp1 = client.patch(
        f"/api/users/{regular_user.id}/",
        data={"department_id": dept_hr.id},
        format="json",
    )
    assert resp1.status_code == 409

    # 2. Deactivation blocked by real guard -> 409 Conflict
    resp2 = client.patch(
        f"/api/users/{regular_user.id}/",
        data={"is_active": False},
        format="json",
    )
    assert resp2.status_code == 409

    # 3. Release user from asset: asset disposed
    asset.status = Asset.Status.DISPOSED
    asset.assigned_to_user = None
    asset.save()

    # 4. Now department change succeeds -> 200 OK
    resp3 = client.patch(
        f"/api/users/{regular_user.id}/",
        data={"department_id": dept_hr.id},
        format="json",
    )
    assert resp3.status_code == 200
    regular_user.refresh_from_db()
    assert regular_user.department == dept_hr

