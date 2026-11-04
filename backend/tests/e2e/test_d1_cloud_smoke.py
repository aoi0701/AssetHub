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
def unblock_db(django_db_blocker):
    """Ensure database access is unblocked during tests."""
    with django_db_blocker.unblock():
        yield


@pytest.fixture(scope="module")
def smoke_context() -> Generator[Dict[str, Any], None, None]:
    """
    Module fixture setting up Cloud D1 connection and test entities.
    Strictly gated by RUN_CLOUD_SMOKE=1.
    Performs deterministic teardown with deletion verification.
    """
    if os.environ.get("RUN_CLOUD_SMOKE") != "1":
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
    created_entities: Dict[str, list] = {
        "department_ids": [],
        "category_ids": [],
        "user_ids": [],
        "asset_ids": [],
        "request_ids": [],
        "history_ids": [],
    }

    d1_client = D1Client(base_url=bridge_url, token=bridge_token, timeout=30.0)

    # Seed initial entities directly in D1
    dept_code = f"SMK_{run_id}"
    dept = Department.objects.using(ALIAS).create(
        code=dept_code,
        name=f"Smoke Dept {run_id}",
        description="Smoke test department",
    )
    created_entities["department_ids"].append(dept.id)

    cat = Category.objects.using(ALIAS).create(
        name=f"SmokeCat_{run_id}",
        description="Smoke test category",
    )
    created_entities["category_ids"].append(cat.id)

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
    created_entities["user_ids"].append(admin_user.id)

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
    created_entities["user_ids"].append(req_user.id)

    ctx = {
        "run_id": run_id,
        "bridge_url": bridge_url,
        "bridge_token": bridge_token,
        "cloud_api_base_url": CLOUD_API_BASE_URL,
        "alias": ALIAS,
        "created": created_entities,
        "d1_client": d1_client,
        "department": dept,
        "category": cat,
        "admin_user": admin_user,
        "admin_password": admin_password,
        "req_user": req_user,
        "req_password": req_password,
    }

    try:
        yield ctx
    finally:
        cleanup_failures = []
        try:
            with connections[ALIAS].cursor() as cursor:
                # 1. Audit logs
                if created_entities["history_ids"]:
                    h_ids = ",".join(str(i) for i in created_entities["history_ids"])
                    cursor.execute(f"DELETE FROM assets_assethistorylog WHERE id IN ({h_ids});")
                if created_entities["asset_ids"]:
                    a_ids = ",".join(str(i) for i in created_entities["asset_ids"])
                    cursor.execute(f"DELETE FROM assets_assethistorylog WHERE asset_id IN ({a_ids});")

                # 2. Requests
                if created_entities["request_ids"]:
                    r_ids = ",".join(str(i) for i in created_entities["request_ids"])
                    cursor.execute(
                        f"DELETE FROM requests_assetrequest WHERE id IN ({r_ids}) AND request_code LIKE %s;",
                        [f"REQ-SMK-{run_id}%"],
                    )

                # 3. Assets
                if created_entities["asset_ids"]:
                    a_ids = ",".join(str(i) for i in created_entities["asset_ids"])
                    cursor.execute(
                        f"DELETE FROM assets_asset WHERE id IN ({a_ids}) AND asset_code LIKE %s;",
                        [f"AST-SMK-{run_id}%"],
                    )

                # 4. Users (never mutate pre-existing users, only delete tracked IDs with run prefix)
                if created_entities["user_ids"]:
                    u_ids = ",".join(str(i) for i in created_entities["user_ids"])
                    cursor.execute(
                        f"DELETE FROM authentication_user WHERE id IN ({u_ids}) AND username LIKE %s;",
                        [f"smoke_%_{run_id}"],
                    )

                # 5. Categories
                if created_entities["category_ids"]:
                    c_ids = ",".join(str(i) for i in created_entities["category_ids"])
                    cursor.execute(
                        f"DELETE FROM departments_category WHERE id IN ({c_ids}) AND name LIKE %s;",
                        [f"SmokeCat_{run_id}%"],
                    )

                # 6. Departments
                if created_entities["department_ids"]:
                    d_ids = ",".join(str(i) for i in created_entities["department_ids"])
                    cursor.execute(
                        f"DELETE FROM departments_department WHERE id IN ({d_ids}) AND code = %s;",
                        [dept_code],
                    )

                # Verify cleanup after deletion: Assert tracked records are gone from DB
                if created_entities["request_ids"]:
                    cursor.execute(f"SELECT COUNT(*) FROM requests_assetrequest WHERE id IN ({r_ids});")
                    if cursor.fetchone()[0] != 0:
                        cleanup_failures.append("requests_assetrequest records remain in DB")

                if created_entities["asset_ids"]:
                    cursor.execute(f"SELECT COUNT(*) FROM assets_asset WHERE id IN ({a_ids});")
                    if cursor.fetchone()[0] != 0:
                        cleanup_failures.append("assets_asset records remain in DB")

                if created_entities["user_ids"]:
                    cursor.execute(f"SELECT COUNT(*) FROM authentication_user WHERE id IN ({u_ids});")
                    if cursor.fetchone()[0] != 0:
                        cleanup_failures.append("authentication_user records remain in DB")

                if created_entities["category_ids"]:
                    cursor.execute(f"SELECT COUNT(*) FROM departments_category WHERE id IN ({c_ids});")
                    if cursor.fetchone()[0] != 0:
                        cleanup_failures.append("departments_category records remain in DB")

                if created_entities["department_ids"]:
                    cursor.execute(f"SELECT COUNT(*) FROM departments_department WHERE id IN ({d_ids});")
                    if cursor.fetchone()[0] != 0:
                        cleanup_failures.append("departments_department records remain in DB")

        except Exception as exc:
            cleanup_failures.append(f"Cleanup execution error: {str(exc)}")

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

        if cleanup_failures:
            raise RuntimeError(f"Cleanup verification failed: {'; '.join(cleanup_failures)}")


# =============================================================================
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
    created["asset_ids"].append(asset_id)
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
    created["request_ids"].append(request_id)

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


@pytest.mark.cloud_smoke
def test_05_workers_ai_smoke(smoke_context):
    """
    Step 5: Workers AI smoke:
    Call POST /api/ai/suggest/ with valid asset name and category prompt.
    Verify either 200 with suggested category or proper quota fallback.
    Network errors (401, 403, 404, connection failure) must NOT be counted as passing fallback!
    """
    cloud_api = smoke_context["cloud_api_base_url"]
    admin_token = smoke_context["admin_access_token"]

    ai_url = f"{cloud_api}/api/ai/suggest/"
    payload = {
        "name": "Apple MacBook Pro 16 M3 Max",
        "description": "High performance laptop for development and engineering",
    }

    status, data = make_real_http_request(
        ai_url,
        method="POST",
        json_data=payload,
        auth_token=admin_token,
    )
    if status == 404:
        ai_url = f"{cloud_api}/api/ai/suggest-category/"
        status, data = make_real_http_request(
            ai_url,
            method="POST",
            json_data=payload,
            auth_token=admin_token,
        )

    # Strictly reject network/auth/not-found errors from being treated as passing
    if status in (401, 403):
        pytest.fail(f"Workers AI smoke failed: Authentication/Permission error HTTP {status}: {data}")
    if status == 404:
        pytest.fail(f"Workers AI endpoint not found HTTP 404: {data}")

    # Permitted outcomes: 200 (with suggested category) or quota/capacity fallback (429/503)
    assert status in (200, 429, 503), f"Workers AI returned unexpected HTTP status {status}: {data}"
    if status == 200:
        assert isinstance(data, dict), f"Expected dict response, got: {data}"
        assert "suggested_category_id" in data
        assert data["suggested_category_id"] is not None


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
    created = smoke_context["created"]
    assert len(created["asset_ids"]) > 0
    assert len(created["user_ids"]) > 0
    assert len(created["department_ids"]) > 0


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
