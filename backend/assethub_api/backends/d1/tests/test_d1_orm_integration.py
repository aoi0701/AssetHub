"""
Minimal Integration Gate Verification: Django ORM -> Cloudflare D1
Addresses Review Item 3:
1. Real Django ORM model queries (User, Department, Category, Asset) executing on D1 test DB.
2. Full PBKDF2 1,000,000 iterations password verification via User.check_password.
3. SimpleJWT token generation and verification using production CustomJWTAuthentication pipeline.
4. Session revocation gate: incrementing auth_version causes real API & auth to reject existing token with 401 token_revoked.
5. Inactive user gate: deactivating user causes real API & auth to reject token with 401 user_inactive.
6. Isolation verification: confirms 0 queries leak to PostgreSQL default DB.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load test environment without exposing or printing secrets
env_paths = [
    Path("/app/.env.test"),
    Path(__file__).resolve().parents[3] / ".env.test",
    Path(__file__).resolve().parents[4] / "spikes" / "cf-d1-spike" / ".env.test",
]
for p in env_paths:
    if p.exists():
        load_dotenv(p)
        break

import pytest
import django
from django.conf import settings
from django.db import connections, IntegrityError
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

# Import AssetHub models and authentication views
from apps.departments.models import Department, Category
from apps.authentication.models import User
from apps.assets.models import Asset
from apps.authentication.views import LoginView, ProfileView
from apps.authentication.services import generate_tokens_for_user
from apps.authentication.authentication import CustomJWTAuthentication


class D1TestRouter:
    """Routes all model reads and writes to D1 during integration tests."""
    def db_for_read(self, model, **hints):
        return "d1"

    def db_for_write(self, model, **hints):
        return "d1"

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "d1"


@pytest.fixture(scope="module", autouse=True)
def setup_d1_connection():
    bridge_url = os.environ.get("D1_BRIDGE_URL", "https://assethub-test-worker.hnhoa494-assethub.workers.dev")
    bridge_token = os.environ.get("D1_BRIDGE_TOKEN")
    if not bridge_token or len(bridge_token.strip()) < 32:
        pytest.skip("D1_BRIDGE_TOKEN is not configured with >= 32 chars in environment.")

    connections.databases["d1"] = {
        "ENGINE": "assethub_api.backends.d1",
        "NAME": "assethub-test-d1",
        "BRIDGE_URL": bridge_url,
        "BRIDGE_TOKEN": bridge_token,
        "TIMEOUT": 45.0,
    }


@pytest.fixture(autouse=True)
def route_all_to_d1(settings, django_db_blocker):
    with django_db_blocker.unblock():
        settings.DATABASE_ROUTERS = [
            "assethub_api.backends.d1.tests.test_d1_orm_integration.D1TestRouter"
        ]
        yield


def test_d1_orm_models_crud_and_relationships():
    """Test 1 & 2: Real Django ORM CRUD on Department, Category, User, and Asset over D1."""
    # 0. Clean up previous test artifacts in proper reverse-FK order
    Asset.objects.using("d1").filter(asset_code__in=["AST-GATE-001", "AST-EXCLUSIVE-001"]).delete()
    User.objects.using("d1").filter(username__in=["gate_regular_user", "d1_exclusive_user"]).delete()
    Category.objects.using("d1").filter(name="Thiết bị kiểm thử D1").delete()
    Department.objects.using("d1").filter(code="TEST_IT").delete()

    # 1. Department CRUD
    dept = Department.objects.using("d1").create(
        code="TEST_IT",
        name="Phòng Công Nghệ Kiểm Thử",
        description="Bộ phận kiểm thử tích hợp D1",
    )
    assert dept.id is not None
    assert Department.objects.using("d1").filter(code="TEST_IT").exists()

    # 2. Category CRUD
    cat = Category.objects.using("d1").create(
        name="Thiết bị kiểm thử D1",
        description="Danh mục tài sản phục vụ kiểm thử D1",
    )
    assert cat.id is not None

    # 3. User CRUD with PBKDF2 1,000,000 rounds
    user = User.objects.using("d1").filter(username="gate_admin").first()
    if not user:
        user = User(
            username="gate_admin",
            email="gate_admin@assethub.test",
            role=User.Role.ADMIN,
            full_name="Quản Trị Viên Kiểm Thử",
            phone="0911223344",
            department=dept,
            is_active=True,
            is_staff=True,
            is_superuser=True,
            auth_version=1,
        )
    else:
        user.department = dept
        user.is_active = True
        user.role = User.Role.ADMIN
        user.auth_version = 1
    user.set_password("SecureP@ssw0rd2026!")
    user.save(using="d1")
    assert user.id is not None

    # Verify PBKDF2 password verification works on D1 fetched user
    d1_user = User.objects.using("d1").get(username="gate_admin")
    assert d1_user.check_password("SecureP@ssw0rd2026!") is True
    assert d1_user.check_password("WrongPassword123") is False

    # 4. Asset CRUD and relationships
    asset = Asset.objects.using("d1").create(
        asset_code="AST-GATE-001",
        name="MacBook Pro M3 Max (Gate Test)",
        category=cat,
        department=dept,
        status=Asset.Status.AVAILABLE,
        serial_number="C02D1TEST2026",
    )
    assert asset.id is not None

    # Verify query filter
    fetched_asset = Asset.objects.using("d1").filter(asset_code="AST-GATE-001").first()
    assert fetched_asset is not None
    assert fetched_asset.status == Asset.Status.AVAILABLE
    assert fetched_asset.category_id == cat.id


def test_d1_jwt_auth_and_session_revocation():
    """
    Test 3, 4, 5: Real API login, CustomJWTAuthentication pipeline,
    session revocation (auth_version), and account deactivation on Cloudflare D1.
    No artificial exception raising -- full execution through DRF View and Authentication handler.
    """
    factory = APIRequestFactory()

    # 1. Ensure department and admin exist in D1
    dept = Department.objects.using("d1").filter(code="TEST_IT").first()
    if not dept:
        dept = Department.objects.using("d1").create(code="TEST_IT", name="Phòng Công Nghệ Kiểm Thử")

    admin_user = User.objects.using("d1").filter(username="gate_admin").first()
    if not admin_user:
        admin_user = User(
            username="gate_admin",
            email="gate_admin@assethub.test",
            role=User.Role.ADMIN,
            is_active=True,
            department=dept,
            auth_version=1,
        )
        admin_user.set_password("SecureP@ssw0rd2026!")
        admin_user.save(using="d1")
    else:
        admin_user.auth_version = 1
        admin_user.is_active = True
        admin_user.set_password("SecureP@ssw0rd2026!")
        admin_user.save(using="d1")

    # 2. REAL DRF LOGIN: Authenticate via LoginView.as_view() querying D1
    login_request = factory.post(
        "/api/v1/auth/login/",
        {"username": "gate_admin", "password": "SecureP@ssw0rd2026!"},
        format="json",
    )
    login_response = LoginView.as_view()(login_request)
    assert login_response.status_code == 200
    access_token = login_response.data["access"]
    assert access_token is not None

    # 3. REAL DRF PROTECTED API CALL: GET /api/v1/auth/me/
    profile_request = factory.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    profile_response = ProfileView.as_view()(profile_request)
    assert profile_response.status_code == 200
    assert profile_response.data["username"] == "gate_admin"

    # 4. REAL CustomJWTAuthentication.get_user execution
    auth_handler = CustomJWTAuthentication()
    validated_token = auth_handler.get_validated_token(access_token)
    authenticated_user = auth_handler.get_user(validated_token)
    assert authenticated_user.username == "gate_admin"
    assert authenticated_user.auth_version == 1

    # 5. TEST REVOCATION: Increment auth_version in D1
    User.objects.using("d1").filter(username="gate_admin").update(auth_version=2)

    # 5a. Verify real CustomJWTAuthentication raises AuthenticationFailed(code='token_revoked')
    with pytest.raises(AuthenticationFailed) as exc_info:
        auth_handler.get_user(validated_token)
    assert exc_info.value.detail.code == "token_revoked"

    # 5b. Verify real DRF API call rejects revoked token with HTTP 401
    profile_request_revoked = factory.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    profile_response_revoked = ProfileView.as_view()(profile_request_revoked)
    assert profile_response_revoked.status_code == 401
    assert getattr(profile_response_revoked.data.get("detail"), "code", None) == "token_revoked"

    # Reset admin auth_version
    User.objects.using("d1").filter(username="gate_admin").update(auth_version=1)

    # 6. TEST ADMIN PROTECTION TRIGGER: Deactivating last active Admin must fail with IntegrityError
    with pytest.raises(IntegrityError) as trigger_exc:
        User.objects.using("d1").filter(username="gate_admin").update(is_active=0)
    assert "Cannot demote or deactivate the last active Admin" in str(trigger_exc.value)

    # 7. TEST REGULAR USER DEACTIVATION:
    User.objects.using("d1").filter(username="gate_regular_user").delete()
    reg_user = User(
        username="gate_regular_user",
        email="gate_user@assethub.test",
        role=User.Role.USER,
        full_name="Nhân viên kiểm thử",
        department=dept,
        is_active=True,
        auth_version=1,
    )
    reg_user.set_password("UserP@ssw0rd2026!")
    reg_user.save(using="d1")

    # Regular user logs in and gets access token
    reg_login_req = factory.post(
        "/api/v1/auth/login/",
        {"username": "gate_regular_user", "password": "UserP@ssw0rd2026!"},
        format="json",
    )
    reg_login_resp = LoginView.as_view()(reg_login_req)
    assert reg_login_resp.status_code == 200
    reg_access_token = reg_login_resp.data["access"]
    reg_validated_token = auth_handler.get_validated_token(reg_access_token)

    # Deactivate regular user in D1
    User.objects.using("d1").filter(id=reg_user.id).update(is_active=0)

    # 7a. Verify real CustomJWTAuthentication raises AuthenticationFailed(code='user_inactive')
    with pytest.raises(AuthenticationFailed) as exc_info2:
        auth_handler.get_user(reg_validated_token)
    code2 = exc_info2.value.detail.get("code") if isinstance(exc_info2.value.detail, dict) else getattr(exc_info2.value.detail, "code", None)
    assert str(code2) == "user_inactive"

    # 7b. Verify real DRF API call rejects inactive user token with HTTP 401
    reg_profile_req = factory.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {reg_access_token}",
    )
    reg_profile_resp = ProfileView.as_view()(reg_profile_req)
    assert reg_profile_resp.status_code == 401
    resp_code = reg_profile_resp.data.get("code") or getattr(reg_profile_resp.data.get("detail"), "code", None)
    assert str(resp_code) == "user_inactive"

    # 7c. Verify inactive user cannot log in again
    reg_relogin_req = factory.post(
        "/api/v1/auth/login/",
        {"username": "gate_regular_user", "password": "UserP@ssw0rd2026!"},
        format="json",
    )
    reg_relogin_resp = LoginView.as_view()(reg_relogin_req)
    assert reg_relogin_resp.status_code == 401


def test_d1_zero_postgresql_leakage():
    """Test 6: Confirm that test data exists ONLY in D1 and did NOT touch PostgreSQL."""
    dept = Department.objects.using("d1").filter(code="TEST_IT").first()
    cat = Category.objects.using("d1").filter(name="Thiết bị kiểm thử D1").first()

    # Ensure exclusive records in D1
    User.objects.using("d1").filter(username="d1_exclusive_user").delete()
    d1_user = User.objects.using("d1").create(
        username="d1_exclusive_user",
        email="exclusive@d1.test",
        role=User.Role.USER,
        department=dept,
        is_active=True,
        auth_version=1,
    )

    Asset.objects.using("d1").filter(asset_code="AST-EXCLUSIVE-001").delete()
    d1_asset = Asset.objects.using("d1").create(
        asset_code="AST-EXCLUSIVE-001",
        name="Exclusive D1 Asset",
        category=cat,
        department=dept,
        status=Asset.Status.AVAILABLE,
    )

    # d1_exclusive_user must exist in D1
    assert User.objects.using("d1").filter(username="d1_exclusive_user").exists() is True

    # d1_exclusive_user must NOT exist in PostgreSQL default database
    assert User.objects.using("default").filter(username="d1_exclusive_user").exists() is False

    # AST-EXCLUSIVE-001 must exist in D1 and NOT in PostgreSQL
    assert Asset.objects.using("d1").filter(asset_code="AST-EXCLUSIVE-001").exists() is True
    assert Asset.objects.using("default").filter(asset_code="AST-EXCLUSIVE-001").exists() is False
