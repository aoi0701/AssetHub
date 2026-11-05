"""
Positive Local HTTP Smoke Subprocess Rehearsal Suite.

Verifies end-to-end positive proof that test_d1_cloud_smoke.py executes and PASSES
via real HTTP requests against a live Django WSGI background server backed by
a local Cloudflare D1 Target Worker Bridge running on 127.0.0.1:8787.
"""

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Generator
from unittest.mock import patch
from wsgiref.simple_server import WSGIServer, make_server

import pytest
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from django.db import connections

from apps.common.tests.harness_d1_target import D1TargetHarness

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_BRIDGE_PORT = 8787
TEST_BRIDGE_TOKEN = "test-bridge-token-with-minimum-32-chars-long"

pytestmark = pytest.mark.rehearsal


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    """Multi-threaded WSGI server for smooth concurrent test request processing."""
    daemon_threads = True
    allow_reuse_address = True


def find_free_port() -> int:
    """Find a free loopback port via ephemeral socket binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_http_ready(url: str, timeout: float = 10.0) -> bool:
    """Poll URL until 200 OK or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "AssetHub-Smoke-Harness/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


@pytest.fixture(autouse=True)
def unblock_db(django_db_blocker):
    """Ensure database access is unblocked during tests."""
    with django_db_blocker.unblock():
        yield


@pytest.fixture(scope="module")
def live_rehearsal_server(django_db_blocker) -> Generator[dict, None, None]:
    """
    Spins up:
    1. Local D1 Target Harness on port 8787 with authoritative schema.sql and baseline admin.
    2. Real WSGI HTTP server running Django on a free loopback port with USE_D1_DATABASE=True.
    Tears down all resources cleanly on completion.
    """
    with django_db_blocker.unblock():
        run_id = secrets.token_hex(6)
        harness = D1TargetHarness(
            port=TEST_BRIDGE_PORT,
            token=TEST_BRIDGE_TOKEN,
            run_id=f"smk_{run_id}",
        )
        harness.setup(clean_first=True)

        # Seed pre-existing baseline admin into D1 so last-admin trigger has active reference
        harness.query(
            """
            INSERT INTO authentication_user (
                id, username, password, email, role, is_active, is_staff, is_superuser, auth_version, date_joined
            ) VALUES (
                99999, 'cloud_baseline_admin', 'pbkdf2_sha256$test$dummy', 'baseline@assethub.local', 'ADMIN', 1, 1, 1, 1, '2026-01-01 00:00:00'
            );
            """
        )

        d1_conf = {
            "ENGINE": "assethub_api.backends.d1",
            "NAME": "assethub-test-d1",
            "BRIDGE_URL": f"http://127.0.0.1:{TEST_BRIDGE_PORT}",
            "BRIDGE_TOKEN": TEST_BRIDGE_TOKEN,
            "TIMEOUT": 45.0,
            "TIME_ZONE": None,
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "OPTIONS": {},
            "AUTOCOMMIT": True,
            "ATOMIC_REQUESTS": False,
        }

        orig_default = dict(settings.DATABASES.get("default", {}))
        orig_allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []))

        # Cloud settings neutralization (guarantee zero cloud calls in rehearsal)
        orig_r2_key = getattr(settings, "R2_ACCESS_KEY_ID", None)
        orig_r2_secret = getattr(settings, "R2_SECRET_ACCESS_KEY", None)
        orig_r2_bucket = getattr(settings, "R2_BUCKET_NAME", None)
        orig_ai_url = getattr(settings, "AI_WORKER_URL", None)
        orig_ai_token = getattr(settings, "AI_SERVICE_TOKEN", None)

        settings.R2_ACCESS_KEY_ID = None
        settings.R2_SECRET_ACCESS_KEY = None
        settings.R2_BUCKET_NAME = None
        settings.AI_WORKER_URL = None
        settings.AI_SERVICE_TOKEN = None

        # Close existing connections before redirecting to D1
        for alias in ("default", "d1_cloud_smoke"):
            if alias in connections:
                try:
                    connections[alias].close()
                except Exception:
                    pass
                try:
                    del connections[alias]
                except Exception:
                    pass

        settings.DATABASES["default"] = d1_conf
        connections.databases["default"] = d1_conf
        settings.ALLOWED_HOSTS = ["*"]

        # Ephemeral free loopback port
        free_port = find_free_port()
        application = get_wsgi_application()
        server = make_server(
            "127.0.0.1",
            free_port,
            application,
            server_class=ThreadedWSGIServer,
        )
        actual_port = server.server_port

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        # Probe health endpoint to guarantee server readiness
        ready = wait_for_http_ready(f"http://127.0.0.1:{actual_port}/api/health/", timeout=10.0)
        if not ready:
            server.shutdown()
            server.server_close()
            harness.teardown()
            raise TimeoutError(f"WSGI rehearsal server failed to start on port {actual_port}")

        context = {
            "harness": harness,
            "server": server,
            "server_thread": server_thread,
            "port": actual_port,
            "base_url": f"http://127.0.0.1:{actual_port}",
            "d1_bridge_url": f"http://127.0.0.1:{TEST_BRIDGE_PORT}",
            "d1_bridge_token": TEST_BRIDGE_TOKEN,
        }

        try:
            yield context
        finally:
            # 1. Stop WSGI server
            try:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5.0)
            except Exception:
                pass

            # 2. Teardown D1 harness
            try:
                harness.teardown()
            except Exception:
                pass

            # 3. Restore original database settings
            for alias in ("default", "d1_cloud_smoke"):
                if alias in connections:
                    try:
                        connections[alias].close()
                    except Exception:
                        pass
                    try:
                        del connections[alias]
                    except Exception:
                        pass
            if orig_default:
                settings.DATABASES["default"] = orig_default
                connections.databases["default"] = orig_default
            settings.ALLOWED_HOSTS = orig_allowed_hosts

            # 4. Restore original cloud settings
            settings.R2_ACCESS_KEY_ID = orig_r2_key
            settings.R2_SECRET_ACCESS_KEY = orig_r2_secret
            settings.R2_BUCKET_NAME = orig_r2_bucket
            settings.AI_WORKER_URL = orig_ai_url
            settings.AI_SERVICE_TOKEN = orig_ai_token


@pytest.mark.rehearsal
def test_rehearsal_negative_preflight_unbootstrapped_d1(django_db_blocker):
    """
    Negative Regression Test:
    Ensures preflight check halts immediately if D1 has no baseline admin.
    Verifies test skips cleanly without writing test entities to unbootstrapped D1.
    """
    from tests.e2e.test_d1_cloud_smoke import ALIAS, smoke_context

    with django_db_blocker.unblock():
        harness = None
        created_harness = False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            in_use = s.connect_ex(("127.0.0.1", TEST_BRIDGE_PORT)) == 0

        if not in_use:
            run_id = secrets.token_hex(6)
            harness = D1TargetHarness(
                port=TEST_BRIDGE_PORT,
                token=TEST_BRIDGE_TOKEN,
                run_id=f"smk_preflight_{run_id}",
            )
            harness.setup(clean_first=True)
            created_harness = True
        else:
            from assethub_api.backends.d1.client import D1Client
            client = D1Client(f"http://127.0.0.1:{TEST_BRIDGE_PORT}", TEST_BRIDGE_TOKEN)
            client.query("DELETE FROM authentication_user;")

        try:
            # Wire settings for D1 bridge
            d1_conf = {
                "ENGINE": "assethub_api.backends.d1",
                "NAME": "assethub-test-d1",
                "BRIDGE_URL": f"http://127.0.0.1:{TEST_BRIDGE_PORT}",
                "BRIDGE_TOKEN": TEST_BRIDGE_TOKEN,
                "TIMEOUT": 45.0,
                "AUTOCOMMIT": True,
                "ATOMIC_REQUESTS": False,
                "OPTIONS": {},
            }
            orig_alias_db = settings.DATABASES.get(ALIAS)
            settings.DATABASES[ALIAS] = d1_conf
            connections.databases[ALIAS] = d1_conf

            with patch.dict(
                os.environ,
                {
                    "RUN_CLOUD_SMOKE": "1",
                    "D1_BRIDGE_URL": f"http://127.0.0.1:{TEST_BRIDGE_PORT}",
                    "D1_BRIDGE_TOKEN": TEST_BRIDGE_TOKEN,
                },
            ):
                smoke_fn = getattr(smoke_context, "__wrapped__", smoke_context)
                gen = smoke_fn(django_db_blocker)
                with pytest.raises(pytest.skip.Exception) as exc_info:
                    next(gen)

                assert "no pre-existing active baseline Admin" in str(exc_info.value)

            # Verify D1 remains completely empty (no departments or users created)
            from assethub_api.backends.d1.client import D1Client
            client = D1Client(f"http://127.0.0.1:{TEST_BRIDGE_PORT}", TEST_BRIDGE_TOKEN)
            user_res = client.query("SELECT count(*) as cnt FROM authentication_user;")
            dept_res = client.query("SELECT count(*) as cnt FROM departments_department;")
            cat_res = client.query("SELECT count(*) as cnt FROM departments_category;")

            assert user_res["results"][0]["cnt"] == 0
            assert dept_res["results"][0]["cnt"] == 0
            assert cat_res["results"][0]["cnt"] == 0

        finally:
            if orig_alias_db:
                settings.DATABASES[ALIAS] = orig_alias_db
                connections.databases[ALIAS] = orig_alias_db
            else:
                settings.DATABASES.pop(ALIAS, None)
                connections.databases.pop(ALIAS, None)

            if created_harness and harness:
                harness.teardown()
            elif in_use:
                client.query(
                    """
                    INSERT INTO authentication_user (
                        id, username, password, email, role, is_active, is_staff, is_superuser, auth_version, date_joined
                    ) VALUES (
                        99999, 'cloud_baseline_admin', 'pbkdf2_sha256$test$dummy', 'baseline@assethub.local', 'ADMIN', 1, 1, 1, 1, '2026-01-01 00:00:00'
                    );
                    """
                )


@pytest.mark.rehearsal
def test_rehearsal_negative_cleanup_sentinel_preservation(live_rehearsal_server):
    """
    Negative Regression Test:
    Ensures execute_smoke_teardown NEVER deletes sentinel assets not owned by the run.
    Detects ownership mismatch and leaves the sentinel record intact in D1.
    """
    from tests.e2e.test_d1_cloud_smoke import execute_smoke_teardown

    harness: D1TargetHarness = live_rehearsal_server["harness"]

    # 1. Insert sentinel department 77, category 77, and asset 77 directly into D1
    harness.query(
        """
        INSERT OR REPLACE INTO departments_department (id, name, code, description)
        VALUES (77, 'Sentinel Department 77', 'SENTINEL_77', 'Sentinel Dept');

        INSERT OR REPLACE INTO departments_category (id, name, description)
        VALUES (77, 'Sentinel Category 77', 'Sentinel Cat');

        INSERT OR REPLACE INTO assets_asset (
            id, asset_code, name, category_id, department_id, serial_number, specification, status
        ) VALUES (
            77, 'AST-SENTINEL-77', 'Sentinel Asset 77', 77, 77, 'SN-77', '{}', 'AVAILABLE'
        );
        """
    )

    try:
        # 2. Construct mock smoke context where tracked_ids has asset 77, but run_id is 'sentinel_test'
        # and department/category point to run-specific IDs (e.g. 9999) rather than 77
        mock_ctx = {
            "run_id": "sentinel_test",
            "alias": "default",
            "tracked_ids": {
                "requests": [],
                "assets": [77],
                "categories": [],
                "departments": [],
                "users": [],
                "r2_keys": [],
            },
            "department": {"id": 9999, "code": "SMK_sentinel_test"},
            "category": {"id": 9999, "name": "SmokeCat_sentinel_test"},
        }

        # 3. Call execute_smoke_teardown(mock_ctx)
        with pytest.raises(RuntimeError) as exc_info:
            execute_smoke_teardown(mock_ctx)

        assert "Ownership mismatch" in str(exc_info.value)
        assert "77" in str(exc_info.value)

        # 4. Assert sentinel asset 77 STILL EXISTS in D1!
        res = harness.query("SELECT id, asset_code, name FROM assets_asset WHERE id = 77;")
        assert res.get("success") is True
        assert len(res.get("results", [])) == 1
        assert res["results"][0]["id"] == 77
        assert res["results"][0]["asset_code"] == "AST-SENTINEL-77"
    finally:
        # Clean up sentinel records cleanly afterwards
        harness.query(
            "DELETE FROM assets_asset WHERE id = 77; "
            "DELETE FROM departments_category WHERE id = 77; "
            "DELETE FROM departments_department WHERE id = 77;"
        )


@pytest.mark.rehearsal
def test_local_http_smoke_subprocess_rehearsal(live_rehearsal_server):
    """
    Execute pytest subprocess on backend/tests/e2e/test_d1_cloud_smoke.py
    against live local WSGI HTTP server backed by Cloudflare D1 bridge.
    Assert subprocess exits with code 0!
    """
    server_port = live_rehearsal_server["port"]
    bridge_url = live_rehearsal_server["d1_bridge_url"]
    bridge_token = live_rehearsal_server["d1_bridge_token"]

    env = os.environ.copy()
    env.update(
        {
            "RUN_CLOUD_SMOKE": "1",
            "CLOUD_API_BASE_URL": f"http://127.0.0.1:{server_port}",
            "D1_BRIDGE_URL": bridge_url,
            "D1_BRIDGE_SECRET_TOKEN": bridge_token,
            "D1_BRIDGE_TOKEN": bridge_token,
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5433",
            "PYTHONPATH": str(BACKEND_DIR),
        }
    )
    # Explicitly wipe R2 and AI environment variables (Zero-Cloud guarantee)
    for cloud_key in [
        "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ENDPOINT_URL",
        "AI_WORKER_URL", "AI_SERVICE_TOKEN",
    ]:
        env[cloud_key] = ""

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/e2e/test_d1_cloud_smoke.py",
        "-v",
        "-s",
    ]

    result = subprocess.run(
        cmd,
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )

    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    assert result.returncode == 0, (
        f"Smoke test rehearsal subprocess failed with return code {result.returncode}!\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    # Positive assertions: Verify critical steps passed
    assert "test_01_bridge_health PASSED" in result.stdout
    assert "test_02_jwt_auth_login PASSED" in result.stdout
    assert "test_03_d1_asset_request_workflow PASSED" in result.stdout
    assert "test_06_d1_last_admin_protection_trigger PASSED" in result.stdout
    assert "test_07_cleanup_verification PASSED" in result.stdout
