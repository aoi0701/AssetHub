"""
Tests for local D1 Target and Worker Bridge harness.

Validates process ownership, socket port guards, directory marker security,
and complete lifecycle management for zero-cloud D1 migration rehearsals.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import pytest

from apps.common.tests import harness_d1_target as harness_module
from apps.common.tests.harness_d1_target import (
    DEFAULT_PORT,
    DEFAULT_HOST,
    DEFAULT_TOKEN,
    TARGET_TABLES,
    D1TargetHarness,
    assert_port_available,
    assert_safe_environment,
    check_port_in_use,
    cleanup_persist_directory,
    create_env_file,
    write_run_marker,
)


@pytest.mark.rehearsal
def test_safe_environment_guard():
    """Verify isolation guards block remote or deploy flags."""
    with pytest.raises(RuntimeError, match="--remote is strictly forbidden"):
        assert_safe_environment(["wrangler", "dev", "--remote"])

    with pytest.raises(RuntimeError, match="--remote is strictly forbidden"):
        assert_safe_environment(["wrangler", "d1", "execute", "db", "-r"])

    with pytest.raises(RuntimeError, match="wrangler deploy is strictly forbidden"):
        assert_safe_environment(["wrangler", "deploy"])


@pytest.mark.rehearsal
def test_create_env_file(tmp_path):
    """Verify env file creation, token validation, and 0600 file permissions."""
    test_env = tmp_path / "test.env"
    created = create_env_file(test_env, DEFAULT_TOKEN)
    assert created.exists()
    content = created.read_text(encoding="utf-8")
    assert f"BRIDGE_SECRET_TOKEN={DEFAULT_TOKEN}" in content

    # Verify 0600 permissions (-rw-------)
    mode = created.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected file mode 0600, got {oct(mode)}"

    with pytest.raises(ValueError, match="at least 32 characters"):
        create_env_file(tmp_path / "short.env", "short_token")


@pytest.mark.rehearsal
def test_sentinel_port_in_use_refuses_to_kill():
    """
    Sentinel Server Test:
    Start a dummy socket listener on a test port.
    Attempt to start D1TargetHarness on that port.
    Assert it raises RuntimeError refusing to terminate external processes,
    AND verify the sentinel server is still alive and accepting connections.
    """
    sentinel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sentinel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sentinel_sock.bind((DEFAULT_HOST, 0))
    sentinel_sock.listen(1)
    _, sentinel_port = sentinel_sock.getsockname()

    try:
        harness = D1TargetHarness(port=sentinel_port)
        expected_msg = (
            f"Port {sentinel_port} on {DEFAULT_HOST} is already in use by another process. "
            "Refusing to terminate external process."
        )

        with pytest.raises(RuntimeError) as exc_info:
            harness.setup()

        assert expected_msg in str(exc_info.value)

        # Assert sentinel server was NOT killed and is still accepting connections
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1.0)
            assert probe.connect_ex((DEFAULT_HOST, sentinel_port)) == 0, (
                "Sentinel socket was killed or stopped listening!"
            )
    finally:
        sentinel_sock.close()


@pytest.mark.rehearsal
def test_marker_validation_refuses_delete(tmp_path):
    """
    Marker Validation Test:
    Attempt cleanup on directories without run_marker.json, with mismatched run_id,
    or outside /tmp. Assert it refuses to delete.
    Only deletes when path is under /tmp and marker matches run_id.
    """
    run_id_a = "run_alpha_123"
    run_id_b = "run_beta_456"

    # 1. Directory without marker file
    dir_no_marker = Path(f"/tmp/assethub_rehearsal_d1_{run_id_a}_nomarker")
    dir_no_marker.mkdir(parents=True, exist_ok=True)
    try:
        # Returns False without raise_on_error
        assert cleanup_persist_directory(dir_no_marker, run_id=run_id_a) is False
        assert dir_no_marker.exists(), "Directory without marker must NOT be deleted"

        # Raises RuntimeError with raise_on_error=True
        with pytest.raises(RuntimeError, match="Missing run_marker.json"):
            cleanup_persist_directory(dir_no_marker, run_id=run_id_a, raise_on_error=True)
        assert dir_no_marker.exists()
    finally:
        if dir_no_marker.exists():
            dir_no_marker.rmdir()

    # 2. Directory with mismatched run_id in marker
    dir_mismatched = Path(f"/tmp/assethub_rehearsal_d1_{run_id_a}_mismatch")
    dir_mismatched.mkdir(parents=True, exist_ok=True)
    try:
        write_run_marker(dir_mismatched, run_id=run_id_a, pid=99999)

        # Attempt deletion with different run_id
        assert cleanup_persist_directory(dir_mismatched, run_id=run_id_b) is False
        assert dir_mismatched.exists(), "Directory with mismatched run_id must NOT be deleted"

        with pytest.raises(RuntimeError, match="does not match expected run_id"):
            cleanup_persist_directory(dir_mismatched, run_id=run_id_b, raise_on_error=True)
        assert dir_mismatched.exists()
    finally:
        marker_file = dir_mismatched / "run_marker.json"
        if marker_file.exists():
            marker_file.unlink()
        if dir_mismatched.exists():
            dir_mismatched.rmdir()

    # 3. Arbitrary directory outside /tmp
    dir_outside_tmp = tmp_path / "assethub_rehearsal_d1_outside"
    dir_outside_tmp.mkdir(parents=True, exist_ok=True)
    write_run_marker(dir_outside_tmp, run_id=run_id_a, pid=99999)
    # tmp_path is inside /tmp on many systems or /home/truong; if inside /tmp test custom path
    arbitrary_dir = Path("/var/tmp/assethub_rehearsal_d1_arbitrary") if Path("/var/tmp").resolve() != Path("/tmp").resolve() else Path("/tmp/other_prefix_test")
    arbitrary_dir.mkdir(parents=True, exist_ok=True)
    try:
        assert cleanup_persist_directory(arbitrary_dir, run_id=run_id_a) is False
        assert arbitrary_dir.exists(), "Arbitrary path must NOT be deleted"
    finally:
        if arbitrary_dir.exists():
            arbitrary_dir.rmdir()

    # 4. Valid directory with matching run_id -> Clean deletion succeeds
    dir_valid = Path(f"/tmp/assethub_rehearsal_d1_{run_id_a}_valid")
    dir_valid.mkdir(parents=True, exist_ok=True)
    write_run_marker(dir_valid, run_id=run_id_a, pid=12345)
    assert cleanup_persist_directory(dir_valid, run_id=run_id_a) is True
    assert not dir_valid.exists(), "Valid directory must be cleanly removed"


@pytest.mark.rehearsal
def test_d1_target_harness_lifecycle():
    """
    Lifecycle Test:
    Normal start and clean teardown leaves port free and deletes only its own directory.
    Verifies sentinel directory belonging to another run is untouched.
    """
    sentinel_other_dir = Path("/tmp/assethub_rehearsal_d1_sentinel_other_run")
    sentinel_other_dir.mkdir(parents=True, exist_ok=True)
    write_run_marker(sentinel_other_dir, run_id="sentinel_other_run", pid=88888)

    try:
        with D1TargetHarness() as harness:
            assert harness.process is not None
            assert harness.process.poll() is None
            assert harness.proc is not None  # alias check

            # Verify run marker was created with matching run_id and pid
            marker_file = harness.persist_dir / "run_marker.json"
            assert marker_file.exists()
            marker_data = json.loads(marker_file.read_text(encoding="utf-8"))
            assert marker_data["run_id"] == harness.run_id
            assert marker_data["pid"] == harness.process.pid

            # Verify env file created with mode 0600
            assert harness.env_file.exists()
            assert (harness.env_file.stat().st_mode & 0o777) == 0o600

            # Verify tables and clean state
            counts = harness.verify_clean()
            assert len(counts) >= 17
            assert "ai_suggestion_logs" in counts
            for tbl in TARGET_TABLES:
                assert tbl in counts
                assert counts[tbl] == 0

            # Query execution test
            res = harness.query("SELECT 100 as val;")
            assert res.get("success") is True
            assert res.get("results") == [{"val": 100}]

            # Capture paths for post-context validation
            persist_dir = harness.persist_dir
            env_file = harness.env_file
            port = harness.port
            host = harness.host

        # Post-teardown assertions
        # 1. Port is free
        assert not check_port_in_use(host, port), f"Port {port} must be free after teardown"

        # 2. Persist dir and env file cleaned up
        assert not persist_dir.exists(), f"Persist dir was not cleaned: {persist_dir}"
        assert not env_file.exists(), f"Env file was not cleaned: {env_file}"

        # 3. Other run directory was NOT deleted
        assert sentinel_other_dir.exists(), "Teardown must only delete its own directory!"
    finally:
        # Clean sentinel other directory
        cleanup_persist_directory(sentinel_other_dir, run_id="sentinel_other_run")


@pytest.mark.rehearsal
def test_foreign_marker_and_sentinel_never_overwritten_or_deleted():
    """
    Regression Test 2 (D1 Target):
    Preflight check prevents overwriting or deleting foreign directories.
    If persist_dir exists with foreign marker and sentinel files:
    - setup() raises RuntimeError immediately.
    - foreign marker is NEVER overwritten with new run_id.
    - sentinel file is NEVER deleted.
    - teardown() refuses to delete foreign directory.
    """
    foreign_run_id = "foreign_run_sentinel_123"
    our_run_id = "our_new_run_456"

    foreign_dir = Path(f"/tmp/assethub_rehearsal_d1_{foreign_run_id}")
    foreign_dir.mkdir(parents=True, exist_ok=True)
    write_run_marker(foreign_dir, run_id=foreign_run_id, pid=77777)

    sentinel_file = foreign_dir / "sentinel_payload.txt"
    sentinel_file.write_text("CRITICAL_FOREIGN_REHEARSAL_DATA", encoding="utf-8")

    try:
        harness = D1TargetHarness(run_id=our_run_id, persist_dir=foreign_dir)

        # 1. setup() must fail with clear ownership preflight error
        with pytest.raises(RuntimeError, match="is owned by foreign run"):
            harness.setup(clean_first=True)

        # 2. Marker must still belong to foreign_run_id (never overwritten)
        marker_data = json.loads((foreign_dir / "run_marker.json").read_text(encoding="utf-8"))
        assert marker_data["run_id"] == foreign_run_id

        # 3. Sentinel file must still exist with unchanged contents
        assert sentinel_file.exists()
        assert sentinel_file.read_text(encoding="utf-8") == "CRITICAL_FOREIGN_REHEARSAL_DATA"

        # 4. teardown() on the new harness must not delete the foreign directory
        harness.teardown()
        assert foreign_dir.exists()
        assert sentinel_file.exists()
    finally:
        if sentinel_file.exists():
            sentinel_file.unlink()
        cleanup_persist_directory(foreign_dir, run_id=foreign_run_id)


@pytest.mark.rehearsal
def test_d1_setup_failure_self_cleans_resources():
    """
    Regression Test 3 (D1 Target):
    Verify that if setup fails at any step (e.g., DDL ingest, bridge start, verify_clean),
    D1TargetHarness.setup() automatically self-cleans its own persist directory, env file,
    and process, and re-raises the original exception without leaving behind leaked resources.
    """
    run_id = "d1_setup_fail_self_clean_test"
    harness = D1TargetHarness(run_id=run_id)

    # Mock ingest_d1_ddl to raise an unexpected failure
    with mock.patch("apps.common.tests.harness_d1_target.ingest_d1_ddl", side_effect=RuntimeError("Simulated DDL crash")):
        with pytest.raises(RuntimeError, match="Simulated DDL crash"):
            harness.setup()

    # Assert persist_dir was cleanly deleted during self-cleanup on failure
    assert not harness.persist_dir.exists(), (
        f"Persist directory {harness.persist_dir} must be cleaned up on setup failure!"
    )
    # Assert env_file was cleanly unlinked
    assert not harness.env_file.exists(), (
        f"Env file {harness.env_file} must be cleaned up on setup failure!"
    )
    assert harness.process is None


@pytest.mark.rehearsal
def test_foreign_env_file_preflight_refuses_overwrite():
    """
    Regression Test 2b & P2 (Env File):
    Preflight check refuses to overwrite an environment file that does not belong to run_id.
    Calling teardown() (even multiple times) NEVER deletes the foreign file, preserving its contents.
    """
    foreign_run_id = "foreign_env_run_123"
    our_run_id = "our_env_run_456"

    foreign_env = Path(f"/tmp/assethub_rehearsal_d1_{foreign_run_id}.env")
    foreign_env.write_text("BRIDGE_SECRET_TOKEN=foreign_secret_token_value_32ch\n", encoding="utf-8")

    try:
        harness = D1TargetHarness(run_id=our_run_id, env_file=foreign_env)
        with pytest.raises(RuntimeError, match="not owned by run"):
            harness.setup()

        # Foreign env file contents must be preserved
        assert foreign_env.exists()
        assert "foreign_secret_token_value_32ch" in foreign_env.read_text(encoding="utf-8")

        # Calling teardown() once must NOT delete foreign env
        harness.teardown()
        assert foreign_env.exists(), "Foreign env file was deleted by teardown()!"
        assert "foreign_secret_token_value_32ch" in foreign_env.read_text(encoding="utf-8")

        # Calling teardown() a second time (idempotency) must STILL not delete foreign env
        harness.teardown()
        assert foreign_env.exists(), "Foreign env file was deleted by second teardown()!"
        assert "foreign_secret_token_value_32ch" in foreign_env.read_text(encoding="utf-8")
    finally:
        if foreign_env.exists():
            foreign_env.unlink()


# ===========================================================================
# Bridge ownership: a real child and real loopback sockets, no Wrangler or D1
# ===========================================================================

BRIDGE_TOKEN = "ownership-test-token-" + "x" * 32

SERVE = """
import http.server, sys
port, log = int(sys.argv[1]), sys.argv[2]
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open(log, "a") as out:
            out.write(self.headers.get("X-Bridge-Token", "") + "\\n")
        body = b'{"success": true, "results": []}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""
SERVE_FROM_DESCENDANT = """
import os, sys, time
new_session = sys.argv[4] == "new-session"
child = os.fork()
if child:
    time.sleep(30)
    os._exit(0)
if new_session:
    os.setsid()
with open(sys.argv[3], "w") as out:
    out.write(str(os.getpid()))
sys.argv = sys.argv[:3]
""" + SERVE


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _foreign_listener(port, address="127.0.0.1"):
    """A listener this test owns, answering like a healthy bridge so that only ownership can refuse it."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append(self.headers.get("X-Bridge-Token"))
            body = b'{"success": true, "results": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer((address, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def _still_accepting(port, address="127.0.0.1"):
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex((address, port)) == 0


def _wait_gone(pid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    """start_d1_bridge with Wrangler replaced by a real child process running the given script."""
    real_popen = subprocess.Popen
    spawned = []
    last_call = {}

    class Bridge:
        port = _free_port()
        token_log = tmp_path / "tokens.txt"
        pid_file = tmp_path / "descendant.pid"
        children = spawned
        captured = last_call

        def use(self, script, *args):
            """Every Wrangler launch from now on starts this script instead."""
            def spawn(cmd, **kwargs):
                last_call.update(cmd=list(cmd), env=kwargs.get("env"))
                kwargs["cwd"] = str(tmp_path)
                child = real_popen(
                    [sys.executable, "-c", script, str(self.port), str(self.token_log), *args], **kwargs
                )
                spawned.append(child)
                return child

            monkeypatch.setattr(harness_module.subprocess, "Popen", spawn)

        def start(self, script, *args, timeout=8.0, **options):
            self.use(script, *args)
            return harness_module.start_d1_bridge(
                port=self.port,
                persist_dir=tmp_path / "persist",
                env_file=tmp_path / "bridge.env",
                token=BRIDGE_TOKEN,
                bridge_worker_dir=tmp_path,
                timeout=timeout,
                **options,
            )

        def tokens_received(self):
            return self.token_log.read_text().split() if self.token_log.exists() else []

    yield Bridge()

    for child in spawned:
        if child.poll() is None:
            harness_module.terminate_process(child)
    if Bridge.pid_file.exists():
        try:
            os.kill(int(Bridge.pid_file.read_text()), 9)
        except ProcessLookupError:
            pass


def _lose_race_to(monkeypatch, port, address="127.0.0.1"):
    """The foreign listener takes the port right after the availability check has passed."""
    real_check = harness_module.assert_port_available
    state = {}

    def check_then_lose(host="127.0.0.1", port=port):
        real_check(host=host, port=port)
        state["server"], state["seen"] = _foreign_listener(port, address)

    monkeypatch.setattr(harness_module, "assert_port_available", check_then_lose)
    return state


def test_owned_child_is_accepted_and_receives_the_token(bridge):
    child = bridge.start(SERVE)

    assert child.poll() is None
    assert bridge.tokens_received() == [BRIDGE_TOKEN]


def test_listener_in_child_process_group_is_owned_and_stopped_with_it(bridge):
    child = bridge.start(SERVE_FROM_DESCENDANT, str(bridge.pid_file), "same-group")
    descendant = int(bridge.pid_file.read_text())

    assert bridge.tokens_received() == [BRIDGE_TOKEN]

    harness_module.terminate_process(child)
    assert child.poll() is not None
    assert _wait_gone(descendant)
    assert not _still_accepting(bridge.port)


def test_listener_outside_child_process_group_is_refused(bridge):
    with pytest.raises(RuntimeError, match="not owned"):
        bridge.start(SERVE_FROM_DESCENDANT, str(bridge.pid_file), "new-session")

    assert bridge.tokens_received() == []
    assert bridge.children[0].poll() is not None


def test_foreign_listener_before_the_check_blocks_the_start(bridge):
    server, seen = _foreign_listener(bridge.port)
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            bridge.start(SERVE)

        assert bridge.children == []
        assert seen == []
        assert _still_accepting(bridge.port)
    finally:
        server.shutdown()
        server.server_close()


def test_foreign_listener_after_the_check_gets_no_token_and_is_left_alone(bridge, monkeypatch):
    state = _lose_race_to(monkeypatch, bridge.port)
    try:
        with pytest.raises(RuntimeError, match="not owned"):
            bridge.start("import time; time.sleep(30)")

        assert state["seen"] == []
        assert _still_accepting(bridge.port)
        assert bridge.children[0].poll() is not None
    finally:
        state["server"].shutdown()
        state["server"].server_close()


def test_foreign_listener_on_another_address_of_the_port_is_refused(bridge, monkeypatch):
    # Bound to a different loopback address, so it can sit beside an owned listener on this port.
    server, seen = _foreign_listener(bridge.port, address="127.0.0.2")
    monkeypatch.setattr(harness_module, "assert_port_available", lambda host, port: None)
    try:
        with pytest.raises(RuntimeError, match="not owned"):
            bridge.start(SERVE)

        assert seen == []
        assert bridge.tokens_received() == []
        assert _still_accepting(bridge.port, "127.0.0.2")
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("declared_pid", [lambda: 2 ** 22 + 7, os.getpid, lambda: os.getpgid(0)],
                         ids=["no-such-process", "this-process", "this-process-group-leader"])
def test_object_only_claiming_a_pid_is_not_an_owned_child(bridge, monkeypatch, tmp_path, declared_pid):
    """poll() returning None and a pid, even the pid that really holds the listener, prove nothing."""

    class Claimed:
        pid = declared_pid()
        returncode = None

        def poll(self):
            return None

    server, seen = _foreign_listener(bridge.port)
    try:
        with pytest.raises(RuntimeError, match="not owned"):
            harness_module.wait_for_bridge_ready(
                proc=Claimed(), url=f"http://127.0.0.1:{bridge.port}/api/v1/query", token=BRIDGE_TOKEN, timeout=3,
            )
        assert seen == []
    finally:
        server.shutdown()
        server.server_close()


def test_readiness_without_a_child_cannot_prove_ownership(bridge):
    server, seen = _foreign_listener(bridge.port)
    try:
        with pytest.raises(RuntimeError, match="not owned"):
            harness_module.wait_for_bridge_ready(url=f"http://127.0.0.1:{bridge.port}/q", token=BRIDGE_TOKEN, timeout=1)
        assert seen == []
    finally:
        server.shutdown()
        server.server_close()


def test_unreadable_proc_fails_closed_without_sending_the_token(bridge, monkeypatch, tmp_path):
    monkeypatch.setattr(harness_module, "PROC_ROOT", tmp_path / "no-proc")

    with pytest.raises(RuntimeError, match="Cannot prove"):
        bridge.start(SERVE)

    assert bridge.tokens_received() == []
    assert bridge.children[0].poll() is not None


def test_child_exit_stops_the_wait_with_its_exit_code(bridge):
    with pytest.raises(RuntimeError, match="exited prematurely with code 3"):
        bridge.start("import sys; sys.exit(3)")

    assert bridge.tokens_received() == []


def test_child_that_never_listens_times_out_and_is_stopped(bridge):
    with pytest.raises(TimeoutError):
        bridge.start("import time; time.sleep(30)", timeout=1.0)

    assert bridge.children[0].poll() is not None
    assert bridge.tokens_received() == []


def test_wrangler_runs_offline_without_install_or_metrics(bridge, monkeypatch, tmp_path):
    monkeypatch.setenv("WRANGLER_SEND_METRICS", "true")
    bridge.start(SERVE)

    ran = {}
    monkeypatch.setattr(
        harness_module.subprocess, "run",
        lambda cmd, **kwargs: ran.update(cmd=list(cmd), env=kwargs.get("env")) or subprocess.CompletedProcess(cmd, 0),
    )
    schema = tmp_path / "schema.sql"
    schema.write_text("")
    harness_module.ingest_d1_ddl(persist_dir=tmp_path / "persist", bridge_worker_dir=tmp_path, schema_file=schema)

    for call in (bridge.captured, ran):
        assert call["cmd"][:3] == ["npx", "--no-install", "wrangler"]
        assert call["env"]["npm_config_offline"] == "true"
        assert call["env"]["WRANGLER_SEND_METRICS"] == "false"
        assert call["env"]["CLOUDFLARE_CF_FETCH_ENABLED"] == "false"
        assert "--remote" not in call["cmd"]
    assert BRIDGE_TOKEN not in bridge.captured["cmd"]


def _instance_for(bridge, child, tmp_path):
    return D1TargetHarness(
        port=bridge.port, token=BRIDGE_TOKEN, proc=child,
        persist_dir=tmp_path / "persist", env_file=tmp_path / "bridge.env",
    )


def _wait_port_free(port, timeout=5.0):
    deadline = time.time() + timeout
    while _still_accepting(port) and time.time() < deadline:
        time.sleep(0.05)
    assert not _still_accepting(port)


def test_harness_stops_sending_the_token_once_its_child_is_gone(bridge, tmp_path):
    child = bridge.start(SERVE)
    harness = _instance_for(bridge, child, tmp_path)

    assert harness.query("SELECT 1;")["success"] is True
    assert bridge.tokens_received() == [BRIDGE_TOKEN, BRIDGE_TOKEN]

    harness_module.terminate_process(child)
    _wait_port_free(bridge.port)
    with pytest.raises(RuntimeError, match="not owned"):
        harness.query("SELECT 1;")

    server, seen = _foreign_listener(bridge.port)
    try:
        with pytest.raises(RuntimeError, match="not owned"):
            harness.query("DELETE FROM assets_asset;")
        with pytest.raises(RuntimeError, match="not owned"):
            harness.verify_clean()

        assert seen == []
        assert _still_accepting(bridge.port)
        assert len(bridge.tokens_received()) == 2
    finally:
        server.shutdown()
        server.server_close()


SERVE_TABLES = SERVE.replace(
    '''self.rfile.read(int(self.headers.get("Content-Length") or 0))''',
    '''sql = self.rfile.read(int(self.headers.get("Content-Length") or 0))''',
).replace(
    """body = b'{"success": true, "results": []}'""",
    """body = (sys.argv[3] if b"sqlite_master" in sql else '{"success": true, "results": []}').encode()""",
)


def test_verify_clean_rechecks_ownership_before_every_query(bridge, monkeypatch, tmp_path):
    metadata = json.dumps({
        "success": True,
        "results": [{"name": name} for name in [*TARGET_TABLES, "ai_suggestion_logs"]],
    })
    child = bridge.start(SERVE_TABLES, metadata)
    harness = _instance_for(bridge, child, tmp_path)

    assert set(harness.verify_clean()) == set(TARGET_TABLES)
    tokens_after_full_run = len(bridge.tokens_received())
    assert tokens_after_full_run == 1 + 1 + len(TARGET_TABLES)

    # The child stops and a foreign listener takes the port right after the metadata answer arrives.
    real_transport = harness_module.query_bridge_http
    transport_calls = []
    state = {}

    def transport(sql, **kwargs):
        transport_calls.append(sql)
        answer = real_transport(sql, **kwargs)
        if "sqlite_master" in sql:
            harness_module.terminate_process(child)
            _wait_port_free(bridge.port)
            state["server"], state["seen"] = _foreign_listener(bridge.port)
        return answer

    monkeypatch.setattr(harness_module, "query_bridge_http", transport)
    try:
        with pytest.raises(RuntimeError, match="not owned"):
            harness.verify_clean()

        assert len(transport_calls) == 1 and "sqlite_master" in transport_calls[0]
        assert state["seen"] == []
        assert _still_accepting(bridge.port)
        assert len(bridge.tokens_received()) == tokens_after_full_run + 1
    finally:
        if "server" in state:
            state["server"].shutdown()
            state["server"].server_close()


def test_assert_bridge_owned_contract(bridge):
    url = f"http://127.0.0.1:{bridge.port}/api/v1/query"
    with pytest.raises(RuntimeError, match="nothing is listening"):
        harness_module.assert_bridge_owned(None, url)

    child = bridge.start(SERVE)
    assert harness_module.assert_bridge_owned(child, url) is None
    assert harness_module.assert_bridge_owned(child, f"http://localhost:{bridge.port}/") is None

    for not_a_child in (None, mock.Mock(pid=child.pid + 10 ** 6)):
        with pytest.raises(RuntimeError, match="not owned"):
            harness_module.assert_bridge_owned(not_a_child, url)


@pytest.mark.parametrize("url", [
    "http://127.evil.tld:8787/", "http://127.0.0.1.evil.tld:8787/", "http://8.8.8.8:8787/",
    "http://[::ffff:8.8.8.8]:8787/", "https://127.0.0.1:8787/", "http://127.0.0.1/",
    "http://user:secret@127.0.0.1:8787/", "http://127.0.0.1:notaport/", "ftp://127.0.0.1:8787/", "",
])
def test_non_loopback_bridge_url_is_refused_before_any_io(monkeypatch, tmp_path, url):
    # Without this proc directory any attempt to look up listeners would report "Cannot prove".
    monkeypatch.setattr(harness_module, "PROC_ROOT", tmp_path / "must-not-be-read")
    with pytest.raises(RuntimeError, match="Bridge URL"):
        harness_module.assert_bridge_owned(mock.Mock(pid=1), url)
    with pytest.raises(RuntimeError, match="Bridge URL"):
        harness_module.wait_for_bridge_ready(proc=mock.Mock(pid=1, poll=lambda: None), url=url, timeout=1)


def _fake_proc_root(tmp_path):
    root = tmp_path / "proc"
    (root / "net").mkdir(parents=True)
    (root / "net" / "tcp").write_text("header\n")
    return root


def test_missing_ipv6_table_means_no_ipv6_listeners(monkeypatch, tmp_path):
    monkeypatch.setattr(harness_module, "PROC_ROOT", _fake_proc_root(tmp_path))
    with pytest.raises(RuntimeError, match="nothing is listening"):
        harness_module.assert_bridge_owned(None, "http://127.0.0.1:1/")


def test_unreadable_ipv6_table_fails_closed(monkeypatch, tmp_path):
    root = _fake_proc_root(tmp_path)
    (root / "net" / "tcp6").mkdir()  # exists, but reading it fails with something other than "not found"
    monkeypatch.setattr(harness_module, "PROC_ROOT", root)
    with pytest.raises(RuntimeError, match="Cannot prove"):
        harness_module.assert_bridge_owned(None, "http://127.0.0.1:1/")


def test_foreign_listener_on_ipv6_is_seen(bridge):
    if not socket.has_ipv6:
        pytest.skip("IPv6 is not available on this host")
    class V6Server(HTTPServer):
        address_family = socket.AF_INET6
    server = V6Server(("::1", bridge.port), BaseHTTPRequestHandler)
    try:
        with pytest.raises(RuntimeError, match="not owned"):
            harness_module.assert_bridge_owned(None, f"http://[::1]:{bridge.port}/")
    finally:
        server.server_close()


# ===========================================================================
# Startup order: the instance knows its child before the first token goes out
# ===========================================================================

def test_on_started_sees_the_child_before_any_readiness_request(bridge):
    seen = []

    child = bridge.start(SERVE, on_started=lambda proc: seen.append((proc, proc.poll(), bridge.tokens_received())))

    assert seen == [(child, None, [])]
    assert bridge.tokens_received() == [BRIDGE_TOKEN]


def test_failing_on_started_stops_the_child_and_sends_nothing(bridge):
    def refuse(proc):
        raise ValueError("bookkeeping failed")

    with pytest.raises(ValueError, match="bookkeeping failed"):
        bridge.start(SERVE, on_started=refuse)

    assert bridge.children[0].poll() is not None
    assert bridge.tokens_received() == []


def test_marker_failure_after_launch_stops_the_child(bridge, monkeypatch, tmp_path):
    real_marker = harness_module.write_run_marker

    def marker(persist_dir, run_id, pid=None):
        if pid is not None:
            raise OSError("disk full")
        return real_marker(persist_dir, run_id=run_id, pid=pid)

    monkeypatch.setattr(harness_module, "write_run_marker", marker)
    (tmp_path / "persist").mkdir()
    with pytest.raises(OSError, match="disk full"):
        bridge.start(SERVE, run_id="marker-failure")

    assert bridge.children[0].poll() is not None


def _bootstrap_harness(bridge, monkeypatch, run_id):
    """The real D1TargetHarness.setup, with only DDL ingestion and the Wrangler binary replaced."""
    monkeypatch.setattr(harness_module, "ingest_d1_ddl", lambda **kwargs: None)
    metadata = json.dumps({
        "success": True,
        "results": [{"name": name} for name in [*TARGET_TABLES, "ai_suggestion_logs"]],
    })
    bridge.use(SERVE_TABLES, metadata)
    return D1TargetHarness(port=bridge.port, token=BRIDGE_TOKEN, run_id=run_id)


def test_setup_records_its_child_before_the_first_authenticated_request(bridge, monkeypatch):
    harness = _bootstrap_harness(bridge, monkeypatch, "bootstrap-order")
    real_transport = harness_module.query_bridge_http
    seen_by_transport = []

    def transport(sql, **kwargs):
        seen_by_transport.append(harness.process)
        return real_transport(sql, **kwargs)

    monkeypatch.setattr(harness_module, "query_bridge_http", transport)
    try:
        harness.setup()
        child = bridge.children[0]
        assert harness.process is child
        assert seen_by_transport and all(proc is child for proc in seen_by_transport)
    finally:
        harness.teardown()

    assert bridge.children[0].poll() is not None
    assert not harness.persist_dir.exists()
    assert not harness.env_file.exists()


@pytest.mark.parametrize("script, error", [
    ("import sys; sys.exit(3)", RuntimeError),
    ("import time; time.sleep(30)", TimeoutError),
], ids=["child-exits", "never-ready"])
def test_failed_setup_leaves_no_child_or_owned_files(bridge, monkeypatch, script, error):
    harness = _bootstrap_harness(bridge, monkeypatch, "bootstrap-failure")
    bridge.use(script)
    real_wait = harness_module.wait_for_bridge_ready
    monkeypatch.setattr(harness_module, "wait_for_bridge_ready", lambda **kwargs: real_wait(**{**kwargs, "timeout": 1.0}))

    with pytest.raises(error):
        harness.setup()

    assert bridge.children[0].poll() is not None
    assert harness.process is None
    assert not harness.persist_dir.exists()
    assert not harness.env_file.exists()
    assert bridge.tokens_received() == []
