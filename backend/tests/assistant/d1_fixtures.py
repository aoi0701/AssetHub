import contextlib
import ipaddress
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

import pytest
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from django.db import connections

from apps.common.tests.harness_d1_target import (
    DEFAULT_PORT,
    D1TargetHarness,
    assert_bridge_owned,
    check_port_in_use,
    cleanup_persist_directory,
    get_repo_root,
)

COMMON_TEST_PASSWORD = "TestP@ssw0rd2026!"
ALLOWED_HOSTS_TARGETS = {"127.0.0.1", "localhost", "::1", "", None}


def _check_network_target(host) -> None:
    """
    Validate target host for local loopback rehearsal isolation.

    Python socket guards cover the Python runtime process only.
    Subprocesses (Node/Wrangler) are governed by env vars
    npm_config_offline=true, WRANGLER_SEND_METRICS=false,
    CLOUDFLARE_CF_FETCH_ENABLED=false, and --no-install.
    These flags disable known metadata and npm network channels in Node,
    but this is an environment policy, not an OS-level sandbox;
    zero-network is not claimed.
    """
    if host in ALLOWED_HOSTS_TARGETS:
        return
    if isinstance(host, (str, bytes)):
        h_str = host.decode("utf-8") if isinstance(host, bytes) else host
        try:
            ip = ipaddress.ip_address(h_str)
            if ip.is_loopback:
                return
        except ValueError:
            pass
    raise RuntimeError(f"Outbound network access blocked during rehearsal: {host}")


def check_rehearsal_opt_in(request=None) -> None:
    """Ensure rehearsal execution only happens with explicit opt-in."""
    is_opt_in = (
        os.environ.get("ASSETHUB_RUN_REHEARSAL") == "1"
        or os.environ.get("RUN_REHEARSAL") == "1"
    )
    if request is not None and hasattr(request, "config") and hasattr(request.config, "getoption"):
        try:
            if request.config.getoption("--rehearsal"):
                is_opt_in = True
        except (ValueError, AttributeError):
            pass
    if not is_opt_in:
        pytest.skip("Rehearsal test skipped. Opt-in with --rehearsal.")


def check_wrangler_available() -> None:
    """Ensure local wrangler binary is available without network install."""
    env = dict(os.environ)
    env["npm_config_offline"] = "true"
    env["WRANGLER_SEND_METRICS"] = "false"
    env["CLOUDFLARE_CF_FETCH_ENABLED"] = "false"
    try:
        res = subprocess.run(
            ["npx", "--no-install", "wrangler", "--version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if res.returncode != 0:
            detail = (res.stderr or res.stdout).strip()
            pytest.fail(f"Wrangler not available locally via npx --no-install or offline cache missing: {detail}")
    except Exception as exc:
        pytest.fail(f"Wrangler not available locally via npx --no-install or offline cache missing: {exc}")


def get_week5_assistant_sql_path() -> Path:
    """Find 0002_week5_assistant.sql file across known directory trees."""
    repo_root = get_repo_root()
    candidates = [
        repo_root / "backend" / "assethub_api" / "backends" / "d1" / "sql" / "0002_week5_assistant.sql",
        Path(__file__).resolve().parents[2] / "assethub_api" / "backends" / "d1" / "sql" / "0002_week5_assistant.sql",
        Path.cwd() / "backend" / "assethub_api" / "backends" / "d1" / "sql" / "0002_week5_assistant.sql",
        Path.cwd() / "assethub_api" / "backends" / "d1" / "sql" / "0002_week5_assistant.sql",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("0002_week5_assistant.sql not found in expected locations")


def apply_week5_assistant_sql(harness: D1TargetHarness) -> None:
    """Execute additive 0002_week5_assistant.sql against running D1 bridge."""
    sql_path = get_week5_assistant_sql_path()
    content = sql_path.read_text(encoding="utf-8")
    cleaned_sql = re.sub(r"--.*$", "", content, flags=re.MULTILINE)
    cleaned_sql = re.sub(r"/\*.*?\*/", "", cleaned_sql, flags=re.DOTALL)
    for statement in cleaned_sql.split(";"):
        cleaned = statement.strip()
        if cleaned:
            res = harness.query(cleaned + ";")
            if not res.get("success"):
                raise RuntimeError(f"Failed to execute SQL statement over D1 bridge: {res} for: {cleaned}")


def seed_assistant_d1_graph(using: str = "default") -> dict:
    """Seed Week 5 data graph on D1 without using Postgres test database."""
    from apps.assets.models import Asset, AssetHistoryLog
    from apps.authentication.models import User
    from apps.departments.models import Category, Department

    departments = Department.objects.using(using)
    it = departments.create(id=1, name="Phòng Công nghệ thông tin", code="IT", description="IT Dept")
    hr = departments.create(id=2, name="Phòng Nhân sự", code="HR", description="HR Dept")

    categories = Category.objects.using(using)
    laptop = categories.create(id=1, name="Laptop", description="Laptop category")
    monitor = categories.create(id=2, name="Màn hình", description="Monitor category")

    def user(uid, username, role, department, full_name):
        u = User.objects.db_manager(using).create_user(
            id=uid,
            username=username,
            email=f"{username}@assethub.local",
            role=role,
            department=department,
            full_name=full_name,
            auth_version=1,
            is_active=True,
        )
        u.set_password(COMMON_TEST_PASSWORD)
        u.save(using=using)
        return u

    admin = user(101, "w5_admin", User.Role.ADMIN, None, "Quản trị viên")
    manager_it = user(102, "w5_manager_it", User.Role.MANAGER, it, "Quản lý IT")
    manager_it_2 = user(107, "w5_manager_it_2", User.Role.MANAGER, it, "Quản lý IT 2")
    manager_hr = user(103, "w5_manager_hr", User.Role.MANAGER, hr, "Quản lý HR")
    user_it = user(104, "w5_user_it", User.Role.USER, it, "Nhân viên IT A")
    other_it_user = user(105, "w5_other_it", User.Role.USER, it, "Nhân viên IT B")
    user_hr = user(106, "w5_user_hr", User.Role.USER, hr, "Nhân viên HR A")

    def asset(aid, code, category, department, status=Asset.Status.AVAILABLE, holder=None):
        return Asset.objects.using(using).create(
            id=aid,
            asset_code=f"W5-{code}",
            name=f"{category.name} {code}",
            category=category,
            department=department,
            status=status,
            assigned_to_user=holder,
            serial_number=f"SN-W5-{code}",
            specification={},
        )

    for number in range(1, 24):
        asset(200 + number, f"IT-L{number:02d}", laptop, it)
    held_asset = asset(224, "IT-L24", laptop, it, Asset.Status.IN_USE, user_it)
    other_held_asset = asset(225, "IT-L25", laptop, it, Asset.Status.IN_USE, other_it_user)
    asset(226, "IT-M01", monitor, it, Asset.Status.DAMAGED_PENDING_REPAIR)
    for number in range(1, 3):
        asset(230 + number, f"HR-L{number:02d}", laptop, hr)
    asset(233, "HR-L03", laptop, hr, Asset.Status.IN_USE, user_hr)

    def log(item, action, from_status, to_status, from_user=None, to_user=None):
        return AssetHistoryLog.objects.using(using).create(
            asset=item,
            actor=manager_it,
            action_type=action,
            from_status=from_status,
            to_status=to_status,
            from_user=from_user,
            to_user=to_user,
            from_department=it if from_user else None,
            to_department=it if to_user else None,
            asset_code_snapshot=item.asset_code,
            asset_name_snapshot=item.name,
        )

    allocate, give_back = AssetHistoryLog.ActionType.ALLOCATE, AssetHistoryLog.ActionType.RETURN
    available, in_use = Asset.Status.AVAILABLE, Asset.Status.IN_USE
    log(held_asset, allocate, available, in_use, to_user=user_it)
    log(held_asset, give_back, in_use, available, from_user=user_it)
    log(held_asset, allocate, available, in_use, to_user=user_it)
    log(other_held_asset, allocate, available, in_use, to_user=other_it_user)

    return {
        "admin": admin,
        "manager_it": manager_it,
        "manager_it_2": manager_it_2,
        "manager_hr": manager_hr,
        "user_it": user_it,
        "user_hr": user_hr,
        "other_it_user": other_it_user,
        "it": it,
        "hr": hr,
        "laptop": laptop,
        "held_asset": held_asset,
        "password": COMMON_TEST_PASSWORD,
    }


@pytest.fixture
def assistant_d1_graph(request):
    """
    Spins up:
    1. Rehearsal opt-in check.
    2. Local wrangler preflight check (npx --no-install wrangler --version).
    3. Port collision check on 8787 (fails during opt-in if occupied by foreign process).
    4. Outbound network guard (socket.getaddrinfo, gethostbyname, gethostbyname_ex, connect, connect_ex, sendto).
    5. Subprocess monkeypatch ensuring --no-install, npm_config_offline=true, WRANGLER_SEND_METRICS=false, and CLOUDFLARE_CF_FETCH_ENABLED=false.
    6. Local Cloudflare D1 Target Harness on port 8787.
    7. Ingests schema.sql (19 tables) and 0002_week5_assistant.sql (assistant tables).
    8. Routes Django default database connection to D1 Worker Bridge.
    9. Seeds Week 5 assistant graph directly in D1.
    10. Confirms harness process is alive and verifies bridge ownership via assert_bridge_owned.
    11. Strict lifecycle cleanup & verification on teardown with outer finally protection.
    """
    check_rehearsal_opt_in(request)
    check_wrangler_available()

    port = DEFAULT_PORT
    if check_port_in_use(port=port):
        pytest.fail(f"Port {port} occupied by foreign process during opt-in rehearsal run.")

    db_blocker = None
    try:
        db_blocker = request.getfixturevalue("django_db_blocker")
    except Exception:
        pass

    blocker_ctx = db_blocker.unblock() if db_blocker else contextlib.nullcontext()

    orig_default_db = dict(settings.DATABASES.get("default", {}))
    orig_assistant_enabled = getattr(settings, "ASSISTANT_ENABLED", False)

    # Socket outbound guards
    orig_getaddrinfo = socket.getaddrinfo
    orig_gethostbyname = socket.gethostbyname
    orig_gethostbyname_ex = socket.gethostbyname_ex
    orig_socket_connect = socket.socket.connect
    orig_socket_connect_ex = socket.socket.connect_ex
    orig_socket_sendto = socket.socket.sendto
    orig_urlopen = urllib.request.urlopen

    def guarded_getaddrinfo(host, port_val, *args, **kwargs):
        _check_network_target(host)
        return orig_getaddrinfo(host, port_val, *args, **kwargs)

    def guarded_gethostbyname(hostname):
        _check_network_target(hostname)
        return orig_gethostbyname(hostname)

    def guarded_gethostbyname_ex(hostname):
        _check_network_target(hostname)
        return orig_gethostbyname_ex(hostname)

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and len(address) >= 1:
            _check_network_target(address[0])
        return orig_socket_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if isinstance(address, tuple) and len(address) >= 1:
            _check_network_target(address[0])
        return orig_socket_connect_ex(sock, address)

    def guarded_sendto(sock, *args, **kwargs):
        address = args[1] if len(args) == 2 else (args[2] if len(args) >= 3 else kwargs.get("address"))
        if isinstance(address, tuple) and len(address) >= 1:
            _check_network_target(address[0])
        return orig_socket_sendto(sock, *args, **kwargs)

    # Subprocess monkeypatch: inject --no-install, npm_config_offline=true, WRANGLER_SEND_METRICS=false, CLOUDFLARE_CF_FETCH_ENABLED=false
    orig_popen = subprocess.Popen
    orig_run = subprocess.run
    orig_wrangler_send_metrics = os.environ.get("WRANGLER_SEND_METRICS")
    orig_npm_config_offline = os.environ.get("npm_config_offline")
    orig_cf_fetch_enabled = os.environ.get("CLOUDFLARE_CF_FETCH_ENABLED")

    def patch_cmd_and_env(cmd_args, kwargs):
        if isinstance(cmd_args, (list, tuple)) and len(cmd_args) > 0 and cmd_args[0] == "npx":
            new_cmd = list(cmd_args)
            if "--no-install" not in new_cmd:
                new_cmd.insert(1, "--no-install")
            cmd_args = new_cmd
        child_env = kwargs.get("env")
        if child_env is None:
            child_env = dict(os.environ)
        else:
            child_env = dict(child_env)
        child_env["npm_config_offline"] = "true"
        child_env["WRANGLER_SEND_METRICS"] = "false"
        child_env["CLOUDFLARE_CF_FETCH_ENABLED"] = "false"
        kwargs["env"] = child_env
        return cmd_args, kwargs

    def guarded_popen(cmd_args, *pargs, **kwargs):
        cmd_args, kwargs = patch_cmd_and_env(cmd_args, kwargs)
        return orig_popen(cmd_args, *pargs, **kwargs)

    def guarded_run(cmd_args, *pargs, **kwargs):
        cmd_args, kwargs = patch_cmd_and_env(cmd_args, kwargs)
        return orig_run(cmd_args, *pargs, **kwargs)

    harness = None

    def guarded_urlopen(url_or_req, *args, **kwargs):
        if isinstance(url_or_req, str):
            req_url = url_or_req
            has_auth = False
            has_bridge_token = False
        else:
            req_url = getattr(url_or_req, "full_url", None) or getattr(url_or_req, "get_full_url", lambda: str(url_or_req))()
            headers = getattr(url_or_req, "headers", {})
            unredirected = getattr(url_or_req, "unredirected_hdrs", {})
            lower_headers = {k.lower(): v for k, v in headers.items()}
            lower_headers.update({k.lower(): v for k, v in unredirected.items()})

            has_bridge_token = bool(
                lower_headers.get("x-bridge-token")
                or (hasattr(url_or_req, "has_header") and (
                    url_or_req.has_header("X-bridge-token")
                    or url_or_req.has_header("x-bridge-token")
                    or url_or_req.has_header("X-Bridge-Token")
                ))
            )

            has_auth = bool(
                has_bridge_token
                or lower_headers.get("authorization")
                or (hasattr(url_or_req, "has_header") and (
                    url_or_req.has_header("Authorization")
                    or url_or_req.has_header("authorization")
                ))
            )
        req_url = str(req_url)

        is_bridge_target = (
            harness is not None
            and (req_url.startswith(harness.base_url) or f":{harness.port}" in req_url or ":8787" in req_url)
        )

        if has_bridge_token or (is_bridge_target and has_auth):
            if harness is None or harness.process is None:
                raise RuntimeError("Harness process is not available for D1 bridge request")
            assert_bridge_owned(harness.process, req_url)

        return orig_urlopen(url_or_req, *args, **kwargs)

    with blocker_ctx:
        try:
            try:
                # Install socket guards
                socket.getaddrinfo = guarded_getaddrinfo
                socket.gethostbyname = guarded_gethostbyname
                socket.gethostbyname_ex = guarded_gethostbyname_ex
                socket.socket.connect = guarded_connect
                socket.socket.connect_ex = guarded_connect_ex
                socket.socket.sendto = guarded_sendto
                urllib.request.urlopen = guarded_urlopen

                # Install subprocess monkeypatch & env
                subprocess.Popen = guarded_popen
                subprocess.run = guarded_run
                os.environ["npm_config_offline"] = "true"
                os.environ["WRANGLER_SEND_METRICS"] = "false"
                os.environ["CLOUDFLARE_CF_FETCH_ENABLED"] = "false"

                run_id = f"w5_{secrets.token_hex(6)}"
                harness = D1TargetHarness(port=port, run_id=run_id)
                harness.setup(clean_first=True)

                apply_week5_assistant_sql(harness)

                d1_conf = {
                    "ENGINE": "assethub_api.backends.d1",
                    "NAME": "assethub-test-d1",
                    "BRIDGE_URL": harness.base_url,
                    "BRIDGE_TOKEN": harness.token,
                    "TIMEOUT": 45.0,
                    "TIME_ZONE": None,
                    "CONN_MAX_AGE": 0,
                    "CONN_HEALTH_CHECKS": False,
                    "OPTIONS": {},
                    "AUTOCOMMIT": True,
                    "ATOMIC_REQUESTS": False,
                }

                if "default" in connections:
                    try:
                        connections["default"].close()
                    except Exception:
                        pass
                    try:
                        del connections["default"]
                    except Exception:
                        pass

                settings.DATABASES["default"] = d1_conf
                connections.databases["default"] = d1_conf

                settings.ASSISTANT_ENABLED = True

                graph = seed_assistant_d1_graph(using="default")
                graph["harness"] = harness

                # Confirm harness process is alive
                if harness.process is None or harness.process.poll() is not None:
                    rc = harness.process.poll() if harness.process else "None"
                    raise RuntimeError(f"Harness process is not alive after setup (code={rc})")

                assert_bridge_owned(harness.process, harness.query_url)

                yield graph

            finally:
                # Inner finally: harness teardown & verification
                if harness is not None:
                    proc = harness.process
                    harness.teardown()

                    # Verify owned process stopped (wait up to 5s)
                    if proc is not None:
                        stop_start = time.time()
                        while proc.poll() is None and time.time() - stop_start < 5.0:
                            time.sleep(0.1)
                        if proc.poll() is None:
                            raise RuntimeError(f"Harness process {proc.pid} still running after teardown")

                    # Verify port 8787 released (wait up to 3s)
                    port_start = time.time()
                    while check_port_in_use(port=port) and time.time() - port_start < 3.0:
                        time.sleep(0.1)
                    if check_port_in_use(port=port):
                        raise RuntimeError(f"Port {port} not released after teardown")

                    # Verify persist directory cleaned up
                    if harness.persist_dir.exists():
                        cleanup_persist_directory(harness.persist_dir, run_id=harness.run_id)
                        if harness.persist_dir.exists():
                            raise RuntimeError(f"Persist directory {harness.persist_dir} not cleaned up after teardown")
        finally:
            # Outer finally: unconditionally restore socket, subprocess, env, db, urlopen
            urllib.request.urlopen = orig_urlopen
            socket.getaddrinfo = orig_getaddrinfo
            socket.gethostbyname = orig_gethostbyname
            socket.gethostbyname_ex = orig_gethostbyname_ex
            socket.socket.connect = orig_socket_connect
            socket.socket.connect_ex = orig_socket_connect_ex
            socket.socket.sendto = orig_socket_sendto

            subprocess.Popen = orig_popen
            subprocess.run = orig_run

            if orig_wrangler_send_metrics is None:
                os.environ.pop("WRANGLER_SEND_METRICS", None)
            else:
                os.environ["WRANGLER_SEND_METRICS"] = orig_wrangler_send_metrics

            if orig_npm_config_offline is None:
                os.environ.pop("npm_config_offline", None)
            else:
                os.environ["npm_config_offline"] = orig_npm_config_offline

            if orig_cf_fetch_enabled is None:
                os.environ.pop("CLOUDFLARE_CF_FETCH_ENABLED", None)
            else:
                os.environ["CLOUDFLARE_CF_FETCH_ENABLED"] = orig_cf_fetch_enabled

            if "default" in connections:
                try:
                    connections["default"].close()
                    del connections["default"]
                except Exception:
                    pass

            settings.DATABASES["default"] = orig_default_db
            connections.databases["default"] = orig_default_db
            settings.ASSISTANT_ENABLED = orig_assistant_enabled


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    """Multi-threaded WSGI server for concurrent rehearsal HTTP requests."""
    daemon_threads = True
    allow_reuse_address = True


def find_free_port() -> int:
    """Find a free loopback port via ephemeral socket binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_http_ready(url: str, timeout: float = 10.0) -> bool:
    """Poll URL until ready (HTTP 200 OK) or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "AssetHub-D1-Test/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


@pytest.fixture
def assistant_d1_http(assistant_d1_graph):
    """
    Spins up Threaded WSGI server on free loopback port backed by D1.
    Yields dict with base_url, graph, and harness.
    Strictly verifies loopback port release on teardown with outer finally.
    """
    graph = assistant_d1_graph
    harness = graph["harness"]

    orig_allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []))
    server = None
    server_thread = None
    port = find_free_port()
    try:
        try:
            for host in ["127.0.0.1", "localhost"]:
                if host not in settings.ALLOWED_HOSTS:
                    settings.ALLOWED_HOSTS.append(host)

            app = get_wsgi_application()
            server = make_server("127.0.0.1", port, app, server_class=ThreadedWSGIServer)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()

            base_url = f"http://127.0.0.1:{port}"
            if not wait_for_http_ready(f"{base_url}/api/health/", timeout=5.0):
                raise RuntimeError(f"HTTP server at {base_url} failed readiness check")

            yield {
                "base_url": base_url,
                "graph": graph,
                "harness": harness,
            }
        finally:
            if server is not None:
                if server_thread is not None and getattr(server_thread, "ident", None) is not None and server_thread.is_alive():
                    server.shutdown()
                server.server_close()
            if server_thread is not None and getattr(server_thread, "ident", None) is not None:
                server_thread.join(timeout=5.0)

            # Verify loopback port released (wait up to 3s)
            port_start = time.time()
            while check_port_in_use(port=port) and time.time() - port_start < 3.0:
                time.sleep(0.1)
            if check_port_in_use(port=port):
                raise RuntimeError(f"HTTP server loopback port {port} not released after teardown")
    finally:
        settings.ALLOWED_HOSTS = orig_allowed_hosts
