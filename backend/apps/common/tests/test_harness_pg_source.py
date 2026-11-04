# -*- coding: utf-8 -*-
"""
Test suite validating PostgreSQL Test Harness (harness_pg_source.py).
Ensures authentic PostgreSQL test harness meets all rehearsal requirements:
- Isolated container on port 5434.
- All 15 mandatory source tables exist.
- ai_suggestion_logs does NOT exist in source database.
- Exact PK preservation and sequence synchronization.
- PBKDF2 1,000,000 password hashes authenticate with Django auth.
- Microsecond UTC datetimes, booleans, and JSON structures.
- Read-only user permissions (SELECT allowed, INSERT rejected).
"""
import datetime
import pytest
from django.contrib.auth.hashers import check_password

from apps.common.tests.harness_pg_source import (
    CONTAINER_NAME,
    PORT,
    DB_NAME,
    ADMIN_USER,
    RO_USER,
    RO_PASSWORD,
    SOURCE_MANDATORY_TABLES,
    TEST_USERS,
    is_container_running,
    get_pg_connection,
    verify_schema_isolation,
    verify_dataset_richness,
    seed_rehearsal_data,
)


def test_pg_container_running():
    """PostgreSQL rehearsal container must be running on port 5434."""
    assert is_container_running(CONTAINER_NAME), f"Container {CONTAINER_NAME} is not running."


def test_schema_isolation_and_no_ai_table():
    """Verify all 15 source tables exist and ai_suggestion_logs is absent."""
    verification = verify_schema_isolation()
    assert verification["verified"] is True
    assert verification["all_source_mandatory_present"] is True
    assert verification["ai_suggestion_logs_absent"] is True


def test_seed_dataset_row_counts_and_completeness():
    """Verify all 15 tables have rows and seed_rehearsal_data succeeds."""
    counts = seed_rehearsal_data()
    for tbl in SOURCE_MANDATORY_TABLES:
        assert tbl in counts, f"Mandatory table {tbl} missing in seed counts"
        assert counts[tbl] > 0, f"Table {tbl} has 0 rows after seeding"


def test_user_passwords_authenticate_with_django():
    """Users must have PBKDF2 1,000,000 hashes that pass Django auth check_password."""
    with get_pg_connection(autocommit=True) as conn:
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


def test_dataset_data_richness_validation():
    """Verify microsecond datetimes, booleans, JSON types, and sequence counters."""
    richness = verify_dataset_richness()
    assert richness["status"] == "PASS", f"Richness verification failed: {richness['errors']}"
    assert len(richness["errors"]) == 0


def test_readonly_user_select_allowed_and_write_blocked():
    """Read-only user assethub_ro_user can SELECT all tables, but fails on INSERT."""
    import psycopg

    with get_pg_connection(user=RO_USER, password=RO_PASSWORD, autocommit=True) as ro_conn:
        with ro_conn.cursor() as cur:
            # 1. SELECT allowed on mandatory tables
            for tbl in ["authentication_user", "assets_asset", "requests_assetrequest"]:
                cur.execute(f'SELECT count(*) FROM "{tbl}"')
                count = cur.fetchone()[0]
                assert count > 0, f"Expected > 0 rows in {tbl} for read-only user"

            # 2. INSERT blocked
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("INSERT INTO departments_department (name, code) VALUES ('Fail', 'FAIL')")
