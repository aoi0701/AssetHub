import pytest
from datetime import timedelta
from django.conf import settings
from django.core.cache import cache
from apps.authentication.models import User


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test to prevent throttling leaks across tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_register_pending_user(api_client):
    """
    POST /api/auth/register/ creates inactive USER with no department and returns 201 without JWT.
    """
    payload = {
        "username": "new_employee",
        "email": "new.employee@assethub.local",
        "password": "StrongPassword123!",
        "full_name": "New Employee",
        "phone": "0912345678",
    }
    response = api_client.post("/api/auth/register/", data=payload, format="json")

    assert response.status_code == 201
    # Must NOT return access or refresh tokens
    assert "access" not in response.data
    assert "refresh" not in response.data
    assert "message" in response.data

    user = User.objects.get(username="new_employee")
    assert user.is_active is False
    assert user.role == User.Role.USER
    assert user.department is None
    assert user.email == "new.employee@assethub.local"
    assert user.full_name == "New Employee"
    assert user.phone == "0912345678"
    assert user.check_password("StrongPassword123!") is True


@pytest.mark.django_db
def test_register_rejects_privileged_fields(api_client):
    """
    POST /api/auth/register/ rejects role, is_active, department or unknown fields with 400.
    """
    base_payload = {
        "username": "hacker_user",
        "email": "hacker@assethub.local",
        "password": "StrongPassword123!",
        "full_name": "Hacker",
    }

    # Reject role injection
    resp1 = api_client.post(
        "/api/auth/register/",
        data={**base_payload, "role": "ADMIN"},
        format="json",
    )
    assert resp1.status_code == 400
    assert not User.objects.filter(username="hacker_user").exists()

    # Reject is_active injection
    resp2 = api_client.post(
        "/api/auth/register/",
        data={**base_payload, "is_active": True},
        format="json",
    )
    assert resp2.status_code == 400

    # Reject department injection
    resp3 = api_client.post(
        "/api/auth/register/",
        data={**base_payload, "department": 1},
        format="json",
    )
    assert resp3.status_code == 400

    # Reject unexpected arbitrary fields
    resp4 = api_client.post(
        "/api/auth/register/",
        data={**base_payload, "is_staff": True},
        format="json",
    )
    assert resp4.status_code == 400

    resp5 = api_client.post(
        "/api/auth/register/",
        data={**base_payload, "unknown_field": "exploit"},
        format="json",
    )
    assert resp5.status_code == 400


@pytest.mark.django_db
def test_register_validation_errors(api_client, regular_user):
    """
    POST /api/auth/register/ validates required fields, unique email/username, and password strength.
    """
    # Duplicate username
    resp = api_client.post(
        "/api/auth/register/",
        data={
            "username": regular_user.username,
            "email": "unique@assethub.local",
            "password": "StrongPassword123!",
        },
        format="json",
    )
    assert resp.status_code == 400

    # Duplicate email
    resp = api_client.post(
        "/api/auth/register/",
        data={
            "username": "different_user",
            "email": regular_user.email,
            "password": "StrongPassword123!",
        },
        format="json",
    )
    assert resp.status_code == 400

    # Weak password
    resp = api_client.post(
        "/api/auth/register/",
        data={
            "username": "different_user",
            "email": "diff@assethub.local",
            "password": "123",
        },
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_inactive_cannot_login(api_client):
    """
    POST /api/auth/login/ returns 401 for inactive users, wrong passwords, or non-existent users.
    """
    inactive_user = User.objects.create_user(
        username="pending_user",
        email="pending@assethub.local",
        password="ValidPassword123!",
        is_active=False,
    )

    # Inactive user with correct password
    resp1 = api_client.post(
        "/api/auth/login/",
        data={"username": "pending_user", "password": "ValidPassword123!"},
        format="json",
    )
    assert resp1.status_code == 401

    # Wrong password
    resp2 = api_client.post(
        "/api/auth/login/",
        data={"username": "pending_user", "password": "WrongPassword"},
        format="json",
    )
    assert resp2.status_code == 401

    # Non-existent user
    resp3 = api_client.post(
        "/api/auth/login/",
        data={"username": "non_existent", "password": "AnyPassword123!"},
        format="json",
    )
    assert resp3.status_code == 401


@pytest.mark.django_db
def test_login_refresh_and_expiry(api_client, regular_user):
    """
    POST /api/auth/login/ returns access, refresh, user object, and refresh gives new valid access token.
    Checks token lifetimes: access 10m, refresh 1d.
    """
    assert settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] == timedelta(minutes=10)
    assert settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] == timedelta(days=1)

    login_resp = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "UserPassword123!"},
        format="json",
    )
    assert login_resp.status_code == 200
    data = login_resp.data
    assert "access" in data
    assert "refresh" in data
    assert "user" in data

    user_info = data["user"]
    assert user_info["id"] == regular_user.id
    assert user_info["username"] == regular_user.username
    assert user_info["email"] == regular_user.email
    assert user_info["role"] == regular_user.role
    assert user_info["department_id"] == regular_user.department_id

    access_token = data["access"]
    refresh_token = data["refresh"]

    # Verify access token works with real header
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    profile_resp = api_client.get("/api/auth/profile/")
    assert profile_resp.status_code == 200
    assert profile_resp.data["username"] == regular_user.username

    # Refresh access token using POST /api/auth/refresh/
    api_client.credentials()  # Clear auth headers
    refresh_resp = api_client.post(
        "/api/auth/refresh/",
        data={"refresh": refresh_token},
        format="json",
    )
    assert refresh_resp.status_code == 200
    assert "access" in refresh_resp.data
    new_access_token = refresh_resp.data["access"]

    # Verify new access token works with real header
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {new_access_token}")
    profile_resp2 = api_client.get("/api/auth/profile/")
    assert profile_resp2.status_code == 200

    # Bad refresh token returns 401
    bad_refresh_resp = api_client.post(
        "/api/auth/refresh/",
        data={"refresh": "invalid.jwt.token"},
        format="json",
    )
    assert bad_refresh_resp.status_code == 401


@pytest.mark.django_db
def test_profile_rejects_role_and_department(api_client, regular_user, dept_hr):
    """
    GET /api/auth/profile/ returns profile.
    PATCH allows full_name/email/phone, but rejects role, department, is_active, and extra fields.
    """
    login_resp = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "UserPassword123!"},
        format="json",
    )
    access_token = login_resp.data["access"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

    # GET profile
    get_resp = api_client.get("/api/auth/profile/")
    assert get_resp.status_code == 200
    assert get_resp.data["username"] == regular_user.username
    assert get_resp.data["email"] == regular_user.email
    assert get_resp.data["role"] == regular_user.role
    assert get_resp.data["department_id"] == regular_user.department_id

    # Valid PATCH full_name and phone
    patch_resp = api_client.patch(
        "/api/auth/profile/",
        data={"full_name": "Updated Name", "phone": "0988776655"},
        format="json",
    )
    assert patch_resp.status_code == 200
    regular_user.refresh_from_db()
    assert regular_user.full_name == "Updated Name"
    assert regular_user.phone == "0988776655"

    # Reject role modification
    role_resp = api_client.patch(
        "/api/auth/profile/",
        data={"role": "ADMIN"},
        format="json",
    )
    assert role_resp.status_code == 400
    regular_user.refresh_from_db()
    assert regular_user.role == User.Role.USER

    # Reject department modification
    dept_resp = api_client.patch(
        "/api/auth/profile/",
        data={"department": dept_hr.id},
        format="json",
    )
    assert dept_resp.status_code == 400
    regular_user.refresh_from_db()
    assert regular_user.department_id != dept_hr.id

    # Reject is_active modification
    active_resp = api_client.patch(
        "/api/auth/profile/",
        data={"is_active": False},
        format="json",
    )
    assert active_resp.status_code == 400

    # Reject unallowed arbitrary fields
    extra_resp = api_client.patch(
        "/api/auth/profile/",
        data={"arbitrary_field": "value"},
        format="json",
    )
    assert extra_resp.status_code == 400


@pytest.mark.django_db
def test_change_password_revokes_old_access_and_refresh(api_client, regular_user):
    """
    POST /api/auth/change-password/ verifies old_password and validates new_password.
    Increments auth_version, invalidates old access and refresh tokens immediately.
    Allows login with new password and rejects old password.
    """
    # 1. Login to get initial tokens
    login_resp = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "UserPassword123!"},
        format="json",
    )
    old_access = login_resp.data["access"]
    old_refresh = login_resp.data["refresh"]

    # 2. Change password with wrong old password
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {old_access}")
    bad_old_resp = api_client.post(
        "/api/auth/change-password/",
        data={"old_password": "WrongPassword123!", "new_password": "BrandNewPassword123!"},
        format="json",
    )
    assert bad_old_resp.status_code == 400

    # 3. Change password with weak new password
    weak_resp = api_client.post(
        "/api/auth/change-password/",
        data={"old_password": "UserPassword123!", "new_password": "123"},
        format="json",
    )
    assert weak_resp.status_code == 400

    # 4. Successful password change
    old_auth_version = regular_user.auth_version
    success_resp = api_client.post(
        "/api/auth/change-password/",
        data={"old_password": "UserPassword123!", "new_password": "BrandNewPassword123!"},
        format="json",
    )
    assert success_resp.status_code == 204

    regular_user.refresh_from_db()
    assert regular_user.auth_version > old_auth_version
    assert regular_user.check_password("BrandNewPassword123!") is True

    # 5. Old access token must be rejected (401)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {old_access}")
    profile_resp = api_client.get("/api/auth/profile/")
    assert profile_resp.status_code == 401

    # 6. Old refresh token must be rejected (401)
    api_client.credentials()
    refresh_resp = api_client.post(
        "/api/auth/refresh/",
        data={"refresh": old_refresh},
        format="json",
    )
    assert refresh_resp.status_code == 401

    # 7. Login with old password fails (401)
    old_login_resp = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "UserPassword123!"},
        format="json",
    )
    assert old_login_resp.status_code == 401

    # 8. Login with new password succeeds (200)
    new_login_resp = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "BrandNewPassword123!"},
        format="json",
    )
    assert new_login_resp.status_code == 200
    new_access = new_login_resp.data["access"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {new_access}")
    assert api_client.get("/api/auth/profile/").status_code == 200


@pytest.mark.django_db
def test_logout_revokes_all_sessions(api_client, regular_user):
    """
    POST /api/auth/logout/ increments auth_version and invalidates tokens across all sessions.
    """
    # Session A login
    login_a = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "UserPassword123!"},
        format="json",
    )
    access_a = login_a.data["access"]
    refresh_a = login_a.data["refresh"]

    # Session B login
    login_b = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "UserPassword123!"},
        format="json",
    )
    access_b = login_b.data["access"]
    refresh_b = login_b.data["refresh"]

    # Both sessions work initially
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_a}")
    assert api_client.get("/api/auth/profile/").status_code == 200

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_b}")
    assert api_client.get("/api/auth/profile/").status_code == 200

    # Logout from session A
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_a}")
    logout_resp = api_client.post("/api/auth/logout/")
    assert logout_resp.status_code == 204

    # Both session A and B access tokens are now revoked
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_a}")
    assert api_client.get("/api/auth/profile/").status_code == 401

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_b}")
    assert api_client.get("/api/auth/profile/").status_code == 401

    # Both session A and B refresh tokens are now revoked
    api_client.credentials()
    assert api_client.post(
        "/api/auth/refresh/",
        data={"refresh": refresh_a},
        format="json",
    ).status_code == 401

    assert api_client.post(
        "/api/auth/refresh/",
        data={"refresh": refresh_b},
        format="json",
    ).status_code == 401


@pytest.mark.django_db
def test_deactivated_user_token_rejected_immediately(api_client, regular_user):
    """
    If an active user is deactivated in DB, their existing JWT token must be rejected immediately.
    """
    login_resp = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "UserPassword123!"},
        format="json",
    )
    access_token = login_resp.data["access"]
    refresh_token = login_resp.data["refresh"]

    # Deactivate user in DB
    regular_user.is_active = False
    regular_user.save(update_fields=["is_active"])

    # Access token rejected
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    assert api_client.get("/api/auth/profile/").status_code == 401

    # Refresh token rejected
    api_client.credentials()
    assert api_client.post(
        "/api/auth/refresh/",
        data={"refresh": refresh_token},
        format="json",
    ).status_code == 401


@pytest.mark.django_db
def test_throttle_login_and_register(api_client, regular_user):
    """
    IP rate limiting on login/register endpoints returns 429 when rate is exceeded.
    Configured rate is 10/minute in settings.
    """
    cache.clear()

    # 10 attempts allowed for login, 11th must return 429
    for i in range(10):
        resp = api_client.post(
            "/api/auth/login/",
            data={"username": regular_user.username, "password": "WrongPassword"},
            format="json",
        )
        assert resp.status_code == 401

    resp_throttled = api_client.post(
        "/api/auth/login/",
        data={"username": regular_user.username, "password": "WrongPassword"},
        format="json",
    )
    assert resp_throttled.status_code == 429

    cache.clear()

    # 10 attempts allowed for register, 11th must return 429
    for i in range(10):
        resp = api_client.post(
            "/api/auth/register/",
            data={
                "username": f"user_throttle_{i}",
                "email": f"user_{i}@assethub.local",
                "password": "StrongPassword123!",
            },
            format="json",
        )
        assert resp.status_code == 201

    resp_reg_throttled = api_client.post(
        "/api/auth/register/",
        data={
            "username": "user_throttle_11",
            "email": "user_11@assethub.local",
            "password": "StrongPassword123!",
        },
        format="json",
    )
    assert resp_reg_throttled.status_code == 429
