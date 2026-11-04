"""
End-to-End Rehearsal Test Suite & Independent Verification:
PostgreSQL Test Source -> Cloudflare D1 Local Target via Worker Bridge.

Validates the full local rehearsal sequence:
1. Preflight:
   - Source is authentic PostgreSQL on port 5434 with read-only user 'assethub_ro_user'.
   - Target is Cloudflare D1 via Worker Bridge on port 8787 (vendor 'cloudflare_d1').
   - Source contains mandatory tables and does NOT contain 'ai_suggestion_logs'.
   - Target contains all tables initialized with exactly 0 rows.
2. Dry Run:
   - 'migrate_pg_to_d1 --dry-run' outputs 4-tier plan and computes checksums.
   - 0 rows written to target; source completely untouched.
3. Real Migration:
   - 'migrate_pg_to_d1' executes without '--allow-non-empty'.
   - Ingests all tiers in topological order with primary keys preserved.
4. 1:1 Automated Reconciliation:
   - 'D1Reconciler' validates across all 7 technical criteria:
     (1) Row Count Parity
     (2) Key Set Integrity
     (3) Foreign Key Integrity (PRAGMA foreign_key_check)
     (4) Password Hash Verbatim Check
     (5) Datetime & Boolean Normalization
     (6) Identifier Sequence Counters
     (7) Row Content Parity
5. PBKDF2 1,000,000-Iteration Password Verification:
   - Tests migrated users via Django's check_password against known plaintexts.
   - Strict secrecy: No passwords, salts, or hashes logged or printed.
6. Identifier Sequences:
   - Validates ('AST', 2026) counter >= max_code in assets_asset.
   - Validates ('REQ', 2026) counter >= max_code in requests_assetrequest.
7. Non-Empty Guard:
   - Running migration again on the populated target without '--allow-non-empty'
     raises CommandError before any writes.
8. Negative Defect Detection:
   - In isolated mutation tests, intentionally alters:
     - Password hash
     - User boolean/permissions ('is_staff')
     - Identifier sequence counter
     - Table content
     - Row deletion
   - Asserts D1Reconciler detects and reports each defect.
"""
import io
import os
import secrets
import time
from typing import Dict, Any, Generator

import pytest
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.management import call_command, CommandError
from django.db import connections

from apps.common.management.commands.migrate_pg_to_d1 import (
    D1Reconciler,
    ALL_TABLES,
    check_table_exists,
)
from apps.common.tests.harness_pg_source import (
    PGSourceHarness,
    TEST_USERS,
    is_port_in_use as is_pg_port_in_use,
)
from apps.common.tests.harness_d1_target import (
    D1TargetHarness,
    check_port_in_use as is_d1_port_in_use,
    DEFAULT_BRIDGE_URL,
    DEFAULT_TOKEN,
    TARGET_TABLES,
)

pytestmark = pytest.mark.rehearsal

SRC_ALIAS = "rehearsal_pg_src"
TGT_ALIAS = "rehearsal_d1_tgt"


@pytest.fixture(scope="module")
def rehearsal_environment(django_db_blocker) -> Generator[Dict[str, Any], None, None]:
    """
    Module-scoped fixture orchestrating both ends of the rehearsal:
    1. Uses PGSourceHarness to spin up isolated Postgres and seed authentic dataset.
    2. Uses D1TargetHarness to spin up local D1 worker bridge.
    3. Configures dynamic Django database aliases: 'rehearsal_pg_src' & 'rehearsal_d1_tgt'.
    4. Guarantees clean teardown of both harnesses and dynamic databases in finally block.
    """
    with django_db_blocker.unblock():
        run_id = secrets.token_hex(6)
        pg_harness = PGSourceHarness(run_id=run_id, port=5434)
        d1_harness = D1TargetHarness(run_id=run_id, port=8787)

        try:
            pg_harness.setup()
            d1_harness.setup()

            pg_conf = {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": pg_harness.dbname,
                "USER": pg_harness.ro_user,
                "PASSWORD": pg_harness.ro_password,
                "HOST": pg_harness.host,
                "PORT": str(pg_harness.port),
                "TIME_ZONE": None,
                "CONN_MAX_AGE": 0,
                "CONN_HEALTH_CHECKS": False,
                "OPTIONS": {},
                "AUTOCOMMIT": True,
                "ATOMIC_REQUESTS": False,
            }

            d1_conf = {
                "ENGINE": "assethub_api.backends.d1",
                "NAME": "assethub-test-d1",
                "BRIDGE_URL": d1_harness.base_url,
                "BRIDGE_TOKEN": d1_harness.token,
                "TIMEOUT": 45.0,
                "TIME_ZONE": None,
                "CONN_MAX_AGE": 0,
                "CONN_HEALTH_CHECKS": False,
                "OPTIONS": {},
                "AUTOCOMMIT": True,
                "ATOMIC_REQUESTS": False,
            }

            settings.DATABASES[SRC_ALIAS] = pg_conf
            settings.DATABASES[TGT_ALIAS] = d1_conf
            connections.databases[SRC_ALIAS] = pg_conf
            connections.databases[TGT_ALIAS] = d1_conf

            env_info = {
                "run_id": run_id,
                "pg_harness": pg_harness,
                "d1_harness": d1_harness,
                "src_alias": SRC_ALIAS,
                "tgt_alias": TGT_ALIAS,
            }

            yield env_info
        finally:
            # Clean teardown ALWAYS runs for BOTH, even if tests fail!
            try:
                d1_harness.teardown()
            finally:
                try:
                    pg_harness.teardown()
                finally:
                    # Clean up settings.DATABASES
                    for alias in (SRC_ALIAS, TGT_ALIAS):
                        if alias in connections:
                            try:
                                connections[alias].close()
                            except Exception:
                                pass
                            del connections[alias]
                        settings.DATABASES.pop(alias, None)
                        connections.databases.pop(alias, None)


def _ensure_migrated():
    """Helper ensuring rehearsal_d1_tgt is populated if a test runs standalone."""
    conn_tgt = connections[TGT_ALIAS]
    with conn_tgt.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM "authentication_user"')
        cnt = cur.fetchone()[0]
    if cnt == 0:
        call_command(
            "migrate_pg_to_d1",
            source_db=SRC_ALIAS,
            target_db=TGT_ALIAS,
        )


# ==============================================================================
# (0) TEARDOWN RELIABILITY REGRESSION TEST
# ==============================================================================

def test_00_teardown_reliability_regression():
    """
    Teardown Reliability Regression Test:
    Verifies that if a test in rehearsal fails or raises an AssertionError,
    the finally block still executes and both port 8787 and port 5434 are cleanly released.
    """
    run_id = f"td_{secrets.token_hex(4)}"
    test_pg_port = 5434 if not is_pg_port_in_use(5434) else 5435
    test_d1_port = 8787 if not is_d1_port_in_use(8787) else 8788

    pg_harness = PGSourceHarness(run_id=run_id, port=test_pg_port)
    d1_harness = D1TargetHarness(run_id=run_id, port=test_d1_port)

    executed_finally = False
    caught_assertion_error = False

    try:
        pg_harness.start()
        d1_harness.setup(clean_first=True)

        assert is_pg_port_in_use(test_pg_port), f"Postgres port {test_pg_port} must be open"
        assert is_d1_port_in_use(test_d1_port), f"D1 bridge port {test_d1_port} must be open"

        try:
            # Simulate an unexpected test assertion failure inside the rehearsal lifecycle
            raise AssertionError("Simulated test failure during migration execution")
        finally:
            executed_finally = True
            try:
                d1_harness.teardown()
            finally:
                pg_harness.teardown()

    except AssertionError as err:
        caught_assertion_error = True
        assert str(err) == "Simulated test failure during migration execution"
    finally:
        # Guarantee safety cleanup in case harness start threw before inner try
        try:
            d1_harness.teardown()
        except Exception:
            pass
        try:
            pg_harness.teardown()
        except Exception:
            pass

    assert executed_finally is True, "The finally block must execute when a test fails"
    assert caught_assertion_error is True, "The AssertionError must propagate out of the test block"
    assert not is_d1_port_in_use(test_d1_port), f"Port {test_d1_port} was not released after teardown"
    assert not is_pg_port_in_use(test_pg_port), f"Port {test_pg_port} was not released after teardown"


# ==============================================================================
# (1) PREFLIGHT VERIFICATION
# ==============================================================================

def test_01_rehearsal_preflight_checks(rehearsal_environment):
    """
    Preflight Requirement:
    - Verify source is PostgreSQL with read-only user on port 5434.
    - Verify target is Cloudflare D1 via Worker Bridge.
    - Verify target has all target tables (including ai_suggestion_logs) with 0 rows.
    - Verify source does NOT have ai_suggestion_logs.
    """
    conn_src = connections[SRC_ALIAS]
    conn_tgt = connections[TGT_ALIAS]
    pg_harness: PGSourceHarness = rehearsal_environment["pg_harness"]

    # Verify vendors
    assert conn_src.vendor == "postgresql", f"Expected postgresql vendor for source, got {conn_src.vendor}"
    assert conn_tgt.vendor in ("cloudflare_d1", "d1"), f"Expected cloudflare_d1 vendor for target, got {conn_tgt.vendor}"

    # Verify active user on source is read-only user
    with conn_src.cursor() as cur:
        cur.execute("SELECT current_user, current_database()")
        db_user, current_db = cur.fetchone()
        assert db_user == pg_harness.ro_user, f"Expected read-only user '{pg_harness.ro_user}', got '{db_user}'"
        assert current_db == pg_harness.dbname

    # Verify source schema isolation: 16 tables, ai_suggestion_logs absent
    src_isolation = pg_harness.verify_schema_isolation()
    assert src_isolation["verified"] is True
    assert src_isolation["ai_suggestion_logs_absent"] is True
    assert not check_table_exists(conn_src, "ai_suggestion_logs")

    # Verify target tables exist and all have exactly 0 rows
    d1_harness: D1TargetHarness = rehearsal_environment["d1_harness"]
    clean_counts = d1_harness.verify_clean()
    assert len(clean_counts) >= 16, f"Expected >= 16 target tables, got {len(clean_counts)}"
    assert "ai_suggestion_logs" in clean_counts, "Target must include 'ai_suggestion_logs'"

    for table, count in clean_counts.items():
        assert count == 0, f"Target table '{table}' must have 0 rows before migration, found {count}"


# ==============================================================================
# (2) DRY RUN
# ==============================================================================

def test_02_rehearsal_dry_run_zero_writes(rehearsal_environment):
    """
    Dry Run Requirement:
    - Execute 'migrate_pg_to_d1 --dry-run'.
    - Verify 4-tier plan and calculated password checksum.
    - Verify 0 rows written to target database.
    """
    out = io.StringIO()
    call_command(
        "migrate_pg_to_d1",
        source_db=SRC_ALIAS,
        target_db=TGT_ALIAS,
        dry_run=True,
        stdout=out,
    )
    output = out.getvalue()

    assert "DRY-RUN" in output
    assert "Tier 1" in output
    assert "Tier 2" in output
    assert "Tier 3" in output
    assert "Tier 4" in output
    assert "Source password hash SHA-256" in output

    # Verify target remains completely empty
    d1_harness: D1TargetHarness = rehearsal_environment["d1_harness"]
    clean_counts = d1_harness.verify_clean()
    assert sum(clean_counts.values()) == 0, "Dry run must NOT write any rows to target database"


# ==============================================================================
# (3) REAL MIGRATION & (4) 1:1 RECONCILIATION
# ==============================================================================

def test_03_rehearsal_real_migration_and_reconciliation(rehearsal_environment):
    """
    Real Migration & Reconciliation Requirement:
    - Execute 'migrate_pg_to_d1' without '--allow-non-empty'.
    - Validate 100% success and 1:1 parity via D1Reconciler across all 7 criteria.
    """
    out = io.StringIO()
    call_command(
        "migrate_pg_to_d1",
        source_db=SRC_ALIAS,
        target_db=TGT_ALIAS,
        stdout=out,
    )
    output = out.getvalue()
    assert "Reconciliation PASSED: 100% 1:1 parity confirmed" in output or "All tables migrated successfully" in output

    # Run independent D1Reconciler verification
    reconciler = D1Reconciler(source_db=SRC_ALIAS, target_db=TGT_ALIAS)
    result = reconciler.reconcile()

    assert result.success is True, f"1:1 Reconciliation failed with errors: {result.errors}"
    assert len(result.errors) == 0
    assert result.summary["row_count_parity"]["match"] is True
    assert result.summary["key_set_integrity"]["match"] is True
    assert result.summary["foreign_key_integrity"]["match"] is True
    assert result.summary["password_hash_verbatim"]["match"] is True
    assert result.summary["datetime_boolean_normalization"]["match"] is True
    assert result.summary["identifier_sequence_counters"]["match"] is True
    assert result.summary["row_content_parity"]["match"] is True


# ==============================================================================
# (5) PBKDF2 PASSWORD AUTHENTICATION CHECK
# ==============================================================================

def test_04_rehearsal_pbkdf2_password_check(rehearsal_environment):
    """
    PBKDF2 Password Check Requirement:
    - Verify migrated users' passwords via Django 'check_password' against known plaintexts.
    - Zero printing, logging, or leaking of secrets.
    """
    _ensure_migrated()
    conn_tgt = connections[TGT_ALIAS]

    with conn_tgt.cursor() as cur:
        cur.execute("SELECT username, password FROM authentication_user ORDER BY id")
        migrated_users = cur.fetchall()

    user_map = {u["username"]: u["raw_password"] for u in TEST_USERS.values()}
    assert len(migrated_users) == len(user_map), f"Expected {len(user_map)} users, found {len(migrated_users)}"

    for username, pw_hash in migrated_users:
        assert username in user_map, f"Unexpected username '{username}' in migrated target"
        expected_raw_password = user_map[username]

        # Verify hash iteration format without logging the secret
        assert "pbkdf2_sha256$1000000$" in pw_hash

        # Cryptographic verification via Django auth hasher
        is_valid = check_password(expected_raw_password, pw_hash)
        assert is_valid is True, f"Authentication check failed for user '{username}'"


# ==============================================================================
# (6) IDENTIFIER SEQUENCES CHECK
# ==============================================================================

def test_05_rehearsal_identifier_sequences(rehearsal_environment):
    """
    Identifier Sequences Requirement:
    - Verify counter >= max_code for ('AST', 2026) and ('REQ', 2026).
    """
    _ensure_migrated()
    conn_tgt = connections[TGT_ALIAS]

    with conn_tgt.cursor() as cur:
        cur.execute("SELECT kind, year, value FROM assets_identifiersequence WHERE year = 2026")
        seq_records = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        cur.execute("SELECT asset_code FROM assets_asset WHERE asset_code LIKE 'AST-2026-%'")
        ast_numbers = [int(r[0].split("-")[-1]) for r in cur.fetchall() if "-" in r[0]]
        max_ast_code = max(ast_numbers) if ast_numbers else 0

        cur.execute("SELECT request_code FROM requests_assetrequest WHERE request_code LIKE 'REQ-2026-%'")
        req_numbers = [int(r[0].split("-")[-1]) for r in cur.fetchall() if "-" in r[0]]
        max_req_code = max(req_numbers) if req_numbers else 0

    assert ("AST", 2026) in seq_records, "Missing sequence counter for ('AST', 2026)"
    assert ("REQ", 2026) in seq_records, "Missing sequence counter for ('REQ', 2026)"

    ast_seq_val = seq_records[("AST", 2026)]
    req_seq_val = seq_records[("REQ", 2026)]

    assert ast_seq_val >= max_ast_code, f"AST sequence ({ast_seq_val}) must be >= max asset code ({max_ast_code})"
    assert req_seq_val >= max_req_code, f"REQ sequence ({req_seq_val}) must be >= max request code ({max_req_code})"


# ==============================================================================
# (7) NON-EMPTY TARGET GUARD
# ==============================================================================

def test_06_rehearsal_non_empty_target_guard(rehearsal_environment):
    """
    Non-Empty Guard Requirement:
    - Execute migration again on the now non-empty target without '--allow-non-empty'.
    - Assert CommandError is raised before any writes occur.
    """
    _ensure_migrated()

    conn_tgt = connections[TGT_ALIAS]
    # Record row count of target before attempting repeat migration
    with conn_tgt.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM authentication_user")
        count_before = cur.fetchone()[0]

    with pytest.raises(CommandError) as exc_info:
        call_command(
            "migrate_pg_to_d1",
            source_db=SRC_ALIAS,
            target_db=TGT_ALIAS,
        )

    err_msg = str(exc_info.value).lower()
    assert "target database" in err_msg or "not empty" in err_msg
    assert "--allow-non-empty" in str(exc_info.value)

    # Verify no writes occurred
    with conn_tgt.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM authentication_user")
        count_after = cur.fetchone()[0]
    assert count_after == count_before


# ==============================================================================
# (8) NEGATIVE DEFECT DETECTION (ISOLATED TESTS)
# ==============================================================================

def test_07_negative_defect_detection_password_mutation(rehearsal_environment):
    """Defect Detection: Altering a password hash on target causes D1Reconciler to report failure."""
    _ensure_migrated()
    conn_tgt = connections[TGT_ALIAS]
    rec = D1Reconciler(source_db=SRC_ALIAS, target_db=TGT_ALIAS)

    # 1. Fetch original password hash for developer user (id=3)
    with conn_tgt.cursor() as cur:
        cur.execute("SELECT password FROM authentication_user WHERE id = 3")
        orig_hash = cur.fetchone()[0]
        # Mutate to tampered hash
        cur.execute("UPDATE authentication_user SET password = 'tampered_pbkdf2_hash_value' WHERE id = 3")

    try:
        res = rec.reconcile()
        assert res.success is False
        assert any("password" in err.lower() for err in res.errors)
    finally:
        # Restore original hash
        with conn_tgt.cursor() as cur:
            cur.execute("UPDATE authentication_user SET password = %s WHERE id = 3", [orig_hash])

    # Verify restored state passes
    assert rec.reconcile().success is True


def test_08_negative_defect_detection_boolean_permission_mutation(rehearsal_environment):
    """Defect Detection: Altering boolean is_staff on developer user triggers normalization/content failure."""
    _ensure_migrated()
    conn_tgt = connections[TGT_ALIAS]
    rec = D1Reconciler(source_db=SRC_ALIAS, target_db=TGT_ALIAS)

    with conn_tgt.cursor() as cur:
        cur.execute("SELECT is_staff FROM authentication_user WHERE id = 3")
        orig_val = cur.fetchone()[0]
        # Mutate is_staff from 0 to 1
        cur.execute("UPDATE authentication_user SET is_staff = 1 WHERE id = 3")

    try:
        res = rec.reconcile()
        assert res.success is False
        assert any("is_staff" in err.lower() or "content" in err.lower() for err in res.errors)
    finally:
        with conn_tgt.cursor() as cur:
            cur.execute("UPDATE authentication_user SET is_staff = %s WHERE id = 3", [orig_val])

    assert rec.reconcile().success is True


def test_09_negative_defect_detection_sequence_counter_mutation(rehearsal_environment):
    """Defect Detection: Tampering with identifier sequence value causes sequence counter failure."""
    _ensure_migrated()
    conn_tgt = connections[TGT_ALIAS]
    rec = D1Reconciler(source_db=SRC_ALIAS, target_db=TGT_ALIAS)

    with conn_tgt.cursor() as cur:
        cur.execute("SELECT value FROM assets_identifiersequence WHERE kind = 'AST' AND year = 2026")
        orig_val = cur.fetchone()[0]
        # Mutate counter to 0
        cur.execute("UPDATE assets_identifiersequence SET value = 0 WHERE kind = 'AST' AND year = 2026")

    try:
        res = rec.reconcile()
        assert res.success is False
        assert any("sequence" in err.lower() or "counter" in err.lower() or "ast" in err.lower() for err in res.errors)
    finally:
        with conn_tgt.cursor() as cur:
            cur.execute("UPDATE assets_identifiersequence SET value = %s WHERE kind = 'AST' AND year = 2026", [orig_val])

    assert rec.reconcile().success is True


def test_10_negative_defect_detection_row_deletion(rehearsal_environment):
    """Defect Detection: Deleting a leaf record from assets_assethistorylog causes row count and key set failure."""
    _ensure_migrated()
    conn_tgt = connections[TGT_ALIAS]
    rec = D1Reconciler(source_db=SRC_ALIAS, target_db=TGT_ALIAS)

    cols = (
        "id, asset_id, actor_id, action_type, from_status, to_status, from_user_id, to_user_id, "
        "from_department_id, to_department_id, reason, asset_code_snapshot, asset_name_snapshot, created_at"
    )

    with conn_tgt.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM assets_assethistorylog WHERE id = 1")
        row_snapshot = cur.fetchone()
        cur.execute("DELETE FROM assets_assethistorylog WHERE id = 1")

    try:
        res = rec.reconcile()
        assert res.success is False
        assert any("assets_assethistorylog" in err for err in res.errors)
        assert res.summary["row_count_parity"]["match"] is False
    finally:
        with conn_tgt.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(row_snapshot))
            cur.execute(
                f"INSERT INTO assets_assethistorylog ({cols}) VALUES ({placeholders})",
                list(row_snapshot),
            )

    assert rec.reconcile().success is True


def test_11_negative_defect_detection_column_content_mutation(rehearsal_environment):
    """Defect Detection: Altering department description causes row content mismatch failure."""
    _ensure_migrated()
    conn_tgt = connections[TGT_ALIAS]
    rec = D1Reconciler(source_db=SRC_ALIAS, target_db=TGT_ALIAS)

    with conn_tgt.cursor() as cur:
        cur.execute("SELECT description FROM departments_department WHERE id = 1")
        orig_desc = cur.fetchone()[0]
        cur.execute("UPDATE departments_department SET description = 'Tampered Department Description' WHERE id = 1")

    try:
        res = rec.reconcile()
        assert res.success is False
        assert any("departments_department" in err for err in res.errors)
        assert res.summary["row_content_parity"]["match"] is False
    finally:
        with conn_tgt.cursor() as cur:
            cur.execute("UPDATE departments_department SET description = %s WHERE id = 1", [orig_desc])

    assert rec.reconcile().success is True
