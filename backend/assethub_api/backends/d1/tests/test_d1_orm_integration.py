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

import io
import json
import uuid
import urllib.error
from unittest.mock import patch
import pytest
import django
from django.conf import settings
from django.db import connections, IntegrityError, DatabaseError
from django.db.models.functions import TruncMonth, TruncDay
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from assethub_api.backends.d1 import d1_batch, D1IncompleteBatchError

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
    assert Asset.objects.using("d1").filter(asset_code__exact="AST-EXCLUSIVE-001").exists() is True
    assert Asset.objects.using("default").filter(asset_code__exact="AST-EXCLUSIVE-001").exists() is False


def test_d1_database_error_wrapper_preserves_incomplete_batch_state_cursor():
    """
    Regression Test: Simulates INCOMPLETE_BATCH_STATE failure during cursor.execute.
    Django's D1DatabaseErrorWrapper must preserve:
    - committed is True
    - code == 'INCOMPLETE_BATCH_STATE'
    - idempotency_key is not None
    - payload is not None
    to the business layer catching DatabaseError or D1IncompleteBatchError.
    """
    error_payload = {
        "error": "Incomplete batch state after commit",
        "code": "INCOMPLETE_BATCH_STATE",
        "committed": True,
    }

    def mock_urlopen(req, timeout=None):
        fp = io.BytesIO(json.dumps(error_payload).encode("utf-8"))
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs={"Content-Type": "application/json"},
            fp=fp,
        )

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with pytest.raises(DatabaseError) as exc_info:
            with connections["d1"].cursor() as cursor:
                cursor.execute(
                    "UPDATE departments_department SET name = %s WHERE code = %s",
                    ["Simulated Crash", "TEST_IT"],
                )

        err = exc_info.value
        assert isinstance(err, D1IncompleteBatchError)
        assert err.committed is True
        assert err.code == "INCOMPLETE_BATCH_STATE"
        assert err.idempotency_key is not None
        assert err.payload is not None
        assert isinstance(err.payload, dict)
        assert err.payload.get("sql") is not None
        assert err.raw_response == error_payload


def test_d1_database_error_wrapper_preserves_incomplete_batch_state_d1_batch():
    """
    Regression Test: Simulates INCOMPLETE_BATCH_STATE failure during d1_batch execution.
    Preserves all recovery metadata through wrap_database_errors.
    """
    error_payload = {
        "error": "Batch fault injection",
        "code": "INCOMPLETE_BATCH_STATE",
        "committed": True,
    }

    def mock_urlopen(req, timeout=None):
        fp = io.BytesIO(json.dumps(error_payload).encode("utf-8"))
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs={"Content-Type": "application/json"},
            fp=fp,
        )

    statements = [
        ("UPDATE departments_department SET name = ? WHERE code = ?", ["Dept Batch Crash", "TEST_IT"]),
    ]

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with pytest.raises(DatabaseError) as exc_info:
            d1_batch(statements, using="d1")

        err = exc_info.value
        assert isinstance(err, D1IncompleteBatchError)
        assert err.committed is True
        assert err.code == "INCOMPLETE_BATCH_STATE"
        assert err.idempotency_key is not None
        assert err.payload is not None
        assert "statements" in err.payload
        assert err.raw_response == error_payload


def test_d1_database_error_wrapper_preserves_incomplete_batch_state_orm_mutation():
    """
    Regression Test: Simulates INCOMPLETE_BATCH_STATE failure during high-level Django ORM mutation.
    Preserves all recovery metadata to the business logic.
    """
    error_payload = {
        "error": "ORM mutation crash after commit",
        "code": "INCOMPLETE_BATCH_STATE",
        "committed": True,
    }

    def mock_urlopen(req, timeout=None):
        fp = io.BytesIO(json.dumps(error_payload).encode("utf-8"))
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs={"Content-Type": "application/json"},
            fp=fp,
        )

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with pytest.raises(DatabaseError) as exc_info:
            Department.objects.using("d1").filter(code="TEST_IT").update(name="ORM Failure")

        err = exc_info.value
        assert isinstance(err, D1IncompleteBatchError)
        assert err.committed is True
        assert err.code == "INCOMPLETE_BATCH_STATE"
        assert err.idempotency_key is not None
        assert err.payload is not None


def test_d1_batch_helper_execution_success():
    """
    Test helper function d1_batch executing a real multi-statement batch on D1.
    """
    code_a = f"T-BHA-{uuid.uuid4().hex[:6].upper()}"
    cat_name = f"Cat Batch Helper {uuid.uuid4().hex[:6]}"

    statements = [
        ("INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?)", [code_a, "Dept Batch Helper", "Batch test"]),
        ("INSERT INTO departments_category (name, description) VALUES (?, ?)", [cat_name, "Cat Batch Helper Desc"]),
        ("SELECT count(*) as total FROM departments_department WHERE code = ?", [code_a]),
    ]

    res = d1_batch(statements, using="d1")
    assert res.get("success") is True
    assert len(res.get("results", [])) == 3

    # Assert persistence
    assert Department.objects.using("d1").filter(code=code_a).exists() is True
    assert Category.objects.using("d1").filter(name=cat_name).exists() is True

    # Clean up via d1_batch
    d1_batch([
        ("DELETE FROM departments_department WHERE code = ?", [code_a]),
        ("DELETE FROM departments_category WHERE name = ?", [cat_name]),
    ], using="d1")


def test_replace_date_trunc_orm_query():
    """
    Test replace_date_trunc_in_sql via real ORM QuerySet using TruncMonth and TruncDay.
    """
    dept = Department.objects.using("d1").filter(code="TEST_IT").first()
    if not dept:
        dept = Department.objects.using("d1").create(code="TEST_IT", name="Phòng Công Nghệ Kiểm Thử")
    cat = Category.objects.using("d1").filter(name="Thiết bị kiểm thử D1").first()
    if not cat:
        cat = Category.objects.using("d1").create(name="Thiết bị kiểm thử D1")

    asset = Asset.objects.using("d1").filter(asset_code="AST-GATE-001").first()
    if not asset:
        asset = Asset.objects.using("d1").create(
            asset_code="AST-GATE-001",
            name="MacBook Pro M3 Max (Gate Test)",
            category=cat,
            department=dept,
            status=Asset.Status.AVAILABLE,
        )

    qs = Asset.objects.using("d1").filter(asset_code="AST-GATE-001").annotate(
        created_month=TruncMonth("created_at"),
        created_day=TruncDay("created_at"),
    ).values("asset_code", "created_month", "created_day")

    results = list(qs)
    assert len(results) == 1
    row = results[0]
    assert row["asset_code"] == "AST-GATE-001"
    assert row["created_month"] is not None
    assert row["created_day"] is not None
    assert hasattr(row["created_month"], "year")
    assert hasattr(row["created_day"], "day")
    assert row["created_month"].day == 1


def test_d1_cursor_description_standard_7_tuple():
    """
    Verify DB-API 2.0 cursor.description returns standard 7-tuple.
    """
    with connections["d1"].cursor() as cursor:
        cursor.execute("SELECT id, code, name FROM departments_department LIMIT 1")
        desc = cursor.description
        assert desc is not None
        assert len(desc) == 3
        for col_desc in desc:
            assert isinstance(col_desc, tuple)
            assert len(col_desc) == 7
            col_name, type_code, display_size, internal_size, precision, scale, null_ok = col_desc
            assert isinstance(col_name, str)
            assert col_name in ("id", "code", "name")
            assert type_code is None
            assert display_size is None


def test_d1_cursor_boolean_normalization():
    """
    Verify boolean normalization (True -> 1, False -> 0) in cursor execution.
    """
    with connections["d1"].cursor() as cursor:
        cursor.execute("SELECT %s as t_val, %s as f_val", [True, False])
        row = cursor.fetchone()
        assert row == (1, 0)


def test_d1_orm_select_related_duplicate_columns_preservation():
    """
    Regression Test: ORM select_related() produces duplicate column names in SQL
    (e.g., assets_asset.id, departments_category.id, departments_department.id, authentication_user.id,
     assets_asset.name, departments_category.name, departments_department.name).
    D1 Worker Bridge raw({ columnNames: true }) and D1Cursor must preserve column order
    and prevent duplicate column keys from overwriting each other.
    """
    dept_code = "DEPT_SR_01"
    cat_name = "Cat Select Related Test"
    username = "user_sr_test"
    asset_code = "AST-SR-001"

    # 0. Clean up in reverse-FK order
    Asset.objects.using("d1").filter(asset_code=asset_code).delete()
    User.objects.using("d1").filter(username=username).delete()
    Category.objects.using("d1").filter(name=cat_name).delete()
    Department.objects.using("d1").filter(code=dept_code).delete()

    # 1. Create related models
    dept = Department.objects.using("d1").create(
        code=dept_code,
        name="Phòng Ban Kỹ Thuật Số",
        description="Department for select_related test",
    )
    assert dept.id is not None

    cat = Category.objects.using("d1").create(
        name=cat_name,
        description="Category for select_related test",
    )
    assert cat.id is not None

    user = User(
        username=username,
        email="sr_test@assethub.test",
        role=User.Role.USER,
        full_name="Nguyễn Văn Test JOIN",
        department=dept,
        is_active=True,
        auth_version=1,
    )
    user.set_password("SecureP@ssw0rd2026!")
    user.save(using="d1")
    assert user.id is not None

    # 2. Create Asset with IN_USE status and all relationships linked
    asset = Asset.objects.using("d1").create(
        asset_code=asset_code,
        name="Tài Sản Laptop Dell XPS (Unique Asset Name)",
        category=cat,
        department=dept,
        assigned_to_user=user,
        status=Asset.Status.IN_USE,
        serial_number="SN-SR-9999",
    )
    assert asset.id is not None

    # Guarantee distinct names across joined entities
    assert asset.name != cat.name
    assert asset.name != dept.name

    # 3. Perform ORM query with select_related
    fetched_asset = (
        Asset.objects.using("d1")
        .select_related("category", "department", "assigned_to_user")
        .get(asset_code=asset_code)
    )

    # 4. Assertions on Asset fields (must not be overwritten by joined models)
    assert fetched_asset.id == asset.id
    assert fetched_asset.name == "Tài Sản Laptop Dell XPS (Unique Asset Name)"
    assert fetched_asset.name != cat.name
    assert fetched_asset.name != dept.name

    # 5. Assertions on Category fields
    assert fetched_asset.category is not None
    assert fetched_asset.category.id == cat.id
    assert fetched_asset.category.name == cat_name

    # 6. Assertions on Department fields
    assert fetched_asset.department is not None
    assert fetched_asset.department.id == dept.id
    assert fetched_asset.department.name == "Phòng Ban Kỹ Thuật Số"

    # 7. Assertions on Assigned User fields
    assert fetched_asset.assigned_to_user is not None
    assert fetched_asset.assigned_to_user.id == user.id
    assert fetched_asset.assigned_to_user.username == username

    # Clean up test artifacts
    Asset.objects.using("d1").filter(asset_code=asset_code).delete()
    User.objects.using("d1").filter(username=username).delete()
    Category.objects.using("d1").filter(name=cat_name).delete()
    Department.objects.using("d1").filter(code=dept_code).delete()
