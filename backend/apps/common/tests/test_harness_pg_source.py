# -*- coding: utf-8 -*-
"""
Test suite validating PostgreSQL Test Harness (harness_pg_source.py).
Ensures authentic PostgreSQL test harness meets all rehearsal requirements:
- Isolated container on 127.0.0.1:5434.
- Container ownership labels (assethub.owner=rehearsal, assethub.run_id).
- Teardown guard strictly protects assethub-db-1, pg-test, and non-owned containers.
- Sentinel port conflict check raises RuntimeError and never kills external processes.
- Dynamic credentials per run without hardcoding or logging.
- All 16 mandatory source tables exist.
- ai_suggestion_logs does NOT exist in source database.
- Exact PK preservation and sequence synchronization.
- PBKDF2 1,000,000 password hashes authenticate with Django auth.
- Microsecond UTC datetimes, booleans, and JSON structures.
- Read-only user permissions (SELECT allowed, INSERT rejected).
"""
import json
import socket
import subprocess
from unittest import mock

import pytest
from django.contrib.auth.hashers import check_password

pytestmark = pytest.mark.rehearsal

from apps.common.tests.harness_pg_source import (
    PGSourceHarness,
    SOURCE_MANDATORY_TABLES,
    TEST_USERS,
    cleanup_rehearsal_containers,
    is_container_running,
    teardown_pg_source,
)


def test_sentinel_port_conflict_guard():
    """
    Sentinel Server Test:
    Start a dummy socket server on test port 5434.
    Attempt to start PGSourceHarness on that port.
    Assert it raises RuntimeError and the sentinel server is NOT killed.
    No bulk cleanup is run beforehand to avoid touching external resources.
    """
    sentinel = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sentinel.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sentinel.bind(("127.0.0.1", 5434))
        sentinel.listen(1)

        # Attempt to start PGSourceHarness on port 5434
        harness = PGSourceHarness(port=5434)
        with pytest.raises(RuntimeError) as exc_info:
            harness.start()

        # Assert correct error message
        assert "Port 5434 on 127.0.0.1 is already in use by an external process." in str(exc_info.value)

        # Assert sentinel server was NOT killed and is still accepting connections
        assert sentinel.fileno() != -1
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            assert probe.connect_ex(("127.0.0.1", 5434)) == 0

    finally:
        sentinel.close()


def test_foreign_container_same_port_not_removed():
    """
    Regression Test 1:
    Verify that foreign container belonging to a different run on the same port
    is NEVER removed by cleanup or start, and port conflict fails safely.
    """
    # 1. Calling cleanup_rehearsal_containers without run_id does nothing
    assert cleanup_rehearsal_containers() == []

    # 2. Mock foreign container on port 5434
    harness = PGSourceHarness(run_id="our_current_run", port=5434)
    with mock.patch("apps.common.tests.harness_pg_source.is_port_in_use", return_value=True):
        with mock.patch.object(harness, "is_container_running", return_value=True):
            with mock.patch.object(harness, "get_container_labels") as mock_labels:
                mock_labels.return_value = {
                    "assethub.owner": "rehearsal",
                    "assethub.run_id": "foreign_run_123",
                }
                with mock.patch("subprocess.run") as mock_run:
                    with pytest.raises(RuntimeError, match="already in use by an external process"):
                        harness.start()
                    # Verify no docker rm was called on foreign container
                    for call_args in mock_run.call_args_list:
                        args = call_args[0][0]
                        assert not (args[0] == "docker" and args[1] == "rm")


def test_pg_setup_failure_self_cleans_resources():
    """
    Regression Test 3 (PG Source):
    Verify that if setup fails at any step (e.g., migrations, schema verify, seeding),
    PGSourceHarness.setup() automatically tears down its own resources and re-raises the error.
    """
    harness = PGSourceHarness(run_id="pg_setup_fail_test")
    with mock.patch.object(harness, "start") as mock_start:
        with mock.patch.object(harness, "apply_django_migrations", side_effect=RuntimeError("Simulated migration crash")):
            with mock.patch.object(harness, "teardown") as mock_teardown:
                with pytest.raises(RuntimeError, match="Simulated migration crash"):
                    harness.setup()

                mock_start.assert_called_once()
                mock_teardown.assert_called_once()


def test_teardown_guard_refuses_foreign_containers():
    """
    Teardown Guard Test:
    Verify teardown refuses to touch containers that don't match the run's label:
    - Protected containers (assethub-db-1, pg-test).
    - Containers with missing or mismatched run_id / owner labels.
    """
    # Case 1: Protected container assethub-db-1
    h_db = PGSourceHarness(run_id="test_db_guard")
    h_db.container_name = "assethub-db-1"
    assert h_db.teardown() is False
    assert is_container_running("assethub-db-1") is True

    # Case 2: Protected container pg-test
    h_pg = PGSourceHarness(run_id="test_pg_guard")
    h_pg.container_name = "pg-test"
    assert h_pg.teardown() is False
    assert is_container_running("pg-test") is True

    # Case 3: Container without matching run_id or rehearsal owner
    h_mismatch = PGSourceHarness(run_id="run_expected_123")
    with mock.patch.object(h_mismatch, "get_container_labels") as mock_labels:
        # Scenario A: Foreign run_id
        mock_labels.return_value = {
            "assethub.owner": "rehearsal",
            "assethub.run_id": "run_other_456",
        }
        with mock.patch("subprocess.run") as mock_run:
            assert h_mismatch.teardown() is False
            mock_run.assert_not_called()

        # Scenario B: Foreign owner
        mock_labels.return_value = {
            "assethub.owner": "production",
            "assethub.run_id": "run_expected_123",
        }
        with mock.patch("subprocess.run") as mock_run:
            assert h_mismatch.teardown() is False
            mock_run.assert_not_called()


def test_random_credentials_and_no_leak():
    """
    Random Credentials Test:
    - Admin and RO credentials generated randomly per run.
    - Zero hardcoded passwords.
    - Credentials never printed in repr or str.
    """
    h1 = PGSourceHarness()
    h2 = PGSourceHarness()

    assert h1.run_id != h2.run_id
    assert h1.admin_user != h2.admin_user
    assert h1.admin_password != h2.admin_password
    assert h1.ro_user != h2.ro_user
    assert h1.ro_password != h2.ro_password

    assert h1.admin_user == f"pgadm_{h1.run_id}"
    assert h1.ro_user == f"pgro_{h1.run_id}"
    assert len(h1.admin_password) >= 16
    assert len(h1.ro_password) >= 16

    assert h1.admin_password not in repr(h1)
    assert h1.ro_password not in repr(h1)
    assert h1.admin_password not in str(h1)
    assert h1.ro_password not in str(h1)


@pytest.fixture(scope="module")
def pg_rehearsal_harness():
    """
    Module-scoped fixture orchestrating isolated PGSourceHarness.
    Guarantees clean teardown in finally block even if assertions fail.
    """
    harness = PGSourceHarness(port=5434)
    harness.setup(recreate=True)
    try:
        yield harness
    finally:
        harness.teardown()


def test_container_labels_and_127_binding(pg_rehearsal_harness):
    """
    Label & Binding Test:
    Verify container has label assethub.owner=rehearsal and binds ONLY to 127.0.0.1.
    """
    harness = pg_rehearsal_harness
    assert harness.is_container_running() is True
    assert harness.container_name == f"assethub-pg-reh-{harness.run_id}"

    labels = harness.get_container_labels()
    assert labels is not None
    assert labels.get("assethub.owner") == "rehearsal"
    assert labels.get("assethub.run_id") == harness.run_id

    # Inspect Docker port bindings
    res = subprocess.run(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Ports}}", harness.container_name],
        capture_output=True,
        text=True,
        check=True,
    )
    ports_info = json.loads(res.stdout.strip())
    pg_port_bindings = ports_info.get("5432/tcp", [])
    assert len(pg_port_bindings) > 0, "No port bindings found for 5432/tcp"

    for binding in pg_port_bindings:
        assert binding["HostIp"] == "127.0.0.1", (
            f"Container must bind ONLY to 127.0.0.1, got {binding['HostIp']}"
        )
        assert int(binding["HostPort"]) == harness.port


def test_pg_container_running(pg_rehearsal_harness):
    """PostgreSQL rehearsal container must be running."""
    assert pg_rehearsal_harness.is_container_running()


def test_schema_isolation_and_no_ai_table(pg_rehearsal_harness):
    """Verify all 16 source tables exist and ai_suggestion_logs is absent."""
    verification = pg_rehearsal_harness.verify_schema_isolation()
    assert verification["verified"] is True
    assert verification["all_source_mandatory_present"] is True
    assert verification["ai_suggestion_logs_absent"] is True


def test_seed_dataset_row_counts_and_completeness(pg_rehearsal_harness):
    """Verify all 16 tables have rows and seed_rehearsal_data succeeds."""
    counts = pg_rehearsal_harness.seed_rehearsal_data()
    for tbl in SOURCE_MANDATORY_TABLES:
        assert tbl in counts, f"Mandatory table {tbl} missing in seed counts"
        assert counts[tbl] > 0, f"Table {tbl} has 0 rows after seeding"


def test_user_passwords_authenticate_with_django(pg_rehearsal_harness):
    """Users must have PBKDF2 1,000,000 hashes that pass Django auth check_password."""
    with pg_rehearsal_harness.get_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, password FROM authentication_user ORDER BY id")
            db_users = {row[1]: row[2] for row in cur.fetchall()}

    for user_key, expected in TEST_USERS.items():
        uname = expected["username"]
        assert uname in db_users, f"User {uname} missing in database"
        stored_hash = db_users[uname]
        assert "pbkdf2_sha256$1000000$" in stored_hash
        assert check_password(expected["raw_password"], stored_hash), (
            f"check_password failed for user {uname}"
        )


def test_dataset_data_richness_validation(pg_rehearsal_harness):
    """Verify microsecond datetimes, booleans, JSON types, and sequence counters."""
    richness = pg_rehearsal_harness.verify_dataset_richness()
    assert richness["status"] == "PASS", f"Richness verification failed: {richness['errors']}"
    assert len(richness["errors"]) == 0


def test_readonly_user_select_allowed_and_write_blocked(pg_rehearsal_harness):
    """Read-only user can SELECT all tables, but fails on INSERT."""
    import psycopg

    harness = pg_rehearsal_harness
    with harness.get_connection(user=harness.ro_user, password=harness.ro_password, autocommit=True) as ro_conn:
        with ro_conn.cursor() as cur:
            # 1. SELECT allowed on mandatory tables
            for tbl in ["authentication_user", "assets_asset", "requests_assetrequest"]:
                cur.execute(f'SELECT count(*) FROM "{tbl}"')
                count = cur.fetchone()[0]
                assert count > 0, f"Expected > 0 rows in {tbl} for read-only user"

            # 2. INSERT blocked
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("INSERT INTO departments_department (name, code) VALUES ('Fail', 'FAIL')")
