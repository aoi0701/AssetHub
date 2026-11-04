"""
Unit tests for Cloudflare D1 Request Workflows, OCC Assert Guard, and Atomic Batch Mutations.
Validates:
1. approve_request for ALLOCATE, RETURN, DAMAGE_REPORT on D1 backend (all statements executed in ONE batch).
2. approve_request OCC conflict: if request already approved or asset already allocated,
   batch fails atomically, request remains PENDING, asset untouched, 0 audit logs.
3. reject_request: status becomes REJECTED, rejection_reason set, asset untouched, 0 AssetHistoryLog entries created.
4. OCC conflict on reject_request: fails atomically when request is not PENDING.
5. Scoping, role permissions, and domain validations.
6. Helper _is_d1 detection across vendor types and mock configurations.
"""

import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from django.db import DatabaseError
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.assets.models import Asset, AssetHistoryLog
from apps.assets.services import AssetService
from apps.authentication.models import User
from apps.common.exceptions import ConflictError
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest
from apps.requests.services import RequestService


# =============================================================================
# Helper Fixtures & In-Memory SQLite Setup
# =============================================================================

@pytest.fixture
def sqlite_occ_conn():
    """In-memory SQLite database initialized with request, asset, guard, and audit tables."""
    conn = sqlite3.connect(":memory:", autocommit=False)
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
        CREATE TABLE authentication_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            department_id INTEGER
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE requests_assetrequest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_code TEXT UNIQUE NOT NULL,
            request_type TEXT NOT NULL,
            requester_id INTEGER NOT NULL,
            asset_id INTEGER,
            requested_category_id INTEGER,
            department_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            reviewed_by_id INTEGER,
            reviewed_at TEXT,
            rejection_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE assets_asset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            department_id INTEGER,
            assigned_to_user_id INTEGER,
            category_id INTEGER,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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


@pytest.fixture
def mock_d1_context():
    """Mock connection to report D1 vendor and capture d1_batch calls."""
    captured_batches = []

    def fake_d1_batch(statements, using="default", **kwargs):
        captured_batches.append(statements)
        return {"success": True}

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch("apps.requests.services.d1_batch", side_effect=fake_d1_batch):
        yield captured_batches


# =============================================================================
# 1. approve_request for ALLOCATE, RETURN, DAMAGE_REPORT on D1 (Single Batch)
# =============================================================================

def test_approve_allocate_request_d1_single_batch(mock_d1_context):
    """
    Test approve_request for ALLOCATE on D1:
    - Bundles request update + req guard + requester guard + requester check + asset update + asset guard + audit log into ONE batch.
    - Batch has exactly 7 statements.
    - Request status -> APPROVED, reviewed_by recorded, asset_id assigned.
    - Asset status -> IN_USE, assigned_to_user -> requester.
    """
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    asset = Asset(
        id=100,
        asset_code="AST-2026-100",
        name="Dell XPS 15",
        status=Asset.Status.AVAILABLE,
        department_id=10,
        category_id=1,
    )
    req = AssetRequest(
        id=50,
        request_code="REQ-2026-050",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        requested_category_id=1,
        reason="Need laptop for project",
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=asset), \
         patch.object(AssetRequest.objects, "get", return_value=req):

        result = RequestService.approve_request(
            request_id=50,
            actor=admin,
            data={"selected_asset_id": 100},
        )

        assert result == req
        assert len(mock_d1_context) == 1, "Must execute in exactly ONE single d1_batch call!"

        batch = mock_d1_context[0]
        assert len(batch) == 7, f"Expected 7 statements in batch, got {len(batch)}"

        # 1. Update request statement
        req_stmt, req_params = batch[0]
        assert "UPDATE requests_assetrequest" in req_stmt
        assert "status = %s" in req_stmt
        assert "reviewed_by_id = %s" in req_stmt
        assert "asset_id = %s" in req_stmt
        assert "WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s" in req_stmt
        assert req_params == [AssetRequest.Status.APPROVED, admin.id, asset.id, req.id, AssetRequest.Status.PENDING, req.requester_id, req.department_id]

        # 2. Request assertion guard
        req_guard_stmt, req_guard_params = batch[1]
        assert "INSERT INTO _assert_guard" in req_guard_stmt
        assert "changes()" in req_guard_stmt

        # 3. Requester active & department guard statement
        requester_guard_stmt, requester_guard_params = batch[2]
        assert "UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s" in requester_guard_stmt
        assert requester_guard_params == [requester.id, req.department_id]

        # 4. Requester assertion guard check
        requester_check_stmt, requester_check_params = batch[3]
        assert "INSERT INTO _assert_guard" in requester_check_stmt
        assert "changes()" in requester_check_stmt

        # 5. Update asset statement
        asset_stmt, asset_params = batch[4]
        assert "UPDATE assets_asset" in asset_stmt
        assert "status = %s" in asset_stmt
        assert "assigned_to_user_id = %s" in asset_stmt
        assert "WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL" in asset_stmt
        assert asset_params == [Asset.Status.IN_USE, requester.id, asset.id, Asset.Status.AVAILABLE, asset.department_id]

        # 6. Asset assertion guard
        asset_guard_stmt, asset_guard_params = batch[5]
        assert "INSERT INTO _assert_guard" in asset_guard_stmt
        assert "changes()" in asset_guard_stmt

        # 7. Audit log statement
        audit_stmt, audit_params = batch[6]
        assert "INSERT INTO assets_assethistorylog" in audit_stmt
        assert audit_params[0] == asset.id
        assert audit_params[1] == admin.id
        assert audit_params[2] == AssetHistoryLog.ActionType.ALLOCATE
        assert audit_params[3] == Asset.Status.AVAILABLE
        assert audit_params[4] == Asset.Status.IN_USE
        assert audit_params[6] == requester.id


def test_approve_return_request_condition_good_d1_single_batch(mock_d1_context):
    """
    Test approve_request for RETURN with condition GOOD on D1:
    - Bundles request update + req guard + asset revoke + asset guard + audit log into ONE batch.
    - Asset status -> AVAILABLE, holder cleared (NULL).
    """
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    asset = Asset(
        id=101,
        asset_code="AST-2026-101",
        name="MacBook Air M2",
        status=Asset.Status.IN_USE,
        department_id=10,
        assigned_to_user_id=5,
    )
    req = AssetRequest(
        id=51,
        request_code="REQ-2026-051",
        request_type=AssetRequest.RequestType.RETURN,
        requester=requester,
        requester_id=5,
        department_id=10,
        asset=asset,
        asset_id=101,
        reason="Returning laptop after project",
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=asset), \
         patch.object(AssetRequest.objects, "get", return_value=req):

        result = RequestService.approve_request(
            request_id=51,
            actor=admin,
            data={"received_confirmed": True, "condition": "GOOD"},
        )

        assert result == req
        assert len(mock_d1_context) == 1

        batch = mock_d1_context[0]
        assert len(batch) == 5

        # 1. Update request statement
        req_stmt, req_params = batch[0]
        assert "WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s" in req_stmt
        assert req_params == [AssetRequest.Status.APPROVED, admin.id, req.id, AssetRequest.Status.PENDING, req.requester_id, req.department_id]

        # 2. Request assertion guard
        req_guard_stmt, req_guard_params = batch[1]
        assert "INSERT INTO _assert_guard" in req_guard_stmt
        assert "changes()" in req_guard_stmt

        # 3. Update asset statement (condition GOOD -> AVAILABLE, holder cleared)
        asset_stmt, asset_params = batch[2]
        assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in asset_stmt
        assert asset_params == [Asset.Status.AVAILABLE, asset.id, Asset.Status.IN_USE, asset.assigned_to_user_id, asset.department_id]

        # 4. Asset assertion guard
        asset_guard_stmt, asset_guard_params = batch[3]
        assert "INSERT INTO _assert_guard" in asset_guard_stmt
        assert "changes()" in asset_guard_stmt

        # 5. Audit log statement
        audit_stmt, audit_params = batch[4]
        assert audit_params[2] == AssetHistoryLog.ActionType.RETURN
        assert audit_params[3] == Asset.Status.IN_USE
        assert audit_params[4] == Asset.Status.AVAILABLE
        assert audit_params[5] == requester.id
        assert audit_params[6] is None


def test_approve_return_request_condition_damaged_d1_single_batch(mock_d1_context):
    """
    Test approve_request for RETURN with condition DAMAGED on D1:
    - Asset status -> DAMAGED_PENDING_REPAIR, holder cleared (NULL).
    """
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    asset = Asset(
        id=102,
        asset_code="AST-2026-102",
        name="MacBook Air M2",
        status=Asset.Status.IN_USE,
        department_id=10,
        assigned_to_user_id=5,
    )
    req = AssetRequest(
        id=52,
        request_code="REQ-2026-052",
        request_type=AssetRequest.RequestType.RETURN,
        requester=requester,
        requester_id=5,
        department_id=10,
        asset=asset,
        asset_id=102,
        reason="Returning broken laptop",
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=asset), \
         patch.object(AssetRequest.objects, "get", return_value=req):

        RequestService.approve_request(
            request_id=52,
            actor=admin,
            data={"received_confirmed": True, "condition": "DAMAGED"},
        )

        assert len(mock_d1_context) == 1
        batch = mock_d1_context[0]
        assert len(batch) == 5

        # 1. Update request statement
        req_stmt, req_params = batch[0]
        assert "WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s" in req_stmt
        assert req_params == [AssetRequest.Status.APPROVED, admin.id, req.id, AssetRequest.Status.PENDING, req.requester_id, req.department_id]

        # Asset transitions to DAMAGED_PENDING_REPAIR
        asset_stmt, asset_params = batch[2]
        assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in asset_stmt
        assert asset_params == [Asset.Status.DAMAGED_PENDING_REPAIR, asset.id, Asset.Status.IN_USE, asset.assigned_to_user_id, asset.department_id]

        # Audit log reflects DAMAGED_PENDING_REPAIR
        audit_stmt, audit_params = batch[4]
        assert audit_params[4] == Asset.Status.DAMAGED_PENDING_REPAIR


def test_approve_damage_report_request_d1_single_batch(mock_d1_context):
    """
    Test approve_request for DAMAGE_REPORT on D1:
    - Asset status -> DAMAGED_PENDING_REPAIR.
    - Holder is preserved for accountability!
    - Bundled into ONE single batch with 5 statements.
    """
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    asset = Asset(
        id=103,
        asset_code="AST-2026-103",
        name="ThinkPad T14",
        status=Asset.Status.IN_USE,
        department_id=10,
        assigned_to_user_id=5,
    )
    req = AssetRequest(
        id=53,
        request_code="REQ-2026-053",
        request_type=AssetRequest.RequestType.DAMAGE_REPORT,
        requester=requester,
        requester_id=5,
        department_id=10,
        asset=asset,
        asset_id=103,
        reason="Keyboard malfunctioned",
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=asset), \
         patch.object(AssetRequest.objects, "get", return_value=req):

        result = RequestService.approve_request(
            request_id=53,
            actor=admin,
            data={},
        )

        assert result == req
        assert len(mock_d1_context) == 1

        batch = mock_d1_context[0]
        assert len(batch) == 5

        # 1. Update request statement
        req_stmt, req_params = batch[0]
        assert "WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s" in req_stmt
        assert req_params == [AssetRequest.Status.APPROVED, admin.id, req.id, AssetRequest.Status.PENDING, req.requester_id, req.department_id]

        # 2. Request assertion guard
        req_guard_stmt, req_guard_params = batch[1]
        assert "INSERT INTO _assert_guard" in req_guard_stmt
        assert "changes()" in req_guard_stmt

        # 3. Update asset statement
        asset_stmt, asset_params = batch[2]
        assert "WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s" in asset_stmt
        assert asset_params == [Asset.Status.DAMAGED_PENDING_REPAIR, asset.id, Asset.Status.IN_USE, asset.assigned_to_user_id, asset.department_id]

        # 4. Asset assertion guard
        asset_guard_stmt, asset_guard_params = batch[3]
        assert "INSERT INTO _assert_guard" in asset_guard_stmt
        assert "changes()" in asset_guard_stmt

        # 5. Audit log statement: to_user_id remains requester.id
        audit_stmt, audit_params = batch[4]
        assert audit_params[2] == AssetHistoryLog.ActionType.DAMAGE
        assert audit_params[3] == Asset.Status.IN_USE
        assert audit_params[4] == Asset.Status.DAMAGED_PENDING_REPAIR
        assert audit_params[5] == requester.id
        assert audit_params[6] == requester.id


# =============================================================================
# 2. OCC Conflict & Atomic Rollback Tests (Request Already Processed / Asset Busy)
# =============================================================================

def test_approve_request_occ_conflict_request_already_processed(sqlite_occ_conn):
    """
    OCC Conflict 1: Request was already approved or rejected concurrently.
    - Initial state: request is in 'APPROVED' state (mismatched OCC condition).
    - Request UPDATE matches 0 rows -> changes() is 0.
    - First _assert_guard fails with CHECK constraint violation.
    - Transaction rolls back atomically.
    - ConflictError is raised.
    - Request remains unchanged, asset untouched, 0 audit logs.
    """
    # 1. Seed initial state
    sqlite_occ_conn.execute(
        "INSERT INTO requests_assetrequest (id, request_code, request_type, requester_id, department_id, reason, status) "
        "VALUES (1, 'REQ-OCC-001', 'ALLOCATE', 5, 10, 'OCC test', 'APPROVED');"
    )
    sqlite_occ_conn.execute(
        "INSERT INTO assets_asset (id, asset_code, name, status, department_id, assigned_to_user_id) "
        "VALUES (100, 'AST-OCC-100', 'Conflicting Asset', 'AVAILABLE', 10, NULL);"
    )
    sqlite_occ_conn.commit()

    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    asset = Asset(id=100, asset_code="AST-OCC-100", name="Conflicting Asset", status=Asset.Status.AVAILABLE, department_id=10, category_id=1)
    req = AssetRequest(
        id=1,
        request_code="REQ-OCC-001",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        requested_category_id=1,
        reason="OCC test",
        status=AssetRequest.Status.PENDING,  # Stale in-memory view
    )

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

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=asset), \
         patch("apps.requests.services.d1_batch", side_effect=d1_batch_sqlite_executor):

        with pytest.raises(ConflictError, match="Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."):
            RequestService.approve_request(
                request_id=1,
                actor=admin,
                data={"selected_asset_id": 100},
            )

    # Verify atomic rollback & zero state corruption
    cur = sqlite_occ_conn.cursor()
    cur.execute("SELECT status FROM requests_assetrequest WHERE id = 1;")
    assert cur.fetchone()[0] == "APPROVED"

    cur.execute("SELECT status, assigned_to_user_id FROM assets_asset WHERE id = 100;")
    asset_row = cur.fetchone()
    assert asset_row[0] == "AVAILABLE"
    assert asset_row[1] is None

    cur.execute("SELECT count(*) FROM assets_assethistorylog WHERE asset_id = 100;")
    assert cur.fetchone()[0] == 0, "Expected 0 audit logs upon conflict failure!"


def test_approve_request_occ_conflict_asset_already_allocated(sqlite_occ_conn):
    """
    OCC Conflict 2: Request is PENDING, but asset was concurrently allocated to someone else!
    - Request UPDATE matches 1 row -> req guard passes.
    - Asset UPDATE matches 0 rows (status was changed to 'IN_USE') -> changes() is 0.
    - Asset _assert_guard fails with CHECK constraint violation.
    - Entire batch is rolled back atomically!
    - ConflictError is raised.
    - Request MUST remain PENDING (rolled back!), asset untouched, 0 audit logs created!
    """
    # 1. Seed initial state: request is PENDING, but asset is already IN_USE by user 99
    sqlite_occ_conn.execute(
        "INSERT INTO authentication_user (id, username, role, is_active, department_id) "
        "VALUES (5, 'requester', 'USER', 1, 10);"
    )
    sqlite_occ_conn.execute(
        "INSERT INTO requests_assetrequest (id, request_code, request_type, requester_id, department_id, reason, status) "
        "VALUES (2, 'REQ-OCC-002', 'ALLOCATE', 5, 10, 'Allocate conflict', 'PENDING');"
    )
    sqlite_occ_conn.execute(
        "INSERT INTO assets_asset (id, asset_code, name, status, department_id, assigned_to_user_id) "
        "VALUES (200, 'AST-OCC-200', 'Laptop Taken', 'IN_USE', 10, 99);"
    )
    sqlite_occ_conn.commit()

    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    # Stale view where asset was AVAILABLE
    stale_asset = Asset(id=200, asset_code="AST-OCC-200", name="Laptop Taken", status=Asset.Status.AVAILABLE, department_id=10, category_id=1)
    req = AssetRequest(
        id=2,
        request_code="REQ-OCC-002",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        requested_category_id=1,
        reason="Allocate conflict",
        status=AssetRequest.Status.PENDING,
    )

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

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=stale_asset), \
         patch("apps.requests.services.d1_batch", side_effect=d1_batch_sqlite_executor):

        with pytest.raises(ConflictError, match="Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."):
            RequestService.approve_request(
                request_id=2,
                actor=admin,
                data={"selected_asset_id": 200},
            )

    # 1. Request MUST remain PENDING (transaction was fully rolled back!)
    cur = sqlite_occ_conn.cursor()
    cur.execute("SELECT status, reviewed_by_id FROM requests_assetrequest WHERE id = 2;")
    req_row = cur.fetchone()
    assert req_row[0] == "PENDING"
    assert req_row[1] is None

    # 2. Asset MUST remain untouched (still IN_USE by user 99)
    cur.execute("SELECT status, assigned_to_user_id FROM assets_asset WHERE id = 200;")
    asset_row = cur.fetchone()
    assert asset_row[0] == "IN_USE"
    assert asset_row[1] == 99

    # 3. Exactly 0 audit logs created!
    cur.execute("SELECT count(*) FROM assets_assethistorylog WHERE asset_id = 200;")
    assert cur.fetchone()[0] == 0


def test_approve_request_occ_conflict_requester_deactivated_concurrently(sqlite_occ_conn):
    """
    OCC Conflict 3: Requester was deactivated concurrently in DB before batch commit.
    - Request is PENDING, asset is AVAILABLE.
    - Requester in DB has is_active = 0.
    - req_stmt matches 1 row -> req_guard passes.
    - requester_guard matches 0 rows (is_active = 1 check fails) -> changes() is 0.
    - requester_guard_check fails with CHECK constraint violation.
    - Entire batch is rolled back atomically.
    - Request remains PENDING, asset remains AVAILABLE, 0 audit logs.
    """
    sqlite_occ_conn.execute(
        "INSERT INTO requests_assetrequest (id, request_code, request_type, requester_id, department_id, reason, status) "
        "VALUES (10, 'REQ-OCC-010', 'ALLOCATE', 5, 10, 'Deactivated requester', 'PENDING');"
    )
    sqlite_occ_conn.execute(
        "INSERT INTO authentication_user (id, username, role, is_active, department_id) "
        "VALUES (5, 'requester', 'USER', 0, 10);"  # Deactivated concurrently in DB!
    )
    sqlite_occ_conn.execute(
        "INSERT INTO assets_asset (id, asset_code, name, status, department_id, assigned_to_user_id) "
        "VALUES (300, 'AST-OCC-300', 'Laptop Available', 'AVAILABLE', 10, NULL);"
    )
    sqlite_occ_conn.commit()

    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)  # Stale in-memory view
    asset = Asset(id=300, asset_code="AST-OCC-300", name="Laptop Available", status=Asset.Status.AVAILABLE, department_id=10, category_id=1)
    req = AssetRequest(
        id=10,
        request_code="REQ-OCC-010",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        requested_category_id=1,
        reason="Deactivated requester",
        status=AssetRequest.Status.PENDING,
    )

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

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=asset), \
         patch("apps.requests.services.d1_batch", side_effect=d1_batch_sqlite_executor):

        with pytest.raises(ConflictError, match="Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."):
            RequestService.approve_request(
                request_id=10,
                actor=admin,
                data={"selected_asset_id": 300},
            )

    cur = sqlite_occ_conn.cursor()
    cur.execute("SELECT status FROM requests_assetrequest WHERE id = 10;")
    assert cur.fetchone()[0] == "PENDING"

    cur.execute("SELECT status, assigned_to_user_id FROM assets_asset WHERE id = 300;")
    asset_row = cur.fetchone()
    assert asset_row[0] == "AVAILABLE"
    assert asset_row[1] is None

    cur.execute("SELECT count(*) FROM assets_assethistorylog WHERE asset_id = 300;")
    assert cur.fetchone()[0] == 0


def test_approve_request_occ_conflict_requester_transferred_department_concurrently(sqlite_occ_conn):
    """
    OCC Conflict 4: Requester was transferred to another department concurrently in DB.
    - Request is PENDING, asset is AVAILABLE.
    - Requester in DB has department_id = 99 (request expects 10).
    - req_stmt matches 1 row -> req_guard passes.
    - requester_guard matches 0 rows (department_id = 10 check fails) -> changes() is 0.
    - requester_guard_check fails with CHECK constraint violation.
    - Entire batch is rolled back atomically.
    - Request remains PENDING, asset remains AVAILABLE, 0 audit logs.
    """
    sqlite_occ_conn.execute(
        "INSERT INTO requests_assetrequest (id, request_code, request_type, requester_id, department_id, reason, status) "
        "VALUES (11, 'REQ-OCC-011', 'ALLOCATE', 5, 10, 'Transferred requester', 'PENDING');"
    )
    sqlite_occ_conn.execute(
        "INSERT INTO authentication_user (id, username, role, is_active, department_id) "
        "VALUES (5, 'requester', 'USER', 1, 99);"  # Transferred to dept 99 in DB!
    )
    sqlite_occ_conn.execute(
        "INSERT INTO assets_asset (id, asset_code, name, status, department_id, assigned_to_user_id) "
        "VALUES (301, 'AST-OCC-301', 'Laptop Available', 'AVAILABLE', 10, NULL);"
    )
    sqlite_occ_conn.commit()

    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)  # Stale in-memory view
    asset = Asset(id=301, asset_code="AST-OCC-301", name="Laptop Available", status=Asset.Status.AVAILABLE, department_id=10, category_id=1)
    req = AssetRequest(
        id=11,
        request_code="REQ-OCC-011",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        requested_category_id=1,
        reason="Transferred requester",
        status=AssetRequest.Status.PENDING,
    )

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

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=requester), \
         patch.object(Asset.objects, "get", return_value=asset), \
         patch("apps.requests.services.d1_batch", side_effect=d1_batch_sqlite_executor):

        with pytest.raises(ConflictError, match="Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."):
            RequestService.approve_request(
                request_id=11,
                actor=admin,
                data={"selected_asset_id": 301},
            )

    cur = sqlite_occ_conn.cursor()
    cur.execute("SELECT status FROM requests_assetrequest WHERE id = 11;")
    assert cur.fetchone()[0] == "PENDING"

    cur.execute("SELECT status, assigned_to_user_id FROM assets_asset WHERE id = 301;")
    asset_row = cur.fetchone()
    assert asset_row[0] == "AVAILABLE"
    assert asset_row[1] is None

    cur.execute("SELECT count(*) FROM assets_assethistorylog WHERE asset_id = 301;")
    assert cur.fetchone()[0] == 0


# =============================================================================
# 3. reject_request on D1 (Atomic, Zero Asset Changes, Zero History Logs)
# =============================================================================

def test_reject_request_d1_success(mock_d1_context):
    """
    Test reject_request on D1:
    - Status becomes REJECTED.
    - rejection_reason recorded.
    - Exactly 2 statements in batch [update, assert_guard].
    - ZERO asset changes, ZERO AssetHistoryLog created!
    """
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    req = AssetRequest(
        id=60,
        request_code="REQ-2026-060",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        reason="Need extra laptop",
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(AssetRequest.objects, "get", return_value=req):

        result = RequestService.reject_request(
            request_id=60,
            actor=admin,
            rejection_reason="Chưa đến thời hạn cấp máy theo quy định công ty",
        )

        assert result == req
        assert len(mock_d1_context) == 1

        batch = mock_d1_context[0]
        assert len(batch) == 2, f"Expected 2 statements in reject batch, got {len(batch)}"

        stmt1, params1 = batch[0]
        assert "UPDATE requests_assetrequest" in stmt1
        assert "status = %s" in stmt1
        assert "rejection_reason = %s" in stmt1
        assert "reviewed_by_id = %s" in stmt1
        assert "WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s" in stmt1
        assert params1 == [
            AssetRequest.Status.REJECTED,
            "Chưa đến thời hạn cấp máy theo quy định công ty",
            admin.id,
            req.id,
            AssetRequest.Status.PENDING,
            req.requester_id,
            req.department_id,
        ]

        stmt2, params2 = batch[1]
        assert "INSERT INTO _assert_guard" in stmt2
        assert "changes()" in stmt2


def test_reject_request_occ_conflict_already_processed(sqlite_occ_conn):
    """
    OCC Conflict on reject_request:
    - If request is already APPROVED or REJECTED, UPDATE matches 0 rows.
    - _assert_guard fails with CHECK constraint violation.
    - ConflictError raised.
    - Zero audit logs created!
    """
    sqlite_occ_conn.execute(
        "INSERT INTO requests_assetrequest (id, request_code, request_type, requester_id, department_id, reason, status) "
        "VALUES (3, 'REQ-OCC-003', 'ALLOCATE', 5, 10, 'Reject race', 'APPROVED');"
    )
    sqlite_occ_conn.commit()

    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    # Stale view
    req = AssetRequest(
        id=3,
        request_code="REQ-OCC-003",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester_id=5,
        department_id=10,
        reason="Reject race",
        status=AssetRequest.Status.PENDING,
    )

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

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch("apps.requests.services.d1_batch", side_effect=d1_batch_sqlite_executor):

        with pytest.raises(ConflictError, match="Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."):
            RequestService.reject_request(
                request_id=3,
                actor=admin,
                rejection_reason="Attempting to reject already approved request",
            )

    cur = sqlite_occ_conn.cursor()
    cur.execute("SELECT status, rejection_reason FROM requests_assetrequest WHERE id = 3;")
    row = cur.fetchone()
    assert row[0] == "APPROVED"
    assert row[1] == ""

    # Zero audit logs
    cur.execute("SELECT count(*) FROM assets_assethistorylog;")
    assert cur.fetchone()[0] == 0


def test_reject_request_occ_conflict_department_tampered_concurrently(sqlite_occ_conn):
    """
    OCC Conflict on reject_request:
    - If request department_id was tampered concurrently, UPDATE matches 0 rows.
    - _assert_guard fails with CHECK constraint violation.
    - ConflictError raised.
    - Zero audit logs created!
    """
    sqlite_occ_conn.execute(
        "INSERT INTO requests_assetrequest (id, request_code, request_type, requester_id, department_id, reason, status) "
        "VALUES (4, 'REQ-OCC-004', 'ALLOCATE', 5, 20, 'Reject race dept', 'PENDING');"
    )
    sqlite_occ_conn.commit()

    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    # Stale view expecting department_id = 10
    req = AssetRequest(
        id=4,
        request_code="REQ-OCC-004",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester_id=5,
        department_id=10,
        reason="Reject race dept",
        status=AssetRequest.Status.PENDING,
    )

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

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch("apps.requests.services.d1_batch", side_effect=d1_batch_sqlite_executor):

        with pytest.raises(ConflictError, match="Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."):
            RequestService.reject_request(
                request_id=4,
                actor=admin,
                rejection_reason="Attempting to reject tampered request",
            )

    cur = sqlite_occ_conn.cursor()
    cur.execute("SELECT status, rejection_reason FROM requests_assetrequest WHERE id = 4;")
    row = cur.fetchone()
    assert row[0] == "PENDING"
    assert row[1] == ""

    cur.execute("SELECT count(*) FROM assets_assethistorylog;")
    assert cur.fetchone()[0] == 0


def test_reject_request_requires_non_empty_reason():
    """Verify reject_request rejects empty or whitespace-only reason with ValidationError."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True)

    for empty_reason in ("", "   ", None):
        with pytest.raises(ValidationError) as exc_info:
            RequestService.reject_request(1, admin, empty_reason)
        assert "rejection_reason" in exc_info.value.detail


# =============================================================================
# 4. Scoping & Role Permission Validations
# =============================================================================

def test_user_role_cannot_approve_or_reject():
    """Verify regular USER role cannot approve or reject requests."""
    user = User(id=10, username="user", role=User.Role.USER, is_active=True)

    with patch.object(RequestService, "_is_d1", return_value=True):
        with pytest.raises(PermissionDenied, match="Nhân viên không có quyền"):
            RequestService.approve_request(1, user, {})

        with pytest.raises(PermissionDenied, match="Nhân viên không có quyền"):
            RequestService.reject_request(1, user, "Reason")


def test_manager_cannot_approve_other_department_request():
    """Verify Manager cannot review requests from another department (404 Not Found scoping)."""
    manager = User(id=2, username="manager", role=User.Role.MANAGER, is_active=True, department_id=10)
    req = AssetRequest(
        id=70,
        request_code="REQ-2026-070",
        request_type=AssetRequest.RequestType.ALLOCATE,
        department_id=20,  # Different department
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(AssetRequest.objects, "get", return_value=req):
        with pytest.raises(NotFound, match="phạm vi phòng ban"):
            RequestService.approve_request(70, manager, {})

        with pytest.raises(NotFound, match="phạm vi phòng ban"):
            RequestService.reject_request(70, manager, "Reason")


def test_approve_rejects_inactive_requester():
    """Verify approve_request rejects if requester is deactivated."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    inactive_requester = User(id=5, username="requester", role=User.Role.USER, is_active=False, department_id=10)
    req = AssetRequest(
        id=80,
        request_code="REQ-2026-080",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=inactive_requester,
        requester_id=5,
        department_id=10,
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=inactive_requester):

        with pytest.raises(ConflictError, match="Người yêu cầu hiện không còn hoạt động"):
            RequestService.approve_request(80, admin, {"selected_asset_id": 1})


def test_approve_rejects_requester_transferred_department():
    """Verify approve_request rejects if requester transferred to another department."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    transferred_requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=20)
    req = AssetRequest(
        id=81,
        request_code="REQ-2026-081",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=transferred_requester,
        requester_id=5,
        department_id=10,  # Request was created for department 10
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(User.objects, "get", return_value=transferred_requester):

        with pytest.raises(ConflictError, match="chuyển sang phòng ban khác"):
            RequestService.approve_request(81, admin, {"selected_asset_id": 1})


# =============================================================================
# 5. Domain Specific Validations (Category, Department, Condition, Ownership)
# =============================================================================

def test_approve_allocate_rejects_category_mismatch():
    """Verify allocate approval rejects asset with mismatched category."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    mismatched_asset = Asset(
        id=105,
        asset_code="AST-105",
        name="Monitor",
        status=Asset.Status.AVAILABLE,
        department_id=10,
        category_id=2,  # Category 2 != requested 1
    )
    req = AssetRequest(
        id=85,
        request_code="REQ-2026-085",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        requested_category_id=1,
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(Asset.objects, "get", return_value=mismatched_asset):

        with pytest.raises(ValidationError) as exc_info:
            RequestService.approve_request(85, admin, {"selected_asset_id": 105})
        assert "Danh mục" in str(exc_info.value.detail)


def test_approve_allocate_rejects_department_mismatch():
    """Verify allocate approval rejects asset belonging to another department."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    foreign_dept_asset = Asset(
        id=106,
        asset_code="AST-106",
        name="Laptop",
        status=Asset.Status.AVAILABLE,
        department_id=20,  # Department 20 != request department 10
        category_id=1,
    )
    req = AssetRequest(
        id=86,
        request_code="REQ-2026-086",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=requester,
        requester_id=5,
        department_id=10,
        requested_category_id=1,
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(Asset.objects, "get", return_value=foreign_dept_asset):

        with pytest.raises(ValidationError) as exc_info:
            RequestService.approve_request(86, admin, {"selected_asset_id": 106})
        assert "phòng ban" in str(exc_info.value.detail)


def test_approve_return_rejects_unconfirmed_receipt():
    """Verify return approval strictly requires received_confirmed = True."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    req = AssetRequest(
        id=87,
        request_code="REQ-2026-087",
        request_type=AssetRequest.RequestType.RETURN,
        requester=requester,
        requester_id=5,
        department_id=10,
        asset_id=107,
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req):

        with pytest.raises(ValidationError) as exc_info:
            RequestService.approve_request(87, admin, {"received_confirmed": False, "condition": "GOOD"})
        assert "received_confirmed" in exc_info.value.detail


def test_approve_return_rejects_invalid_condition():
    """Verify return approval rejects invalid condition values."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    req = AssetRequest(
        id=88,
        request_code="REQ-2026-088",
        request_type=AssetRequest.RequestType.RETURN,
        requester=requester,
        requester_id=5,
        department_id=10,
        asset_id=108,
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req):

        with pytest.raises(ValidationError) as exc_info:
            RequestService.approve_request(88, admin, {"received_confirmed": True, "condition": "BROKEN"})
        assert "condition" in exc_info.value.detail


def test_approve_return_rejects_asset_holder_mismatch():
    """Verify return approval fails if asset is not currently held by requester."""
    admin = User(id=1, username="admin", role=User.Role.ADMIN, is_active=True, department_id=10)
    requester = User(id=5, username="requester", role=User.Role.USER, is_active=True, department_id=10)
    asset = Asset(
        id=109,
        asset_code="AST-109",
        name="Laptop",
        status=Asset.Status.IN_USE,
        department_id=10,
        assigned_to_user_id=99,  # Held by someone else!
    )
    req = AssetRequest(
        id=89,
        request_code="REQ-2026-089",
        request_type=AssetRequest.RequestType.RETURN,
        requester=requester,
        requester_id=5,
        department_id=10,
        asset=asset,
        asset_id=109,
        status=AssetRequest.Status.PENDING,
    )

    with patch.object(RequestService, "_is_d1", return_value=True), \
         patch.object(RequestService, "_get_locked_request", return_value=req), \
         patch.object(Asset.objects, "get", return_value=asset):

        with pytest.raises(ConflictError, match="không còn được giữ bởi người yêu cầu"):
            RequestService.approve_request(89, admin, {"received_confirmed": True, "condition": "GOOD"})


# =============================================================================
# 6. _is_d1 Helper Detection Tests
# =============================================================================

def test_is_d1_detection_helper():
    """Verify RequestService._is_d1 helper handles vendor checks and mocks accurately."""
    # 1. Explicit aliases 'd1' and 'cloudflare_d1'
    assert RequestService._is_d1(using="d1") is True
    assert RequestService._is_d1(using="cloudflare_d1") is True

    # 2. Connection with vendor 'postgresql'
    mock_pg_conn = MagicMock()
    mock_pg_conn.vendor = "postgresql"
    with patch("apps.requests.services.connections", {"pg_db": mock_pg_conn}):
        assert RequestService._is_d1(using="pg_db") is False

    # 3. Connection with vendor 'cloudflare_d1'
    mock_d1_conn = MagicMock()
    mock_d1_conn.vendor = "cloudflare_d1"
    with patch("apps.requests.services.connections", {"d1_db": mock_d1_conn}):
        assert RequestService._is_d1(using="d1_db") is True

    # 4. Connection with client attribute
    mock_client_conn = MagicMock()
    mock_client_conn.vendor = "custom"
    mock_client_conn.client = MagicMock()
    with patch("apps.requests.services.connections", {"client_db": mock_client_conn}):
        assert RequestService._is_d1(using="client_db") is True
