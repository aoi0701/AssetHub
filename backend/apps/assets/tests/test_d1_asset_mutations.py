"""
Unit tests for Cloudflare D1 Asset Mutations, OCC Assert Guard, Serializers, and Identifiers.
Validates:
1. Atomic SQLite / PostgreSQL UPSERT RETURNING for generate_identifier.
2. AssetUpdateSerializer strict allowlist, protection of status/holder/department/code,
   and rejection of DISPOSED assets via ConflictError.
3. All 9 lifecycle actions in D1 mode (guarded batch OCC execution).
4. Statement builder helper methods for Agent C's RequestService.
5. OCC conflict detection: assert_guard CHECK failure raises ConflictError with zero orphan logs.
"""

import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from django.db import DatabaseError
from rest_framework.exceptions import ValidationError

from apps.assets.identifiers import generate_identifier
from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.assets.serializers import AssetUpdateSerializer
from apps.assets.services import AssetService
from apps.authentication.models import User
from apps.common.exceptions import ConflictError
from apps.departments.models import Category, Department


# =============================================================================
# Helper Fixtures & In-Memory SQLite Setup
# =============================================================================

class MockCursorWrapper:
    """Wraps a sqlite3 cursor to support %s placeholder substitution in raw queries."""
    def __init__(self, sqlite_cur):
        self.cur = sqlite_cur

    def execute(self, sql, params=None):
        if params is not None and "%s" in sql:
            sql = sql.replace("%s", "?")
        return self.cur.execute(sql, params or [])

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def close(self):
        self.cur.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class MockConnectionWrapper:
    """Mock Django connection backed by in-memory SQLite for isolated identifier testing."""
    def __init__(self, sqlite_conn):
        self.conn = sqlite_conn
        self.vendor = "sqlite"

    def cursor(self):
        return MockCursorWrapper(self.conn.cursor())


@pytest.fixture
def sqlite_identifier_conn():
    """In-memory SQLite database initialized with assets_identifiersequence table."""
    conn = sqlite3.connect(":memory:", autocommit=True)
    conn.execute(
        """
        CREATE TABLE assets_identifiersequence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            year INTEGER NOT NULL,
            value INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT unique_identifier_sequence_kind_year UNIQUE (kind, year)
        );
        """
    )
    yield MockConnectionWrapper(conn)
    conn.close()


@pytest.fixture
def sqlite_occ_conn():
    """In-memory SQLite database with Asset, AssertGuard, and AuditLog tables."""
    conn = sqlite3.connect(":memory:", autocommit=False)
    conn.execute(
        """
        CREATE TABLE assets_asset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            department_id INTEGER,
            assigned_to_user_id INTEGER,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE _assert_guard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rows_affected INTEGER NOT NULL CHECK(rows_affected > 0),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE assets_assethistorylog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            actor_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            from_status TEXT NOT NULL DEFAULT '',
            to_status TEXT NOT NULL,
            from_user_id INTEGER,
            to_user_id INTEGER,
            from_department_id INTEGER,
            to_department_id INTEGER,
            reason TEXT NOT NULL DEFAULT '',
            asset_code_snapshot TEXT NOT NULL,
            asset_name_snapshot TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    yield conn
    conn.close()


# =============================================================================
# 1. Identifier Generation Tests (Atomic UPSERT RETURNING)
# =============================================================================

def test_generate_identifier_format_and_sequence(sqlite_identifier_conn):
    """Verify generated identifier matches format <KIND>-<YEAR>-<NNN> and increments sequentially."""
    with patch("apps.assets.identifiers.connections") as mock_conns:
        mock_conns.__getitem__.return_value = sqlite_identifier_conn
        id1 = generate_identifier("AST", year=2026, using="sqlite_test")
        id2 = generate_identifier("AST", year=2026, using="sqlite_test")
        id3 = generate_identifier("AST", year=2026, using="sqlite_test")

        assert id1 == "AST-2026-001"
        assert id2 == "AST-2026-002"
        assert id3 == "AST-2026-003"


def test_generate_identifier_multiple_kinds(sqlite_identifier_conn):
    """Verify independent sequence counters for AST and REQ kinds."""
    with patch("apps.assets.identifiers.connections") as mock_conns:
        mock_conns.__getitem__.return_value = sqlite_identifier_conn
        ast1 = generate_identifier("AST", year=2026, using="sqlite_test")
        req1 = generate_identifier("REQ", year=2026, using="sqlite_test")
        ast2 = generate_identifier("AST", year=2026, using="sqlite_test")

        assert ast1 == "AST-2026-001"
        assert req1 == "REQ-2026-001"
        assert ast2 == "AST-2026-002"


def test_generate_identifier_exceeds_999(sqlite_identifier_conn):
    """Verify sequence numbers >= 1000 format correctly without truncation."""
    with patch("apps.assets.identifiers.connections") as mock_conns:
        mock_conns.__getitem__.return_value = sqlite_identifier_conn
        # Seed value at 999
        sqlite_identifier_conn.conn.execute(
            "INSERT INTO assets_identifiersequence (kind, year, value) VALUES ('AST', 2026, 999);"
        )
        code1 = generate_identifier("AST", year=2026, using="sqlite_test")
        assert code1 == "AST-2026-1000"

        code2 = generate_identifier("AST", year=2026, using="sqlite_test")
        assert code2 == "AST-2026-1001"


def test_generate_identifier_invalid_kind():
    """Verify invalid kind raises ValueError."""
    with pytest.raises(ValueError, match="Invalid identifier kind"):
        generate_identifier("INVALID", year=2026)


# =============================================================================
# 2. AssetUpdateSerializer Tests (Allowlist, Rejection, Disposed)
# =============================================================================

def test_serializer_allowlist_validation_success():
    """Verify allowlisted fields pass validation."""
    cat = Category(id=1, name="Laptop")
    asset = Asset(
        id=1,
        asset_code="AST-2026-001",
        name="Dell Laptop",
        status=Asset.Status.AVAILABLE,
        category=cat,
        serial_number="SN123",
        specification={"ram": "16GB"},
    )
    with patch.object(Category.objects, "all", return_value=[cat]):
        serializer = AssetUpdateSerializer(
            instance=asset,
            data={
                "name": "Updated Laptop Name",
                "serial_number": "SN999",
                "specification": {"ram": "32GB"},
            },
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors


def test_serializer_rejects_workflow_and_protected_fields():
    """Verify PATCH strictly rejects status, department, assigned_to_user, and asset_code with 400."""
    cat = Category(id=1, name="Laptop")
    asset = Asset(
        id=1,
        asset_code="AST-2026-001",
        name="Dell Laptop",
        status=Asset.Status.AVAILABLE,
        category=cat,
    )
    forbidden_payloads = [
        {"status": "IN_USE"},
        {"department_id": 2},
        {"assigned_to_user_id": 5},
        {"holder": 5},
        {"asset_code": "AST-MANUAL-001"},
        {"code": "AST-MANUAL-001"},
    ]
    for payload in forbidden_payloads:
        serializer = AssetUpdateSerializer(instance=asset, data=payload, partial=True)
        assert not serializer.is_valid()
        forbidden_key = list(payload.keys())[0]
        assert forbidden_key in serializer.errors


def test_serializer_rejects_disposed_asset():
    """Verify updating a DISPOSED asset raises ConflictError."""
    cat = Category(id=1, name="Laptop")
    asset = Asset(
        id=1,
        asset_code="AST-2026-001",
        name="Disposed Laptop",
        status=Asset.Status.DISPOSED,
        category=cat,
    )
    serializer = AssetUpdateSerializer(
        instance=asset,
        data={"name": "New Name Attempt"},
        partial=True,
    )
    with pytest.raises(ConflictError, match="DISPOSED"):
        serializer.is_valid(raise_exception=True)

    with pytest.raises(ConflictError, match="DISPOSED"):
        serializer.update(asset, {"name": "Direct Update Attempt"})


def test_serializer_update_saves_only_allowlisted_fields():
    """Verify serializer.update performs conditional update on allowlisted fields and excludes DISPOSED."""
    cat = Category(id=1, name="Laptop")
    asset = Asset(
        id=1,
        asset_code="AST-2026-001",
        name="Original Name",
        status=Asset.Status.AVAILABLE,
        category=cat,
        department_id=10,
        assigned_to_user_id=None,
    )
    captured_updates = {}

    mock_qs = MagicMock()
    mock_qs.filter.return_value = mock_qs
    mock_qs.exclude.return_value = mock_qs

    def fake_update(**kwargs):
        captured_updates.update(kwargs)
        return 1

    mock_qs.update = fake_update

    with patch.object(Asset.objects, "using", return_value=mock_qs):
        serializer = AssetUpdateSerializer()
        updated = serializer.update(asset, {"name": "Updated Name", "serial_number": "SN-NEW"})

        assert updated.name == "Updated Name"
        assert updated.serial_number == "SN-NEW"
        assert "name" in captured_updates
        assert "serial_number" in captured_updates
        assert "updated_at" in captured_updates
        # Protected fields must never be passed to update()
        for forbidden in ("status", "assigned_to_user", "assigned_to_user_id", "department", "department_id", "asset_code"):
            assert forbidden not in captured_updates


def test_serializer_update_conditional_write_occ_conflict():
    """Verify serializer.update raises ConflictError if concurrent change causes 0 rows updated."""
    cat = Category(id=1, name="Laptop")
    asset = Asset(
        id=2,
        asset_code="AST-2026-002",
        name="Concurrent Laptop",
        status=Asset.Status.AVAILABLE,
        category=cat,
        department_id=10,
    )
    serializer = AssetUpdateSerializer()
    mock_qs = MagicMock()
    mock_qs.filter.return_value = mock_qs
    mock_qs.exclude.return_value = mock_qs
    mock_qs.update.return_value = 0
    mock_qs.first.return_value = asset

    with patch.object(Asset.objects, "using", return_value=mock_qs):
        with pytest.raises(ConflictError, match="thay đổi đồng thời"):
            serializer.update(asset, {"name": "New Name"})


def test_serializer_update_conditional_write_disposed():
    """Verify serializer.update raises ConflictError if asset was concurrently disposed."""
    cat = Category(id=1, name="Laptop")
    asset = Asset(
        id=3,
        asset_code="AST-2026-003",
        name="Disposing Laptop",
        status=Asset.Status.AVAILABLE,
        category=cat,
        department_id=10,
    )
    serializer = AssetUpdateSerializer()
    mock_qs = MagicMock()
    mock_qs.filter.return_value = mock_qs
    mock_qs.exclude.return_value = mock_qs
    mock_qs.update.return_value = 0
    disposed_asset = Asset(id=3, status=Asset.Status.DISPOSED)
    mock_qs.first.return_value = disposed_asset

    with patch.object(Asset.objects, "using", return_value=mock_qs):
        with pytest.raises(ConflictError, match="DISPOSED"):
            serializer.update(asset, {"name": "New Name"})


def test_serializer_update_conditional_write_manager_scope():
    """Verify serializer.update raises PermissionDenied if manager edits outside department."""
    from rest_framework.exceptions import PermissionDenied
    cat = Category(id=1, name="Laptop")
    asset = Asset(
        id=4,
        asset_code="AST-2026-004",
        name="Dept 20 Laptop",
        status=Asset.Status.AVAILABLE,
        category=cat,
        department_id=20,
    )
    manager = User(id=5, username="mgr1", role=User.Role.MANAGER, department_id=10)
    mock_request = MagicMock(user=manager)
    serializer = AssetUpdateSerializer(context={"request": mock_request})

    mock_qs = MagicMock()
    mock_qs.filter.return_value = mock_qs
    mock_qs.exclude.return_value = mock_qs
    mock_qs.update.return_value = 0
    current_asset = Asset(id=4, status=Asset.Status.AVAILABLE, department_id=20)
    mock_qs.first.return_value = current_asset

    with patch.object(Asset.objects, "using", return_value=mock_qs):
        with pytest.raises(PermissionDenied, match="ngoài phạm vi phòng ban"):
            serializer.update(asset, {"name": "New Name"})


# =============================================================================
# 3. Statement Builder Helper Methods (for Agent C's RequestService)
# =============================================================================

def test_build_allocate_statements():
    """Verify build_allocate_statements returns [update, assert_guard, audit_log] tuples with department and holder guards."""
    asset = Asset(id=1, asset_code="AST-001", name="Laptop", status=Asset.Status.AVAILABLE, department_id=5)
    actor = User(id=10, username="manager", role=User.Role.MANAGER)
    user = User(id=20, username="employee")

    stmts = AssetService.build_allocate_statements(asset, actor, user, "Assign laptop")
    assert len(stmts) == 3

    update_stmt, guard_stmt, audit_stmt = stmts
    assert "UPDATE assets_asset" in update_stmt[0]
    assert "WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL" in update_stmt[0]
    assert update_stmt[1] == [Asset.Status.IN_USE, 20, 1, Asset.Status.AVAILABLE, 5]

    assert "_assert_guard" in guard_stmt[0]
    assert "changes()" in guard_stmt[0]

    assert "INSERT INTO assets_assethistorylog" in audit_stmt[0]
    assert audit_stmt[1][0] == 1  # asset_id
    assert audit_stmt[1][1] == 10  # actor_id
    assert audit_stmt[1][2] == AssetHistoryLog.ActionType.ALLOCATE
    assert audit_stmt[1][4] == Asset.Status.IN_USE
    assert audit_stmt[1][6] == 20  # to_user_id
    assert audit_stmt[1][7] == 5  # from_department_id
    assert audit_stmt[1][8] == 5  # to_department_id


def test_build_revoke_statements():
    """Verify build_revoke_statements generates correct update and audit tuples guarding department and holder."""
    asset = Asset(id=2, asset_code="AST-002", name="Laptop 2", status=Asset.Status.IN_USE, department_id=5, assigned_to_user_id=20)
    actor = User(id=10, username="manager", role=User.Role.MANAGER)

    # Condition GOOD -> AVAILABLE
    stmts_good = AssetService.build_revoke_statements(asset, actor, condition="GOOD", reason="Returned intact")
    assert len(stmts_good) == 3
    assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in stmts_good[0][0]
    assert stmts_good[0][1] == [Asset.Status.AVAILABLE, 2, Asset.Status.IN_USE, 20, 5]
    assert stmts_good[2][1][2] == AssetHistoryLog.ActionType.RETURN
    assert stmts_good[2][1][4] == Asset.Status.AVAILABLE
    assert stmts_good[2][1][5] == 20  # from_user_id
    assert stmts_good[2][1][6] is None  # to_user_id

    # Condition DAMAGED -> DAMAGED_PENDING_REPAIR
    stmts_damaged = AssetService.build_revoke_statements(asset, actor, condition="DAMAGED", reason="Returned broken")
    assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in stmts_damaged[0][0]
    assert stmts_damaged[0][1] == [Asset.Status.DAMAGED_PENDING_REPAIR, 2, Asset.Status.IN_USE, 20, 5]
    assert stmts_damaged[2][1][4] == Asset.Status.DAMAGED_PENDING_REPAIR


def test_build_confirm_damage_statements():
    """Verify build_confirm_damage_statements guards department_id and assigned_to_user_id and retains holder."""
    asset = Asset(id=3, asset_code="AST-003", name="Laptop 3", status=Asset.Status.IN_USE, department_id=5, assigned_to_user_id=20)
    actor = User(id=10, username="manager", role=User.Role.MANAGER)

    stmts = AssetService.build_confirm_damage_statements(asset, actor, reason="Confirmed broken screen")
    assert len(stmts) == 3
    assert "assigned_to_user_id = NULL" not in stmts[0][0]
    assert "SET status = %s, updated_at = datetime('now')" in stmts[0][0]
    assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in stmts[0][0]
    assert stmts[0][1] == [Asset.Status.DAMAGED_PENDING_REPAIR, 3, Asset.Status.IN_USE, 20, 5]
    assert stmts[2][1][2] == AssetHistoryLog.ActionType.DAMAGE
    assert stmts[2][1][5] == 20  # from_user_id
    assert stmts[2][1][6] == 20  # to_user_id (retains holder in audit log)


# =============================================================================
# 4. All 9 Lifecycle Methods in D1 Mode
# =============================================================================

@pytest.fixture
def mock_d1_context():
    """Mock connection to report D1 vendor and capture d1_batch calls."""
    captured_batches = []

    def fake_d1_batch(statements, using="default", **kwargs):
        captured_batches.append(statements)
        return {"success": True}

    with patch.object(AssetService, "_is_d1", return_value=True), \
         patch("apps.assets.services.d1_batch", side_effect=fake_d1_batch):
        yield captured_batches


def test_d1_action_allocate(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    target_user = User(id=2, username="user", role=User.Role.USER, department_id=10, is_active=True)
    asset = Asset(id=100, asset_code="AST-100", name="PC", status=Asset.Status.AVAILABLE, department_id=10)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(User.objects, "get", return_value=target_user), \
         patch.object(Asset.objects, "get", return_value=asset):
        res = AssetService.allocate(100, admin, 2, "Allocate to user")
        assert res == asset
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        # Target user guard + assert_guard + update_asset + assert_guard + audit_log
        assert len(stmts) == 5
        assert "UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s" in stmts[0][0]
        assert stmts[0][1] == [2, 10]
        assert "_assert_guard" in stmts[1][0]
        assert "WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL" in stmts[2][0]
        assert stmts[2][1] == [Asset.Status.IN_USE, 2, 100, Asset.Status.AVAILABLE, 10]


def test_d1_action_revoke(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=101, asset_code="AST-101", name="PC", status=Asset.Status.IN_USE, department_id=10, assigned_to_user_id=2)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        AssetService.revoke(101, admin, received_confirmed=True, condition="GOOD", reason="Returned")
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.AVAILABLE, 101, Asset.Status.IN_USE, 2, 10]


def test_d1_action_mark_damaged(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=102, asset_code="AST-102", name="PC", status=Asset.Status.AVAILABLE, department_id=10, assigned_to_user_id=None)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        AssetService.mark_damaged(102, admin, "Found broken")
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.DAMAGED_PENDING_REPAIR, 102, Asset.Status.AVAILABLE, 10]


def test_d1_action_confirm_damage_report(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=103, asset_code="AST-103", name="PC", status=Asset.Status.IN_USE, department_id=10, assigned_to_user_id=2)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        res = AssetService.confirm_damage_report(103, admin, "Confirmed report")
        assert res == asset
        assert res.assigned_to_user_id == 2  # Retained, NOT cleared to NULL
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "assigned_to_user_id = NULL" not in stmts[0][0]
        assert "SET status = %s, updated_at = datetime('now')" in stmts[0][0]
        assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.DAMAGED_PENDING_REPAIR, 103, Asset.Status.IN_USE, 2, 10]
        assert stmts[2][1][5] == 2  # from_user_id
        assert stmts[2][1][6] == 2  # to_user_id (retained)


def test_d1_action_send_maintenance(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=104, asset_code="AST-104", name="PC", status=Asset.Status.DAMAGED_PENDING_REPAIR, department_id=10)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        AssetService.send_maintenance(104, admin, "Sent to vendor")
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND department_id = %s" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.UNDER_MAINTENANCE, 104, Asset.Status.DAMAGED_PENDING_REPAIR, 10]


def test_d1_action_complete_maintenance_return_to_user(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    holder = User(id=2, username="staff", is_active=True, department_id=10)
    asset = Asset(id=105, asset_code="AST-105", name="PC", status=Asset.Status.UNDER_MAINTENANCE, department_id=10, assigned_to_user=holder)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        AssetService.complete_maintenance(105, admin, result="RETURN_TO_USER", reason="Repaired")
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        # Target user guard + assert_guard + update_asset + assert_guard + audit_log
        assert len(stmts) == 5
        assert "UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s" in stmts[0][0]
        assert stmts[0][1] == [2, 10]
        assert "_assert_guard" in stmts[1][0]
        assert "WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id = %s" in stmts[2][0]
        assert stmts[2][1] == [Asset.Status.IN_USE, 105, Asset.Status.UNDER_MAINTENANCE, 10, 2]


def test_d1_action_complete_maintenance_return_to_stock(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=106, asset_code="AST-106", name="PC", status=Asset.Status.UNDER_MAINTENANCE, department_id=10, assigned_to_user=None)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        AssetService.complete_maintenance(106, admin, result="RETURN_TO_STOCK", reason="Repaired to stock")
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND department_id = %s" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.AVAILABLE, 106, Asset.Status.UNDER_MAINTENANCE, 10]


def test_d1_action_transfer(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    target_dept = Department(id=20, name="Sales")
    asset = Asset(id=107, asset_code="AST-107", name="PC", status=Asset.Status.AVAILABLE, department_id=10, assigned_to_user=None)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Department.objects, "get", return_value=target_dept), \
         patch.object(Asset.objects, "get", return_value=asset):
        AssetService.transfer(107, admin, to_department_id=20, reason="Reallocated to sales")
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND assigned_to_user_id IS NULL AND (department_id = %s OR (department_id IS NULL AND %s IS NULL))" in stmts[0][0]
        assert stmts[0][1] == [20, 107, Asset.Status.AVAILABLE, 10, 10]
        assert stmts[2][1][7] == 10  # from_department_id
        assert stmts[2][1][8] == 20  # to_department_id


def test_d1_action_propose_disposal(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=108, asset_code="AST-108", name="PC", status=Asset.Status.AVAILABLE, department_id=10, assigned_to_user=None)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        res = AssetService.propose_disposal(108, admin, "End of life")
        assert res == asset
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.PENDING_DISPOSAL, 108, Asset.Status.AVAILABLE, 10]
        assert stmts[2][1][7] == 10  # from_department_id
        assert stmts[2][1][8] == 10  # to_department_id


def test_d1_action_propose_disposal_company_pool(mock_d1_context):
    """Verify propose_disposal for company pool asset (department_id=None, assigned_to_user_id=None)."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=110, asset_code="AST-POOL-1", name="Shared Printer", status=Asset.Status.AVAILABLE, department_id=None, assigned_to_user=None)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        res = AssetService.propose_disposal(110, admin, "Scrap shared pool asset")
        assert res == asset
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND department_id IS NULL AND assigned_to_user_id IS NULL" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.PENDING_DISPOSAL, 110, Asset.Status.AVAILABLE]
        assert stmts[2][1][7] is None  # from_department_id (company pool)
        assert stmts[2][1][8] is None  # to_department_id (company pool)


def test_d1_action_confirm_disposal(mock_d1_context):
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=109, asset_code="AST-109", name="PC", status=Asset.Status.PENDING_DISPOSAL, department_id=10, assigned_to_user=None)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        res = AssetService.confirm_disposal(109, admin, "Approved scrapping")
        assert res == asset
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.DISPOSED, 109, Asset.Status.PENDING_DISPOSAL, 10]
        assert stmts[2][1][7] == 10  # from_department_id
        assert stmts[2][1][8] == 10  # to_department_id


def test_d1_action_confirm_disposal_company_pool(mock_d1_context):
    """Verify confirm_disposal for company pool asset (department_id=None, assigned_to_user_id=None)."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=111, asset_code="AST-POOL-2", name="Shared Server", status=Asset.Status.PENDING_DISPOSAL, department_id=None, assigned_to_user=None)

    with patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        res = AssetService.confirm_disposal(111, admin, "Confirm scrap shared pool asset")
        assert res == asset
        assert len(mock_d1_context) == 1
        stmts = mock_d1_context[0]
        assert len(stmts) == 3
        assert "WHERE id = %s AND status = %s AND department_id IS NULL AND assigned_to_user_id IS NULL" in stmts[0][0]
        assert stmts[0][1] == [Asset.Status.DISPOSED, 111, Asset.Status.PENDING_DISPOSAL]
        assert stmts[2][1][7] is None  # from_department_id (company pool)
        assert stmts[2][1][8] is None  # to_department_id (company pool)


def test_d1_company_pool_disposal_lifecycle_sqlite_occ(sqlite_occ_conn):
    """Verify propose_disposal and confirm_disposal against SQLite with department_id IS NULL and assigned_to_user_id IS NULL."""
    # 1. Seed initial company pool asset
    sqlite_occ_conn.execute(
        "INSERT INTO assets_asset (id, asset_code, name, status, department_id, assigned_to_user_id) "
        "VALUES (200, 'AST-POOL-OCC', 'Pool Server', 'AVAILABLE', NULL, NULL);"
    )
    sqlite_occ_conn.commit()

    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)
    asset = Asset(id=200, asset_code="AST-POOL-OCC", name="Pool Server", status=Asset.Status.AVAILABLE, department_id=None, assigned_to_user=None)

    def d1_batch_sqlite(stmts, using="default", **kwargs):
        cur = sqlite_occ_conn.cursor()
        for sql, params in stmts:
            sql_q = sql.replace("%s", "?")
            cur.execute(sql_q, params)
        sqlite_occ_conn.commit()
        return {"success": True}

    with patch.object(AssetService, "_is_d1", return_value=True), \
         patch("apps.assets.services.d1_batch", side_effect=d1_batch_sqlite), \
         patch.object(AssetService, "_get_locked_asset", return_value=asset), \
         patch.object(Asset.objects, "get", return_value=asset):
        # 1. Propose disposal
        res_propose = AssetService.propose_disposal(200, admin, "Propose pool disposal")
        assert res_propose.id == 200

        # Verify DB state in SQLite
        cur = sqlite_occ_conn.cursor()
        cur.execute("SELECT status, department_id, assigned_to_user_id FROM assets_asset WHERE id = 200;")
        row = cur.fetchone()
        assert row == ("PENDING_DISPOSAL", None, None)

        # Verify audit log in SQLite
        cur.execute("SELECT action_type, from_status, to_status, from_department_id, to_department_id FROM assets_assethistorylog WHERE asset_id = 200;")
        log_row = cur.fetchone()
        assert log_row == (AssetHistoryLog.ActionType.DISPOSAL_PROPOSE, "AVAILABLE", "PENDING_DISPOSAL", None, None)

        # 2. Confirm disposal
        asset.status = Asset.Status.PENDING_DISPOSAL
        res_confirm = AssetService.confirm_disposal(200, admin, "Confirm pool disposal")
        assert res_confirm.id == 200

        cur.execute("SELECT status, department_id, assigned_to_user_id FROM assets_asset WHERE id = 200;")
        row = cur.fetchone()
        assert row == ("DISPOSED", None, None)

        cur.execute("SELECT action_type, from_status, to_status, from_department_id, to_department_id FROM assets_assethistorylog WHERE asset_id = 200 AND to_status = 'DISPOSED';")
        log_row = cur.fetchone()
        assert log_row == (AssetHistoryLog.ActionType.DISPOSAL_CONFIRM, "PENDING_DISPOSAL", "DISPOSED", None, None)


# =============================================================================
# 5. OCC Conflict and _assert_guard Verification
# =============================================================================

def test_occ_conflict_assert_guard_raises_conflict_error_no_orphan_log(sqlite_occ_conn):
    """
    Test OCC Conflict:
    1. Asset is in AVAILABLE state.
    2. A mutation expects status = 'IN_USE' (mismatched/concurrent state).
    3. SQLite executes UPDATE -> 0 rows affected -> changes() is 0.
    4. _assert_guard INSERT fails with CHECK constraint violation.
    5. Batch transaction aborts, rollback is triggered.
    6. AssetService catches DatabaseError and raises ConflictError.
    7. Zero orphan audit log entries are persisted.
    """
    # 1. Seed initial asset with status AVAILABLE
    sqlite_occ_conn.execute(
        "INSERT INTO assets_asset (id, asset_code, name, status, department_id, assigned_to_user_id) "
        "VALUES (1, 'AST-OCC-001', 'Conflicting Asset', 'AVAILABLE', 1, NULL);"
    )
    sqlite_occ_conn.commit()

    # Statement batch expecting status = 'IN_USE' (which will match 0 rows)
    statements = [
        ("UPDATE assets_asset SET status = %s WHERE id = %s AND status = %s", ["AVAILABLE", 1, "IN_USE"]),
        ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", []),
        (
            "INSERT INTO assets_assethistorylog (asset_id, actor_id, action_type, from_status, to_status, reason, asset_code_snapshot, asset_name_snapshot) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [1, 10, "RETURN", "IN_USE", "AVAILABLE", "Orphan test", "AST-OCC-001", "Conflicting Asset"],
        ),
    ]

    # Emulate Cloudflare D1 batch behavior on the SQLite connection
    def d1_batch_sqlite_executor(stmts, using="default", **kwargs):
        cur = sqlite_occ_conn.cursor()
        try:
            for sql, params in stmts:
                sql_q = sql.replace("%s", "?")
                cur.execute(sql_q, params)
            sqlite_occ_conn.commit()
        except Exception as exc:
            sqlite_occ_conn.rollback()
            raise DatabaseError(f"D1 Constraint Conflict: {exc}") from exc

    with patch("apps.assets.services.d1_batch", side_effect=d1_batch_sqlite_executor):
        with pytest.raises(ConflictError, match="OCC conflict"):
            AssetService._execute_d1_mutation(statements, asset_id=1, using="default")

    # Verify zero audit logs exist (atomic rollback succeeded, no orphan log)
    cur = sqlite_occ_conn.cursor()
    cur.execute("SELECT count(*) FROM assets_assethistorylog WHERE asset_id = 1;")
    log_count = cur.fetchone()[0]
    assert log_count == 0, f"Expected 0 orphan logs, found {log_count}"

    # Verify asset status remained AVAILABLE
    cur.execute("SELECT status FROM assets_asset WHERE id = 1;")
    current_status = cur.fetchone()[0]
    assert current_status == "AVAILABLE"
