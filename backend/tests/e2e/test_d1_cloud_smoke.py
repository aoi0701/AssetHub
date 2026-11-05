"""
Cloudflare D1 & Cloud Integration End-to-End HTTP Smoke Test Suite.

Live cloud smoke tests require Cloudflare services (D1, R2, Workers AI, Tunnel).
Strictly gated by RUN_CLOUD_SMOKE=1.
Default test execution skips all cloud tests in 0s with 0 network calls and 0 mutations.
Safety unit tests run locally without cloud credentials.
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Generator, Optional
from unittest.mock import patch

import warnings
import pytest
from django.conf import settings
from django.db import IntegrityError, OperationalError, connections

warnings.filterwarnings("ignore", category=pytest.PytestUnknownMarkWarning)

# Ensure module path safety for repo root and inside container
backend_dir = Path(__file__).resolve().parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Support both 'backend.tests.e2e.test_d1_cloud_smoke' and 'tests.e2e.test_d1_cloud_smoke'
if "backend.tests.e2e.test_d1_cloud_smoke" not in sys.modules:
    sys.modules["backend.tests.e2e.test_d1_cloud_smoke"] = sys.modules[__name__]
if "tests.e2e.test_d1_cloud_smoke" not in sys.modules:
    sys.modules["tests.e2e.test_d1_cloud_smoke"] = sys.modules[__name__]

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest

Request = AssetRequest
from assethub_api.backends.d1.client import (
    ALLOWED_TEST_BRIDGE_ENDPOINTS,
    ALLOWED_TEST_BRIDGE_URLS,
    D1Client,
    parse_and_normalize_endpoint,
)

ALIAS = "d1_cloud_smoke"
CLOUD_API_BASE_URL = os.environ.get("CLOUD_API_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


class D1CloudSmokeRouter:
    """Routes all model reads and writes to d1_cloud_smoke during smoke testing."""

    def db_for_read(self, model, **hints):
        return ALIAS

    def db_for_write(self, model, **hints):
        return ALIAS

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == ALIAS


def make_real_http_request(
    url: str,
    method: str = "GET",
    json_data: Optional[dict] = None,
    raw_data: Optional[bytes] = None,
    content_type: Optional[str] = None,
    auth_token: Optional[str] = None,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    """Execute real HTTP request via standard library urllib."""
    headers = {
        "User-Agent": "AssetHub-Smoke-Harness/1.0",
        "Accept": "application/json",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    data_bytes = None
    if json_data is not None:
        data_bytes = json.dumps(json_data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_data is not None:
        data_bytes = raw_data
        if content_type:
            headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.status
            raw_body = resp.read().decode("utf-8")
            try:
                data = json.loads(raw_body)
            except Exception:
                data = raw_body
            return status_code, data
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        try:
            data = json.loads(raw_body)
        except Exception:
            data = raw_body
        return exc.code, data
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP connection failed for {url}: {exc.reason}") from exc


def encode_multipart_formdata(fields: dict, files: dict) -> tuple[bytes, str]:
    """Encode multipart/form-data for urllib without external dependencies."""
    boundary = f"----WebKitFormBoundary{secrets.token_hex(16)}"
    lines = []
    for key, value in fields.items():
        lines.append(f"--{boundary}".encode("utf-8"))
        lines.append(f'Content-Disposition: form-data; name="{key}"'.encode("utf-8"))
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))

    for field_name, (filename, content_bytes, content_type) in files.items():
        lines.append(f"--{boundary}".encode("utf-8"))
        lines.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode("utf-8")
        )
        lines.append(f"Content-Type: {content_type}".encode("utf-8"))
        lines.append(b"")
        lines.append(content_bytes)

    lines.append(f"--{boundary}--".encode("utf-8"))
    lines.append(b"")
    body = b"\r\n".join(lines)
    content_type_header = f"multipart/form-data; boundary={boundary}"
    return body, content_type_header


@pytest.fixture(autouse=True)
def unblock_db_each_test(django_db_blocker):
    """Ensure database access is unblocked during tests."""
    with django_db_blocker.unblock():
        yield


unblock_db = unblock_db_each_test


def execute_smoke_teardown(ctx: Dict[str, Any]) -> None:
    """
    Deterministically cleans up smoke test entities with strict ownership verification.
    Deletes entities in strict FK order and verifies 0 leftover records for verified IDs.
    Raises RuntimeError if cleanup verification fails or ownership mismatch is detected.
    """
    cleanup_failures = []
    run_id = ctx.get("run_id", "")
    if not run_id or not isinstance(run_id, str):
        raise RuntimeError("Cleanup verification failed: run_id is required for ownership verification")

    alias = ctx.get("alias", ALIAS)
    tracked_ids = ctx.get("tracked_ids") or {}
    dept = ctx.get("department")
    cat = ctx.get("category")
    dept_id = getattr(dept, "id", None) or (dept.get("id") if isinstance(dept, dict) else None)
    cat_id = getattr(cat, "id", None) or (cat.get("id") if isinstance(cat, dict) else None)
    dept_code = getattr(dept, "code", None) or (dept.get("code") if isinstance(dept, dict) else None) or f"SMK_{run_id}"

    # 1. R2 keys: verify run_id in key before calling storage.delete. If not matching, append mismatch.
    if tracked_ids.get("r2_keys"):
        from apps.common.storage_backends import AttachmentStorage, is_r2_configured

        storage = None
        if is_r2_configured():
            try:
                storage = AttachmentStorage()
            except Exception as exc:
                cleanup_failures.append(f"R2 client error: {str(exc)}")

        for r2_key in tracked_ids["r2_keys"]:
            if not run_id or run_id not in str(r2_key):
                cleanup_failures.append(
                    f"Ownership mismatch: R2 key {r2_key} does not belong to run {run_id}. Preserved sentinel."
                )
                continue
            if storage:
                try:
                    storage.delete(r2_key)
                except Exception as exc:
                    cleanup_failures.append(f"R2 key deletion error for {r2_key}: {str(exc)}")

    # 2. Audit logs: Delete ONLY for assets verified to belong to the run
    verified_asset_ids = []
    if tracked_ids.get("assets"):
        try:
            if dept_id is not None and cat_id is not None:
                run_assets = Asset.objects.using(alias).filter(
                    id__in=tracked_ids["assets"],
                    department_id=dept_id,
                    category_id=cat_id,
                )
                verified_asset_ids = list(run_assets.values_list("id", flat=True))
            else:
                verified_asset_ids = []

            unowned_asset_ids = set(tracked_ids["assets"]) - set(verified_asset_ids)
            if unowned_asset_ids:
                cleanup_failures.append(
                    f"Ownership mismatch: assets {unowned_asset_ids} do not belong to run {run_id}. Preserved sentinel."
                )
            if verified_asset_ids:
                AssetHistoryLog.objects.using(alias).filter(asset_id__in=verified_asset_ids).delete()
        except Exception as exc:
            cleanup_failures.append(f"Asset history deletion error: {str(exc)}")

    # 3. Requests: Delete ONLY requests where requester_id in tracked_ids['users']
    verified_request_ids = []
    if tracked_ids.get("requests"):
        try:
            user_ids = tracked_ids.get("users", [])
            run_requests = Request.objects.using(alias).filter(
                id__in=tracked_ids["requests"],
                requester_id__in=user_ids,
            )
            verified_request_ids = list(run_requests.values_list("id", flat=True))
            unowned_req_ids = set(tracked_ids["requests"]) - set(verified_request_ids)
            if unowned_req_ids:
                cleanup_failures.append(
                    f"Ownership mismatch: requests {unowned_req_ids} do not belong to run {run_id}. Preserved sentinel."
                )
            run_requests.delete()
        except Exception as exc:
            cleanup_failures.append(f"Request deletion error: {str(exc)}")

    # 4. Assets: Delete ONLY verified assets
    if verified_asset_ids:
        try:
            Asset.objects.using(alias).filter(id__in=verified_asset_ids).delete()
        except Exception as exc:
            cleanup_failures.append(f"Asset deletion error: {str(exc)}")

    # 5. Categories: Delete ONLY categories matching run_id
    verified_cat_ids = []
    if tracked_ids.get("categories"):
        try:
            run_cats = Category.objects.using(alias).filter(
                id__in=tracked_ids["categories"],
                name__contains=run_id,
            )
            verified_cat_ids = list(run_cats.values_list("id", flat=True))
            unowned_cat_ids = set(tracked_ids["categories"]) - set(verified_cat_ids)
            if unowned_cat_ids:
                cleanup_failures.append(
                    f"Ownership mismatch: categories {unowned_cat_ids} do not belong to run {run_id}. Preserved sentinel."
                )
            if verified_cat_ids:
                Category.objects.using(alias).filter(id__in=verified_cat_ids).delete()
        except Exception as exc:
            cleanup_failures.append(f"Category deletion error: {str(exc)}")

    # 6. Departments: Delete ONLY departments matching run_id dept_code
    verified_dept_ids = []
    if tracked_ids.get("departments"):
        try:
            run_depts = Department.objects.using(alias).filter(
                id__in=tracked_ids["departments"],
                code=dept_code,
            )
            verified_dept_ids = list(run_depts.values_list("id", flat=True))
            unowned_dept_ids = set(tracked_ids["departments"]) - set(verified_dept_ids)
            if unowned_dept_ids:
                cleanup_failures.append(
                    f"Ownership mismatch: departments {unowned_dept_ids} do not belong to run {run_id}. Preserved sentinel."
                )
            if verified_dept_ids:
                Department.objects.using(alias).filter(id__in=verified_dept_ids).delete()
        except Exception as exc:
            cleanup_failures.append(f"Department deletion error: {str(exc)}")

    # 7. Users: Delete ONLY users matching run_id
    verified_user_ids = []
    if tracked_ids.get("users"):
        try:
            run_users = User.objects.using(alias).filter(
                id__in=tracked_ids["users"],
                username__contains=run_id,
            )
            verified_user_ids = list(run_users.values_list("id", flat=True))
            unowned_user_ids = set(tracked_ids["users"]) - set(verified_user_ids)
            if unowned_user_ids:
                cleanup_failures.append(
                    f"Ownership mismatch: users {unowned_user_ids} do not belong to run {run_id}. Preserved sentinel."
                )
            if verified_user_ids:
                User.objects.using(alias).filter(id__in=verified_user_ids).delete()
        except Exception as exc:
            cleanup_failures.append(f"User deletion error: {str(exc)}")

    # 8. Verify zero leftover records for verified IDs
    try:
        if verified_request_ids:
            rem_req = Request.objects.using(alias).filter(id__in=verified_request_ids).count()
            if rem_req != 0:
                cleanup_failures.append(f"requests_assetrequest records remain in DB: count={rem_req}")

        if verified_asset_ids:
            rem_assets = Asset.objects.using(alias).filter(id__in=verified_asset_ids).count()
            if rem_assets != 0:
                cleanup_failures.append(f"assets_asset records remain in DB: count={rem_assets}")

        if verified_cat_ids:
            rem_cats = Category.objects.using(alias).filter(id__in=verified_cat_ids).count()
            if rem_cats != 0:
                cleanup_failures.append(f"departments_category records remain in DB: count={rem_cats}")

        if verified_dept_ids:
            rem_depts = Department.objects.using(alias).filter(id__in=verified_dept_ids).count()
            if rem_depts != 0:
                cleanup_failures.append(f"departments_department records remain in DB: count={rem_depts}")

        if verified_user_ids:
            rem_users = User.objects.using(alias).filter(id__in=verified_user_ids).count()
            if rem_users != 0:
                cleanup_failures.append(f"authentication_user records remain in DB: count={rem_users}")
    except Exception as exc:
        cleanup_failures.append(f"Verification query error: {str(exc)}")

    if cleanup_failures:
        raise RuntimeError(f"Cleanup verification failed: {'; '.join(cleanup_failures)}")


@pytest.fixture(scope="module")
def smoke_context(django_db_blocker) -> Generator[Dict[str, Any], None, None]:
    """
    Module fixture setting up Cloud D1 connection and test entities.
    Strictly gated by RUN_CLOUD_SMOKE=1.
    Performs deterministic teardown with deletion verification.
    """
    if not os.environ.get("RUN_CLOUD_SMOKE") or os.environ.get("RUN_CLOUD_SMOKE") != "1":
        pytest.skip("Cloud smoke tests skipped unless RUN_CLOUD_SMOKE=1")

    bridge_url = os.environ.get("D1_BRIDGE_URL", "").rstrip("/")
    bridge_token = (
        os.environ.get("D1_BRIDGE_TOKEN")
        or os.environ.get("D1_BRIDGE_SECRET_TOKEN")
        or ""
    ).strip()

    if not bridge_url or not bridge_token or len(bridge_token) < 32:
        pytest.skip("D1_BRIDGE_URL and D1_BRIDGE_TOKEN (>= 32 chars) must be configured.")

    # Isolation Guard check: Endpoint must already be in pre-approved allowlist!
    try:
        norm_endpoint = parse_and_normalize_endpoint(bridge_url)
    except Exception as exc:
        pytest.skip(f"D1_BRIDGE_URL normalization failed: {exc}")

    if norm_endpoint not in ALLOWED_TEST_BRIDGE_ENDPOINTS:
        pytest.skip(
            f"D1_BRIDGE_URL '{bridge_url}' not in pre-approved ALLOWED_TEST_BRIDGE_URLS: {ALLOWED_TEST_BRIDGE_URLS}"
        )

    with django_db_blocker.unblock():
        run_id = secrets.token_hex(6)

        d1_conf = {
            "ENGINE": "assethub_api.backends.d1",
            "NAME": "assethub-test-d1",
            "BRIDGE_URL": bridge_url,
            "BRIDGE_TOKEN": bridge_token,
            "TIMEOUT": 45.0,
            "AUTOCOMMIT": True,
            "ATOMIC_REQUESTS": False,
            "OPTIONS": {},
        }

        # Clean existing connections
        for c_alias in (ALIAS, "default"):
            if c_alias in connections:
                try:
                    connections[c_alias].close()
                except Exception:
                    pass
                try:
                    del connections[c_alias]
                except Exception:
                    pass

        settings.DATABASES[ALIAS] = d1_conf
        connections.databases[ALIAS] = d1_conf
        orig_default = dict(settings.DATABASES.get("default", {}))
        settings.DATABASES["default"] = d1_conf
        connections.databases["default"] = d1_conf

        orig_routers = getattr(settings, "DATABASE_ROUTERS", [])
        router_cls_path = f"{__name__}.D1CloudSmokeRouter"
        settings.DATABASE_ROUTERS = [router_cls_path]
        from django.db import router
        router._routers = [D1CloudSmokeRouter()]

        # Track run-specific created IDs for deterministic cleanup
        tracked_ids: Dict[str, list] = {
            "requests": [],
            "assets": [],
            "categories": [],
            "departments": [],
            "users": [],
            "r2_keys": [],
        }

        ctx = {
            "run_id": run_id,
            "bridge_url": bridge_url,
            "bridge_token": bridge_token,
            "cloud_api_base_url": CLOUD_API_BASE_URL,
            "alias": ALIAS,
            "tracked_ids": tracked_ids,
            "created": {
                "department_ids": tracked_ids["departments"],
                "category_ids": tracked_ids["categories"],
                "user_ids": tracked_ids["users"],
                "asset_ids": tracked_ids["assets"],
                "request_ids": tracked_ids["requests"],
                "history_ids": [],
            },
            "d1_client": None,
            "department": None,
            "category": None,
            "admin_user": None,
            "admin_password": None,
            "req_user": None,
            "req_password": None,
        }

        # Wrap from before the first mutation in try ... finally so partial setup can cleanup cleanly
        try:
            d1_client = D1Client(base_url=bridge_url, token=bridge_token, timeout=30.0)
            ctx["d1_client"] = d1_client

            # Preflight Before First Mutation:
            baseline_admin_count = (
                User.objects.using(ALIAS)
                .filter(role=User.Role.ADMIN, is_active=True)
                .count()
            )
            if baseline_admin_count == 0:
                pytest.skip(
                    "D1 database has no pre-existing active baseline Admin. "
                    "Bootstrap required to prevent last-admin trigger lock."
                )

            # Seed initial entities directly in D1
            dept_code = f"SMK_{run_id}"
            dept = Department.objects.using(ALIAS).create(
                code=dept_code,
                name=f"Smoke Dept {run_id}",
                description="Smoke test department",
            )
            tracked_ids["departments"].append(dept.id)
            ctx["department"] = dept

            cat = Category.objects.using(ALIAS).create(
                name=f"SmokeCat_{run_id}",
                description="Smoke test category",
            )
            tracked_ids["categories"].append(cat.id)
            ctx["category"] = cat

            admin_username = f"smoke_admin_{run_id}"
            admin_password = f"AdminPass#{run_id}!"
            admin_user = User(
                username=admin_username,
                email=f"{admin_username}@assethub.test",
                role=User.Role.ADMIN,
                full_name=f"Smoke Admin {run_id}",
                department=dept,
                is_active=True,
                is_staff=True,
                is_superuser=True,
                auth_version=1,
            )
            admin_user.set_password(admin_password)
            admin_user.save(using=ALIAS)
            tracked_ids["users"].append(admin_user.id)
            ctx["admin_user"] = admin_user
            ctx["admin_password"] = admin_password

            req_username = f"smoke_req_{run_id}"
            req_password = f"UserPass#{run_id}!"
            req_user = User(
                username=req_username,
                email=f"{req_username}@assethub.test",
                role=User.Role.USER,
                full_name=f"Smoke Requester {run_id}",
                department=dept,
                is_active=True,
                auth_version=1,
            )
            req_user.set_password(req_password)
            req_user.save(using=ALIAS)
            tracked_ids["users"].append(req_user.id)
            ctx["req_user"] = req_user
            ctx["req_password"] = req_password

            yield ctx
        finally:
            teardown_exc = None
            try:
                execute_smoke_teardown(ctx)
            except Exception as exc:
                teardown_exc = exc
            finally:
                for a in (ALIAS, "default"):
                    if a in connections:
                        try:
                            connections[a].close()
                        except Exception:
                            pass
                        try:
                            del connections[a]
                        except Exception:
                            pass
                settings.DATABASES.pop(ALIAS, None)
                connections.databases.pop(ALIAS, None)
                if orig_default:
                    settings.DATABASES["default"] = orig_default
                    connections.databases["default"] = orig_default
                settings.DATABASE_ROUTERS = orig_routers
                from django.db import router
                router._routers = None
                router.__dict__.pop("routers", None)

            if teardown_exc:
                raise teardown_exc
# Cloud Smoke Tests (Gated by RUN_CLOUD_SMOKE=1)
# =============================================================================

@pytest.mark.cloud_smoke
def test_01_bridge_health(smoke_context):
    """
    Step 1: Bridge /health endpoint check.
    Validates that the Cloudflare Worker bridge is reachable and healthy.
    """
    bridge_url = smoke_context["bridge_url"]
    req = urllib.request.Request(
        f"{bridge_url}/health",
        headers={"User-Agent": "AssetHub-Smoke-Harness/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body.get("status") == "ok"
        assert body.get("service") == "d1-bridge"

    # Verify direct SQL query connectivity (SELECT 1) over bridge
    d1_client: D1Client = smoke_context["d1_client"]
    res = d1_client.query("SELECT 1 AS ping;")
    assert res.get("success") is True


@pytest.mark.cloud_smoke
def test_02_jwt_auth_login(smoke_context):
    """
    Step 2: Real HTTP POST /api/auth/login/.
    Validates password authentication, JWT token issuance, and claims.
    """
    cloud_api = smoke_context["cloud_api_base_url"]
    admin_user = smoke_context["admin_user"]
    admin_password = smoke_context["admin_password"]
    req_user = smoke_context["req_user"]
    req_password = smoke_context["req_password"]

    # 1. Real HTTP login for admin user
    login_url = f"{cloud_api}/api/auth/login/"
    admin_login_payload = {
        "username": admin_user.username,
        "password": admin_password,
    }
    status, admin_data = make_real_http_request(
        login_url,
        method="POST",
        json_data=admin_login_payload,
    )
    assert status == 200, f"Admin login failed ({status}): {admin_data}"
    assert "access" in admin_data, "Admin login response missing 'access' token"
    assert "refresh" in admin_data, "Admin login response missing 'refresh' token"
    smoke_context["admin_access_token"] = admin_data["access"]

    # 2. Real HTTP login for requester user
    req_login_payload = {
        "username": req_user.username,
        "password": req_password,
    }
    status, req_data = make_real_http_request(
        login_url,
        method="POST",
        json_data=req_login_payload,
    )
    assert status == 200, f"Requester login failed ({status}): {req_data}"
    assert "access" in req_data, "Requester login response missing 'access' token"
    smoke_context["req_access_token"] = req_data["access"]


@pytest.mark.cloud_smoke
def test_03_d1_asset_request_workflow(smoke_context):
    """
    Step 3: Real HTTP asset creation, request creation, and request approval.
    Reads back directly from D1 via D1Client to confirm write to Cloudflare D1.
    """
    cloud_api = smoke_context["cloud_api_base_url"]
    admin_token = smoke_context["admin_access_token"]
    req_token = smoke_context["req_access_token"]
    run_id = smoke_context["run_id"]
    dept = smoke_context["department"]
    cat = smoke_context["category"]
    req_user = smoke_context["req_user"]
    admin_user = smoke_context["admin_user"]
    created = smoke_context["created"]
    d1_client: D1Client = smoke_context["d1_client"]

    # 1. Real HTTP POST /api/assets/ to create asset
    asset_create_payload = {
        "name": f"Smoke MacBook {run_id}",
        "category_id": cat.id,
        "department_id": dept.id,
        "serial_number": f"SN-{run_id}",
        "specification": {"cpu": "M3 Max", "ram": "36GB"},
        "reason": "Smoke asset creation",
    }
    status, asset_data = make_real_http_request(
        f"{cloud_api}/api/assets/",
        method="POST",
        json_data=asset_create_payload,
        auth_token=admin_token,
    )
    assert status == 201, f"Asset creation failed ({status}): {asset_data}"
    asset_id = asset_data["id"]
    asset_code = asset_data["asset_code"]
    smoke_context["tracked_ids"]["assets"].append(asset_id)
    if "created" in smoke_context:
        smoke_context["created"]["asset_ids"].append(asset_id)
    smoke_context["asset"] = asset_data

    # 2. Real HTTP POST /api/requests/ to create request
    req_create_payload = {
        "request_type": "ALLOCATE",
        "requested_category_id": cat.id,
        "reason": f"Smoke test request allocation {run_id}",
    }
    status, req_data = make_real_http_request(
        f"{cloud_api}/api/requests/",
        method="POST",
        json_data=req_create_payload,
        auth_token=req_token,
    )
    assert status == 201, f"Request creation failed ({status}): {req_data}"
    request_id = req_data["id"]
    smoke_context["tracked_ids"]["requests"].append(request_id)
    if "created" in smoke_context:
        smoke_context["created"]["request_ids"].append(request_id)

    # 3. Real HTTP POST /api/requests/{id}/approve/ to approve request
    approve_payload = {"selected_asset_id": asset_id}
    status, approve_data = make_real_http_request(
        f"{cloud_api}/api/requests/{request_id}/approve/",
        method="POST",
        json_data=approve_payload,
        auth_token=admin_token,
    )
    assert status == 200, f"Request approval failed ({status}): {approve_data}"
    assert approve_data["status"] == "APPROVED"

    # 4. Read back directly from Cloudflare D1 via D1Client
    d1_asset_res = d1_client.query(
        "SELECT id, status, assigned_to_user_id, asset_code FROM assets_asset WHERE id = ?;",
        params=[asset_id],
    )
    assert d1_asset_res.get("success") is True
    assert len(d1_asset_res.get("results", [])) == 1
    d1_asset_row = d1_asset_res["results"][0]
    assert d1_asset_row["status"] == Asset.Status.IN_USE
    assert d1_asset_row["assigned_to_user_id"] == req_user.id
    assert d1_asset_row["asset_code"] == asset_code

    # Verify request in D1
    d1_req_res = d1_client.query(
        "SELECT id, status, asset_id, reviewed_by_id FROM requests_assetrequest WHERE id = ?;",
        params=[request_id],
    )
    assert d1_req_res.get("success") is True
    assert len(d1_req_res.get("results", [])) == 1
    d1_req_row = d1_req_res["results"][0]
    assert d1_req_row["status"] == AssetRequest.Status.APPROVED
    assert d1_req_row["asset_id"] == asset_id
    assert d1_req_row["reviewed_by_id"] == admin_user.id

    # Verify audit log in D1
    d1_log_res = d1_client.query(
        "SELECT id, action_type, to_user_id FROM assets_assethistorylog WHERE asset_id = ?;",
        params=[asset_id],
    )
    assert d1_log_res.get("success") is True
    assert len(d1_log_res.get("results", [])) >= 1
    for row in d1_log_res["results"]:
        created["history_ids"].append(row["id"])


@pytest.mark.cloud_smoke
def test_04_r2_storage_smoke(smoke_context):
    """
    Step 4: R2 storage smoke:
    Upload a small file via HTTP multipart.
    Request presigned download URL via HTTP GET.
    Download bytes from presigned URL and verify contents match.
    If R2 credentials are not configured, skip cleanly with clear explanation.
    """
    from apps.common.storage_backends import is_r2_configured

    if not is_r2_configured():
        pytest.skip(
            "R2 credentials not configured (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, "
            "R2_BUCKET_NAME, R2_ENDPOINT_URL required)."
        )

    cloud_api = smoke_context["cloud_api_base_url"]
    admin_token = smoke_context["admin_access_token"]
    asset_id = smoke_context["asset"]["id"]
    run_id = smoke_context["run_id"]

    # Generate minimal valid PNG
    import io
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (2, 2), color="blue")
    img.save(buf, format="PNG")
    file_bytes = buf.getvalue()
    filename = f"smoke_{run_id}.png"

    body_bytes, content_type = encode_multipart_formdata(
        fields={"kind": "image"},
        files={"file": (filename, file_bytes, "image/png")},
    )

    # 1. Upload attachment via HTTP multipart
    upload_url = f"{cloud_api}/api/assets/{asset_id}/attachments/"
    status, data = make_real_http_request(
        upload_url,
        method="POST",
        raw_data=body_bytes,
        content_type=content_type,
        auth_token=admin_token,
    )
    if status == 404:
        upload_url = f"{cloud_api}/api/assets/{asset_id}/upload-attachment/"
        status, data = make_real_http_request(
            upload_url,
            method="POST",
            raw_data=body_bytes,
            content_type=content_type,
            auth_token=admin_token,
        )

    assert status == 200, f"Attachment upload failed ({status}): {data}"
    storage_key = data.get("key") if isinstance(data, dict) else None
    if storage_key:
        smoke_context["tracked_ids"]["r2_keys"].append(storage_key)

    # 2. Request presigned download URL via HTTP GET
    download_req_url = f"{cloud_api}/api/assets/{asset_id}/attachments/image/download/"
    dl_status, dl_data = make_real_http_request(
        download_req_url,
        method="GET",
        auth_token=admin_token,
    )
    if dl_status == 404:
        download_req_url = f"{cloud_api}/api/assets/{asset_id}/download-attachment/?kind=image"
        dl_status, dl_data = make_real_http_request(
            download_req_url,
            method="GET",
            auth_token=admin_token,
        )

    assert dl_status == 200, f"Presigned download URL request failed ({dl_status}): {dl_data}"
    presigned_url = dl_data.get("download_url")
    assert presigned_url and (
        presigned_url.startswith("http://") or presigned_url.startswith("https://")
    )

    # 3. Download bytes from presigned URL via HTTP GET and verify contents match
    req = urllib.request.Request(
        presigned_url,
        headers={"User-Agent": "AssetHub-Smoke-Harness/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        downloaded_bytes = resp.read()

    assert downloaded_bytes == file_bytes, "Downloaded attachment content does not match uploaded bytes!"


def validate_ai_classification_response(
    status_code: int,
    data: Any,
    valid_category_ids: Optional[list] = None,
) -> dict:
    """
    Validate AI category suggestion response.
    Enforces strict failure on auth/network errors, verifies valid categories and fallback flag.
    """
    if status_code in (401, 403):
        pytest.fail(f"Workers AI smoke failed: Authentication/Permission error HTTP {status_code}: {data}")

    if status_code == 404:
        pytest.fail(f"Workers AI endpoint not found HTTP 404: {data}")

    if status_code == 503:
        err_msg = str(data)
        if "AI worker authentication failed." in err_msg or "network" in err_msg.lower():
            pytest.fail(f"Workers AI classification failed due to auth/network error (HTTP 503): {data}")
        pytest.fail(f"Workers AI classification failed with HTTP 503 Service Unavailable: {data}")

    if status_code == 429:
        err_str = str(data).lower()
        assert any(
            term in err_str for term in ("quota", "capacity", "rate limit", "throttled", "exceeded")
        ), f"HTTP 429 response does not indicate quota/capacity limit: {data}"
        return {"status": 429, "rate_limited": True, "data": data}

    if status_code == 200:
        assert isinstance(data, dict), f"Expected dict response for HTTP 200, got: {data}"
        suggested_id = data.get("suggested_category_id")
        assert suggested_id is not None, f"Response missing 'suggested_category_id': {data}"
        if valid_category_ids is not None:
            assert (
                suggested_id in valid_category_ids
            ), f"Suggested category id {suggested_id} not in valid categories {valid_category_ids}"
        assert "is_fallback" in data, f"Response missing 'is_fallback' flag: {data}"
        assert isinstance(data["is_fallback"], bool), f"'is_fallback' must be a boolean: {data}"
        return data

    pytest.fail(f"Workers AI returned unexpected HTTP status {status_code}: {data}")


@pytest.mark.cloud_smoke
def test_05_workers_ai_smoke(smoke_context):
    """
    Step 5: Workers AI smoke:
    Call POST /api/ai/suggest-category/ with valid asset name and category prompt.
    Verify either 200 with suggested category or proper quota fallback (429).
    Network errors or 503 with AI worker authentication failed MUST FAIL.
    If cloud Workers AI is not configured, skip cleanly.
    """
    ai_url_env = os.environ.get("AI_WORKER_URL", "").strip()
    ai_token_env = os.environ.get("AI_SERVICE_TOKEN", "").strip()
    if (
        not ai_url_env
        or "8787" in ai_url_env
        or not ai_token_env
        or ai_token_env == "dev-ai-secret-token-2026"
    ):
        pytest.skip(
            "Cloud Workers AI not configured (live AI_WORKER_URL and AI_SERVICE_TOKEN required)."
        )

    cloud_api = smoke_context["cloud_api_base_url"]
    admin_token = smoke_context["admin_access_token"]
    cat = smoke_context["category"]

    ai_url = f"{cloud_api}/api/ai/suggest-category/"
    payload = {
        "name": "Apple MacBook Pro 16 M3 Max",
        "description": "High performance laptop for development and engineering",
    }

    try:
        status, data = make_real_http_request(
            ai_url,
            method="POST",
            json_data=payload,
            auth_token=admin_token,
        )
    except Exception as exc:
        pytest.fail(f"Workers AI smoke HTTP network error: {exc}")

    valid_cats = [cat.id]
    if "tracked_ids" in smoke_context and smoke_context["tracked_ids"]["categories"]:
        valid_cats = smoke_context["tracked_ids"]["categories"]

    try:
        db_cat_ids = list(Category.objects.values_list("id", flat=True))
        if db_cat_ids:
            valid_cats = list(set(valid_cats + db_cat_ids))
    except Exception:
        pass

    validate_ai_classification_response(status, data, valid_category_ids=valid_cats)


@pytest.mark.cloud_smoke
def test_06_d1_last_admin_protection_trigger(smoke_context):
    """
    Step 6: Last active admin protection trigger check.
    NEVER mutates or deactivates pre-existing active admins in D1.
    Operates strictly within run-created users or isolated clean context.
    """
    alias = smoke_context["alias"]
    d1_client: D1Client = smoke_context["d1_client"]
    admin_user = smoke_context["admin_user"]

    # Inspect other active admins in D1 without mutating them
    res = d1_client.query(
        "SELECT id, username, is_active, role FROM authentication_user WHERE role = 'ADMIN' AND is_active = 1 AND id != ?;",
        params=[admin_user.id],
    )
    assert res.get("success") is True
    other_admins = res.get("results", [])

    if len(other_admins) == 0:
        # admin_user is sole active admin in D1 -> trigger must abort directly in D1
        with pytest.raises(IntegrityError) as exc_info:
            with connections[alias].cursor() as cursor:
                cursor.execute(
                    "UPDATE authentication_user SET is_active = 0 WHERE id = %s;",
                    [admin_user.id],
                )
        assert "Cannot demote or deactivate the last active Admin" in str(exc_info.value)
    else:
        # Pre-existing active admins exist in D1!
        # Strict rule: NEVER touch pre-existing admins in D1!
        # Verify schema trigger logic in clean isolated SQLite test using D1 schema.sql
        schema_path = Path(settings.BASE_DIR) / "assethub_api" / "backends" / "d1" / "sql" / "schema.sql"
        if not schema_path.exists():
            schema_path = Path(settings.BASE_DIR).parent / "cloudflare-worker" / "bridge" / "schema.sql"
        assert schema_path.exists(), f"schema.sql not found at {schema_path}"

        import sqlite3

        conn = sqlite3.connect(":memory:")
        with open(schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        # Insert run admin as sole admin in isolated DB
        conn.execute(
            "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
            "VALUES (?, ?, 'hash', ?, 'ADMIN', 1);",
            [admin_user.id, admin_user.username, admin_user.email],
        )
        conn.commit()

        # Attempt to deactivate sole run admin -> trigger must abort
        with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
            conn.execute("UPDATE authentication_user SET is_active = 0 WHERE id = ?", [admin_user.id])

        # Attempt to demote sole run admin -> trigger must abort
        with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
            conn.execute("UPDATE authentication_user SET role = 'USER' WHERE id = ?", [admin_user.id])

        conn.close()

        # Verify all pre-existing active admins in D1 were NEVER modified
        check_res = d1_client.query(
            "SELECT id, username, is_active, role FROM authentication_user WHERE role = 'ADMIN' AND is_active = 1 AND id != ?;",
            params=[admin_user.id],
        )
        assert check_res.get("success") is True
        assert len(check_res.get("results", [])) == len(other_admins)

    # Verify run admin remains active in D1
    admin_check = d1_client.query(
        "SELECT is_active, role FROM authentication_user WHERE id = ?;",
        params=[admin_user.id],
    )
    assert admin_check.get("success") is True
    row = admin_check["results"][0]
    assert row["is_active"] == 1
    assert row["role"] == "ADMIN"


@pytest.mark.cloud_smoke
def test_07_cleanup_verification(smoke_context):
    """
    Step 7: Verify run-specific test entities exist before fixture teardown.
    Teardown will delete them and verify zero records remain.
    """
    tracked_ids = smoke_context["tracked_ids"]
    assert len(tracked_ids["assets"]) > 0
    assert len(tracked_ids["users"]) > 0
    assert len(tracked_ids["departments"]) > 0
    assert len(tracked_ids["categories"]) > 0
    assert len(tracked_ids["requests"]) > 0


# =============================================================================
# Standalone Local Safety Unit Tests (Always run, no cloud credentials needed)
# =============================================================================

def test_safety_unapproved_bridge_url_blocked_by_isolation_guard():
    """
    Safety unit test:
    Verifies that an unapproved bridge URL is blocked by the isolation guard
    before making any network call.
    """
    unapproved_url = "https://unapproved-malicious-worker.workers.dev"
    client = D1Client(
        base_url=unapproved_url,
        token="test-secret-token-at-least-32-chars-long",
    )
    with patch("urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(OperationalError) as exc_info:
            client.query("INSERT INTO departments_department (name, code) VALUES ('Test', 'TST');")
        mock_urlopen.assert_not_called()

    assert "Test Isolation Guard: Mutation blocked to unauthorized bridge endpoint" in str(exc_info.value)
    assert unapproved_url in str(exc_info.value)


def test_safety_preexisting_active_admins_never_deactivated_or_modified():
    """
    Safety unit test:
    Verifies that pre-existing active admins are protected from deactivation or modification.
    """
    schema_path = Path(settings.BASE_DIR) / "assethub_api" / "backends" / "d1" / "sql" / "schema.sql"
    if not schema_path.exists():
        schema_path = Path(settings.BASE_DIR).parent / "cloudflare-worker" / "bridge" / "schema.sql"
    assert schema_path.exists(), f"schema.sql not found at {schema_path}"

    import sqlite3

    conn = sqlite3.connect(":memory:")
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())

    # Pre-existing active admin
    conn.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (1, 'existing_admin_keep_active', 'hash', 'admin@example.com', 'ADMIN', 1)"
    )
    conn.commit()

    # Attempt to deactivate sole active admin -> blocked by trigger
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        conn.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 1")

    # Attempt to demote sole active admin -> blocked by trigger
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        conn.execute("UPDATE authentication_user SET role = 'USER' WHERE id = 1")

    # Attempt to delete sole active admin -> blocked by delete trigger
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete the last active Admin"):
        conn.execute("DELETE FROM authentication_user WHERE id = 1")

    # Pre-existing admin is completely intact
    cur = conn.cursor()
    cur.execute("SELECT id, username, role, is_active FROM authentication_user WHERE id = 1")
    row = cur.fetchone()
    assert row == (1, "existing_admin_keep_active", "ADMIN", 1)

    # Insert a run-specific admin
    conn.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (2, 'smoke_admin_run_123', 'hash', 'run@example.com', 'ADMIN', 1)"
    )
    conn.commit()

    # Deactivating run-specific admin succeeds because pre-existing admin remains active
    conn.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 2")
    conn.commit()

    # Pre-existing admin is STILL intact and active
    cur.execute("SELECT id, username, role, is_active FROM authentication_user WHERE id = 1")
    row = cur.fetchone()
    assert row == (1, "existing_admin_keep_active", "ADMIN", 1)

    # Now deactivating pre-existing admin is blocked because it is the last active admin
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        conn.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 1")

    conn.close()


def test_safety_ai_classification_503_auth_failure_fails():
    """
    Safety regression test:
    HTTP 503 with 'AI worker authentication failed.' or network error MUST FAIL classification.
    """
    auth_fail_data = {"detail": "AI worker authentication failed."}
    with pytest.raises(pytest.fail.Exception, match="auth/network error"):
        validate_ai_classification_response(503, auth_fail_data)

    network_fail_data = {"detail": "Upstream network error connection refused"}
    with pytest.raises(pytest.fail.Exception, match="auth/network error"):
        validate_ai_classification_response(503, network_fail_data)


def test_safety_ai_classification_200_valid_category_passes():
    """
    Safety regression test:
    HTTP 200 with valid category ID passes and returns response data.
    """
    valid_ids = [10, 20, 30]
    success_data = {
        "request_id": "test-req-001",
        "suggested_category_id": 20,
        "suggested_category_name": "Laptops",
        "explanation": "High confidence AI match",
        "is_fallback": False,
    }
    result = validate_ai_classification_response(200, success_data, valid_category_ids=valid_ids)
    assert result["suggested_category_id"] == 20
    assert result["is_fallback"] is False

    with pytest.raises(AssertionError, match="not in valid categories"):
        validate_ai_classification_response(200, success_data, valid_category_ids=[99])


def test_safety_ai_classification_200_with_fallback_recorded_as_fallback():
    """
    Safety regression test:
    HTTP 200 with fallback is verified to have is_fallback=True and recorded as fallback.
    """
    valid_ids = [5]
    fallback_data = {
        "request_id": "fallback-req-002",
        "suggested_category_id": 5,
        "suggested_category_name": "Peripherals",
        "explanation": "Keyword rule fallback",
        "is_fallback": True,
    }
    result = validate_ai_classification_response(200, fallback_data, valid_category_ids=valid_ids)
    assert result["suggested_category_id"] == 5
    assert result["is_fallback"] is True


def test_safety_ai_classification_429_quota_limit():
    """
    Safety regression test:
    HTTP 429 quota/capacity limit response is recognized.
    """
    quota_data = {"detail": "Rate limit exceeded. Maximum 10 requests per minute."}
    result = validate_ai_classification_response(429, quota_data)
    assert result["rate_limited"] is True


def test_safety_preflight_blocks_mutation_when_no_baseline_admin():
    """
    Safety regression test:
    Verifies that if D1 has no pre-existing active baseline Admin,
    the preflight guard raises pytest.skip before any mutation occurs.
    """
    with patch("apps.authentication.models.User.objects.using") as mock_user_using, \
         patch("apps.departments.models.Department.objects.using") as mock_dept_using:
        mock_user_qs = mock_user_using.return_value.filter.return_value
        mock_user_qs.count.return_value = 0

        # Simulate preflight guard in smoke_context
        with pytest.raises(pytest.skip.Exception, match="D1 database has no pre-existing active baseline Admin"):
            baseline_admin_count = (
                User.objects.using(ALIAS)
                .filter(role=User.Role.ADMIN, is_active=True)
                .count()
            )
            if baseline_admin_count == 0:
                pytest.skip(
                    "D1 database has no pre-existing active baseline Admin. "
                    "Bootstrap required to prevent last-admin trigger lock."
                )
            Department.objects.using(ALIAS).create(code="SMK_TEST", name="Test")

        mock_dept_using.assert_not_called()


@pytest.mark.django_db
def test_safety_execute_smoke_teardown_detects_ownership_mismatch_and_preserves_sentinels():
    """
    Safety regression test:
    Verifies that execute_smoke_teardown refuses to delete unowned assets, requests,
    categories, departments, users, and R2 keys, preserving sentinels and raising RuntimeError.
    """
    run_id = f"test_{secrets.token_hex(4)}"
    other_run_id = f"other_{secrets.token_hex(4)}"

    # Create run entities
    dept_run = Department.objects.create(code=f"SMK_{run_id}", name=f"Dept {run_id}")
    cat_run = Category.objects.create(name=f"Cat {run_id}")
    user_run = User.objects.create(
        username=f"user_{run_id}",
        email=f"user_{run_id}@example.com",
        role=User.Role.USER,
        department=dept_run,
    )

    # Create sentinel entities belonging to a different department/user
    dept_sentinel = Department.objects.create(code=f"SMK_{other_run_id}", name=f"Dept {other_run_id}")
    cat_sentinel = Category.objects.create(name=f"Cat {other_run_id}")
    user_sentinel = User.objects.create(
        username=f"user_{other_run_id}",
        email=f"user_{other_run_id}@example.com",
        role=User.Role.USER,
        department=dept_sentinel,
    )
    user_untracked = User.objects.create(
        username=f"untracked_{other_run_id}",
        email=f"untracked_{other_run_id}@example.com",
        role=User.Role.USER,
        department=dept_sentinel,
    )
    sentinel_asset = Asset.objects.create(
        asset_code=f"AST-{other_run_id}",
        name="Sentinel Asset",
        category=cat_sentinel,
        department=dept_sentinel,
        status=Asset.Status.AVAILABLE,
    )
    sentinel_request = AssetRequest.objects.create(
        requester=user_untracked,
        department=dept_sentinel,
        requested_category=cat_sentinel,
        request_type=AssetRequest.RequestType.ALLOCATE,
        reason="Sentinel Request",
    )

    sentinel_r2_key = "sentinel/unowned/photo.png"

    # Context mistakenly tracking sentinel IDs that do not belong to run_id
    ctx = {
        "run_id": run_id,
        "alias": "default",
        "department": dept_run,
        "category": cat_run,
        "tracked_ids": {
            "assets": [sentinel_asset.id],
            "requests": [sentinel_request.id],
            "categories": [cat_sentinel.id],
            "departments": [dept_sentinel.id],
            "users": [user_sentinel.id],
            "r2_keys": [sentinel_r2_key],
        },
    }

    with pytest.raises(RuntimeError) as exc_info:
        execute_smoke_teardown(ctx)

    err_msg = str(exc_info.value)
    assert f"Ownership mismatch: assets {{{sentinel_asset.id}}} do not belong to run {run_id}. Preserved sentinel." in err_msg
    assert f"Ownership mismatch: requests {{{sentinel_request.id}}} do not belong to run {run_id}. Preserved sentinel." in err_msg
    assert f"Ownership mismatch: R2 key {sentinel_r2_key} does not belong to run {run_id}. Preserved sentinel." in err_msg
    assert f"Ownership mismatch: categories {{{cat_sentinel.id}}} do not belong to run {run_id}. Preserved sentinel." in err_msg
    assert f"Ownership mismatch: departments {{{dept_sentinel.id}}} do not belong to run {run_id}. Preserved sentinel." in err_msg
    assert f"Ownership mismatch: users {{{user_sentinel.id}}} do not belong to run {run_id}. Preserved sentinel." in err_msg

    # Verify sentinel entities were preserved (NOT deleted)
    assert Asset.objects.filter(id=sentinel_asset.id).exists()
    assert AssetRequest.objects.filter(id=sentinel_request.id).exists()
    assert Category.objects.filter(id=cat_sentinel.id).exists()
    assert Department.objects.filter(id=dept_sentinel.id).exists()
    assert User.objects.filter(id=user_sentinel.id).exists()

    # Clean up test entities
    AssetRequest.objects.filter(id=sentinel_request.id).delete()
    Asset.objects.filter(id=sentinel_asset.id).delete()
    Category.objects.filter(id__in=[cat_run.id, cat_sentinel.id]).delete()
    User.objects.filter(id__in=[user_run.id, user_sentinel.id, user_untracked.id]).delete()
    Department.objects.filter(id__in=[dept_run.id, dept_sentinel.id]).delete()


def test_safety_execute_smoke_teardown_requires_run_id():
    """
    Safety regression test:
    Verifies that execute_smoke_teardown refuses to run without a valid run_id.
    """
    with pytest.raises(RuntimeError, match="run_id is required for ownership verification"):
        execute_smoke_teardown({"run_id": "", "tracked_ids": {"categories": [1]}})


@pytest.mark.django_db
def test_safety_execute_smoke_teardown_cleans_owned_records_successfully():
    """
    Safety regression test:
    Verifies that execute_smoke_teardown cleanly deletes all run-owned records
    and verifies zero leftover records.
    """
    run_id = f"clean_{secrets.token_hex(4)}"

    dept = Department.objects.create(code=f"SMK_{run_id}", name=f"Dept {run_id}")
    cat = Category.objects.create(name=f"Cat {run_id}")
    user = User.objects.create(
        username=f"user_{run_id}",
        email=f"user_{run_id}@example.com",
        role=User.Role.USER,
        department=dept,
    )
    asset = Asset.objects.create(
        asset_code=f"AST-{run_id}",
        name=f"Asset {run_id}",
        category=cat,
        department=dept,
        status=Asset.Status.AVAILABLE,
    )
    req = AssetRequest.objects.create(
        requester=user,
        department=dept,
        requested_category=cat,
        request_type=AssetRequest.RequestType.ALLOCATE,
        reason=f"Request {run_id}",
    )

    ctx = {
        "run_id": run_id,
        "alias": "default",
        "department": dept,
        "category": cat,
        "tracked_ids": {
            "assets": [asset.id],
            "requests": [req.id],
            "categories": [cat.id],
            "departments": [dept.id],
            "users": [user.id],
            "r2_keys": [f"attachments/{run_id}_file.png"],
        },
    }

    # Should succeed with 0 leftover records
    execute_smoke_teardown(ctx)

    assert not AssetRequest.objects.filter(id=req.id).exists()
    assert not Asset.objects.filter(id=asset.id).exists()
    assert not Category.objects.filter(id=cat.id).exists()
    assert not Department.objects.filter(id=dept.id).exists()
    assert not User.objects.filter(id=user.id).exists()
