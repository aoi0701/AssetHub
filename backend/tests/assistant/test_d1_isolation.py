"""Default safety regressions for the D1 rehearsal fixtures. No Wrangler, D1, database or external network.

These drive the real assistant_d1_graph and assistant_d1_http generators. Only the harness, schema
and seed steps are replaced, and the fake harness serves from a real child process on a real loopback
port, so assert_bridge_owned and the installed guards run for real. This proves the guards, not D1.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.db import connections

from apps.common.tests import harness_d1_target as hdt
from apps.common.tests.harness_d1_target import assert_port_available, check_port_in_use, terminate_process
from assethub_api.backends.d1.client import D1Client
from tests.assistant import d1_fixtures as fx

REAL_POPEN = subprocess.Popen
_REAL_CHECK_WRANGLER = fx.check_wrangler_available
# Read before any test runs, so a leak from an earlier test cannot become the expected value.
ORIGINAL_DATABASE = dict(settings.DATABASES["default"])
CHILD_ENV_KEYS = ("npm_config_offline", "WRANGLER_SEND_METRICS", "CLOUDFLARE_CF_FETCH_ENABLED")
BRIDGE_TOKEN = "isolation-test-token-" + "x" * 32
BLOCKED_HOSTS = ["8.8.8.8", "127.evil.tld", "127.0.0.1.evil.tld", "example.com", "::ffff:8.8.8.8"]
LOOPBACK_ADDRESSES = [
    (socket.AF_INET, ("127.0.0.1", 9)),
    (socket.AF_INET, ("localhost", 9)),
    (socket.AF_INET6, ("::1", 9)),
    (socket.AF_UNIX, "/tmp/d1-isolation-probe.sock"),
]

SERVE = """
import http.server, json, pathlib, sys
port, tokens, ready = int(sys.argv[1]), sys.argv[2], sys.argv[3]
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open(tokens, "a") as out:
            out.write(self.headers.get("X-Bridge-Token", "") + "\\n")
        body = json.dumps({"success": True, "results": []}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(("127.0.0.1", port), Handler)
pathlib.Path(ready).touch()
server.serve_forever()
"""


def raw(fixture):
    getter = getattr(fixture, "_get_wrapped_function", None)
    return getter() if getter else fixture.__wrapped__


REQUEST = SimpleNamespace(getfixturevalue=lambda name: (_ for _ in ()).throw(LookupError(name)))


def start_graph():
    generator = raw(fx.assistant_d1_graph)(REQUEST)
    return generator, next(generator)


def assert_run_fails_instead_of_skipping(match):
    # A skip is not a Failed, and pytest.raises would let it turn this test into a harmless skip.
    try:
        start_graph()
    except pytest.fail.Exception as failure:
        assert match in str(failure)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"An opted-in run must fail, but it skipped: {skipped}")
    else:
        pytest.fail("The fixture started although its preflight should have stopped it")


class Listener:
    """A foreign listener owned by the test, answering like a healthy bridge."""

    def __init__(self, port):
        self.requests = requests = []

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                requests.append({"path": self.path, "token": self.headers.get("X-Bridge-Token"),
                                 "authorization": self.headers.get("Authorization")})
                body = b'{"success": true, "results": []}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer

            def log_message(self, *args):
                pass

        self.port = port
        self.server = HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def alive(self):
        with socket.socket() as probe:
            probe.settimeout(1)
            return probe.connect_ex(("127.0.0.1", self.port)) == 0

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Everything except the guards under test: a harness whose bridge is a real child process."""
    state = SimpleNamespace(
        port=fx.find_free_port(),
        harnesses=[],
        children=[],
        tokens=tmp_path / "tokens.txt",
        setup_error=None,
        teardown_error=None,
        keep_child_running=False,
        on_teardown=None,
    )

    class ChildBackedHarness:
        def __init__(self, port, run_id):
            self.port, self.run_id = port, run_id
            self.base_url = f"http://127.0.0.1:{port}"
            self.query_url = f"{self.base_url}/api/v1/query"
            self.token = BRIDGE_TOKEN
            self.process = None
            self.persist_dir = tmp_path / "no-persist-dir"
            self.torn_down = 0
            state.harnesses.append(self)

        def setup(self, clean_first=True):
            if state.setup_error:
                raise state.setup_error
            ready = tmp_path / "ready"
            self.process = REAL_POPEN(
                [sys.executable, "-c", SERVE, str(self.port), str(state.tokens), str(ready)],
                start_new_session=True,
            )
            state.children.append(self.process)
            deadline = time.time() + 10
            while not ready.exists():
                assert time.time() < deadline, "test bridge child did not start"
                time.sleep(0.02)

        def teardown(self):
            self.torn_down += 1
            if not state.keep_child_running and self.process is not None:
                terminate_process(self.process)
            if state.on_teardown:
                state.on_teardown()
            if state.teardown_error:
                raise state.teardown_error

    state.received_tokens = lambda: state.tokens.read_text().split() if state.tokens.exists() else []
    monkeypatch.setenv("ASSETHUB_RUN_REHEARSAL", "1")
    for key in CHILD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(fx, "DEFAULT_PORT", state.port)
    monkeypatch.setattr(fx, "D1TargetHarness", ChildBackedHarness)
    monkeypatch.setattr(fx, "check_wrangler_available", lambda: None)
    monkeypatch.setattr(fx, "apply_week5_assistant_sql", lambda harness: None)
    monkeypatch.setattr(fx, "seed_assistant_d1_graph", lambda using="default": {})
    yield state
    for child in state.children:
        if child.poll() is None:
            terminate_process(child)


def global_state():
    return {
        "urlopen": urllib.request.urlopen,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
        "gethostbyname_ex": socket.gethostbyname_ex,
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "popen": subprocess.Popen,
        "run": subprocess.run,
        "env": {key: os.environ.get(key) for key in CHILD_ENV_KEYS},
        "database": dict(settings.DATABASES["default"]),
        "assistant_enabled": getattr(settings, "ASSISTANT_ENABLED", False),
    }


def assert_restored(before):
    assert global_state() == before
    assert settings.DATABASES["default"] == ORIGINAL_DATABASE
    assert connections.databases["default"]["ENGINE"] == ORIGINAL_DATABASE["ENGINE"]
    assert connections["default"].settings_dict["ENGINE"] == ORIGINAL_DATABASE["ENGINE"]


# --- 1. Socket guards installed by the real fixture -----------------------------------------

@pytest.fixture
def io_spies(monkeypatch, world):
    """Spies stand in for the real calls, so a guard that lets something through is counted, not sent."""
    calls = []

    def spy(name, result=None):
        def record(*args, **kwargs):
            calls.append(name)
            return result
        return record

    monkeypatch.setattr(socket.socket, "connect", spy("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", spy("connect_ex", 111))
    monkeypatch.setattr(socket.socket, "sendto", spy("sendto", 0))
    monkeypatch.setattr(socket, "getaddrinfo", spy("getaddrinfo", []))
    monkeypatch.setattr(socket, "gethostbyname", spy("gethostbyname", "192.0.2.1"))
    monkeypatch.setattr(socket, "gethostbyname_ex", spy("gethostbyname_ex", ("x", [], [])))
    generator, _ = start_graph()
    calls.clear()  # the preflight port probe ran before the guards existed
    yield calls
    generator.close()


def _attempts(host):
    def on_socket(method, *args):
        def attempt():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                getattr(sock, method)(*args)
        return attempt

    return {
        "connect": on_socket("connect", (host, 80)),
        "connect_ex": on_socket("connect_ex", (host, 80)),
        "sendto": on_socket("sendto", b"x", (host, 53)),
        "sendto_with_flags": on_socket("sendto", b"x", 0, (host, 53)),
        "getaddrinfo": lambda: socket.getaddrinfo(host, 80),
        "gethostbyname": lambda: socket.gethostbyname(host),
        "gethostbyname_ex": lambda: socket.gethostbyname_ex(host),
        "create_connection": lambda: socket.create_connection((host, 80), timeout=1),
    }


@pytest.mark.parametrize("operation", sorted(_attempts("x")))
@pytest.mark.parametrize("host", BLOCKED_HOSTS)
def test_installed_guards_block_external_targets_before_any_io(io_spies, operation, host):
    with pytest.raises(RuntimeError, match="Outbound network access blocked"):
        _attempts(host)[operation]()
    assert io_spies == []


@pytest.mark.parametrize("family, address", LOOPBACK_ADDRESSES, ids=["ipv4", "localhost", "ipv6", "unix"])
def test_installed_guards_let_loopback_and_unix_through(io_spies, family, address):
    with socket.socket(family) as sock:
        sock.connect(address)
    assert io_spies == ["connect"]


def test_installed_guards_let_loopback_name_lookups_and_datagrams_through(io_spies):
    socket.getaddrinfo("127.0.0.1", 80)
    socket.gethostbyname("localhost")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(b"x", ("127.0.0.1", 9))
    assert io_spies == ["getaddrinfo", "gethostbyname", "sendto"]


# --- 2. Preflight of the real fixture ------------------------------------------------------

def test_busy_port_fails_before_any_harness_or_request(world):
    foreign = Listener(world.port)
    try:
        assert_run_fails_instead_of_skipping("occupied")
        assert world.harnesses == []
        assert foreign.requests == []
        assert foreign.alive()
    finally:
        foreign.close()


def test_without_opt_in_the_fixture_skips_and_starts_nothing(world, monkeypatch):
    monkeypatch.delenv("ASSETHUB_RUN_REHEARSAL")
    monkeypatch.delenv("RUN_REHEARSAL", raising=False)
    with pytest.raises(pytest.skip.Exception):
        start_graph()
    assert world.harnesses == []


@pytest.mark.parametrize("outcome", [
    subprocess.CompletedProcess([], 1, stdout="", stderr="npx canceled due to missing packages"),
    FileNotFoundError("npx"),
    subprocess.TimeoutExpired("npx", 5),
], ids=["missing-cache", "no-npx", "timeout"])
def test_missing_wrangler_cache_fails_the_opted_in_run_instead_of_skipping(world, monkeypatch, outcome):
    def fake_run(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(fx, "check_wrangler_available", _REAL_CHECK_WRANGLER)
    assert_run_fails_instead_of_skipping("offline cache missing")
    assert world.harnesses == []


def test_preflight_helper_refuses_port_in_use_and_leaves_listener_alone():
    foreign = Listener(fx.find_free_port())
    try:
        assert check_port_in_use(port=foreign.port) is True
        with pytest.raises(RuntimeError, match="already in use"):
            assert_port_available(port=foreign.port)
        assert foreign.alive()
        assert foreign.requests == []
    finally:
        foreign.close()


# --- 3. Transport guard, using the real D1Client ---------------------------------------------

def _client_for(url):
    return D1Client(base_url=url, token=BRIDGE_TOKEN, timeout=2.0)


def test_real_client_reaches_the_owned_bridge_through_the_guard(world):
    generator, graph = start_graph()
    try:
        assert _client_for(graph["harness"].base_url).query("SELECT 1")["success"] is True
        assert world.received_tokens() == [BRIDGE_TOKEN]
    finally:
        generator.close()


def test_token_never_reaches_a_listener_that_took_over_the_bridge_port(world, monkeypatch):
    generator, graph = start_graph()
    harness = graph["harness"]
    checks = []
    real_check = fx.assert_bridge_owned
    monkeypatch.setattr(fx, "assert_bridge_owned", lambda proc, url: (checks.append(url), real_check(proc, url))[1])
    terminate_process(harness.process)
    deadline = time.time() + 5
    while check_port_in_use(port=world.port) and time.time() < deadline:
        time.sleep(0.05)
    foreign = Listener(world.port)
    try:
        with pytest.raises(Exception, match="not owned"):
            _client_for(harness.base_url).query("SELECT 1")

        assert foreign.requests == []
        assert world.received_tokens() == []
        assert len(checks) == 3, "each retry of a read must pass through the guard again"
        assert foreign.alive()
    finally:
        foreign.close()
        generator.close()


def test_token_to_any_other_local_endpoint_is_refused(world):
    """The token is the bridge's secret wherever the URL points, not only on the bridge port."""
    generator, _ = start_graph()
    other = Listener(fx.find_free_port())
    try:
        with pytest.raises(Exception, match="not owned"):
            _client_for(f"http://127.0.0.1:{other.port}").query("SELECT 1")
        assert [request["token"] for request in other.requests] == []
    finally:
        other.close()
        generator.close()


def test_requests_without_the_bridge_token_still_pass(world):
    generator, _ = start_graph()
    django_like = Listener(fx.find_free_port())
    try:
        url = f"http://127.0.0.1:{django_like.port}/api/health/"
        with urllib.request.urlopen(url, timeout=2) as response:
            assert response.status == 200
        signed_in = urllib.request.Request(url, headers={"Authorization": "Bearer user-jwt"})
        with urllib.request.urlopen(signed_in, timeout=2) as response:
            assert response.status == 200
        assert [request["authorization"] for request in django_like.requests] == [None, "Bearer user-jwt"]
    finally:
        django_like.close()
        generator.close()


# --- 4. Restoration by the real fixtures -----------------------------------------------------

@pytest.mark.parametrize("stage", ["setup", "sql", "seed", "body", "teardown"])
def test_graph_fixture_restores_everything_after_an_error(world, monkeypatch, stage):
    before = global_state()
    boom = ValueError(stage)
    if stage == "setup":
        world.setup_error = boom
    if stage == "sql":
        monkeypatch.setattr(fx, "apply_week5_assistant_sql", lambda harness: (_ for _ in ()).throw(boom))
    if stage == "seed":
        monkeypatch.setattr(fx, "seed_assistant_d1_graph", lambda using="default": (_ for _ in ()).throw(boom))
    if stage == "teardown":
        world.teardown_error = boom

    with pytest.raises(ValueError, match=stage):
        generator, _ = start_graph()
        assert settings.DATABASES["default"]["ENGINE"] == "assethub_api.backends.d1"
        assert urllib.request.urlopen is not before["urlopen"]
        if stage == "body":
            generator.throw(boom)
        generator.close()

    assert_restored(before)
    assert world.harnesses[0].torn_down == 1


def test_graph_fixture_restores_and_reports_a_child_that_will_not_stop(world):
    before = global_state()
    world.keep_child_running = True
    generator, _ = start_graph()

    with pytest.raises(RuntimeError, match="still running"):
        generator.close()

    assert_restored(before)


def test_graph_fixture_restores_and_reports_a_port_that_is_not_released(world):
    before = global_state()
    holder = {}
    world.on_teardown = lambda: holder.update(listener=_listener_after_child_stops(world.port))
    generator, _ = start_graph()
    try:
        with pytest.raises(RuntimeError, match="not released"):
            generator.close()
        assert_restored(before)
        assert holder["listener"].alive()
    finally:
        holder["listener"].close()


def _listener_after_child_stops(port):
    deadline = time.time() + 5
    while check_port_in_use(port=port) and time.time() < deadline:
        time.sleep(0.05)
    return Listener(port)


def _ok_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


@pytest.fixture
def http_world(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["only-this-host"])
    monkeypatch.setattr(fx, "get_wsgi_application", lambda: _ok_app)
    return list(settings.ALLOWED_HOSTS)


def _run_with_watchdog(action, seconds=8):
    result = {}

    def target():
        try:
            action()
        except BaseException as error:
            result["error"] = error

    runner = threading.Thread(target=target, daemon=True)
    runner.start()
    runner.join(seconds)
    assert not runner.is_alive(), "fixture hung instead of finishing"
    return result.get("error")


def test_http_fixture_serves_then_restores_hosts_and_releases_its_port(http_world):
    generator = raw(fx.assistant_d1_http)({"harness": None})
    value = next(generator)
    assert "127.0.0.1" in settings.ALLOWED_HOSTS
    port = int(value["base_url"].rsplit(":", 1)[1])
    with urllib.request.urlopen(f"{value['base_url']}/api/health/", timeout=2) as response:
        assert response.status == 200

    generator.close()

    assert settings.ALLOWED_HOSTS == http_world
    assert not check_port_in_use(port=port)


def test_http_fixture_refuses_to_start_when_not_ready(http_world, monkeypatch):
    monkeypatch.setattr(fx, "wait_for_http_ready", lambda url, timeout=10.0: False)

    with pytest.raises(RuntimeError, match="readiness"):
        next(raw(fx.assistant_d1_http)({"harness": None}))

    assert settings.ALLOWED_HOSTS == http_world


def test_http_fixture_does_not_hang_when_its_server_thread_cannot_start(http_world, monkeypatch):
    class CannotStart(threading.Thread):
        def start(self):
            raise RuntimeError("cannot start thread")

    real_thread = threading.Thread
    monkeypatch.setattr(fx.threading, "Thread", CannotStart)

    result = {}

    def attempt():
        try:
            next(raw(fx.assistant_d1_http)({"harness": None}))
        except BaseException as error:
            result["error"] = error

    runner = real_thread(target=attempt, daemon=True)
    real_thread.start(runner)
    runner.join(8)

    assert not runner.is_alive(), "server.shutdown() blocked because serve_forever never ran"
    assert "cannot start thread" in str(result["error"]), "the start failure must not be hidden by cleanup"
    assert settings.ALLOWED_HOSTS == http_world


def test_http_fixture_restores_hosts_when_cleanup_fails(http_world, monkeypatch):
    generator = raw(fx.assistant_d1_http)({"harness": None})
    next(generator)
    monkeypatch.setattr(fx, "check_port_in_use", lambda port=None: True)

    error = _run_with_watchdog(generator.close)

    assert "not released" in str(error)
    assert settings.ALLOWED_HOSTS == http_world


# --- 5. Child command and environment --------------------------------------------------------

def test_preflight_runs_wrangler_offline_without_installing(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: (
        calls.append((list(cmd), kwargs["env"])) or subprocess.CompletedProcess(cmd, 0, stdout="4", stderr="")
    ))

    _REAL_CHECK_WRANGLER()

    (cmd, env), = calls
    assert cmd[:3] == ["npx", "--no-install", "wrangler"]
    assert env["npm_config_offline"] == "true"
    assert env["WRANGLER_SEND_METRICS"] == "false"
    assert env["CLOUDFLARE_CF_FETCH_ENABLED"] == "false"


@pytest.mark.parametrize("via", ["run", "Popen"])
@pytest.mark.parametrize("caller_env", [None, {"WRANGLER_SEND_METRICS": "true", "npm_config_offline": "false"}],
                         ids=["inherited-env", "explicit-env"])
def test_children_started_under_the_fixture_are_forced_offline(world, monkeypatch, via, caller_env):
    calls = []

    def record(cmd, **kwargs):
        calls.append((list(cmd), kwargs["env"]))

    monkeypatch.setattr(subprocess, "run", record)
    monkeypatch.setattr(subprocess, "Popen", record)
    generator, _ = start_graph()
    try:
        getattr(subprocess, via)(["npx", "wrangler", "dev"], env=caller_env)
    finally:
        generator.close()

    (cmd, env), = calls
    assert cmd == ["npx", "--no-install", "wrangler", "dev"]
    assert env["npm_config_offline"] == "true"
    assert env["WRANGLER_SEND_METRICS"] == "false"
    assert env["CLOUDFLARE_CF_FETCH_ENABLED"] == "false"


# --- Helpers that are correct on their own --------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1", "", None, b"127.0.0.1"])
def test_network_target_check_allows_loopback(host):
    fx._check_network_target(host)


class _StatusHandler(BaseHTTPRequestHandler):
    status = 404

    def do_GET(self):
        self.send_response(type(self).status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("status, ready", [(404, False), (500, False), (200, True)])
def test_http_readiness_accepts_only_200(status, ready):
    handler = type("Handler", (_StatusHandler,), {"status": status})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/health/"
        assert fx.wait_for_http_ready(url, timeout=0.4) is ready
    finally:
        server.shutdown()
        server.server_close()


# --- Bootstrap: the real harness under the real fixture ---------------------------------------

SERVE_BRIDGE = SERVE.replace(
    '''self.rfile.read(int(self.headers.get("Content-Length") or 0))''',
    '''sql = self.rfile.read(int(self.headers.get("Content-Length") or 0))''',
).replace(
    '''body = json.dumps({"success": True, "results": []}).encode()''',
    '''body = (sys.argv[4] if b"sqlite_master" in sql else json.dumps({"success": True, "results": []})).encode()''',
)
COMPLETE_SCHEMA = json.dumps(
    {"success": True, "results": [{"name": name} for name in [*hdt.TARGET_TABLES, "ai_suggestion_logs"]]}
)


@pytest.fixture
def real_harness(monkeypatch, tmp_path):
    """
    assistant_d1_graph with the real D1TargetHarness.setup and start_d1_bridge. Only the Wrangler
    binary (a Python bridge child instead), DDL ingestion, schema SQL and seed are replaced.
    """
    state = SimpleNamespace(port=fx.find_free_port(), children=[], created=[], checks=[],
                            tokens=tmp_path / "tokens.txt", script=SERVE_BRIDGE, schema=COMPLETE_SCHEMA)
    state.received_tokens = lambda: state.tokens.read_text().split() if state.tokens.exists() else []

    def spawn(cmd, **kwargs):
        kwargs["cwd"] = str(tmp_path)
        child = REAL_POPEN([sys.executable, "-c", state.script, str(state.port), str(state.tokens),
                            str(tmp_path / f"ready-{len(state.children)}"), state.schema], **kwargs)
        state.children.append(child)
        return child

    class RecordedHarness(hdt.D1TargetHarness):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            state.created.append(self)

    real_owned = fx.assert_bridge_owned

    def checked(proc, url):
        state.checks.append({"proc": proc, "tokens_before": len(state.received_tokens())})
        return real_owned(proc, url)

    real_wait = hdt.wait_for_bridge_ready
    monkeypatch.setenv("ASSETHUB_RUN_REHEARSAL", "1")
    for key in CHILD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(fx, "DEFAULT_PORT", state.port)
    monkeypatch.setattr(fx, "D1TargetHarness", RecordedHarness)
    monkeypatch.setattr(fx, "check_wrangler_available", lambda: None)
    monkeypatch.setattr(fx, "apply_week5_assistant_sql", lambda harness: None)
    monkeypatch.setattr(fx, "seed_assistant_d1_graph", lambda using="default": {})
    monkeypatch.setattr(fx, "assert_bridge_owned", checked)
    monkeypatch.setattr(hdt, "ingest_d1_ddl", lambda **kwargs: None)
    monkeypatch.setattr(hdt, "wait_for_bridge_ready", lambda **kwargs: real_wait(**{**kwargs, "timeout": 3.0}))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    yield state
    for child in state.children:
        if child.poll() is None:
            terminate_process(child)


def _owned_files_gone(harness):
    return not harness.persist_dir.exists() and not harness.env_file.exists()


def test_fixture_guard_sees_the_owned_child_before_the_first_readiness_request(real_harness):
    before = global_state()
    generator, graph = start_graph()
    harness, child = graph["harness"], real_harness.children[0]
    try:
        first = real_harness.checks[0]
        assert first["proc"] is child and first["tokens_before"] == 0
        assert all(check["proc"] is child for check in real_harness.checks)
        assert harness.process is child
        assert real_harness.received_tokens(), "the owned bridge received the readiness and verify requests"
    finally:
        generator.close()

    assert child.poll() is not None
    assert _owned_files_gone(harness)
    assert_restored(before)


@pytest.mark.parametrize("script, schema, error", [
    ("import sys; sys.exit(3)", COMPLETE_SCHEMA, RuntimeError),
    ("import time; time.sleep(30)", COMPLETE_SCHEMA, TimeoutError),
    (SERVE_BRIDGE, json.dumps({"success": True, "results": []}), AssertionError),
], ids=["child-exits", "never-ready", "schema-check-fails"])
def test_failed_bootstrap_leaves_no_child_files_or_patches(real_harness, script, schema, error):
    real_harness.script, real_harness.schema = script, schema
    before = global_state()

    with pytest.raises(error):
        start_graph()

    harness = real_harness.created[0]
    assert all(child.poll() is not None for child in real_harness.children)
    assert harness.process is None
    assert _owned_files_gone(harness)
    assert_restored(before)
