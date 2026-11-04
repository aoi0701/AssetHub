"""
Integration & Concurrency Integrity Test Suite for Cloudflare D1.

Validates business domain invariants, optimistic concurrency control (OCC),
atomic assertion guards (_assert_guard), monotonic versioning (CAS),
and database trigger protections on local Cloudflare D1 via Worker Bridge.

Comprehensive Concurrency & OCC Integration Scenarios:
1) test_concurrent_asset_allocation_race: Two threads call real AssetService.allocate
   concurrently on the same AVAILABLE asset -> exactly 1 succeeds, 1 raises ConflictError,
   exactly 1 audit log created (zero orphan logs!).
2) test_concurrent_request_approval_vs_rejection: Two threads call real approve_request
   and reject_request concurrently on the same PENDING request -> exactly 1 succeeds,
   1 raises ConflictError.
3) test_interleaved_asset_patch_and_disposal: Asset is confirmed disposed;
   concurrent AssetUpdateSerializer PATCH fails with ConflictError; status remains DISPOSED.
4) test_profile_patch_vs_admin_deactivation_race: User account is deactivated (is_active=False);
   concurrent ProfileUpdateSerializer PATCH fails with PermissionDenied; account remains inactive.
5) test_change_password_auth_version_cas: Real ChangePasswordView via APIRequestFactory;
   auth_version increments monotonically and detects stale sessions via CAS.
6) test_last_admin_protection_trigger: Attempting to deactivate or demote the last active admin
   fails at both serializer and database trigger level.
7) test_concurrent_identifier_generation: 8 threads generate identifiers concurrently
   via generate_identifier -> zero duplicate codes, zero gaps, monotonically increasing.
8) test_real_d1_batch_rollback_on_constraint_violation: Atomic D1 batch execution where
   a later statement violates CHECK constraint rolls back earlier UPDATE statements completely.
9) test_codex_regression_manager_allocates_after_transfer_to_other_department:
   Manager allocates asset after asset was transferred to another department -> raises ConflictError / NotFound.
10) test_codex_regression_approve_request_after_requester_deactivated:
   Approve request after requester deactivated in D1 -> raises ConflictError.
11) test_codex_regression_stale_admin_patch_reactivates_user:
   Stale Admin PATCH with stale auth_version -> raises ConflictError.
12) test_codex_regression_stale_asset_patch_updates_name_after_disposal:
   Stale Asset PATCH updating name after disposal -> raises ConflictError.
13) test_d1_all_asset_service_lifecycle_actions:
   Full lifecycle happy-path coverage across all 9 AssetService actions.
14) test_d1_all_asset_service_invalid_transitions:
   Comprehensive invalid state transition guards across AssetService.
15) test_d1_request_service_workflows_and_invalid_states:
   Comprehensive happy-path and invalid state coverage across RequestService actions.
"""

import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import DatabaseError, IntegrityError, connections
from django.utils import timezone
from apps.common.exceptions import ConflictError
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.assets.identifiers import generate_identifier
from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.assets.serializers import AssetUpdateSerializer
from apps.assets.services import AssetService
from apps.assets.views import AssetViewSet
from apps.authentication.admin_api import UserAdminUpdateSerializer
from apps.authentication.models import User
from apps.authentication.serializers import ProfileUpdateSerializer
from apps.authentication.views import ChangePasswordView
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
# ==============================================================================
# SCENARIO 1: Concurrent Asset Allocation Race & Assertion Guard
# ==============================================================================


def test_concurrent_asset_allocation_race(d1_env):
    """
    Scenario 1: Two threads call real AssetService.allocate concurrently on the same
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

    def worker(target_user_id: int, reason: str):
        try:
            barrier.wait(timeout=5.0)
            res = AssetService.allocate(
                asset_id=asset.id,
                actor=admin,
                assigned_to_user_id=target_user_id,
                reason=reason,
                using=alias,
            )
            results.append(("SUCCESS", target_user_id, res))
        except Exception as exc:
            results.append(("ERROR", target_user_id, exc))
        finally:
            connections.close_all()

    t1 = threading.Thread(target=worker, args=(user1.id, "Allocate to user 1"))
    t2 = threading.Thread(target=worker, args=(user2.id, "Allocate to user 2"))

    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    connections.close_all()

    # 1. Exactly 1 succeeded, exactly 1 failed with ConflictError
    successes = [r for r in results if r[0] == "SUCCESS"]
    errors = [r for r in results if r[0] == "ERROR"]

    assert len(successes) == 1, f"Expected 1 SUCCESS, got: {results}"
    assert len(errors) == 1, f"Expected 1 ERROR, got: {results}"

    winning_user_id = successes[0][1]
    conflict_exc = errors[0][2]
    assert isinstance(conflict_exc, ConflictError), f"Expected ConflictError, got: {type(conflict_exc)}"

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
    Scenario 2: Two threads call real approve_request and reject_request concurrently
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

    def approve_worker():
        try:
            barrier.wait(timeout=5.0)
            res = RequestService.approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
                using=alias,
            )
            results.append(("APPROVE_SUCCESS", res))
        except Exception as exc:
            results.append(("APPROVE_ERROR", exc))
        finally:
            connections.close_all()

    def reject_worker():
        try:
            barrier.wait(timeout=5.0)
            res = RequestService.reject_request(
                request_id=req.id,
                actor=admin,
                rejection_reason="Từ chối do nhu cầu không phù hợp",
                using=alias,
            )
            results.append(("REJECT_SUCCESS", res))
        except Exception as exc:
            results.append(("REJECT_ERROR", exc))
        finally:
            connections.close_all()

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
    Calls production ProfileUpdateSerializer directly with zero mocks.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    user1 = seed["user1"]

    assert user1.is_active is True

    # Admin deactivates user account in D1
    User.objects.using(alias).filter(id=user1.id).update(is_active=0, auth_version=2)
    user1.refresh_from_db(using=alias)
    assert user1.is_active is False

    # Production ProfileUpdateSerializer directly called without mock
    serializer = ProfileUpdateSerializer(
        instance=user1,
        data={"full_name": "Tên Mới Sau Khóa"},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(PermissionDenied) as exc_info:
        serializer.save()

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
    Calls production ChangePasswordView directly via APIRequestFactory.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    user1 = seed["user1"]

    assert user1.auth_version == 1

    factory = APIRequestFactory()
    view = ChangePasswordView.as_view()

    # Call 1 with fresh auth_version=1 -> SUCCEEDS, version becomes 2
    req1 = factory.post(
        "/api/v1/auth/change-password/",
        {"old_password": "Staff1P@ssw0rd2026!", "new_password": "NewStaffPass#2026_A"},
        format="json",
    )
    force_authenticate(req1, user=user1)
    resp1 = view(req1)
    assert resp1.status_code == 204

    user1.refresh_from_db(using=alias)
    assert user1.auth_version == 2
    assert check_password("NewStaffPass#2026_A", user1.password) is True

    # Call 2: simulate concurrent bump in DB (stale auth_version in session)
    User.objects.using(alias).filter(id=user1.id).update(auth_version=5)

    req2 = factory.post(
        "/api/v1/auth/change-password/",
        {"old_password": "NewStaffPass#2026_A", "new_password": "StalePass#2026_B"},
        format="json",
    )
    force_authenticate(req2, user=user1)  # user1 still has auth_version=2 in Python memory
    resp2 = view(req2)
    assert resp2.status_code == 409
    assert "Tài khoản đã bị thay đổi bởi phiên khác" in str(resp2.data)

    user1.refresh_from_db(using=alias)
    assert user1.auth_version == 5


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
    calling production generate_identifier directly on D1
    -> zero duplicate codes, zero gaps, monotonically increasing.
    """
    alias = d1_env["alias"]
    test_year = 2089
    num_threads = 8
    barrier = threading.Barrier(num_threads)
    generated_ast_codes = []
    generated_req_codes = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5.0)
            ast_code = generate_identifier("AST", test_year, using=alias)
            req_code = generate_identifier("REQ", test_year, using=alias)
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
# SCENARIO 8: Real Rollback Test on D1
# ==============================================================================


def test_real_d1_batch_rollback_on_constraint_violation(d1_env):
    """
    Scenario 8: Test atomic batch rollback by executing a batch where the first UPDATE
    succeeds, but a later statement in the batch fails at D1 execution time
    (e.g. statement violating CHECK constraint on _assert_guard).
    Confirm that earlier UPDATEs were rolled back on D1:
    request stays PENDING, asset stays AVAILABLE, 0 audit logs.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    req = seed["req1"]
    asset = seed["asset1"]
    admin = seed["admin_user"]
    requester = seed["user1"]

    assert req.status == AssetRequest.Status.PENDING
    assert asset.status == Asset.Status.AVAILABLE
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0

    # Construct batch:
    # 1. Update asset to IN_USE
    stmt1 = (
        "UPDATE assets_asset SET status = 'IN_USE', assigned_to_user_id = %s, updated_at = datetime('now') WHERE id = %s",
        [requester.id, asset.id],
    )
    # 2. Update request to APPROVED
    stmt2 = (
        "UPDATE requests_assetrequest SET status = 'APPROVED', reviewed_by_id = %s, reviewed_at = datetime('now') WHERE id = %s",
        [admin.id, req.id],
    )
    # 3. Violation of CHECK(rows_affected > 0)
    stmt3 = (
        "INSERT INTO _assert_guard (rows_affected) VALUES (0)",
        [],
    )

    with pytest.raises(DatabaseError) as exc_info:
        d1_batch([stmt1, stmt2, stmt3], using=alias)

    assert "CHECK constraint failed" in str(exc_info.value) or "_assert_guard" in str(exc_info.value)

    # Verify atomic rollback on D1
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user_id is None

    req.refresh_from_db(using=alias)
    assert req.status == AssetRequest.Status.PENDING
    assert req.reviewed_by is None
    assert req.reviewed_at is None

    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0


# ==============================================================================
# CODEX REGRESSION TESTS
# ==============================================================================


def test_codex_regression_manager_allocates_after_transfer_to_other_department(d1_env):
    """
    (1) Manager allocates asset after asset was transferred to another department:
    Asset AVAILABLE in IT. Manager IT and requester IT.
    Interleave: Admin transfer moves asset to HR.
    Manager calls AssetService.allocate: must raise ConflictError / NotFound!
    Asset remains in HR, AVAILABLE, holder NULL, 0 allocate audit logs.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    manager = seed["manager_user"]
    admin = seed["admin_user"]
    requester = seed["user1"]
    dept_hr = seed["dept_hr"]

    assert asset.status == Asset.Status.AVAILABLE
    assert asset.department_id == seed["dept_it"].id

    # Interleave: Admin transfer moves asset to HR
    AssetService.transfer(asset.id, admin, dept_hr.id, "Admin chuyển kho sang HR", using=alias)
    asset.refresh_from_db(using=alias)
    assert asset.department_id == dept_hr.id

    # Manager calls AssetService.allocate: must reject
    with pytest.raises((ConflictError, NotFound)):
        AssetService.allocate(
            asset_id=asset.id,
            actor=manager,
            assigned_to_user_id=requester.id,
            reason="Manager cố cấp phát tài sản đã chuyển",
            using=alias,
        )

    # Asset remains in HR, AVAILABLE, holder NULL, 0 allocate audit logs
    asset.refresh_from_db(using=alias)
    assert asset.department_id == dept_hr.id
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user_id is None
    assert AssetHistoryLog.objects.using(alias).filter(
        asset_id=asset.id, action_type=AssetHistoryLog.ActionType.ALLOCATE
    ).count() == 0


def test_codex_regression_approve_request_after_requester_deactivated(d1_env):
    """
    (2) Approve request after requester deactivated in D1:
    Request PENDING in IT. Requester active in IT.
    Interleave: Requester deactivated in D1 (is_active = 0).
    RequestService.approve_request: must raise ConflictError!
    Request remains PENDING, asset remains AVAILABLE, 0 audit logs.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    req = seed["req1"]
    asset = seed["asset1"]
    admin = seed["admin_user"]
    requester = seed["user1"]

    assert req.status == AssetRequest.Status.PENDING
    assert requester.is_active is True

    # Interleave: Requester deactivated in D1
    User.objects.using(alias).filter(id=requester.id).update(is_active=0)

    # RequestService.approve_request: must raise ConflictError!
    with pytest.raises(ConflictError) as exc_info:
        RequestService.approve_request(
            request_id=req.id,
            actor=admin,
            data={"selected_asset_id": asset.id},
            using=alias,
        )

    assert "hoạt động" in str(exc_info.value)

    # Request remains PENDING, asset remains AVAILABLE, 0 audit logs
    req.refresh_from_db(using=alias)
    assert req.status == AssetRequest.Status.PENDING
    assert req.reviewed_by is None

    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user is None
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0


def test_codex_regression_stale_admin_patch_reactivates_user(d1_env):
    """
    (3) Stale Admin PATCH reactivates user with stale auth_version:
    User instance has is_active=True, auth_version=1.
    DB user changed to is_active=False, auth_version=2.
    UserAdminUpdateSerializer(instance, data={"is_active": True}, partial=True).
    serializer.is_valid(); serializer.save(): must raise ConflictError!
    DB user remains is_active=False, auth_version=2.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    user2 = seed["user2"]

    assert user2.is_active is True
    assert user2.auth_version == 1

    # Interleave: DB user changed to is_active=False, auth_version=2
    User.objects.using(alias).filter(id=user2.id).update(is_active=0, auth_version=2)

    # User instance has is_active=True, auth_version=1 in memory
    serializer = UserAdminUpdateSerializer(user2, data={"is_active": True}, partial=True)
    assert serializer.is_valid(), serializer.errors

    with pytest.raises(ConflictError) as exc_info:
        serializer.save()

    # DB user remains is_active=False, auth_version=2
    user2.refresh_from_db(using=alias)
    assert user2.is_active is False
    assert user2.auth_version == 2


def test_codex_regression_stale_asset_patch_updates_name_after_disposal(d1_env):
    """
    (4) Stale asset PATCH updates name after disposal:
    Asset AVAILABLE instance fetched.
    ser = AssetUpdateSerializer(instance, data={"name": "Stale Name Post Disposal"}, partial=True).
    ser.is_valid().
    Asset disposed via AssetService.propose_disposal + AssetService.confirm_disposal (DB is DISPOSED).
    ser.save(): must raise ConflictError!
    DB asset status remains DISPOSED, name remains original!
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]
    original_name = asset.name

    ser = AssetUpdateSerializer(instance=asset, data={"name": "Stale Name Post Disposal"}, partial=True)
    assert ser.is_valid(), ser.errors

    # Asset disposed via AssetService.propose_disposal + AssetService.confirm_disposal
    AssetService.propose_disposal(asset.id, admin, "Đề xuất thanh lý máy cũ", using=alias)
    AssetService.confirm_disposal(asset.id, admin, "Xác nhận thanh lý tiêu hủy", using=alias)

    asset_db = Asset.objects.using(alias).get(id=asset.id)
    assert asset_db.status == Asset.Status.DISPOSED

    with pytest.raises(ConflictError) as exc_info:
        ser.save()

    # DB asset status remains DISPOSED, name remains original!
    asset_db.refresh_from_db(using=alias)
    assert asset_db.status == Asset.Status.DISPOSED
    assert asset_db.name == original_name


# ==============================================================================
# COMPREHENSIVE 9 ASSETSERVICE + 2 REQUESTSERVICE ACTIONS ON LOCAL D1
# ==============================================================================


def test_d1_all_asset_service_lifecycle_actions(d1_env):
    """
    Comprehensive happy-path coverage on real local D1 for all 9 AssetService actions:
    1. allocate (AVAILABLE -> IN_USE)
    2. confirm_damage_report (IN_USE -> DAMAGED_PENDING_REPAIR)
    3. send_maintenance (DAMAGED_PENDING_REPAIR -> UNDER_MAINTENANCE)
    4. complete_maintenance RETURN_TO_USER (UNDER_MAINTENANCE -> IN_USE)
    5. revoke condition=GOOD (IN_USE -> AVAILABLE)
    6. mark_damaged (AVAILABLE -> DAMAGED_PENDING_REPAIR)
    7. send_maintenance + complete_maintenance RETURN_TO_STOCK (UNDER_MAINTENANCE -> AVAILABLE)
    8. transfer (AVAILABLE IT -> HR)
    9. propose_disposal + confirm_disposal (AVAILABLE -> PENDING_DISPOSAL -> DISPOSED)
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]
    user1 = seed["user1"]
    dept_hr = seed["dept_hr"]

    # 1. allocate: AVAILABLE -> IN_USE
    a1 = AssetService.allocate(asset.id, admin, user1.id, "Cấp phát cho user1", using=alias)
    assert a1.status == Asset.Status.IN_USE
    assert a1.assigned_to_user_id == user1.id
    log1 = AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).latest("id")
    assert log1.action_type == AssetHistoryLog.ActionType.ALLOCATE
    assert log1.from_status == Asset.Status.AVAILABLE
    assert log1.to_status == Asset.Status.IN_USE
    assert log1.to_user_id == user1.id

    # 2. confirm_damage_report: IN_USE -> DAMAGED_PENDING_REPAIR (retains holder!)
    a2 = AssetService.confirm_damage_report(asset.id, admin, "Báo hỏng bàn phím", using=alias)
    assert a2.status == Asset.Status.DAMAGED_PENDING_REPAIR
    assert a2.assigned_to_user_id == user1.id
    log2 = AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).latest("id")
    assert log2.action_type == AssetHistoryLog.ActionType.DAMAGE
    assert log2.from_status == Asset.Status.IN_USE
    assert log2.to_status == Asset.Status.DAMAGED_PENDING_REPAIR
    assert log2.from_user_id == user1.id
    assert log2.to_user_id == user1.id

    # 3. send_maintenance: DAMAGED_PENDING_REPAIR -> UNDER_MAINTENANCE (retains holder!)
    a3 = AssetService.send_maintenance(asset.id, admin, "Gửi trung tâm bảo hành", using=alias)
    assert a3.status == Asset.Status.UNDER_MAINTENANCE
    assert a3.assigned_to_user_id == user1.id
    log3 = AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).latest("id")
    assert log3.action_type == AssetHistoryLog.ActionType.MAINTENANCE_START
    assert log3.from_status == Asset.Status.DAMAGED_PENDING_REPAIR
    assert log3.to_status == Asset.Status.UNDER_MAINTENANCE
    assert log3.from_user_id == user1.id
    assert log3.to_user_id == user1.id

    # 4. complete_maintenance RETURN_TO_USER: UNDER_MAINTENANCE -> IN_USE (retains holder!)
    a4 = AssetService.complete_maintenance(asset.id, admin, "RETURN_TO_USER", "Đã thay bàn phím mới", using=alias)
    assert a4.status == Asset.Status.IN_USE
    assert a4.assigned_to_user_id == user1.id
    log4 = AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).latest("id")
    assert log4.action_type == AssetHistoryLog.ActionType.MAINTENANCE_END
    assert log4.from_status == Asset.Status.UNDER_MAINTENANCE
    assert log4.to_status == Asset.Status.IN_USE
    assert log4.from_user_id == user1.id
    assert log4.to_user_id == user1.id

    # 5. revoke condition=GOOD: IN_USE -> AVAILABLE
    a5 = AssetService.revoke(asset.id, admin, received_confirmed=True, condition="GOOD", reason="Thu hồi về kho", using=alias)
    assert a5.status == Asset.Status.AVAILABLE
    assert a5.assigned_to_user_id is None

    # 6. mark_damaged: AVAILABLE -> DAMAGED_PENDING_REPAIR
    a6 = AssetService.mark_damaged(asset.id, admin, "Hỏng pin tại kho", using=alias)
    assert a6.status == Asset.Status.DAMAGED_PENDING_REPAIR

    # 7. send_maintenance + complete_maintenance RETURN_TO_STOCK
    AssetService.send_maintenance(asset.id, admin, "Gửi sửa pin", using=alias)
    a7 = AssetService.complete_maintenance(asset.id, admin, "RETURN_TO_STOCK", "Thay pin xong nhập lại kho", using=alias)
    assert a7.status == Asset.Status.AVAILABLE
    assert a7.assigned_to_user_id is None

    # 8. transfer: AVAILABLE IT -> HR
    a8 = AssetService.transfer(asset.id, admin, dept_hr.id, "Điều chuyển sang HR", using=alias)
    assert a8.status == Asset.Status.AVAILABLE
    assert a8.department_id == dept_hr.id

    # 9. propose_disposal + confirm_disposal
    a9_prop = AssetService.propose_disposal(asset.id, admin, "Máy quá cũ đề xuất thanh lý", using=alias)
    assert a9_prop.status == Asset.Status.PENDING_DISPOSAL

    a9_conf = AssetService.confirm_disposal(asset.id, admin, "Xác nhận tiêu hủy", using=alias)
    assert a9_conf.status == Asset.Status.DISPOSED


def test_d1_all_asset_service_invalid_transitions(d1_env):
    """
    Comprehensive invalid-state error handling on real local D1 for AssetService:
    - allocate rejects already IN_USE
    - revoke rejects AVAILABLE
    - mark_damaged rejects IN_USE
    - confirm_damage_report rejects AVAILABLE
    - send_maintenance rejects AVAILABLE
    - complete_maintenance rejects AVAILABLE
    - transfer rejects IN_USE
    - propose_disposal rejects DISPOSED
    - confirm_disposal rejects AVAILABLE
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]
    user1 = seed["user1"]
    dept_hr = seed["dept_hr"]

    # Initial: AVAILABLE
    with pytest.raises(ConflictError):
        AssetService.revoke(asset.id, admin, True, "GOOD", "Cannot revoke available", using=alias)

    with pytest.raises(ConflictError):
        AssetService.confirm_damage_report(asset.id, admin, "Cannot confirm damage on available", using=alias)

    with pytest.raises(ConflictError):
        AssetService.send_maintenance(asset.id, admin, "Cannot send available to maintenance", using=alias)

    with pytest.raises(ConflictError):
        AssetService.complete_maintenance(asset.id, admin, "RETURN_TO_STOCK", "Cannot complete maintenance on available", using=alias)

    with pytest.raises(ConflictError):
        AssetService.confirm_disposal(asset.id, admin, "Cannot confirm disposal on available", using=alias)

    # Allocate -> IN_USE
    AssetService.allocate(asset.id, admin, user1.id, "Cấp phát", using=alias)

    # In IN_USE:
    with pytest.raises(ConflictError):
        AssetService.allocate(asset.id, admin, user1.id, "Cannot allocate in_use", using=alias)

    with pytest.raises(ConflictError):
        AssetService.mark_damaged(asset.id, admin, "Cannot mark damaged in_use directly", using=alias)

    with pytest.raises(ConflictError):
        AssetService.transfer(asset.id, admin, dept_hr.id, "Cannot transfer in_use", using=alias)

    # Transition to DISPOSED
    AssetService.revoke(asset.id, admin, True, "GOOD", "Thu hồi", using=alias)
    AssetService.propose_disposal(asset.id, admin, "Đề xuất", using=alias)
    AssetService.confirm_disposal(asset.id, admin, "Xác nhận", using=alias)

    with pytest.raises(ConflictError):
        AssetService.propose_disposal(asset.id, admin, "Cannot propose already disposed", using=alias)

    with pytest.raises(ConflictError):
        AssetService.allocate(asset.id, admin, user1.id, "Cannot allocate disposed", using=alias)


def test_d1_request_service_workflows_and_invalid_states(d1_env):
    """
    Comprehensive happy-path and invalid-state coverage on real local D1 for RequestService:
    1. approve_request ALLOCATE: PENDING -> APPROVED, asset AVAILABLE -> IN_USE
    2. approve_request RETURN: PENDING -> APPROVED, asset IN_USE -> AVAILABLE
    3. approve_request DAMAGE_REPORT: PENDING -> APPROVED, asset IN_USE -> DAMAGED_PENDING_REPAIR
    4. reject_request: PENDING -> REJECTED
    5. Invalid transitions: cannot re-approve or re-reject processed requests
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]
    user1 = seed["user1"]
    dept_it = seed["dept_it"]
    cat_laptop = seed["cat_laptop"]

    # 1. Happy approve ALLOCATE
    req_alloc = seed["req1"]
    approved_alloc = RequestService.approve_request(
        request_id=req_alloc.id,
        actor=admin,
        data={"selected_asset_id": asset.id},
        using=alias,
    )
    assert approved_alloc.status == AssetRequest.Status.APPROVED
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == user1.id

    # Re-approve attempt fails with ConflictError
    with pytest.raises(ConflictError):
        RequestService.approve_request(
            request_id=req_alloc.id,
            actor=admin,
            data={"selected_asset_id": asset.id},
            using=alias,
        )

    # 2. Happy approve RETURN
    req_return = AssetRequest.objects.using(alias).create(
        id=201,
        request_code="REQ-2026-201",
        request_type=AssetRequest.RequestType.RETURN,
        requester=user1,
        department=dept_it,
        asset=asset,
        reason="Hoàn trả máy khi kết thúc dự án",
        status=AssetRequest.Status.PENDING,
    )
    approved_return = RequestService.approve_request(
        request_id=req_return.id,
        actor=admin,
        data={"received_confirmed": True, "condition": "GOOD"},
        using=alias,
    )
    assert approved_return.status == AssetRequest.Status.APPROVED
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user_id is None

    # Re-allocate to user1 to test DAMAGE_REPORT
    AssetService.allocate(asset.id, admin, user1.id, "Cấp phát lại", using=alias)

    # 3. Happy approve DAMAGE_REPORT
    req_dmg = AssetRequest.objects.using(alias).create(
        id=202,
        request_code="REQ-2026-202",
        request_type=AssetRequest.RequestType.DAMAGE_REPORT,
        requester=user1,
        department=dept_it,
        asset=asset,
        reason="Máy bị rơi vỡ màn hình",
        status=AssetRequest.Status.PENDING,
    )
    approved_dmg = RequestService.approve_request(
        request_id=req_dmg.id,
        actor=admin,
        data={},
        using=alias,
    )
    assert approved_dmg.status == AssetRequest.Status.APPROVED
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.DAMAGED_PENDING_REPAIR

    # 4. Happy reject_request
    req_reject = AssetRequest.objects.using(alias).create(
        id=203,
        request_code="REQ-2026-203",
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=user1,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Yêu cầu cấp thêm máy phụ",
        status=AssetRequest.Status.PENDING,
    )
    rejected_req = RequestService.reject_request(
        request_id=req_reject.id,
        actor=admin,
        rejection_reason="Chính sách công ty không cấp 2 laptop",
        using=alias,
    )
    assert rejected_req.status == AssetRequest.Status.REJECTED
    assert rejected_req.rejection_reason == "Chính sách công ty không cấp 2 laptop"

    # Re-reject attempt fails with ConflictError
    with pytest.raises(ConflictError):
        RequestService.reject_request(
            request_id=req_reject.id,
            actor=admin,
            rejection_reason="Từ chối lại lần 2",
            using=alias,
        )


# ==============================================================================
# COMPANY POOL DISPOSAL & MAINTENANCE (NULL DEPARTMENT)
# ==============================================================================


def test_d1_company_pool_disposal_and_maintenance(d1_env):
    """
    Test company pool asset (department_id=None, assigned_to_user_id=None):
    1. Verify company-pool complete_maintenance(result='RETURN_TO_STOCK') succeeds.
    2. Verify propose_disposal and confirm_disposal on company-pool asset both succeed cleanly on local D1.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    admin = seed["admin_user"]
    cat_laptop = seed["cat_laptop"]

    # 1. Test company pool maintenance return to stock
    pool_maint_asset = Asset.objects.using(alias).create(
        id=701,
        asset_code="AST-POOL-MNT",
        name="Company Pool Server",
        category=cat_laptop,
        department=None,
        assigned_to_user=None,
        status=Asset.Status.DAMAGED_PENDING_REPAIR,
    )
    AssetService.send_maintenance(pool_maint_asset.id, admin, "Bảo trì máy dùng chung", using=alias)
    m_res = AssetService.complete_maintenance(
        pool_maint_asset.id, admin, "RETURN_TO_STOCK", "Đã sửa xong hoàn về kho chung", using=alias
    )
    assert m_res.status == Asset.Status.AVAILABLE
    assert m_res.department_id is None
    assert m_res.assigned_to_user_id is None

    # 2. Test company pool disposal (propose_disposal and confirm_disposal)
    pool_disp_asset = Asset.objects.using(alias).create(
        id=702,
        asset_code="AST-POOL-DISP",
        name="Company Pool Legacy Router",
        category=cat_laptop,
        department=None,
        assigned_to_user=None,
        status=Asset.Status.AVAILABLE,
    )
    p_res = AssetService.propose_disposal(
        pool_disp_asset.id, admin, "Đề xuất thanh lý router kho chung", using=alias
    )
    assert p_res.status == Asset.Status.PENDING_DISPOSAL
    assert p_res.department_id is None
    assert p_res.assigned_to_user_id is None

    c_res = AssetService.confirm_disposal(
        pool_disp_asset.id, admin, "Xác nhận tiêu hủy router kho chung", using=alias
    )
    assert c_res.status == Asset.Status.DISPOSED
    assert c_res.department_id is None
    assert c_res.assigned_to_user_id is None

    # Verify audit logs in D1
    logs = list(
        AssetHistoryLog.objects.using(alias)
        .filter(asset_id=pool_disp_asset.id)
        .order_by("id")
    )
    assert len(logs) == 2
    assert logs[0].action_type == AssetHistoryLog.ActionType.DISPOSAL_PROPOSE
    assert logs[0].from_department_id is None
    assert logs[0].to_department_id is None
    assert logs[1].action_type == AssetHistoryLog.ActionType.DISPOSAL_CONFIRM
    assert logs[1].from_department_id is None
    assert logs[1].to_department_id is None


# ==============================================================================
# INTERLEAVING REQUESTER STATE (INACTIVE / TRANSFERRED) FOR ALL 3 REQUEST TYPES
# ==============================================================================


def test_interleaved_requester_state_allocate(d1_env):
    """
    ALLOCATE request:
    Interleaving requester deactivation and department transfer between read and write
    -> approve_request raises ConflictError, request remains PENDING, asset AVAILABLE, 0 audit logs.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    req = seed["req1"]
    asset = seed["asset1"]
    admin = seed["admin_user"]
    user1 = seed["user1"]
    dept_hr = seed["dept_hr"]

    import apps.requests.services
    orig_batch = apps.requests.services.d1_batch

    # Case A: Interleaving requester deactivation between read and write
    def hook_deact_allocate(batch, using="default", **kwargs):
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET is_active = 0 WHERE id = %s", [user1.id])
        return orig_batch(batch, using=using, **kwargs)

    with patch("apps.requests.services.d1_batch", side_effect=hook_deact_allocate):
        with pytest.raises(ConflictError):
            RequestService.approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
                using=alias,
            )

    req.refresh_from_db(using=alias)
    assert req.status == AssetRequest.Status.PENDING
    assert req.reviewed_by is None
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user_id is None
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0

    # Reset user1 is_active=1
    with connections[alias].cursor() as cursor:
        cursor.execute("UPDATE authentication_user SET is_active = 1 WHERE id = %s", [user1.id])

    # Case B: Interleaving requester department transfer between read and write
    def hook_transfer_allocate(batch, using="default", **kwargs):
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET department_id = %s WHERE id = %s", [dept_hr.id, user1.id])
        return orig_batch(batch, using=using, **kwargs)

    with patch("apps.requests.services.d1_batch", side_effect=hook_transfer_allocate):
        with pytest.raises(ConflictError):
            RequestService.approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
                using=alias,
            )

    req.refresh_from_db(using=alias)
    assert req.status == AssetRequest.Status.PENDING
    assert req.reviewed_by is None
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user_id is None
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0


def test_interleaved_requester_state_return(d1_env):
    """
    RETURN request:
    Interleaving requester deactivation and department transfer between read and write
    -> approve_request raises ConflictError, request remains PENDING, asset IN_USE, 0 revoke audit logs.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]
    user1 = seed["user1"]
    dept_it = seed["dept_it"]
    dept_hr = seed["dept_hr"]

    # Allocate asset to user1 first so it is IN_USE
    AssetService.allocate(asset.id, admin, user1.id, "Setup for return test", using=alias)
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == user1.id

    import apps.requests.services
    orig_batch = apps.requests.services.d1_batch

    # Case A: Interleaving requester deactivation
    req_return1 = AssetRequest.objects.using(alias).create(
        id=301,
        request_code="REQ-2026-301",
        request_type=AssetRequest.RequestType.RETURN,
        requester=user1,
        department=dept_it,
        asset=asset,
        reason="Hoàn trả thiết bị",
        status=AssetRequest.Status.PENDING,
    )

    def hook_deact_return(batch, using="default", **kwargs):
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET is_active = 0 WHERE id = %s", [user1.id])
        return orig_batch(batch, using=using, **kwargs)

    with patch("apps.requests.services.d1_batch", side_effect=hook_deact_return):
        with pytest.raises(ConflictError):
            RequestService.approve_request(
                request_id=req_return1.id,
                actor=admin,
                data={"received_confirmed": True, "condition": "GOOD"},
                using=alias,
            )

    req_return1.refresh_from_db(using=alias)
    assert req_return1.status == AssetRequest.Status.PENDING
    assert req_return1.reviewed_by is None
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == user1.id
    assert AssetHistoryLog.objects.using(alias).filter(
        asset_id=asset.id, action_type=AssetHistoryLog.ActionType.RETURN
    ).count() == 0

    # Reset user1 is_active=1
    with connections[alias].cursor() as cursor:
        cursor.execute("UPDATE authentication_user SET is_active = 1 WHERE id = %s", [user1.id])

    # Delete req_return1 so Case B can create a new RETURN request for the same asset
    AssetRequest.objects.using(alias).filter(id=req_return1.id).delete()

    # Case B: Interleaving requester department transfer
    req_return2 = AssetRequest.objects.using(alias).create(
        id=302,
        request_code="REQ-2026-302",
        request_type=AssetRequest.RequestType.RETURN,
        requester=user1,
        department=dept_it,
        asset=asset,
        reason="Hoàn trả thiết bị sau chuyển phòng",
        status=AssetRequest.Status.PENDING,
    )

    def hook_transfer_return(batch, using="default", **kwargs):
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET department_id = %s WHERE id = %s", [dept_hr.id, user1.id])
        return orig_batch(batch, using=using, **kwargs)

    with patch("apps.requests.services.d1_batch", side_effect=hook_transfer_return):
        with pytest.raises(ConflictError):
            RequestService.approve_request(
                request_id=req_return2.id,
                actor=admin,
                data={"received_confirmed": True, "condition": "GOOD"},
                using=alias,
            )

    req_return2.refresh_from_db(using=alias)
    assert req_return2.status == AssetRequest.Status.PENDING
    assert req_return2.reviewed_by is None
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == user1.id
    assert AssetHistoryLog.objects.using(alias).filter(
        asset_id=asset.id, action_type=AssetHistoryLog.ActionType.RETURN
    ).count() == 0


def test_interleaved_requester_state_damage_report(d1_env):
    """
    DAMAGE_REPORT request:
    Interleaving requester deactivation and department transfer between read and write
    -> approve_request raises ConflictError, request remains PENDING, asset IN_USE, 0 damage audit logs.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    asset = seed["asset1"]
    admin = seed["admin_user"]
    user1 = seed["user1"]
    dept_it = seed["dept_it"]
    dept_hr = seed["dept_hr"]

    # Allocate asset to user1 first so it is IN_USE
    AssetService.allocate(asset.id, admin, user1.id, "Setup for damage test", using=alias)
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == user1.id

    import apps.requests.services
    orig_batch = apps.requests.services.d1_batch

    # Case A: Interleaving requester deactivation
    req_dmg1 = AssetRequest.objects.using(alias).create(
        id=303,
        request_code="REQ-2026-303",
        request_type=AssetRequest.RequestType.DAMAGE_REPORT,
        requester=user1,
        department=dept_it,
        asset=asset,
        reason="Báo hỏng chuột máy tính",
        status=AssetRequest.Status.PENDING,
    )

    def hook_deact_dmg(batch, using="default", **kwargs):
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET is_active = 0 WHERE id = %s", [user1.id])
        return orig_batch(batch, using=using, **kwargs)

    with patch("apps.requests.services.d1_batch", side_effect=hook_deact_dmg):
        with pytest.raises(ConflictError):
            RequestService.approve_request(
                request_id=req_dmg1.id,
                actor=admin,
                data={},
                using=alias,
            )

    req_dmg1.refresh_from_db(using=alias)
    assert req_dmg1.status == AssetRequest.Status.PENDING
    assert req_dmg1.reviewed_by is None
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == user1.id
    assert AssetHistoryLog.objects.using(alias).filter(
        asset_id=asset.id, action_type=AssetHistoryLog.ActionType.DAMAGE
    ).count() == 0

    # Reset user1 is_active=1
    with connections[alias].cursor() as cursor:
        cursor.execute("UPDATE authentication_user SET is_active = 1 WHERE id = %s", [user1.id])

    # Delete req_dmg1 so Case B can create a new DAMAGE_REPORT request for the same asset
    AssetRequest.objects.using(alias).filter(id=req_dmg1.id).delete()

    # Case B: Interleaving requester department transfer
    req_dmg2 = AssetRequest.objects.using(alias).create(
        id=304,
        request_code="REQ-2026-304",
        request_type=AssetRequest.RequestType.DAMAGE_REPORT,
        requester=user1,
        department=dept_it,
        asset=asset,
        reason="Báo hỏng nguồn máy tính",
        status=AssetRequest.Status.PENDING,
    )

    def hook_transfer_dmg(batch, using="default", **kwargs):
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE authentication_user SET department_id = %s WHERE id = %s", [dept_hr.id, user1.id])
        return orig_batch(batch, using=using, **kwargs)

    with patch("apps.requests.services.d1_batch", side_effect=hook_transfer_dmg):
        with pytest.raises(ConflictError):
            RequestService.approve_request(
                request_id=req_dmg2.id,
                actor=admin,
                data={},
                using=alias,
            )

    req_dmg2.refresh_from_db(using=alias)
    assert req_dmg2.status == AssetRequest.Status.PENDING
    assert req_dmg2.reviewed_by is None
    asset.refresh_from_db(using=alias)
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user_id == user1.id
    assert AssetHistoryLog.objects.using(alias).filter(
        asset_id=asset.id, action_type=AssetHistoryLog.ActionType.DAMAGE
    ).count() == 0


# ==============================================================================
# INTERLEAVING CATEGORY CHANGE ON ALLOCATE REQUEST
# ==============================================================================


def test_interleaved_category_change_on_allocate_request(d1_env):
    """
    ALLOCATE request:
    Request for Category A. Asset has Category A.
    Between read and write, alter asset category to Category B.
    approve_request must raise ConflictError, request remains PENDING,
    asset remains in Category B and AVAILABLE, 0 audit logs.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    req = seed["req1"]
    asset = seed["asset1"]
    admin = seed["admin_user"]
    cat_laptop = seed["cat_laptop"]

    cat_phone = Category.objects.using(alias).filter(id=2).first()
    if not cat_phone:
        cat_phone = Category.objects.using(alias).create(
            id=2,
            name="Điện thoại",
            description="Điện thoại di động kiểm thử",
        )

    assert req.requested_category_id == cat_laptop.id
    assert asset.category_id == cat_laptop.id
    assert asset.status == Asset.Status.AVAILABLE
    assert req.status == AssetRequest.Status.PENDING

    import apps.requests.services
    orig_batch = apps.requests.services.d1_batch

    def hook_category_change(batch, using="default", **kwargs):
        with connections[alias].cursor() as cursor:
            cursor.execute("UPDATE assets_asset SET category_id = %s WHERE id = %s", [cat_phone.id, asset.id])
        return orig_batch(batch, using=using, **kwargs)

    with patch("apps.requests.services.d1_batch", side_effect=hook_category_change):
        with pytest.raises(ConflictError):
            RequestService.approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
                using=alias,
            )

    # Request remains PENDING
    req.refresh_from_db(using=alias)
    assert req.status == AssetRequest.Status.PENDING
    assert req.reviewed_by is None

    # Asset remains in Category B and AVAILABLE
    asset.refresh_from_db(using=alias)
    assert asset.category_id == cat_phone.id
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user_id is None

    # 0 audit logs created
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0


# ==============================================================================
# REAL PRODUCTION ROLLBACK VIA D1 TEST TRIGGER ON APPROVE_REQUEST
# ==============================================================================


def test_production_approve_request_rollback_via_trigger(d1_env):
    """
    Real production rollback test for RequestService.approve_request:
    Create a test-only trigger on local D1:
    CREATE TRIGGER test_fail_audit_insert BEFORE INSERT ON assets_assethistorylog
    BEGIN SELECT RAISE(FAIL, 'Simulated audit table trigger failure for rollback test'); END;

    In try ... finally:
    Call real RequestService.approve_request (zero mocks!).
    Assert it raises ConflictError or DatabaseError.
    Assert on D1 that request remains PENDING, asset remains AVAILABLE, 0 audit logs created!
    Always drop trigger test_fail_audit_insert in finally block.
    """
    alias = d1_env["alias"]
    seed = seed_business_fixtures(alias)
    req = seed["req1"]
    asset = seed["asset1"]
    admin = seed["admin_user"]

    assert req.status == AssetRequest.Status.PENDING
    assert asset.status == Asset.Status.AVAILABLE
    assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0

    with connections[alias].cursor() as cursor:
        cursor.execute(
            """
            CREATE TRIGGER test_fail_audit_insert
            BEFORE INSERT ON assets_assethistorylog
            BEGIN
                SELECT RAISE(FAIL, 'Simulated audit table trigger failure for rollback test');
            END;
            """
        )

    try:
        with pytest.raises((ConflictError, DatabaseError)):
            RequestService.approve_request(
                request_id=req.id,
                actor=admin,
                data={"selected_asset_id": asset.id},
                using=alias,
            )

        # Assert on D1 that request remains PENDING
        req.refresh_from_db(using=alias)
        assert req.status == AssetRequest.Status.PENDING
        assert req.reviewed_by is None
        assert req.reviewed_at is None

        # Assert on D1 that asset remains AVAILABLE
        asset.refresh_from_db(using=alias)
        assert asset.status == Asset.Status.AVAILABLE
        assert asset.assigned_to_user_id is None

        # Assert 0 audit logs created
        assert AssetHistoryLog.objects.using(alias).filter(asset_id=asset.id).count() == 0

    finally:
        with connections[alias].cursor() as cursor:
            cursor.execute("DROP TRIGGER IF EXISTS test_fail_audit_insert;")
