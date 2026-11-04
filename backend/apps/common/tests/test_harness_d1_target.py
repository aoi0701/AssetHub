"""
Tests for local D1 Target and Worker Bridge harness.

Validates process ownership, socket port guards, directory marker security,
and complete lifecycle management for zero-cloud D1 migration rehearsals.
"""

import json
import socket
from pathlib import Path

import pytest

pytestmark = pytest.mark.rehearsal

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


def test_safe_environment_guard():
    """Verify isolation guards block remote or deploy flags."""
    with pytest.raises(RuntimeError, match="--remote is strictly forbidden"):
        assert_safe_environment(["wrangler", "dev", "--remote"])

    with pytest.raises(RuntimeError, match="--remote is strictly forbidden"):
        assert_safe_environment(["wrangler", "d1", "execute", "db", "-r"])

    with pytest.raises(RuntimeError, match="wrangler deploy is strictly forbidden"):
        assert_safe_environment(["wrangler", "deploy"])


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
