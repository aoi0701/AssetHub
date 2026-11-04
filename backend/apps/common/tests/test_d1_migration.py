"""
Test Suite for pg-to-d1 Migration Tool and 1:1 Automated Reconciliation.
Validates:
1. --dry-run extracts source, calculates checksums, outputs 4-tier plan, 0 target writes.
2. Target non-empty check runs by default and aborts unless --allow-non-empty is set.
3. --allow-non-empty still raises CommandError when reconciliation fails.
4. Strict 4-Tier topological loading sequence with 100% original primary key preservation.
5. Auto-update of sqlite_sequence to MAX(id) without duplicate entries; next autoincrement increments id.
6. Verbatim preservation of 1,000,000-iteration PBKDF2 password hashes (no re-hashing).
7. Datetime normalization to ISO-8601 UTC and boolean mapping to 1/0 integers.
8. Batch chunking up to 50 records per batch via d1_batch.
9. D1Reconciler validation:
   - Row count parity (COUNT(*))
   - Key set integrity (SELECT id / PK)
   - Foreign key integrity (PRAGMA foreign_key_check)
   - Password hash verbatim checksum (SHA-256 match)
   - Datetime & boolean normalization
   - Identifier sequence counters strictly partitioned by (kind, year)
   - Detection of data mutations and account permissions drift
   - Canonical equivalence handling (no false positives for JSON/bool/ISO datetime)
   - Sensitive data preservation (no credential leaks in error messages)
10. Isolation Guard error propagation (no silent cursor fallback).
11. Atomic batch transaction rollback on SQLite testbed.
12. Source and target DB configuration validation (alias conflict, same file, missing alias).
"""
import datetime
import io
import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connections, OperationalError

from apps.common.management.commands.migrate_pg_to_d1 import (
    Command,
    D1Reconciler,
    TOPO_TIERS,
    ALL_TABLES,
    AUTOINCREMENT_TABLES,
    normalize_value,
    execute_batch_statements,
)


@pytest.fixture(autouse=True)
def allow_test_db_access(django_db_blocker):
    with django_db_blocker.unblock():
        yield


def get_schema_sql_content() -> str:
    """Read authoritative D1 schema SQL."""
    schema_path = Path(settings.BASE_DIR) / "assethub_api" / "backends" / "d1" / "sql" / "schema.sql"
    assert schema_path.exists(), f"schema.sql not found at {schema_path}"
    return schema_path.read_text(encoding="utf-8")


@pytest.fixture
def migration_dbs(tmp_path):
    """
    Sets up two isolated SQLite databases for migration testing:
    - 'source_test': populated with comprehensive test fixtures across all 4 tiers.
    - 'target_test': fresh database with official D1 schema loaded and foreign keys enabled.
    """
    source_file = tmp_path / "source_test.sqlite3"
    target_file = tmp_path / "target_test.sqlite3"

    schema_sql = get_schema_sql_content()

    # Initialize source DB with schema
    s_conn = sqlite3.connect(str(source_file))
    s_conn.execute("PRAGMA foreign_keys = ON;")
    s_conn.executescript(schema_sql)
    s_conn.commit()
    s_conn.close()

    # Initialize target DB with schema
    t_conn = sqlite3.connect(str(target_file))
    t_conn.execute("PRAGMA foreign_keys = ON;")
    t_conn.executescript(schema_sql)
    t_conn.commit()
    t_conn.close()

    test_id = uuid.uuid4().hex[:8]
    source_alias = f"src_{test_id}"
    target_alias = f"tgt_{test_id}"

    db_conf = {
        "ENGINE": "django.db.backends.sqlite3",
        "TIME_ZONE": None,
        "AUTOCOMMIT": True,
        "ATOMIC_REQUESTS": False,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "OPTIONS": {},
    }

    settings.DATABASES[source_alias] = {**db_conf, "NAME": str(source_file)}
    settings.DATABASES[target_alias] = {**db_conf, "NAME": str(target_file)}
    connections.databases[source_alias] = {**db_conf, "NAME": str(source_file)}
    connections.databases[target_alias] = {**db_conf, "NAME": str(target_file)}

    try:
        yield source_alias, target_alias, source_file, target_file
    finally:
        if source_alias in connections:
            connections[source_alias].close()
            del connections[source_alias]
        if target_alias in connections:
            connections[target_alias].close()
            del connections[target_alias]
        settings.DATABASES.pop(source_alias, None)
        settings.DATABASES.pop(target_alias, None)
        connections.databases.pop(source_alias, None)
        connections.databases.pop(target_alias, None)


def seed_source_test_data(db_alias: str):
    """Seed comprehensive test dataset across all 4 tiers with explicit, non-standard IDs."""
    conn = connections[db_alias]
    with conn.cursor() as cur:
        # Tier 1
        cur.execute(
            'INSERT INTO django_content_type (id, app_label, model) VALUES (?, ?, ?)',
            [101, 'departments', 'department']
        )
        cur.execute(
            'INSERT INTO django_content_type (id, app_label, model) VALUES (?, ?, ?)',
            [102, 'authentication', 'user']
        )
        cur.execute(
            'INSERT INTO auth_permission (id, name, content_type_id, codename) VALUES (?, ?, ?, ?)',
            [201, 'Can add department', 101, 'add_department']
        )
        cur.execute(
            'INSERT INTO auth_permission (id, name, content_type_id, codename) VALUES (?, ?, ?, ?)',
            [202, 'Can view user', 102, 'view_user']
        )
        cur.execute(
            'INSERT INTO auth_group (id, name) VALUES (?, ?)',
            [301, 'IT Administrators']
        )
        cur.execute(
            'INSERT INTO departments_department (id, name, code, description, created_at) VALUES (?, ?, ?, ?, ?)',
            [401, 'Phòng Kỹ Thuật', 'TECH', 'Ban Công nghệ thông tin', '2026-11-01 08:00:00']
        )
        cur.execute(
            'INSERT INTO departments_category (id, name, description, created_at) VALUES (?, ?, ?, ?)',
            [501, 'Máy tính xách tay', 'Laptop văn phòng và kỹ thuật', '2026-11-01 08:00:00']
        )

        # Tier 2
        cur.execute(
            'INSERT INTO auth_group_permissions (id, group_id, permission_id) VALUES (?, ?, ?)',
            [601, 301, 201]
        )
        pbkdf2_admin_pw = 'pbkdf2_sha256$1000000$testSalt123$verbatimAdminHashPreservedExact1234567890='
        pbkdf2_user_pw = 'pbkdf2_sha256$1000000$testSalt456$verbatimUserHashPreservedExact0987654321='
        cur.execute(
            '''INSERT INTO authentication_user (
                id, password, last_login, is_superuser, username, first_name, last_name,
                email, is_staff, is_active, date_joined, role, full_name, phone, department_id, auth_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [701, pbkdf2_admin_pw, '2026-11-01 09:00:00', 1, 'admin_super', 'Admin', 'Root',
             'admin@assethub.vn', 1, 1, '2026-11-01 08:00:00', 'ADMIN', 'Quản Trị Viên', '0901234567',
             401, 1, '2026-11-01 08:00:00']
        )
        cur.execute(
            '''INSERT INTO authentication_user (
                id, password, last_login, is_superuser, username, first_name, last_name,
                email, is_staff, is_active, date_joined, role, full_name, phone, department_id, auth_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [702, pbkdf2_user_pw, '2026-11-01 10:00:00', 0, 'user_dev', 'Dev', 'One',
             'dev@assethub.vn', 0, 1, '2026-11-01 08:30:00', 'USER', 'Kỹ Sư Phần Mềm', '0907654321',
             401, 1, '2026-11-01 08:30:00']
        )

        # Tier 3
        cur.execute(
            'INSERT INTO authentication_user_groups (id, user_id, group_id) VALUES (?, ?, ?)',
            [801, 701, 301]
        )
        cur.execute(
            'INSERT INTO authentication_user_user_permissions (id, user_id, permission_id) VALUES (?, ?, ?)',
            [851, 702, 202]
        )
        cur.execute(
            '''INSERT INTO django_admin_log (
                id, action_time, user_id, content_type_id, object_id, object_repr, action_flag, change_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            [901, '2026-11-01 09:15:00', 701, 101, '401', 'Phòng Kỹ Thuật', 1, 'Tạo mới']
        )
        cur.execute(
            'INSERT INTO django_migrations (id, app, name, applied) VALUES (?, ?, ?, ?)',
            [951, 'departments', '0001_initial', '2026-11-01 08:00:00']
        )
        cur.execute(
            'INSERT INTO django_session (session_key, session_data, expire_date) VALUES (?, ?, ?)',
            ['sess_key_test_001', 'session_data_payload_hash_123', '2026-12-01 00:00:00']
        )
        cur.execute(
            'INSERT INTO assets_identifiersequence (id, kind, year, value) VALUES (?, ?, ?, ?)',
            [1001, 'AST', 2026, 15]
        )
        cur.execute(
            'INSERT INTO assets_identifiersequence (id, kind, year, value) VALUES (?, ?, ?, ?)',
            [1002, 'REQ', 2026, 8]
        )
        cur.execute(
            '''INSERT INTO assets_asset (
                id, asset_code, name, serial_number, category_id, specification, status,
                department_id, assigned_to_user_id, image, document, purchase_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [1101, 'AST-2026-001', 'MacBook Pro M3 Max', 'SN-M3-001', 501, '{"ram": "36GB"}', 'IN_USE',
             401, 702, '', '', '2026-01-15', '2026-11-01 09:00:00', '2026-11-01 09:00:00']
        )
        cur.execute(
            '''INSERT INTO assets_asset (
                id, asset_code, name, serial_number, category_id, specification, status,
                department_id, assigned_to_user_id, image, document, purchase_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [1102, 'AST-2026-002', 'Dell XPS 15', 'SN-DELL-002', 501, '{"ram": "32GB"}', 'AVAILABLE',
             401, None, '', '', '2026-02-10', '2026-11-01 09:10:00', '2026-11-01 09:10:00']
        )

        # Tier 4
        cur.execute(
            '''INSERT INTO requests_assetrequest (
                id, request_code, request_type, requester_id, asset_id, requested_category_id,
                department_id, reason, status, reviewed_by_id, reviewed_at, rejection_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [1201, 'REQ-2026-001', 'ALLOCATE', 702, 1101, 501, 401, 'Cần máy làm việc', 'APPROVED',
             701, '2026-11-01 09:30:00', '', '2026-11-01 09:20:00']
        )
        cur.execute(
            '''INSERT INTO assets_assethistorylog (
                id, asset_id, actor_id, action_type, from_status, to_status, from_user_id, to_user_id,
                from_department_id, to_department_id, reason, asset_code_snapshot, asset_name_snapshot, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [1301, 1101, 701, 'ALLOCATE', 'AVAILABLE', 'IN_USE', None, 702, 401, 401,
             'Bàn giao thiết bị', 'AST-2026-001', 'MacBook Pro M3 Max', '2026-11-01 09:30:00']
        )
        cur.execute(
            '''INSERT INTO ai_suggestion_logs (
                id, asset_name, description, suggested_category_id, suggested_category_name, model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
            ['ai-log-uuid-001', 'MacBook Pro', 'Laptop Apple', 501, 'Máy tính xách tay', 'llama-3.1-8b', '2026-11-01 08:45:00']
        )


# ==============================================================================
# 1. DRY-RUN & SAFETY CHECKS
# ==============================================================================

def test_dry_run_leaves_target_empty_and_prints_plan(migration_dbs):
    """Test: --dry-run prints 4-Tier topological plan, calculates checksums, 0 target writes."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    out = io.StringIO()
    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        dry_run=True,
        stdout=out,
    )
    output = out.getvalue()

    assert "DRY-RUN" in output
    assert "Tier 1" in output
    assert "Tier 2" in output
    assert "Tier 3" in output
    assert "Tier 4" in output
    assert "django_content_type" in output
    assert "authentication_user" in output

    # Verify target DB is completely empty (0 rows across all tables)
    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        for tier_num, tables in TOPO_TIERS.items():
            for t in tables:
                cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                count = cur.fetchone()[0]
                assert count == 0, f"Table {t} should have 0 rows after dry-run, found {count}"


def test_dry_run_leaves_both_source_and_target_untouched(migration_dbs):
    """Test: --dry-run leaves both source and target untouched (0 writes)."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    # Record row counts in source before dry-run
    conn_src = connections[source_alias]
    src_counts_before = {}
    with conn_src.cursor() as cur:
        for t in ALL_TABLES:
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            src_counts_before[t] = cur.fetchone()[0]

    out = io.StringIO()
    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        dry_run=True,
        stdout=out,
    )

    # Verify source row counts untouched
    with conn_src.cursor() as cur:
        for t in ALL_TABLES:
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            assert cur.fetchone()[0] == src_counts_before[t]

    # Verify target has 0 rows in all tables
    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        for t in ALL_TABLES:
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            assert cur.fetchone()[0] == 0


def test_target_check_by_default_aborts_when_target_not_empty(migration_dbs):
    """Defect Area 5: Target non-empty check runs by DEFAULT without --target-check flag."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    # Insert 1 row in target DB to simulate non-empty target
    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute(
            'INSERT INTO departments_category (id, name, description, created_at) VALUES (?, ?, ?, ?)',
            [999, 'Danh mục cũ', 'Old', '2026-01-01 00:00:00']
        )

    # Must raise CommandError without requiring --target-check
    with pytest.raises(CommandError) as exc_info:
        call_command(
            'migrate_pg_to_d1',
            source_db=source_alias,
            target_db=target_alias,
        )
    err_msg = str(exc_info.value).lower()
    assert "target database" in err_msg or "not empty" in err_msg
    assert "--allow-non-empty" in str(exc_info.value)


def test_target_check_aborts_when_target_not_empty_without_allow_flag(migration_dbs):
    """Explicit --target-check blocks migration when target database has existing rows."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute(
            'INSERT INTO departments_category (id, name, description, created_at) VALUES (?, ?, ?, ?)',
            [999, 'Danh mục cũ', 'Old', '2026-01-01 00:00:00']
        )

    with pytest.raises(CommandError) as exc_info:
        call_command(
            'migrate_pg_to_d1',
            source_db=source_alias,
            target_db=target_alias,
            target_check=True,
        )
    assert "target database" in str(exc_info.value).lower() or "not empty" in str(exc_info.value).lower()
    assert "--allow-non-empty" in str(exc_info.value)


def test_target_check_permits_when_allow_non_empty_specified(migration_dbs):
    """
    Defect Area 1 (P1): --allow-non-empty bypasses target guard but MUST STILL raise CommandError
    when reconciliation detects mismatch (key/count/content mismatch from extra target data).
    """
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    # Seed target with extra row not present in source
    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute(
            'INSERT INTO departments_category (id, name, description, created_at) VALUES (?, ?, ?, ?)',
            [999, 'Danh mục thừa ở đích', 'Extra target row', '2026-01-01 00:00:00']
        )

    # Must raise CommandError because reconciliation detects key set / row count mismatch!
    with pytest.raises(CommandError) as exc_info:
        call_command(
            'migrate_pg_to_d1',
            source_db=source_alias,
            target_db=target_alias,
            target_check=True,
            allow_non_empty=True,
        )
    err_str = str(exc_info.value).lower()
    assert "reconciliation" in err_str or "parity" in err_str or "mismatch" in err_str or "violation" in err_str


def test_allow_non_empty_succeeds_when_reconciliation_passes(migration_dbs):
    """Defect Area 1 (P1): --allow-non-empty completes successfully when reconciliation passes."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    out = io.StringIO()
    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
        stdout=out,
    )
    output = out.getvalue()
    assert "completed successfully" in output or "PASSED" in output


# ==============================================================================
# 2. SOURCE & TARGET DB VALIDATION
# ==============================================================================

def test_source_and_target_same_db_alias_raises_command_error(migration_dbs):
    """Defect Area 5: Source and target having same alias raises CommandError."""
    source_alias, _, _, _ = migration_dbs
    with pytest.raises(CommandError) as exc_info:
        call_command(
            'migrate_pg_to_d1',
            source_db=source_alias,
            target_db=source_alias,
        )
    err_str = str(exc_info.value).lower()
    assert "same" in err_str or "identical" in err_str or "cannot" in err_str


def test_source_and_target_pointing_to_same_physical_file_raises_command_error(migration_dbs):
    """Defect Area 5: Source and target pointing to same physical DB file raises CommandError."""
    source_alias, _, source_file, _ = migration_dbs
    dup_alias = f"{source_alias}_physical_dup"

    db_conf = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(source_file),
    }
    settings.DATABASES[dup_alias] = db_conf
    connections.databases[dup_alias] = db_conf

    try:
        with pytest.raises(CommandError) as exc_info:
            call_command(
                'migrate_pg_to_d1',
                source_db=source_alias,
                target_db=dup_alias,
            )
        err_str = str(exc_info.value).lower()
        assert "same" in err_str or "identical" in err_str or "physical" in err_str or "file" in err_str
    finally:
        if dup_alias in connections:
            connections[dup_alias].close()
            del connections[dup_alias]
        settings.DATABASES.pop(dup_alias, None)
        connections.databases.pop(dup_alias, None)


def test_missing_source_db_alias_raises_command_error_without_silent_fallback(migration_dbs):
    """Defect Area 5: Missing source DB alias raises CommandError without silent fallback to 'default'."""
    _, target_alias, _, _ = migration_dbs
    missing_alias = "unconfigured_source_db_alias_9999"

    with pytest.raises(CommandError) as exc_info:
        call_command(
            'migrate_pg_to_d1',
            source_db=missing_alias,
            target_db=target_alias,
        )
    assert missing_alias in str(exc_info.value)


def test_missing_postgres_alias_raises_command_error_without_fallback_to_default(migration_dbs):
    """Defect Area 5: Default 'postgres' source raises CommandError when unconfigured, no silent fallback."""
    _, target_alias, _, _ = migration_dbs
    if "postgres" in settings.DATABASES:
        pytest.skip("'postgres' is configured in settings.DATABASES")

    with pytest.raises(CommandError) as exc_info:
        call_command(
            'migrate_pg_to_d1',
            source_db="postgres",
            target_db=target_alias,
        )
    assert "postgres" in str(exc_info.value)


# ==============================================================================
# 3. TOPO ORDER, PRIMARY KEYS, PBKDF2 & CHUNKING
# ==============================================================================

def test_4_tier_topo_order_and_exact_primary_key_preservation(migration_dbs):
    """Strict 4-Tier topological loading sequence with 100% original PK preservation."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    out = io.StringIO()
    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
        stdout=out,
    )

    conn_src = connections[source_alias]
    conn_tgt = connections[target_alias]

    with conn_src.cursor() as s_cur, conn_tgt.cursor() as t_cur:
        # Check django_content_type exact IDs
        s_cur.execute('SELECT id, app_label, model FROM django_content_type ORDER BY id')
        t_cur.execute('SELECT id, app_label, model FROM django_content_type ORDER BY id')
        assert s_cur.fetchall() == t_cur.fetchall() == [
            (101, 'departments', 'department'),
            (102, 'authentication', 'user'),
        ]

        # Check auth_permission exact IDs and FKs
        s_cur.execute('SELECT id, name, content_type_id, codename FROM auth_permission ORDER BY id')
        t_cur.execute('SELECT id, name, content_type_id, codename FROM auth_permission ORDER BY id')
        assert s_cur.fetchall() == t_cur.fetchall() == [
            (201, 'Can add department', 101, 'add_department'),
            (202, 'Can view user', 102, 'view_user'),
        ]

        # Check authentication_user exact IDs
        s_cur.execute('SELECT id, username, role, department_id FROM authentication_user ORDER BY id')
        t_cur.execute('SELECT id, username, role, department_id FROM authentication_user ORDER BY id')
        assert s_cur.fetchall() == t_cur.fetchall() == [
            (701, 'admin_super', 'ADMIN', 401),
            (702, 'user_dev', 'USER', 401),
        ]

        # Check assets_asset exact IDs
        s_cur.execute('SELECT id, asset_code, status, assigned_to_user_id FROM assets_asset ORDER BY id')
        t_cur.execute('SELECT id, asset_code, status, assigned_to_user_id FROM assets_asset ORDER BY id')
        assert s_cur.fetchall() == t_cur.fetchall() == [
            (1101, 'AST-2026-001', 'IN_USE', 702),
            (1102, 'AST-2026-002', 'AVAILABLE', None),
        ]


def test_sqlite_sequence_updated_to_max_id(migration_dbs):
    """sqlite_sequence is synchronized to match MAX(id) for each autoincrement table."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute('SELECT name, seq FROM sqlite_sequence ORDER BY name')
        seq_map = dict(cur.fetchall())

    expected_sequences = {
        'django_content_type': 102,
        'auth_permission': 202,
        'auth_group': 301,
        'departments_department': 401,
        'departments_category': 501,
        'auth_group_permissions': 601,
        'authentication_user': 702,
        'authentication_user_groups': 801,
        'authentication_user_user_permissions': 851,
        'django_admin_log': 901,
        'django_migrations': 951,
        'assets_identifiersequence': 1002,
        'assets_asset': 1102,
        'requests_assetrequest': 1201,
        'assets_assethistorylog': 1301,
    }

    for table, expected_max in expected_sequences.items():
        assert table in seq_map, f"sqlite_sequence missing entry for {table}"
        assert seq_map[table] == expected_max, (
            f"sqlite_sequence for {table} expected {expected_max}, found {seq_map[table]}"
        )


def test_sqlite_sequence_has_no_duplicates_and_next_insert_increments(migration_dbs):
    """
    Defect Area 3 (P2): sqlite_sequence has 0 duplicate rows after migration;
    next autoincrement insert without ID allocates id > max_id without collision.
    """
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        # Check for duplicate table names in sqlite_sequence
        cur.execute("SELECT name, COUNT(*) FROM sqlite_sequence GROUP BY name HAVING COUNT(*) > 1")
        duplicate_rows = cur.fetchall()
        assert len(duplicate_rows) == 0, f"sqlite_sequence contains duplicate table names: {duplicate_rows}"

        # Insert record without explicit id
        cur.execute(
            'INSERT INTO departments_department (name, code, description, created_at) VALUES (?, ?, ?, ?)',
            ['Phòng Kế Hoạch Mới', 'PLAN', 'Ban Kế hoạch', '2026-11-01 11:00:00']
        )
        cur.execute('SELECT last_insert_rowid()')
        new_id = cur.fetchone()[0]
        # Seeded max id was 401
        assert new_id > 401, f"Expected autoincrement id > 401, got {new_id}"


def test_pbkdf2_passwords_preserved_verbatim_100_percent(migration_dbs):
    """1,000,000-iteration PBKDF2 hash strings preserved character-for-character, never re-hashed."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_src = connections[source_alias]
    conn_tgt = connections[target_alias]

    with conn_src.cursor() as s_cur, conn_tgt.cursor() as t_cur:
        s_cur.execute('SELECT id, password FROM authentication_user ORDER BY id')
        s_passwords = s_cur.fetchall()
        t_cur.execute('SELECT id, password FROM authentication_user ORDER BY id')
        t_passwords = t_cur.fetchall()

    assert s_passwords == t_passwords
    for uid, pw in t_passwords:
        assert 'pbkdf2_sha256$1000000$' in pw, f"Password hash for user {uid} altered from 1,000,000 PBKDF2"


def test_batch_size_chunking_with_d1_batch(migration_dbs):
    """Batches are split into chunks of at most 50 statements and submitted via d1_batch."""
    source_alias, target_alias, _, _ = migration_dbs

    conn_src = connections[source_alias]
    with conn_src.cursor() as cur:
        for i in range(1, 126):
            cur.execute(
                'INSERT INTO departments_department (id, name, code, description, created_at) VALUES (?, ?, ?, ?, ?)',
                [i, f'Phòng ban {i:03d}', f'DEPT_{i:03d}', 'Mô tả', '2026-11-01 00:00:00']
            )

    captured_batches = []

    def mock_d1_batch(statements, using="default", **kwargs):
        captured_batches.append(statements)
        conn = connections[using]
        with conn.cursor() as cur:
            for stmt in statements:
                if isinstance(stmt, (tuple, list)):
                    cur.execute(stmt[0], stmt[1] if len(stmt) > 1 else None)
                elif isinstance(stmt, dict):
                    cur.execute(stmt["sql"], stmt.get("params"))
                else:
                    cur.execute(str(stmt))
        return [{"success": True}]

    with patch("apps.common.management.commands.migrate_pg_to_d1.d1_batch", side_effect=mock_d1_batch):
        call_command(
            'migrate_pg_to_d1',
            source_db=source_alias,
            target_db=target_alias,
            batch_size=50,
            allow_non_empty=True,
        )

    data_batches = [b for b in captured_batches if any('INSERT INTO "departments_department"' in str(s) for s in b)]
    assert len(data_batches) == 3
    assert len(data_batches[0]) == 50
    assert len(data_batches[1]) == 50
    assert len(data_batches[2]) == 25


# ==============================================================================
# 4. ISOLATION GUARD & TRANSACTION ROLLBACK
# ==============================================================================

def test_isolation_guard_error_propagates_without_cursor_fallback(migration_dbs):
    """
    Defect Area 4 (P1): When d1_batch raises OperationalError('D1 Database Isolation Guard: blocked'),
    the error must propagate and NOT be caught or bypassed via cursor fallback.
    """
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    def mock_blocked_guard(*args, **kwargs):
        raise OperationalError("D1 Database Isolation Guard: blocked")

    with patch("apps.common.management.commands.migrate_pg_to_d1.d1_batch", side_effect=mock_blocked_guard):
        with pytest.raises(OperationalError) as exc_info:
            call_command(
                'migrate_pg_to_d1',
                source_db=source_alias,
                target_db=target_alias,
                allow_non_empty=True,
            )
        assert "D1 Database Isolation Guard: blocked" in str(exc_info.value)


def test_sqlite_testbed_batch_failure_rolls_back_atomically(migration_dbs):
    """
    Defect Area 4 (P1): When a statement in a batch fails on SQLite testbed,
    the entire batch is rolled back atomically (no partial commits).
    """
    _, target_alias, _, _ = migration_dbs

    batch = [
        (
            'INSERT INTO departments_department (id, name, code, description, created_at) VALUES (?, ?, ?, ?, ?)',
            [9001, 'Batch Test Dept 1', 'BTD1', 'Desc', '2026-11-01 00:00:00']
        ),
        (
            'INSERT INTO departments_department (id, name, code, description, created_at) VALUES (?, ?, ?, ?, ?)',
            [9001, 'Batch Test Dept 1 Duplicate PK', 'BTD1_DUP', 'Desc', '2026-11-01 00:00:00']
        ),
    ]

    with pytest.raises(Exception):
        execute_batch_statements(target_alias, batch)

    with connections[target_alias].cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM departments_department WHERE id = 9001")
        count = cur.fetchone()[0]
        assert count == 0, f"Batch statements were not rolled back atomically; found {count} rows"


# ==============================================================================
# 5. RECONCILIATION ENGINE & DATA MUTATION DETECTION
# ==============================================================================

def test_d1_reconciler_full_parity_success(migration_dbs):
    """D1Reconciler validates 1:1 match across all criteria."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()

    assert result.success is True, f"Reconciliation failed: {result.errors}"
    assert len(result.errors) == 0
    assert result.summary["row_count_parity"]["match"] is True
    assert result.summary["key_set_integrity"]["match"] is True
    assert result.summary["foreign_key_integrity"]["match"] is True
    assert result.summary["password_hash_verbatim"]["match"] is True
    assert result.summary["datetime_boolean_normalization"]["match"] is True
    assert result.summary["identifier_sequence_counters"]["match"] is True


def test_d1_reconciler_detects_row_count_mismatch(migration_dbs):
    """D1Reconciler detects row count discrepancies between source and target."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute('DELETE FROM ai_suggestion_logs WHERE id = "ai-log-uuid-001"')

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()

    assert result.success is False
    assert any("Row count mismatch on ai_suggestion_logs" in err for err in result.errors)


def test_d1_reconciler_detects_missing_or_corrupted_key_set(migration_dbs):
    """D1Reconciler detects key set integrity failure (PK drift)."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute('UPDATE ai_suggestion_logs SET id = "tampered-uuid-999" WHERE id = "ai-log-uuid-001"')

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()

    assert result.success is False
    assert any("Key set mismatch on ai_suggestion_logs" in err for err in result.errors)


def test_d1_reconciler_detects_foreign_key_violations(migration_dbs):
    """D1Reconciler detects foreign key violations via PRAGMA foreign_key_check."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute("PRAGMA foreign_keys = OFF;")
        cur.execute(
            '''INSERT INTO assets_asset (
                id, asset_code, name, serial_number, category_id, specification, status,
                department_id, assigned_to_user_id, image, document, purchase_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [9999, 'AST-2026-999', 'Ghost Asset', 'SN-GHOST', 99999, '{}', 'AVAILABLE',
             None, None, '', '', '2026-01-01', '2026-11-01 00:00:00', '2026-11-01 00:00:00']
        )
        cur.execute("PRAGMA foreign_keys = ON;")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    fk_result = reconciler.check_foreign_key_integrity()

    assert fk_result["match"] is False
    assert len(fk_result["violations"]) > 0


def test_d1_reconciler_detects_password_hash_drift(migration_dbs):
    """D1Reconciler detects password hash alterations or unauthorized re-hashing."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute("UPDATE authentication_user SET password = 'tampered_hash_value' WHERE id = 701")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    pw_result = reconciler.check_password_hash_verbatim()

    assert pw_result["match"] is False
    assert pw_result["source_hash"] != pw_result["target_hash"]


def test_d1_reconciler_detects_sequence_counter_lower_than_existing_code(migration_dbs):
    """D1Reconciler flags identifier sequence counters smaller than maximum existing code."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        cur.execute("UPDATE assets_identifiersequence SET value = 1 WHERE kind = 'AST'")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    seq_result = reconciler.check_identifier_sequence_counters()

    assert seq_result["match"] is False
    assert any("out of sync" in err for err in seq_result.get("errors", []))


def test_identifier_sequence_counters_partitioned_strictly_by_kind_and_year(migration_dbs):
    """
    Defect Area 3 (P2): Identifier sequence counters must be validated strictly per (kind, year).
    Setting AST for year 2026 to 0 while AST for year 2025 is 999 must NOT hide the 2026 failure.
    """
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    call_command(
        'migrate_pg_to_d1',
        source_db=source_alias,
        target_db=target_alias,
        allow_non_empty=True,
    )

    conn_tgt = connections[target_alias]
    with conn_tgt.cursor() as cur:
        # Clear existing AST sequences and insert year 2025=999, year 2026=0
        cur.execute("DELETE FROM assets_identifiersequence WHERE kind = 'AST'")
        cur.execute(
            "INSERT INTO assets_identifiersequence (id, kind, year, value) VALUES (?, ?, ?, ?)",
            [2001, 'AST', 2025, 999]
        )
        cur.execute(
            "INSERT INTO assets_identifiersequence (id, kind, year, value) VALUES (?, ?, ?, ?)",
            [2002, 'AST', 2026, 0]
        )

    # In assets_asset, AST-2026-002 exists (max code for 2026 is 2).
    # Since year 2026 counter is 0 < 2, reconciler must fail and identify the 2026 mismatch.
    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    seq_result = reconciler.check_identifier_sequence_counters()

    assert seq_result["match"] is False
    assert any("AST" in err and "2026" in err for err in seq_result.get("errors", []))


# ==============================================================================
# 6. ROW DATA MUTATION & PERMISSION DRIFT DETECTION (Defect Area 2)
# ==============================================================================

def test_d1_reconciler_detects_data_mutation_asset_name(migration_dbs):
    """Defect Area 2: Reconciler detects changes to assets_asset.name."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE assets_asset SET name = 'MacBook Pro M3 Altered' WHERE id = 1101")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("assets_asset" in err for err in result.errors)


def test_d1_reconciler_detects_data_mutation_asset_status(migration_dbs):
    """Defect Area 2: Reconciler detects changes to assets_asset.status."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE assets_asset SET status = 'UNDER_MAINTENANCE' WHERE id = 1101")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("assets_asset" in err for err in result.errors)


def test_d1_reconciler_detects_account_drift_role(migration_dbs):
    """Defect Area 2: Reconciler detects changes to authentication_user.role."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE authentication_user SET role = 'MANAGER' WHERE id = 702")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("authentication_user" in err for err in result.errors)


def test_d1_reconciler_detects_account_drift_is_active(migration_dbs):
    """Defect Area 2: Reconciler detects changes to authentication_user.is_active (1 -> 0)."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 702")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("authentication_user" in err for err in result.errors)


def test_d1_reconciler_detects_account_drift_auth_version(migration_dbs):
    """Defect Area 2: Reconciler detects changes to authentication_user.auth_version."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE authentication_user SET auth_version = 2 WHERE id = 701")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("authentication_user" in err for err in result.errors)


def test_d1_reconciler_detects_foreign_key_drift_to_different_valid_row(migration_dbs):
    """Defect Area 2: Reconciler detects foreign key pointing to a different valid row."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)

    # Insert a second valid department in both source and target
    dept_stmt = 'INSERT INTO departments_department (id, name, code, description, created_at) VALUES (?, ?, ?, ?, ?)'
    with connections[source_alias].cursor() as s_cur, connections[target_alias].cursor() as t_cur:
        s_cur.execute(dept_stmt, [402, 'Phòng Kế Toán', 'ACC', 'Ban Kế toán', '2026-11-01 08:00:00'])
        t_cur.execute(dept_stmt, [402, 'Phòng Kế Toán', 'ACC', 'Ban Kế toán', '2026-11-01 08:00:00'])

    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    # Change user 701's department on target from 401 to 402 (both valid, so PRAGMA foreign_key_check passes)
    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE authentication_user SET department_id = 402 WHERE id = 701")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("authentication_user" in err for err in result.errors)


def test_d1_reconciler_detects_json_specification_modification(migration_dbs):
    """Defect Area 2: Reconciler detects JSON content modification (e.g. specification)."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE assets_asset SET specification = '{\"ram\": \"64GB\"}' WHERE id = 1101")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("assets_asset" in err for err in result.errors)


def test_d1_reconciler_detects_audit_log_content_modification(migration_dbs):
    """Defect Area 2: Reconciler detects audit log content modification (assets_assethistorylog)."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE assets_assethistorylog SET reason = 'Lý do bị sửa đổi trái phép' WHERE id = 1301")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False
    assert any("assets_assethistorylog" in err for err in result.errors)


def test_d1_reconciler_canonical_equivalent_values_do_not_trigger_false_positives(migration_dbs):
    """
    Defect Area 2: Canonical equivalent values do NOT trigger false positives:
    - JSON formatting differences (whitespace, key ordering)
    - Bool representation (0 vs 1, True vs False)
    - Datetime ISO representation (e.g. '2026-11-01 09:00:00' vs '2026-11-01T09:00:00Z')
    """
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    with connections[target_alias].cursor() as cur:
        # JSON formatting difference: whitespace and newline
        cur.execute("UPDATE assets_asset SET specification = '{\n  \"ram\":  \"36GB\"\n}' WHERE id = 1101")
        # Datetime ISO representation: ISO UTC 'T...Z' format
        cur.execute("UPDATE assets_asset SET created_at = '2026-11-01T09:00:00Z' WHERE id = 1101")
        # Bool integer representation
        cur.execute("UPDATE authentication_user SET is_staff = 1 WHERE id = 701")

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is True, f"Canonical equivalent triggered false positive: {result.errors}"


def test_d1_reconciler_error_messages_do_not_leak_sensitive_data(migration_dbs):
    """Defect Area 2: Error messages do NOT leak passwords, hashes, or secret credentials."""
    source_alias, target_alias, _, _ = migration_dbs
    seed_source_test_data(source_alias)
    call_command('migrate_pg_to_d1', source_db=source_alias, target_db=target_alias, allow_non_empty=True)

    secret_raw_pass = "super_secret_plain_text_password_leaked_xyz123"
    with connections[target_alias].cursor() as cur:
        cur.execute("UPDATE authentication_user SET password = ? WHERE id = 701", [secret_raw_pass])

    reconciler = D1Reconciler(source_db=source_alias, target_db=target_alias)
    result = reconciler.reconcile()
    assert result.success is False

    all_err_text = " ".join(result.errors)
    assert secret_raw_pass not in all_err_text
    assert "testSalt123" not in all_err_text
    assert "verbatimAdminHashPreservedExact1234567890=" not in all_err_text


# ==============================================================================
# 7. VALUE TYPE NORMALIZATION UNIT TESTS
# ==============================================================================

def test_normalize_value_types():
    """Value normalization handles booleans, datetimes, dates, UUIDs, JSON dictionaries."""
    assert normalize_value(True) == 1
    assert normalize_value(False) == 0
    assert normalize_value(1) == 1
    assert normalize_value(0) == 0

    now_naive = datetime.datetime(2026, 11, 1, 12, 0, 0)
    norm_naive = normalize_value(now_naive)
    assert norm_naive.startswith("2026-11-01T12:00:00")

    d = datetime.date(2026, 11, 1)
    assert normalize_value(d) == "2026-11-01"

    json_dict = {"model": "XPS", "specs": {"ram": 16}}
    assert normalize_value(json_dict) == json.dumps(json_dict, ensure_ascii=False)
