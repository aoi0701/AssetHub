"""
Test Suite: Cloudflare D1 Schema DDL, Constraints & Admin Protection Triggers.
Validates:
1. Exact byte-level parity between primary schema.sql and backend mirror.
2. Clean in-memory SQLite schema loading without syntax or dependency errors.
3. Inventory of exactly 19 tables, 2 triggers, and schema CHECK constraints.
4. Admin protection triggers preventing demotion or deletion of the last active Admin.
5. Strict 6-status CHECK constraint and holder validation on assets_asset.
6. Safe re-entrant / idempotent schema execution preserving existing table data.
7. Strict security guards on test database reset script (reset_d1_test_db.sh).
8. Segregation of destructive DROP statements to dedicated test reset tool.
"""
import sqlite3
import subprocess
from pathlib import Path
import pytest
from django.conf import settings


def get_schema_paths():
    """Locate primary and mirror schema paths across local and containerized environments."""
    mirror_path = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    if not mirror_path.exists():
        mirror_path = Path(settings.BASE_DIR) / "assethub_api" / "backends" / "d1" / "sql" / "schema.sql"

    candidate_sources = [
        Path("/cloudflare-worker/bridge/schema.sql"),
        Path(__file__).resolve().parents[5] / "cloudflare-worker" / "bridge" / "schema.sql",
        Path(settings.BASE_DIR).parent / "cloudflare-worker" / "bridge" / "schema.sql",
    ]
    source_path = None
    for candidate in candidate_sources:
        if candidate.exists():
            source_path = candidate
            break

    return source_path, mirror_path


def get_reset_script_paths():
    """Locate reset_d1_test_db.sh and reset_d1_test_db.sql across environments."""
    candidate_scripts = [
        Path("/scripts/reset_d1_test_db.sh"),
        Path("/app/scripts/reset_d1_test_db.sh"),
        Path(__file__).resolve().parents[5] / "scripts" / "reset_d1_test_db.sh",
        Path(settings.BASE_DIR).parent / "scripts" / "reset_d1_test_db.sh",
        Path(settings.BASE_DIR) / "scripts" / "reset_d1_test_db.sh",
    ]
    candidate_sqls = [
        Path("/scripts/reset_d1_test_db.sql"),
        Path("/app/scripts/reset_d1_test_db.sql"),
        Path(__file__).resolve().parents[5] / "scripts" / "reset_d1_test_db.sql",
        Path(settings.BASE_DIR).parent / "scripts" / "reset_d1_test_db.sql",
        Path(settings.BASE_DIR) / "scripts" / "reset_d1_test_db.sql",
    ]
    sh_path = next((p for p in candidate_scripts if p.exists()), None)
    sql_path = next((p for p in candidate_sqls if p.exists()), None)
    return sh_path, sql_path


@pytest.fixture
def db_conn():
    """Provides an in-memory SQLite database connection pre-loaded with official schema."""
    _, mirror_path = get_schema_paths()
    assert mirror_path.exists(), f"Mirror schema.sql not found at {mirror_path}"

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    schema_sql = mirror_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_schema_ddl_copies_are_identical():
    """Test 1: Verify primary schema.sql and backend mirror are 100% byte-identical."""
    source_path, mirror_path = get_schema_paths()
    assert source_path is not None, "Primary cloudflare-worker/bridge/schema.sql missing"
    assert mirror_path.exists(), "Mirror backend/assethub_api/backends/d1/sql/schema.sql missing"

    source_bytes = source_path.read_bytes()
    mirror_bytes = mirror_path.read_bytes()

    assert len(source_bytes) > 0, "Primary schema.sql must not be empty"
    assert source_bytes == mirror_bytes, (
        "Schema copies are out of sync. Run scripts/sync_d1_schema.sh to synchronize."
    )


def test_schema_loads_cleanly_into_in_memory_sqlite():
    """Test 2: Load official schema.sql into in-memory SQLite with foreign keys enabled."""
    _, mirror_path = get_schema_paths()
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    schema_sql = mirror_path.read_text(encoding="utf-8")

    # executescript must succeed without raising any SQLite syntax or execution errors
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()


def test_verify_19_tables_and_2_triggers_and_check_constraints(db_conn):
    """Test 3: Verify inventory of exactly 19 tables, 2 triggers, and asset CHECK constraints."""
    cursor = db_conn.cursor()

    # Verify 19 tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';")
    tables = {row[0] for row in cursor.fetchall()}

    expected_tables = {
        "departments_department",
        "departments_category",
        "django_content_type",
        "auth_permission",
        "auth_group",
        "auth_group_permissions",
        "authentication_user",
        "authentication_user_groups",
        "authentication_user_user_permissions",
        "django_admin_log",
        "django_migrations",
        "django_session",
        "assets_identifiersequence",
        "assets_asset",
        "requests_assetrequest",
        "assets_assethistorylog",
        "_assert_guard",
        "_idempotency_log",
        "ai_suggestion_logs",
    }
    assert tables == expected_tables, (
        f"Tables mismatch. Extra: {tables - expected_tables}, Missing: {expected_tables - tables}"
    )
    assert len(tables) == 19

    # Verify 2 triggers
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger';")
    triggers = {row[0] for row in cursor.fetchall()}

    expected_triggers = {
        "protect_last_admin_before_update",
        "protect_last_admin_before_delete",
    }
    assert triggers == expected_triggers, (
        f"Triggers mismatch. Extra: {triggers - expected_triggers}, Missing: {expected_triggers - triggers}"
    )

    # Verify CHECK constraints defined on assets_asset
    cursor.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'assets_asset';")
    asset_ddl = cursor.fetchone()[0]
    assert "chk_asset_status" in asset_ddl
    assert "chk_asset_available_no_holder" in asset_ddl
    for status in ('AVAILABLE', 'IN_USE', 'DAMAGED_PENDING_REPAIR', 'UNDER_MAINTENANCE', 'PENDING_DISPOSAL', 'DISPOSED'):
        assert status in asset_ddl


def test_admin_protection_triggers(db_conn):
    """Test 4: Verify protect_last_admin triggers block demotion or deletion of last active Admin."""
    # Seed single active admin
    db_conn.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (1, 'sole_admin', 'hash123', 'admin@example.com', 'ADMIN', 1)"
    )
    db_conn.commit()

    # 4a. Demote sole active admin -> must fail with exact trigger message
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        db_conn.execute("UPDATE authentication_user SET role = 'USER' WHERE id = 1")

    # 4b. Deactivate sole active admin -> must fail with exact trigger message
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        db_conn.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 1")

    # 4c. Delete sole active admin -> must fail with exact trigger message
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete the last active Admin"):
        db_conn.execute("DELETE FROM authentication_user WHERE id = 1")

    # 4d. Add second active admin
    db_conn.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (2, 'second_admin', 'hash456', 'admin2@example.com', 'ADMIN', 1)"
    )
    db_conn.commit()

    # Now demoting sole_admin succeeds because second_admin remains active admin
    db_conn.execute("UPDATE authentication_user SET role = 'USER' WHERE id = 1")
    db_conn.commit()

    # Second admin is now the sole active admin; deleting second admin must fail
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete the last active Admin"):
        db_conn.execute("DELETE FROM authentication_user WHERE id = 2")


def test_asset_status_and_holder_check_constraints(db_conn):
    """Test 5: Verify 6-status CHECK constraint and holder validation on assets_asset."""
    # Seed prerequisites: department, category, and assigned user
    db_conn.execute("INSERT INTO departments_department (id, name, code) VALUES (1, 'Kho Vận', 'DEPT_WH')")
    db_conn.execute("INSERT INTO departments_category (id, name) VALUES (1, 'Máy tính xách tay')")
    db_conn.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (10, 'assigned_staff', 'hash', 'staff@example.com', 'USER', 1)"
    )
    db_conn.commit()

    # 5a. Verify all 6 valid statuses insert successfully
    valid_statuses = [
        'AVAILABLE',
        'IN_USE',
        'DAMAGED_PENDING_REPAIR',
        'UNDER_MAINTENANCE',
        'PENDING_DISPOSAL',
        'DISPOSED',
    ]
    for idx, status in enumerate(valid_statuses, start=1):
        assigned_user = 10 if status == 'IN_USE' else None
        db_conn.execute(
            "INSERT INTO assets_asset (id, asset_code, name, category_id, status, department_id, assigned_to_user_id) "
            "VALUES (?, ?, ?, 1, ?, 1, ?)",
            (idx, f"AST-TEST-{idx:03d}", f"Asset {idx}", status, assigned_user)
        )
    db_conn.commit()

    # 5b. Insert asset with invalid status -> must fail with CHECK constraint error
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        db_conn.execute(
            "INSERT INTO assets_asset (id, asset_code, name, category_id, status, department_id) "
            "VALUES (99, 'AST-INVALID-001', 'Invalid Asset', 1, 'INVALID', 1)"
        )

    # 5c. Insert AVAILABLE asset with assigned_to_user_id != NULL -> must fail with CHECK constraint error
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        db_conn.execute(
            "INSERT INTO assets_asset (id, asset_code, name, category_id, status, department_id, assigned_to_user_id) "
            "VALUES (100, 'AST-BAD-001', 'Available With User', 1, 'AVAILABLE', 1, 10)"
        )

    # 5d. Update existing IN_USE asset to AVAILABLE without unassigning user -> must fail with CHECK constraint error
    # Asset id=2 is IN_USE with assigned_to_user_id = 10
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        db_conn.execute("UPDATE assets_asset SET status = 'AVAILABLE' WHERE id = 2")


def test_schema_reentrant_preserves_data():
    """
    Test 6: Verify schema idempotency and re-entrant execution.
    Executing schema.sql repeatedly against an existing populated database MUST
    preserve all tables, triggers, and user data without dropping or truncating tables.
    """
    _, mirror_path = get_schema_paths()
    schema_sql = mirror_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Initial schema load
    conn.executescript(schema_sql)
    conn.commit()

    # Seed test data in departments, category, user, and assets
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO departments_department (id, name, code, description) "
        "VALUES (10, 'Phòng Kỹ Thuật', 'DEPT_TECH', 'Phòng kỹ thuật và công nghệ');"
    )
    cursor.execute(
        "INSERT INTO departments_category (id, name, description) "
        "VALUES (20, 'Thiết bị mạng', 'Router, switch và phụ kiện');"
    )
    cursor.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (30, 'tech_lead', 'pbkdf2_hash', 'tech@example.com', 'MANAGER', 1);"
    )
    cursor.execute(
        "INSERT INTO assets_asset (id, asset_code, name, category_id, status, department_id, assigned_to_user_id) "
        "VALUES (100, 'AST-TECH-001', 'Cisco Switch 2960', 20, 'IN_USE', 10, 30);"
    )
    cursor.execute(
        "INSERT INTO assets_asset (id, asset_code, name, category_id, status, department_id) "
        "VALUES (101, 'AST-TECH-002', 'Unallocated Router', 20, 'AVAILABLE', 10);"
    )
    conn.commit()

    # Re-run the full schema.sql a second time (Re-entrant / Idempotent execution)
    conn.executescript(schema_sql)
    conn.commit()

    # Assert 100% data preservation in departments_department
    cursor.execute("SELECT id, name, code, description FROM departments_department WHERE id = 10;")
    dept = cursor.fetchone()
    assert dept == (10, 'Phòng Kỹ Thuật', 'DEPT_TECH', 'Phòng kỹ thuật và công nghệ')

    # Assert 100% data preservation in assets_asset
    cursor.execute(
        "SELECT id, asset_code, name, status, department_id, assigned_to_user_id FROM assets_asset WHERE id = 100;"
    )
    asset1 = cursor.fetchone()
    assert asset1 == (100, 'AST-TECH-001', 'Cisco Switch 2960', 'IN_USE', 10, 30)

    cursor.execute(
        "SELECT id, asset_code, name, status, department_id, assigned_to_user_id FROM assets_asset WHERE id = 101;"
    )
    asset2 = cursor.fetchone()
    assert asset2 == (101, 'AST-TECH-002', 'Unallocated Router', 'AVAILABLE', 10, None)

    # Verify triggers are intact and active after re-running schema
    cursor.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (1, 'sole_admin', 'hash', 'admin@example.com', 'ADMIN', 1);"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete the last active Admin"):
        conn.execute("DELETE FROM authentication_user WHERE id = 1;")

    conn.close()


def test_reset_script_security_guards():
    """
    Test 7: Verify reset_d1_test_db.sh enforces strict security guards.
    - Missing --confirm-destructive MUST fail with non-zero exit code.
    - Missing or incorrect target database MUST fail with non-zero exit code.
    - Only target database 'assethub-test-d1' with --confirm-destructive is accepted.
    """
    sh_path, sql_path = get_reset_script_paths()
    assert sh_path is not None, "reset_d1_test_db.sh not found in candidate paths"
    assert sql_path is not None, "reset_d1_test_db.sql not found in candidate paths"
    assert sh_path.exists(), f"Script missing at {sh_path}"
    assert sql_path.exists(), f"SQL missing at {sql_path}"

    # Guard Check 1: No arguments -> Must exit non-zero
    proc = subprocess.run([str(sh_path)], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "confirm-destructive" in proc.stderr

    # Guard Check 2: Missing --confirm-destructive with correct database -> Must exit non-zero
    proc = subprocess.run([str(sh_path), "--database", "assethub-test-d1"], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "confirm-destructive" in proc.stderr

    # Guard Check 3: With --confirm-destructive but missing database -> Must exit non-zero
    proc = subprocess.run([str(sh_path), "--confirm-destructive"], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "Database name is required" in proc.stderr or "assethub-test-d1" in proc.stderr

    # Guard Check 4: With --confirm-destructive but wrong database (e.g., production) -> Must exit non-zero
    proc = subprocess.run(
        [str(sh_path), "--confirm-destructive", "--database", "production_d1"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "forbidden" in proc.stderr or "assethub-test-d1" in proc.stderr

    # Guard Check 5: Another disallowed database name -> Must exit non-zero
    proc = subprocess.run(
        [str(sh_path), "--confirm-destructive", "--database", "assethub"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0

    # Guard Check 6: Valid guards with --dry-run -> Must exit with code 0
    proc = subprocess.run(
        [str(sh_path), "--confirm-destructive", "--database", "assethub-test-d1", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Security guards verified" in proc.stdout


def test_reset_sql_contains_destructive_drops():
    """
    Test 8: Verify reset_d1_test_db.sql contains the complete teardown DDL
    (DROP TABLE IF EXISTS and DROP TRIGGER IF EXISTS) isolated from production schema.
    """
    _, sql_path = get_reset_script_paths()
    assert sql_path is not None, "reset_d1_test_db.sql not found in candidate paths"
    assert sql_path.exists(), f"reset_d1_test_db.sql missing at {sql_path}"

    content = sql_path.read_text(encoding="utf-8")
    assert "DROP TRIGGER IF EXISTS protect_last_admin_before_update;" in content
    assert "DROP TRIGGER IF EXISTS protect_last_admin_before_delete;" in content
    assert "DROP TABLE IF EXISTS departments_department;" in content
    assert "DROP TABLE IF EXISTS assets_asset;" in content
    assert "DROP TABLE IF EXISTS requests_assetrequest;" in content
    assert "DROP TABLE IF EXISTS authentication_user;" in content
    assert "DROP TABLE IF EXISTS _assert_guard;" in content
    assert "DROP TABLE IF EXISTS _idempotency_log;" in content
    assert "DROP TABLE IF EXISTS ai_suggestion_logs;" in content
