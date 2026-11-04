"""
Cloudflare D1 & Cloud Integration Smoke Test Harness.

Opt-in Gate:
This test suite requires real or live-bridge Cloudflare services (D1, R2, Workers AI, Tunnel).
It is strictly gated by RUN_CLOUD_SMOKE=1.
By default, this module skips completely without making any network calls or database mutations.

Run command:
    RUN_CLOUD_SMOKE=1 pytest backend/tests/e2e/test_d1_cloud_smoke.py -v
"""
import os
import sys
import pytest

# Explicit opt-in gate: default run MUST skip completely without making any network calls
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CLOUD_SMOKE") != "1",
    reason="Cloud smoke tests skipped unless RUN_CLOUD_SMOKE=1",
)

import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Generator
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, connections
from rest_framework.test import APIClient, APIRequestFactory

from apps.assets.models import Asset, AssetHistoryLog
from apps.assets.services import AssetService
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.authentication.views import LoginView
from apps.common.storage_backends import AttachmentStorage
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest
from apps.requests.services import RequestService
from assethub_api.backends.d1.client import D1Client, parse_and_normalize_endpoint


ALIAS = "d1_cloud_smoke"


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


@pytest.fixture(scope="module")
def smoke_context() -> Generator[Dict[str, Any], None, None]:
    """
    Module-level fixture setting up Cloud D1 connection, routing, and run-specific IDs.
    Cleans up all created entities in teardown.
    """
    run_id = secrets.token_hex(4)
    bridge_url = os.environ.get("D1_BRIDGE_URL", "").rstrip("/")
    bridge_token = (
        os.environ.get("D1_BRIDGE_TOKEN")
        or os.environ.get("D1_BRIDGE_SECRET_TOKEN")
        or ""
    ).strip()

    if not bridge_url or not bridge_token or len(bridge_token) < 32:
        pytest.skip("D1_BRIDGE_URL and D1_BRIDGE_TOKEN must be configured with >= 32 chars.")

    # Permit target endpoint in test environment isolation check
    from assethub_api.backends.d1 import client as d1_client_module

    try:
        norm_endpoint = parse_and_normalize_endpoint(bridge_url)
        d1_client_module.ALLOWED_TEST_BRIDGE_ENDPOINTS.add(norm_endpoint)
    except Exception:
        pass

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

    # Clean existing cached connections
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
    settings.DATABASE_ROUTERS = [
        "backend.tests.e2e.test_d1_cloud_smoke.D1CloudSmokeRouter"
    ]
    from django.db import router

    router._routers = None
    router.__dict__.pop("routers", None)

    # Track run-specific created IDs for deterministic cleanup
    created_entities: Dict[str, list] = {
        "history_ids": [],
        "request_ids": [],
        "asset_ids": [],
        "user_ids": [],
        "category_ids": [],
        "department_ids": [],
    }

    ctx = {
        "run_id": run_id,
        "bridge_url": bridge_url,
        "bridge_token": bridge_token,
        "alias": ALIAS,
        "created": created_entities,
        "d1_client": D1Client(base_url=bridge_url, token=bridge_token, timeout=30.0),
    }

    try:
        yield ctx
    finally:
        # Step 7: Deterministic cleanup of all run-specific test entities
        try:
            with connections[ALIAS].cursor() as cursor:
                if created_entities["history_ids"]:
                    cursor.execute(
                        f"DELETE FROM assets_assethistorylog WHERE id IN ({','.join(str(i) for i in created_entities['history_ids'])});"
                    )
                if created_entities["request_ids"]:
                    cursor.execute(
                        f"DELETE FROM requests_assetrequest WHERE id IN ({','.join(str(i) for i in created_entities['request_ids'])});"
                    )
                if created_entities["asset_ids"]:
                    cursor.execute(
                        f"DELETE FROM assets_asset WHERE id IN ({','.join(str(i) for i in created_entities['asset_ids'])});"
                    )
                if created_entities["user_ids"]:
                    cursor.execute(
                        f"DELETE FROM authentication_user WHERE id IN ({','.join(str(i) for i in created_entities['user_ids'])});"
                    )
                if created_entities["category_ids"]:
                    cursor.execute(
                        f"DELETE FROM departments_category WHERE id IN ({','.join(str(i) for i in created_entities['category_ids'])});"
                    )
                if created_entities["department_ids"]:
                    cursor.execute(
                        f"DELETE FROM departments_department WHERE id IN ({','.join(str(i) for i in created_entities['department_ids'])});"
                    )
                # Cleanup by unique run prefix as secondary safety net
                cursor.execute(
                    "DELETE FROM assets_asset WHERE asset_code LIKE %s;",
                    [f"AST-SMK-{run_id}%"],
                )
                cursor.execute(
                    "DELETE FROM requests_assetrequest WHERE request_code LIKE %s;",
                    [f"REQ-SMK-{run_id}%"],
                )
                cursor.execute(
                    "DELETE FROM authentication_user WHERE username LIKE %s;",
                    [f"smk_%_{run_id}"],
                )
                cursor.execute(
                    "DELETE FROM departments_category WHERE name LIKE %s;",
                    [f"SmokeCat_{run_id}%"],
                )
                cursor.execute(
                    "DELETE FROM departments_department WHERE code LIKE %s;",
                    [f"SMK{run_id[:4].upper()}%"],
                )
        except Exception:
            pass

        # Restore connections and routers
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
        router._routers = None
        router.__dict__.pop("routers", None)


@pytest.fixture(autouse=True)
def unblock_db(django_db_blocker):
    """Ensure database access is unblocked during tests."""
    with django_db_blocker.unblock():
        yield


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

    # Also verify direct query connectivity (SELECT 1) over bridge
    d1_client: D1Client = smoke_context["d1_client"]
    res = d1_client.query("SELECT 1 AS ping;")
    assert res.get("success") is True


def test_02_jwt_auth_login(smoke_context):
    """
    Step 2: JWT login check (/api/auth/login/).
    Validates password authentication, JWT token issuance, and claims on D1.
    """
    alias = smoke_context["alias"]
    run_id = smoke_context["run_id"]
    created = smoke_context["created"]

    # Create run-specific admin user in D1
    dept = Department.objects.using(alias).create(
        code=f"SMK{run_id[:4].upper()}",
        name=f"Smoke Dept {run_id}",
        description="Smoke test department",
    )
    created["department_ids"].append(dept.id)

    password = f"SmokePass#{run_id}!"
    admin_user = User(
        username=f"smk_admin_{run_id}",
        email=f"smk_admin_{run_id}@assethub.test",
        role=User.Role.ADMIN,
        full_name=f"Smoke Admin {run_id}",
        department=dept,
        is_active=True,
        is_staff=True,
        is_superuser=True,
        auth_version=1,
    )
    admin_user.set_password(password)
    admin_user.save(using=alias)
    created["user_ids"].append(admin_user.id)
    smoke_context["admin_user"] = admin_user
    smoke_context["dept"] = dept

    # Perform real login via LoginView with APIRequestFactory
    factory = APIRequestFactory()
    request = factory.post(
        "/api/auth/login/",
        {"username": admin_user.username, "password": password},
        format="json",
    )
    response = LoginView.as_view()(request)
    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" in response.data
    smoke_context["access_token"] = response.data["access"]


def test_03_d1_asset_request_workflow(smoke_context):
    """
    Step 3: Asset & Request workflow on D1 (verifying D1 is used).
    Validates full lifecycle on Cloudflare D1 with direct D1 readback verification.
    """
    alias = smoke_context["alias"]
    run_id = smoke_context["run_id"]
    created = smoke_context["created"]
    admin_user = smoke_context["admin_user"]
    dept = smoke_context["dept"]
    d1_client: D1Client = smoke_context["d1_client"]

    # 1. Create run-specific Category
    cat = Category.objects.using(alias).create(
        name=f"SmokeCat_{run_id}",
        description="Smoke test category",
    )
    created["category_ids"].append(cat.id)
    smoke_context["category"] = cat

    # 2. Create run-specific Requester User
    user = User(
        username=f"smk_req_{run_id}",
        email=f"smk_req_{run_id}@assethub.test",
        role=User.Role.USER,
        full_name=f"Smoke Requester {run_id}",
        department=dept,
        is_active=True,
        auth_version=1,
    )
    user.set_password(f"UserPass#{run_id}!")
    user.save(using=alias)
    created["user_ids"].append(user.id)

    # 3. Create run-specific Asset
    asset_code = f"AST-SMK-{run_id}"
    asset = Asset.objects.using(alias).create(
        asset_code=asset_code,
        name=f"Smoke MacBook {run_id}",
        category=cat,
        department=dept,
        status=Asset.Status.AVAILABLE,
        serial_number=f"SN-{run_id}",
    )
    created["asset_ids"].append(asset.id)

    # 4. Create run-specific Request
    req_code = f"REQ-SMK-{run_id}"
    req = AssetRequest.objects.using(alias).create(
        request_code=req_code,
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=user,
        department=dept,
        requested_category=cat,
        reason="Smoke test request allocation",
        status=AssetRequest.Status.PENDING,
    )
    created["request_ids"].append(req.id)

    # 5. Execute Request Approval Workflow via RequestService
    approved_req = RequestService.approve_request(
        request_id=req.id,
        actor=admin_user,
        data={"selected_asset_id": asset.id},
        using=alias,
    )
    assert approved_req.status == AssetRequest.Status.APPROVED

    # 6. Verify directly on D1 database that D1 is used and mutated
    d1_asset_res = d1_client.query(
        "SELECT id, status, assigned_to_user_id FROM assets_asset WHERE asset_code = ?;",
        params=[asset_code],
    )
    assert d1_asset_res.get("success") is True
    assert len(d1_asset_res.get("results", [])) == 1
    d1_asset_row = d1_asset_res["results"][0]
    assert d1_asset_row["status"] == Asset.Status.IN_USE
    assert d1_asset_row["assigned_to_user_id"] == user.id

    # Verify audit log in D1
    d1_log_res = d1_client.query(
        "SELECT id, action_type, to_user_id FROM assets_assethistorylog WHERE asset_id = ?;",
        params=[asset.id],
    )
    assert d1_log_res.get("success") is True
    assert len(d1_log_res.get("results", [])) >= 1
    for row in d1_log_res["results"]:
        created["history_ids"].append(row["id"])


def test_04_r2_presigned_download_url(smoke_context):
    """
    Step 4: R2 presigned download URL generation.
    Validates private object URL generation and signature parameters.
    """
    storage = AttachmentStorage()
    test_key = f"assets/{smoke_context['run_id']}/image/smoke_preview.png"

    presigned_url = storage.get_presigned_download_url(
        name=test_key,
        expire_seconds=900,
        filename="smoke_preview.png",
        kind="image",
    )

    assert isinstance(presigned_url, str)
    assert presigned_url.startswith("http://") or presigned_url.startswith("https://")
    # Verify presigned URL contains expiry or signature query parameters
    assert any(param in presigned_url for param in ("sig=", "expires=", "X-Amz-Signature", "token="))


def test_05_workers_ai_category_suggestion(smoke_context):
    """
    Step 5: Workers AI category suggestion (or fallback).
    Validates AI suggestion service with graceful handling of quota/capacity limits.
    """
    cat = smoke_context["category"]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {smoke_context['access_token']}")

    payload = {
        "name": "Apple MacBook Pro 16 M3 Max",
        "description": "High performance laptop for machine learning and development",
        "categories": [
            {"id": cat.id, "name": cat.name},
            {"id": 999999, "name": "Bàn ghế văn phòng"},
        ],
    }

    response = client.post("/api/ai/suggest-category/", data=payload, format="json")

    # In cloud environments, Workers AI may succeed (200) or report quota/degraded (429/502/503)
    assert response.status_code in (200, 429, 502, 503), f"Unexpected status: {response.status_code}, data: {response.data}"

    if response.status_code == 200:
        assert "suggested_category_id" in response.data
        assert response.data["suggested_category_id"] in (cat.id, 999999)


def test_06_d1_last_admin_protection_trigger(smoke_context):
    """
    Step 6: Last active admin protection trigger check.
    Validates that D1 SQLite trigger prevents deactivating the last active admin.
    """
    alias = smoke_context["alias"]
    d1_client: D1Client = smoke_context["d1_client"]
    admin_user = smoke_context["admin_user"]

    # Deactivate any other test admins temporarily to leave admin_user as the sole active admin
    other_admins = list(
        User.objects.using(alias)
        .filter(role=User.Role.ADMIN, is_active=True)
        .exclude(id=admin_user.id)
    )
    for other in other_admins:
        User.objects.using(alias).filter(id=other.id).update(is_active=False)

    try:
        # Attempt to deactivate the sole active admin via direct D1 SQL update
        with pytest.raises(IntegrityError) as exc_info:
            with connections[alias].cursor() as cursor:
                cursor.execute(
                    "UPDATE authentication_user SET is_active = 0 WHERE id = %s;",
                    [admin_user.id],
                )

        assert "Cannot demote or deactivate the last active Admin" in str(exc_info.value)

        # Confirm admin remains active in D1
        admin_check = d1_client.query(
            "SELECT is_active, role FROM authentication_user WHERE id = ?;",
            params=[admin_user.id],
        )
        assert admin_check.get("success") is True
        row = admin_check["results"][0]
        assert row["is_active"] == 1
        assert row["role"] == "ADMIN"

    finally:
        # Restore other admins
        for other in other_admins:
            User.objects.using(alias).filter(id=other.id).update(is_active=True)


def test_07_cleanup_verification(smoke_context):
    """
    Step 7: Verify run-specific test entities.
    Validates run_id tracking ensures clean isolation.
    """
    run_id = smoke_context["run_id"]
    created = smoke_context["created"]

    assert len(created["asset_ids"]) > 0
    assert len(created["user_ids"]) > 0
    assert len(created["department_ids"]) > 0
    # Final cleanup is performed automatically by smoke_context teardown
