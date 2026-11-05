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
from wsgiref.simple_server import WSGIServer, make_server

import pytest
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from django.db import connections

from apps.common.tests.harness_d1_target import D1TargetHarness

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_BRIDGE_PORT = 8787
TEST_BRIDGE_TOKEN = "test-bridge-token-with-minimum-32-chars-long"


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
