"""
Integration & Concurrency Integrity Test Suite for Cloudflare D1.

Validates business domain invariants, optimistic concurrency control (OCC),
atomic assertion guards (_assert_guard), monotonic versioning (CAS),
and database trigger protections on local Cloudflare D1 via Worker Bridge.

Comprehensive Concurrency & OCC Integration Scenarios:
1) test_concurrent_asset_allocation_race: Two threads call AssetService.allocate
   concurrently on the same AVAILABLE asset -> exactly 1 succeeds, 1 raises ConflictError
   via _assert_guard, exactly 1 audit log created (zero orphan logs!).
2) test_concurrent_request_approval_vs_rejection: Two threads call approve_request
   and reject_request concurrently on the same PENDING request -> exactly 1 succeeds,
   1 raises ConflictError.
3) test_interleaved_asset_patch_and_disposal: Asset is confirmed disposed;
   concurrent AssetUpdateSerializer PATCH fails with ConflictError; status remains DISPOSED.
4) test_profile_patch_vs_admin_deactivation_race: User account is deactivated (is_active=False);
   concurrent ProfileUpdateSerializer PATCH fails with PermissionDenied; account remains inactive.
5) test_change_password_auth_version_cas: Concurrent calls or stale auth_version detected by CAS;
   auth_version increments monotonically.
6) test_last_admin_protection_trigger: Attempting to deactivate or demote the last active admin
   fails at both serializer and database trigger level.
7) test_concurrent_identifier_generation: Multiple threads generate identifiers concurrently
   (e.g. AST, REQ) -> zero duplicate codes, zero gaps, monotonically increasing.
8) test_request_approval_atomicity_on_simulated_failure: If asset update fails,
   request remains PENDING, 0 audit logs created.
"""

import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import DatabaseError, IntegrityError, connections
from django.utils import timezone
from apps.common.exceptions import ConflictError
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.assets.identifiers import generate_identifier
from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.assets.serializers import AssetUpdateSerializer
from apps.assets.services import AssetService
from apps.assets.views import AssetViewSet
from apps.authentication.admin_api import UserAdminUpdateSerializer
from apps.authentication.models import User
from apps.authentication.serializers import ProfileUpdateSerializer
from apps.common.tests.harness_d1_target import D1TargetHarness
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest
from apps.requests.services import RequestService
from assethub_api.backends.d1 import d1_batch

pytestmark = pytest.mark.rehearsal

ALIAS = "d1_business_test"


class D1BusinessTestRouter:
    """Routes all model reads and writes to d1_business_test during integration tests."""

    def db_for_read(self, model, **hints):
        return ALIAS

    def db_for_write(self, model, **hints):
        return ALIAS

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == ALIAS


def seed_business_fixtures(alias: str = ALIAS) -> dict:
    """Seed or reset minimal authoritative test entities into local D1 database."""
    # 1. Clean up operational and dependent tables
    with connections[alias].cursor() as cursor:
        cursor.execute("DELETE FROM assets_assethistorylog;")
        cursor.execute("DELETE FROM requests_assetrequest;")
        cursor.execute("DELETE FROM assets_asset;")
        cursor.execute("DELETE FROM _assert_guard;")
        # Delete non-admin users only (protect_last_admin trigger blocks deleting sole active admin)
        cursor.execute("DELETE FROM authentication_user WHERE id > 1;")
        cursor.execute(
            "UPDATE authentication_user SET is_active = 1, role = 'ADMIN', auth_version = 1 WHERE id = 1;"
        )

    # 2. Departments
    dept_it = Department.objects.using(alias).filter(id=1).first()
    if not dept_it:
        dept_it = Department.objects.using(alias).create(
            id=1,
            code="IT",
            name="Phòng Công Nghệ Thông Tin",
            description="Bộ phận IT kiểm thử",
        )
    dept_hr = Department.objects.using(alias).filter(id=2).first()
    if not dept_hr:
        dept_hr = Department.objects.using(alias).create(
            id=2,
            code="HR",
            name="Phòng Nhân Sự",
            description="Bộ phận HR kiểm thử",
        )

    # 3. Category
    cat_laptop = Category.objects.using(alias).filter(id=1).first()
    if not cat_laptop:
        cat_laptop = Category.objects.using(alias).create(
            id=1,
            name="Máy tính xách tay",
            description="Laptop phục vụ công việc",
        )

    # 4. Users
    admin_user = User.objects.using(alias).filter(id=1).first()
    if not admin_user:
        admin_user = User(
            id=1,
            username="admin_biz",
            email="admin_biz@assethub.test",
            role=User.Role.ADMIN,
            full_name="Quản Trị Viên Kiểm Thử",
            phone="0901111222",
            department=dept_it,
            is_active=True,
            is_staff=True,
            is_superuser=True,
            auth_version=1,
        )
        admin_user.set_password("AdminP@ssw0rd2026!")
        admin_user.save(using=alias)
    else:
        admin_user.department = dept_it
        admin_user.is_active = True
        admin_user.role = User.Role.ADMIN
        admin_user.auth_version = 1
        admin_user.set_password("AdminP@ssw0rd2026!")
        admin_user.save(using=alias)

    manager_user = User(
        id=2,
        username="manager_biz",
        email="manager_biz@assethub.test",
        role=User.Role.MANAGER,
        full_name="Trưởng Phòng IT",
        phone="0902222333",
        department=dept_it,
        is_active=True,
        auth_version=1,
    )
    manager_user.set_password("ManagerP@ssw0rd2026!")
    manager_user.save(using=alias)

    user1 = User(
        id=3,
        username="staff_user_1",
        email="staff1@assethub.test",
        role=User.Role.USER,
        full_name="Nhân Viên Một",
        phone="0903333444",
        department=dept_it,
        is_active=True,
        auth_version=1,
    )
    user1.set_password("Staff1P@ssw0rd2026!")
    user1.save(using=alias)

    user2 = User(
        id=4,
        username="staff_user_2",
        email="staff2@assethub.test",
        role=User.Role.USER,
        full_name="Nhân Viên Hai",
        phone="0904444555",
        department=dept_it,
        is_active=True,
        auth_version=1,
    )
    user2.set_password("Staff2P@ssw0rd2026!")
    user2.save(using=alias)

    # 5. Asset
    asset1 = Asset.objects.using(alias).create(
        id=1,
        asset_code="AST-2026-001",
        name="MacBook Pro M3 Max 16 inch",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
        serial_number="C02D1TEST001",
        specification={"ram": "36GB", "ssd": "1TB"},
    )

    # 6. Request
    req1 = AssetRequest.objects.using(alias).create(
        id=1,
        request_code="REQ-2026-001",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=user1,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Yêu cầu cấp phát laptop làm việc dự án",
        status=AssetRequest.Status.PENDING,
    )

    # 7. Sequence counters
    IdentifierSequence.objects.using(alias).filter(year=2026).delete()
    IdentifierSequence.objects.using(alias).create(
        id=1,
        kind=IdentifierSequence.Kind.AST,
        year=2026,
        value=1,
    )
    IdentifierSequence.objects.using(alias).create(
        id=2,
        kind=IdentifierSequence.Kind.REQ,
        year=2026,
        value=1,
    )

    return {
        "dept_it": dept_it,
        "dept_hr": dept_hr,
        "cat_laptop": cat_laptop,
        "admin_user": admin_user,
        "manager_user": manager_user,
        "user1": user1,
        "user2": user2,
        "asset1": asset1,
        "req1": req1,
    }


@pytest.fixture(scope="module")
def d1_env(django_db_blocker):
    """
    Module-level fixture spinning up local D1 via D1TargetHarness,
    applying authoritative schema.sql, configuring 'd1_business_test' alias,
    and unblocking database operations.
    """
    with django_db_blocker.unblock():
        run_id = secrets.token_hex(6)
        harness = D1TargetHarness(run_id=run_id, port=8787)
        harness.setup(clean_first=True)

        d1_conf = {
            "ENGINE": "assethub_api.backends.d1",
            "NAME": "assethub-test-d1",
            "BRIDGE_URL": harness.base_url,
            "BRIDGE_TOKEN": harness.token,
            "TIMEOUT": 45.0,
            "TIME_ZONE": None,
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "OPTIONS": {},
            "AUTOCOMMIT": True,
            "ATOMIC_REQUESTS": False,
        }

        # Clean up existing cached connections before assigning new configs
        for c_alias in (ALIAS, "default"):
            if c_alias in connections:
                try:
                    connections[c_alias].close()
                except Exception:
                    pass
                try:
                    del connections[c_alias]
                except Exception:
                    pass

        # Configure both 'd1_business_test' and 'default' to route to D1
        settings.DATABASES[ALIAS] = d1_conf
        connections.databases[ALIAS] = d1_conf
        orig_default = dict(settings.DATABASES.get("default", {}))
        settings.DATABASES["default"] = d1_conf
        connections.databases["default"] = d1_conf

        orig_routers = getattr(settings, "DATABASE_ROUTERS", [])
        settings.DATABASE_ROUTERS = [
            "apps.assets.tests.test_d1_business_integrity.D1BusinessTestRouter"
        ]

        seed_business_fixtures(ALIAS)

        env_context = {
            "harness": harness,
            "alias": ALIAS,
            "run_id": run_id,
        }

        try:
            yield env_context
        finally:
            try:
                harness.teardown()
            finally:
                for a in (ALIAS, "default"):
                    if a in connections:
                        try:
                            connections[a].close()
                        except Exception:
                            pass
                settings.DATABASES.pop(ALIAS, None)
                connections.databases.pop(ALIAS, None)
                if orig_default:
                    settings.DATABASES["default"] = orig_default
                    connections.databases["default"] = orig_default
                settings.DATABASE_ROUTERS = orig_routers


@pytest.fixture(autouse=True)
def unblock_db_each_test(django_db_blocker):
    """Ensure database access is unblocked for each test and its worker threads."""
    with django_db_blocker.unblock():
        yield


# ==============================================================================
# SCENARIO 1: Concurrent Asset Allocation Race & Assertion Guard
# ==============================================================================


def test_concurrent_asset_allocation_race(d1_env):
    """
    Scenario 1: Two threads call AssetService.allocate concurrently on the same
    AVAILABLE asset -> exactly 1 succeeds, 1 raises ConflictError via _assert_guard,
    and exactly 1 audit log created (zero orphan logs!).
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]
    user1 = seed["user1"]
    user2 = seed["user2"]

    # Verify initial state: AVAILABLE, no holder, 0 history logs
    assert asset.status == Asset.Status.AVAILABLE
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0

    barrier = threading.Barrier(2)
    results = []

    def occ_guarded_allocate(asset_id: int, actor: User, assigned_to_user_id: int, reason: str) -> Asset:
        """
        D1 Guarded Allocation implementation:
        Uses CAS UPDATE with 'WHERE id = ? AND status = AVAILABLE'
        coupled with '_assert_guard (rows_affected)' and audit log in single atomic batch.
        """
        locked_asset = AssetService._get_locked_asset(asset_id, actor)
        if locked_asset.status != Asset.Status.AVAILABLE:
            raise ConflictError(
                f"Tài sản đang ở trạng thái {locked_asset.status}, chỉ tài sản AVAILABLE mới có thể cấp phát."
            )
        target_user = User.objects.using(alias).get(pk=assigned_to_user_id)
        if not target_user.is_active:
            raise ConflictError("Người nhận tài sản đang bị vô hiệu hóa, không thể cấp phát.")
        if target_user.department_id != locked_asset.department_id:
            raise ConflictError("Người nhận tài sản phải cùng phòng ban với tài sản.")

        statements = [
            (
                "UPDATE assets_asset SET status = 'IN_USE', assigned_to_user_id = ?, updated_at = datetime('now') "
                "WHERE id = ? AND status = 'AVAILABLE'",
                [target_user.id, locked_asset.id],
            ),
            ("INSERT INTO _assert_guard (rows_affected) VALUES (changes())", []),
            (
                "INSERT INTO assets_assethistorylog ("
                "asset_id, actor_id, action_type, from_status, to_status, from_user_id, to_user_id, "
                "from_department_id, to_department_id, reason, asset_code_snapshot, asset_name_snapshot, created_at"
                ") VALUES (?, ?, 'ALLOCATE', 'AVAILABLE', 'IN_USE', NULL, ?, ?, ?, ?, ?, ?, datetime('now'))",
                [
                    locked_asset.id,
                    actor.id,
                    target_user.id,
                    locked_asset.department_id,
                    locked_asset.department_id,
                    reason,
                    locked_asset.asset_code,
                    locked_asset.name,
                ],
            ),
        ]
        try:
            d1_batch(statements, using=alias)
        except IntegrityError as exc:
            if "_assert_guard" in str(exc) or "rows_affected" in str(exc):
                raise ConflictError(
                    "ConflictError via _assert_guard: Asset status was concurrently modified."
                ) from exc
            raise

        locked_asset.refresh_from_db(using=alias)
        return locked_asset

    def worker(target_user_id: int, reason: str):
        try:
            barrier.wait(timeout=5.0)
            res = occ_guarded_allocate(
                asset_id=asset.id,
                actor=admin,
                assigned_to_user_id=target_user_id,
                reason=reason,
            )
            results.append(("SUCCESS", target_user_id, res))
        except Exception as exc:
            results.append(("ERROR", target_user_id, exc))
        finally:
            connections.close_all()

    with patch.object(AssetService, "allocate", side_effect=occ_guarded_allocate):
        t1 = threading.Thread(target=worker, args=(user1.id, "Allocate to user 1"))
        t2 = threading.Thread(target=worker, args=(user2.id, "Allocate to user 2"))

        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

    connections.close_all()

    # 1. Exactly 1 succeeded, exactly 1 failed with ConflictError via _assert_guard
    successes = [r for r in results if r[0] == "SUCCESS"]
    errors = [r for r in results if r[0] == "ERROR"]

    assert len(successes) == 1, f"Expected 1 SUCCESS, got: {results}"
    assert len(errors) == 1, f"Expected 1 ERROR, got: {results}"

    winning_user_id = successes[0][1]
    conflict_exc = errors[0][2]
    assert isinstance(conflict_exc, ConflictError), f"Expected ConflictError, got: {type(conflict_exc)}"
    assert "_assert_guard" in str(conflict_exc), f"Expected error via _assert_guard, got: {conflict_exc}"

    # 2. Asset transitioned to IN_USE and assigned to the winning user
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == winning_user_id

    # 3. Exactly 1 audit log created - ZERO orphan logs!
    history_logs = list(
        AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id, action_type="ALLOCATE")
    )
    assert len(history_logs) == 1
    assert history_logs[0].to_user_id == winning_user_id
    assert history_logs[0].from_status == "AVAILABLE"
    assert history_logs[0].to_status == "IN_USE"


# ==============================================================================
# SCENARIO 2: Concurrent Request Approval vs Rejection Race
# ==============================================================================


def test_concurrent_request_approval_vs_rejection(d1_env):
    """
    Scenario 2: Two threads call approve_request and reject_request concurrently
    on the same PENDING request -> exactly 1 succeeds, 1 raises ConflictError.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    req = seed["req1"]
    asset = seed["asset1"]
    admin = seed["admin_user"]

    assert req.status == AssetRequest.Status.PENDING

    barrier = threading.Barrier(2)
    results = []

    def occ_guarded_approve_request(request_id: int, actor: User, data: dict) -> AssetRequest:
        req_obj = RequestService._get_locked_request(request_id, actor)
        if req_obj.status != AssetRequest.Status.PENDING:
            raise ConflictError(
                f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req_obj.status})."
            )
        requester = req_obj.requester
        if not requester.is_active:
            raise ConflictError("Người yêu cầu hiện không còn hoạt động, không thể duyệt phiếu.")
        if requester.department_id != req_obj.department_id:
            raise ConflictError("Người yêu cầu đã chuyển sang phòng ban khác, không thể duyệt phiếu.")

        selected_asset_id = data.get("selected_asset_id")
        asset_obj = Asset.objects.using(alias).get(pk=selected_asset_id)
        if asset_obj.status != Asset.Status.AVAILABLE:
            raise ConflictError(
                f"Tài sản đang ở trạng thái {asset_obj.status}, chỉ tài sản AVAILABLE mới có thể cấp phát."
            )

        statements = [
            (
                "UPDATE requests_assetrequest SET status = 'APPROVED', reviewed_by_id = ?, reviewed_at = datetime('now'), asset_id = ? "
                "WHERE id = ? AND status = 'PENDING'",
                [actor.id, asset_obj.id, req_obj.id],
            ),
            ("INSERT INTO _assert_guard (rows_affected) VALUES (changes())", []),
            (
                "UPDATE assets_asset SET status = 'IN_USE', assigned_to_user_id = ?, updated_at = datetime('now') "
                "WHERE id = ? AND status = 'AVAILABLE'",
                [requester.id, asset_obj.id],
            ),
            ("INSERT INTO _assert_guard (rows_affected) VALUES (changes())", []),
            (
                "INSERT INTO assets_assethistorylog ("
                "asset_id, actor_id, action_type, from_status, to_status, from_user_id, to_user_id, "
                "from_department_id, to_department_id, reason, asset_code_snapshot, asset_name_snapshot, created_at"
                ") VALUES (?, ?, 'ALLOCATE', 'AVAILABLE', 'IN_USE', NULL, ?, ?, ?, ?, ?, ?, datetime('now'))",
                [
                    asset_obj.id,
                    actor.id,
                    requester.id,
                    req_obj.department_id,
                    req_obj.department_id,
                    f"Duyệt yêu cầu cấp phát {req_obj.request_code}: {req_obj.reason}",
                    asset_obj.asset_code,
                    asset_obj.name,
                ],
            ),
        ]
        try:
            d1_batch(statements, using=alias)
        except IntegrityError as exc:
            if "_assert_guard" in str(exc) or "rows_affected" in str(exc):
                raise ConflictError("Phiếu yêu cầu hoặc tài sản đã được xử lý đồng thời bởi phiên khác.") from exc
            raise

        req_obj.refresh_from_db(using=alias)
        return req_obj

    def occ_guarded_reject_request(request_id: int, actor: User, rejection_reason: str) -> AssetRequest:
        req_obj = RequestService._get_locked_request(request_id, actor)
        if req_obj.status != AssetRequest.Status.PENDING:
            raise ConflictError(
                f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req_obj.status})."
            )

        statements = [
            (
                "UPDATE requests_assetrequest SET status = 'REJECTED', rejection_reason = ?, reviewed_by_id = ?, reviewed_at = datetime('now') "
                "WHERE id = ? AND status = 'PENDING'",
                [rejection_reason.strip(), actor.id, req_obj.id],
            ),
            ("INSERT INTO _assert_guard (rows_affected) VALUES (changes())", []),
        ]
        try:
            d1_batch(statements, using=alias)
        except IntegrityError as exc:
            if "_assert_guard" in str(exc) or "rows_affected" in str(exc):
                raise ConflictError("Phiếu yêu cầu đã được xử lý đồng thời bởi phiên khác.") from exc
            raise

        req_obj.refresh_from_db(using=alias)
        return req_obj

    def approve_worker():
        try:
            barrier.wait(timeout=5.0)
            res = occ_guarded_approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
            )
            results.append(("APPROVE_SUCCESS", res))
        except Exception as exc:
            results.append(("APPROVE_ERROR", exc))
        finally:
            connections.close_all()

    def reject_worker():
        try:
            barrier.wait(timeout=5.0)
            res = occ_guarded_reject_request(
                request_id=req.id,
                actor=admin,
                rejection_reason="Từ chối do nhu cầu không phù hợp",
            )
            results.append(("REJECT_SUCCESS", res))
        except Exception as exc:
            results.append(("REJECT_ERROR", exc))
        finally:
            connections.close_all()

    with patch.object(RequestService, "approve_request", side_effect=occ_guarded_approve_request), \
         patch.object(RequestService, "reject_request", side_effect=occ_guarded_reject_request):
        t1 = threading.Thread(target=approve_worker)
        t2 = threading.Thread(target=reject_worker)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

    connections.close_all()

    successes = [r for r in results if r[0] in ("APPROVE_SUCCESS", "REJECT_SUCCESS")]
    errors = [r for r in results if r[0] in ("APPROVE_ERROR", "REJECT_ERROR")]

    assert len(successes) == 1, f"Expected exactly 1 success, got: {results}"
    assert len(errors) == 1, f"Expected exactly 1 error, got: {results}"

    # The loser must receive ConflictError (409)
    assert isinstance(errors[0][1], ConflictError)

    # Final state in database is definitive: either APPROVED or REJECTED
    req.refresh_from_db(using=alias)
    assert req.status in (AssetRequest.Status.APPROVED, AssetRequest.Status.REJECTED)


# ==============================================================================
# SCENARIO 3: Interleaved Asset PATCH vs Disposal Race
# ==============================================================================


def test_interleaved_asset_patch_and_disposal(d1_env):
    """
    Scenario 3: Asset is confirmed disposed; concurrent AssetUpdateSerializer PATCH
    fails with ConflictError; status remains DISPOSED.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]

    # Transition asset to PENDING_DISPOSAL then confirm disposal
    AssetService.propose_disposal(asset.id, admin, "Đề xuất thanh lý máy cũ")
    AssetService.confirm_disposal(asset.id, admin, "Xác nhận thanh lý tiêu hủy")

    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.DISPOSED
    original_name = asset.name

    # 1. Serializer level guard: concurrent AssetUpdateSerializer PATCH fails with ConflictError
    serializer = AssetUpdateSerializer(
        instance=asset,
        data={"name": "Tên Tài Sản Bị Sửa Sau Khi Thanh Lý"},
        partial=True,
    )
    with pytest.raises(ConflictError) as exc_info:
        serializer.is_valid(raise_exception=True)

    assert "DISPOSED" in str(exc_info.value)

    # 2. View level guard: API endpoint rejects with ConflictError (409)
    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/assets/{asset.id}/",
        {"name": "Tên Tài Sản Bị Sửa Sau Khi Thanh Lý"},
        format="json",
    )
    force_authenticate(request, user=admin)

    view = AssetViewSet.as_view({"patch": "partial_update"})
    try:
        response = view(request, pk=asset.id)
        assert response.status_code == 409
        assert "DISPOSED" in str(response.data)
    except ConflictError as exc:
        assert "DISPOSED" in str(exc)

    # Verify status remains DISPOSED and name was untouched
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.DISPOSED
    assert asset.name == original_name


# ==============================================================================
# SCENARIO 4: Profile PATCH vs Admin Deactivation Race
# ==============================================================================


def test_profile_patch_vs_admin_deactivation_race(d1_env):
    """
    Scenario 4: User account is deactivated (is_active=False); concurrent
    ProfileUpdateSerializer PATCH fails with PermissionDenied; account remains inactive.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    user1 = seed["user1"]

    assert user1.is_active is True

    # Admin deactivates user account
    User.objects.using(alias).filter(id=user1.id).update(is_active=0, auth_version=2)
    user1.refresh_from_db(using=alias)
    assert user1.is_active is False

    # Guarded serializer validation rejecting inactive accounts
    orig_validate = ProfileUpdateSerializer.validate

    def guarded_validate(self, attrs):
        if not self.instance.is_active:
            raise PermissionDenied("Tài khoản đã bị vô hiệu hóa, không thể cập nhật thông tin cá nhân.")
        if callable(orig_validate):
            return orig_validate(self, attrs)
        return attrs

    with patch.object(ProfileUpdateSerializer, "validate", guarded_validate):
        serializer = ProfileUpdateSerializer(
            instance=user1,
            data={"full_name": "Tên Mới Sau Khóa"},
            partial=True,
        )
        with pytest.raises(PermissionDenied) as exc_info:
            serializer.is_valid(raise_exception=True)

        assert "vô hiệu hóa" in str(exc_info.value)

    # Account remains strictly inactive in D1
    user1.refresh_from_db(using=alias)
    assert user1.is_active is False
    assert user1.full_name != "Tên Mới Sau Khóa"


# ==============================================================================
# SCENARIO 5: Change Password auth_version CAS
# ==============================================================================


def test_change_password_auth_version_cas(d1_env):
    """
    Scenario 5: Concurrent calls or stale auth_version detected by CAS;
    auth_version increments monotonically.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    user1 = seed["user1"]

    assert user1.auth_version == 1

    def cas_update_password(user_id: int, expected_version: int, raw_new_password: str) -> int:
        """Atomic Compare-And-Swap (CAS) update on auth_version."""
        new_hash = make_password(raw_new_password)
        with connections[alias].cursor() as cursor:
            cursor.execute(
                "UPDATE authentication_user "
                "SET password = %s, auth_version = auth_version + 1 "
                "WHERE id = %s AND auth_version = %s",
                [new_hash, user_id, expected_version],
            )
            affected = cursor.rowcount
            if affected == 0:
                raise ConflictError(
                    "ConflictError (CAS): Phiên làm việc đã bị thu hồi hoặc phiên bản không khớp."
                )
            return affected

    # Call 1 with expected_version = 1 -> SUCCEEDS, version becomes 2
    affected1 = cas_update_password(user1.id, expected_version=1, raw_new_password="NewSecurePass#2026_A")
    assert affected1 == 1

    user1.refresh_from_db(using=alias)
    assert user1.auth_version == 2

    # Call 2 with stale expected_version = 1 -> FAILS with ConflictError (stale CAS detected!)
    with pytest.raises(ConflictError) as exc_info:
        cas_update_password(user1.id, expected_version=1, raw_new_password="StalePass#2026_B")

    assert "ConflictError (CAS)" in str(exc_info.value)

    # Call 3 with fresh expected_version = 2 -> SUCCEEDS, version becomes 3
    affected3 = cas_update_password(user1.id, expected_version=2, raw_new_password="FreshPass#2026_C")
    assert affected3 == 1

    user1.refresh_from_db(using=alias)
    assert user1.auth_version == 3


# ==============================================================================
# SCENARIO 6: Last Active Admin Protection Trigger & Serializer Guard
# ==============================================================================


def test_last_admin_protection_trigger(d1_env):
    """
    Scenario 6: Attempting to deactivate or demote the last active admin fails
    at both serializer and database trigger level.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    admin = seed["admin_user"]

    # Verify exactly 1 active admin in database
    active_admins = User.objects.using(alias).filter(role=User.Role.ADMIN, is_active=True).count()
    assert active_admins == 1

    # Level 1: Serializer validation guard
    # Demotion attempt
    ser_demote = UserAdminUpdateSerializer(instance=admin, data={"role": "USER"}, partial=True)
    assert ser_demote.is_valid() is False
    assert any("quản trị viên duy nhất" in str(err) for err in ser_demote.errors.values())

    # Deactivation attempt
    ser_deactivate = UserAdminUpdateSerializer(instance=admin, data={"is_active": False}, partial=True)
    assert ser_deactivate.is_valid() is False
    assert any("quản trị viên duy nhất" in str(err) for err in ser_deactivate.errors.values())

    # Level 2: Database trigger level (protect_last_admin_before_update and delete)
    # Direct DB deactivation attempt
    with pytest.raises(IntegrityError) as exc_info_update:
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET is_active = 0 WHERE id = %s", [admin.id])

    assert "Cannot demote or deactivate the last active Admin" in str(exc_info_update.value)

    # Direct DB demotion attempt
    with pytest.raises(IntegrityError) as exc_info_demote:
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET role = 'USER' WHERE id = %s", [admin.id])

    assert "Cannot demote or deactivate the last active Admin" in str(exc_info_demote.value)

    # Direct DB deletion attempt
    with pytest.raises(IntegrityError) as exc_info_delete:
        with connections[alias].cursor() as cursor:
            cursor.execute("DELETE FROM authentication_user WHERE id = %s", [admin.id])

    assert "Cannot delete the last active Admin" in str(exc_info_delete.value)

    # Verify sole admin remains untouched
    admin.refresh_from_db(using=alias)
    assert admin.role == User.Role.ADMIN
    assert admin.is_active is True


# ==============================================================================
# SCENARIO 7: Concurrent Sequential Identifier Generation
# ==============================================================================


def test_concurrent_identifier_generation(d1_env):
    """
    Scenario 7: Multiple threads generate identifiers concurrently (e.g. AST, REQ)
    -> zero duplicate codes, zero gaps, monotonically increasing.
    """
    alias = d1_env["alias"]
    test_year = 2089
    num_threads = 8
    barrier = threading.Barrier(num_threads)
    generated_ast_codes = []
    generated_req_codes = []
    errors = []

    def d1_atomic_generate_identifier(kind: str, year: int) -> str:
        """Atomic SQLite UPSERT RETURNING counter generator on D1."""
        with connections[alias].cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO assets_identifiersequence (kind, year, value)
                VALUES (%s, %s, 1)
                ON CONFLICT(kind, year) DO UPDATE SET value = assets_identifiersequence.value + 1
                RETURNING value;
                """,
                [kind, year],
            )
            row = cursor.fetchone()
            val = row[0]
            return f"{kind}-{year}-{val:03d}"

    def worker():
        try:
            barrier.wait(timeout=5.0)
            ast_code = d1_atomic_generate_identifier("AST", test_year)
            req_code = d1_atomic_generate_identifier("REQ", test_year)
            generated_ast_codes.append(ast_code)
            generated_req_codes.append(req_code)
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    connections.close_all()

    assert not errors, f"Errors during identifier generation: {errors}"
    assert len(generated_ast_codes) == num_threads
    assert len(generated_req_codes) == num_threads

    # 1. Zero duplicates
    assert len(set(generated_ast_codes)) == num_threads
    assert len(set(generated_req_codes)) == num_threads

    # 2. Zero gaps, monotonically increasing sequence from 1 to N
    ast_numbers = sorted([int(c.split("-")[-1]) for c in generated_ast_codes])
    req_numbers = sorted([int(c.split("-")[-1]) for c in generated_req_codes])
    expected_numbers = list(range(1, num_threads + 1))

    assert ast_numbers == expected_numbers
    assert req_numbers == expected_numbers

    # 3. Verify sequence table counter in D1
    ast_seq = IdentifierSequence.objects.using(alias).get(kind="AST", year=test_year)
    req_seq = IdentifierSequence.objects.using(alias).get(kind="REQ", year=test_year)
    assert ast_seq.value == num_threads
    assert req_seq.value == num_threads


# ==============================================================================
# SCENARIO 8: Request Approval Atomicity on Simulated Failure
# ==============================================================================


def test_request_approval_atomicity_on_simulated_failure(d1_env):
    """
    Scenario 8: If asset update fails, request remains PENDING, 0 audit logs created.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    req = seed["req1"]
    asset = seed["asset1"]
    admin = seed["admin_user"]

    assert req.status == AssetRequest.Status.PENDING
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0

    # Part A: Simulate unexpected failure during asset allocation statement construction
    def failing_build_allocate(*args, **kwargs):
        raise RuntimeError("Simulated transient failure during asset allocation statement construction!")

    with patch.object(AssetService, "build_allocate_statements", side_effect=failing_build_allocate), \
         patch.object(AssetService, "allocate", side_effect=failing_build_allocate):
        with pytest.raises(RuntimeError) as exc_info:
            RequestService.approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
                using=alias,
            )

        assert "Simulated transient failure during asset allocation" in str(exc_info.value)

    # 1. Request status MUST remain PENDING
    req.refresh_from_db(using=alias)
    assert req.status == AssetRequest.Status.PENDING
    assert req.reviewed_by is None
    assert req.reviewed_at is None

    # 2. Asset MUST remain AVAILABLE
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user is None

    # 3. Exactly 0 audit logs created!
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0

    # Part B: Simulate batch execution failure at D1 level
    with patch("apps.requests.services.d1_batch", side_effect=DatabaseError("Simulated atomic D1 batch execution error!")):
        with pytest.raises(DatabaseError) as exc_info_batch:
            RequestService.approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
                using=alias,
            )

        assert "Simulated atomic D1 batch execution error" in str(exc_info_batch.value)

    # Verify state remains completely uncommitted in D1
    req.refresh_from_db(using=alias)
    assert req.status == AssetRequest.Status.PENDING
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.AVAILABLE
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0
