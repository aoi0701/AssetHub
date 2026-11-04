import os
import sqlite3
from pathlib import Path
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import connections
from rest_framework import exceptions, serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.authentication.admin_api import UserAdminUpdateSerializer
from apps.authentication.authentication import CustomJWTAuthentication
from apps.authentication.serializers import ProfileUpdateSerializer
from apps.authentication.services import generate_tokens_for_user
from apps.authentication.views import ChangePasswordView
from apps.common.exceptions import ConflictError
from apps.departments.models import Department

User = get_user_model()


@pytest.fixture
def department(db):
    """Fixture providing a test department."""
    dept, _ = Department.objects.get_or_create(
        code="D1_AUTH_TEST",
        defaults={"name": "Phòng Ban Kiểm Thử D1 Auth"},
    )
    return dept


@pytest.fixture
def active_user(db, department):
    """Fixture providing a standard active user."""
    user, _ = User.objects.get_or_create(
        username="d1_auth_user",
        defaults={
            "email": "d1_user@assethub.test",
            "full_name": "Người Dùng Thường",
            "phone": "0901234567",
            "role": User.Role.USER,
            "department": department,
            "is_active": True,
            "auth_version": 1,
        },
    )
    user.set_password("OldP@ssw0rd123!")
    user.auth_version = 1
    user.is_active = True
    user.save()
    return user


@pytest.fixture
def deactivated_user(db, department):
    """Fixture providing a deactivated user."""
    user, _ = User.objects.get_or_create(
        username="d1_deactivated_user",
        defaults={
            "email": "d1_deactivated@assethub.test",
            "full_name": "Người Dùng Khóa",
            "phone": "0909999888",
            "role": User.Role.USER,
            "department": department,
            "is_active": False,
            "auth_version": 1,
        },
    )
    user.is_active = False
    user.save()
    return user


@pytest.fixture
def single_admin(db):
    """Fixture ensuring a single active admin in database."""
    # Deactivate any other admin temporarily for clean single-admin test
    User.objects.filter(role=User.Role.ADMIN).exclude(username="d1_sole_admin").update(is_active=False)

    admin, _ = User.objects.get_or_create(
        username="d1_sole_admin",
        defaults={
            "email": "d1_sole_admin@assethub.test",
            "full_name": "Quản Trị Viên Duy Nhất",
            "role": User.Role.ADMIN,
            "is_active": True,
            "auth_version": 1,
        },
    )
    admin.is_active = True
    admin.role = User.Role.ADMIN
    admin.save()
    return admin


# =========================================================================
# 1. ProfileUpdateSerializer tests
# =========================================================================

@pytest.mark.django_db
def test_profile_update_serializer_allowlist_only(active_user):
    """
    ProfileUpdateSerializer allows only allowlist fields: full_name, email, phone.
    Any attempt to supply disallowed fields (role, department, is_active) is rejected.
    """
    # 1a. Successful update of allowed fields
    valid_data = {
        "full_name": "Họ Tên Đã Cập Nhật",
        "email": "updated_d1_email@assethub.test",
        "phone": "0988776655",
    }
    serializer = ProfileUpdateSerializer(active_user, data=valid_data, partial=True)
    assert serializer.is_valid(), serializer.errors
    updated_user = serializer.save()

    assert updated_user.full_name == "Họ Tên Đã Cập Nhật"
    assert updated_user.email == "updated_d1_email@assethub.test"
    assert updated_user.phone == "0988776655"

    # 1b. Disallowed fields rejected by serializer validation
    disallowed_data = {"role": User.Role.ADMIN}
    bad_serializer = ProfileUpdateSerializer(active_user, data=disallowed_data, partial=True)
    assert not bad_serializer.is_valid()
    assert "role" in bad_serializer.errors


@pytest.mark.django_db
def test_profile_update_serializer_deactivated_user_raises_permission_denied(deactivated_user):
    """
    ProfileUpdateSerializer.update() enforces is_active=True condition.
    Updating a deactivated account raises PermissionDenied.
    """
    serializer = ProfileUpdateSerializer(
        deactivated_user,
        data={"full_name": "Tên Mới Khi Bị Khóa"},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors

    with pytest.raises(exceptions.PermissionDenied) as exc_info:
        serializer.save()

    assert "Tài khoản đã bị vô hiệu hóa hoặc không tồn tại." in str(exc_info.value)


@pytest.mark.django_db
def test_profile_update_serializer_cannot_reactivate_deactivated_account(deactivated_user):
    """
    ProfileUpdateSerializer never allows reactivating a deactivated account via profile PATCH.
    Directly invoking update with is_active=True must raise PermissionDenied and leave is_active=False.
    """
    serializer = ProfileUpdateSerializer(deactivated_user)
    with pytest.raises(exceptions.PermissionDenied):
        serializer.update(deactivated_user, {"is_active": True, "full_name": "Cố Gắng Kích Hoạt"})

    deactivated_user.refresh_from_db()
    assert deactivated_user.is_active is False


# =========================================================================
# 2. ChangePasswordView tests
# =========================================================================

@pytest.mark.django_db
def test_change_password_view_increments_auth_version_and_invalidates_tokens(active_user):
    """
    ChangePasswordView updates password hash, increments auth_version using CAS,
    and invalidates old tokens immediately.
    """
    factory = APIRequestFactory()
    tokens = generate_tokens_for_user(active_user)
    old_access_token = tokens["access"]
    initial_version = active_user.auth_version

    # Authenticate token before password change
    auth_handler = CustomJWTAuthentication()
    val_token = auth_handler.get_validated_token(old_access_token)
    assert auth_handler.get_user(val_token).pk == active_user.pk

    request = factory.post(
        "/api/v1/auth/change-password/",
        {
            "old_password": "OldP@ssw0rd123!",
            "new_password": "NewP@ssw0rd456!",
        },
        format="json",
    )
    force_authenticate(request, user=active_user)
    response = ChangePasswordView.as_view()(request)

    assert response.status_code == 204

    active_user.refresh_from_db()
    assert active_user.auth_version == initial_version + 1
    assert check_password("NewP@ssw0rd456!", active_user.password) is True

    # Old token must now be rejected with token_revoked
    with pytest.raises(exceptions.AuthenticationFailed) as exc_info:
        auth_handler.get_user(val_token)
    assert exc_info.value.detail.code == "token_revoked"


@pytest.mark.django_db
def test_change_password_view_concurrent_conflict_detected_via_cas(active_user):
    """
    Concurrent change password requests or auth_version mismatch triggers ConflictError (409).
    """
    factory = APIRequestFactory()
    request = factory.post(
        "/api/v1/auth/change-password/",
        {
            "old_password": "OldP@ssw0rd123!",
            "new_password": "NewP@ssw0rd456!",
        },
        format="json",
    )
    force_authenticate(request, user=active_user)

    # Simulate another session incrementing auth_version before CAS update executes
    User.objects.filter(pk=active_user.pk).update(auth_version=active_user.auth_version + 5)

    # Calling via DRF view returns HTTP 409 response
    response = ChangePasswordView.as_view()(request)
    assert response.status_code == 409
    assert "Tài khoản đã bị thay đổi bởi phiên khác" in str(response.data)

    # Directly calling post() raises ConflictError exception
    with pytest.raises(ConflictError) as exc_info:
        view = ChangePasswordView()
        drf_request = view.initialize_request(request)
        view.post(drf_request)

    assert exc_info.value.status_code == 409


# =========================================================================
# 3. UserAdminUpdateSerializer tests
# =========================================================================

@pytest.mark.django_db
def test_user_admin_update_protects_last_active_admin_from_deactivation_or_demotion(single_admin):
    """
    UserAdminUpdateSerializer prevents deactivating or demoting the last active Admin.
    Raises ValidationError in both validate() and update().
    """
    # 3a. Deactivation attempt
    deact_serializer = UserAdminUpdateSerializer(
        single_admin,
        data={"is_active": False},
        partial=True,
    )
    assert not deact_serializer.is_valid()
    assert "Không thể vô hiệu hóa hoặc hạ quyền quản trị viên duy nhất đang hoạt động." in str(deact_serializer.errors)

    # 3b. Demotion attempt
    demote_serializer = UserAdminUpdateSerializer(
        single_admin,
        data={"role": User.Role.USER},
        partial=True,
    )
    assert not demote_serializer.is_valid()
    assert "Không thể vô hiệu hóa hoặc hạ quyền quản trị viên duy nhất đang hoạt động." in str(demote_serializer.errors)

    # 3c. Direct update() method also raises ValidationError
    with pytest.raises(serializers.ValidationError) as exc_info:
        deact_serializer.update(single_admin, {"is_active": False})
    assert "Không thể vô hiệu hóa hoặc hạ quyền quản trị viên duy nhất đang hoạt động." in str(exc_info.value)


@pytest.mark.django_db
def test_user_admin_update_allows_deactivation_when_other_active_admin_exists(single_admin):
    """
    When another active admin exists, deactivating or demoting an admin is allowed.
    """
    # Create second active admin
    second_admin, _ = User.objects.get_or_create(
        username="d1_second_admin",
        defaults={
            "email": "d1_second_admin@assethub.test",
            "role": User.Role.ADMIN,
            "is_active": True,
            "auth_version": 1,
        },
    )
    second_admin.is_active = True
    second_admin.role = User.Role.ADMIN
    second_admin.save()

    serializer = UserAdminUpdateSerializer(
        single_admin,
        data={"is_active": False},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors
    updated = serializer.save()
    assert updated.is_active is False

    # Cleanup: restore single_admin so later fixtures are clean
    single_admin.is_active = True
    single_admin.save()
    second_admin.is_active = False
    second_admin.save()


@pytest.mark.django_db
def test_user_admin_update_cas_auth_version_mismatch_raises_conflict_error(single_admin, department):
    """
    When security_changed is True, CAS update on auth_version verifies current_version.
    If auth_version has been bumped concurrently, update() raises ConflictError (409).
    """
    # Target regular user for role change
    target_user, _ = User.objects.get_or_create(
        username="cas_target_user",
        defaults={
            "email": "cas_target@assethub.test",
            "role": User.Role.USER,
            "department": department,
            "is_active": True,
            "auth_version": 1,
        },
    )
    target_user.auth_version = 1
    target_user.save()

    serializer = UserAdminUpdateSerializer(
        target_user,
        data={"role": User.Role.MANAGER},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors

    # Simulate concurrent modification bumping auth_version behind our back
    User.objects.filter(pk=target_user.pk).update(auth_version=target_user.auth_version + 1)

    with pytest.raises(ConflictError) as exc_info:
        serializer.save()

    assert exc_info.value.status_code == 409
    assert "Tài khoản đã bị thay đổi bởi phiên khác" in str(exc_info.value)


@pytest.mark.django_db
def test_user_admin_update_stale_instance_is_active_true_raises_conflict(department):
    """
    Regression test: Instance fetched with is_active=True and auth_version=1.
    Concurrently, database changed to is_active=False and auth_version=2.
    Then admin PATCH {"is_active": True} on stale instance must NOT bypass CAS
    just because validated_data['is_active'] matches the stale instance's in-memory is_active.
    It must raise ConflictError, and the account in DB must remain is_active=False.
    """
    user = User.objects.create(
        username="stale_active_user",
        email="stale_active@assethub.test",
        role=User.Role.USER,
        department=department,
        is_active=True,
        auth_version=1,
    )

    # Serializer bound to stale in-memory instance where is_active is True and auth_version is 1
    serializer = UserAdminUpdateSerializer(
        user,
        data={"is_active": True},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors

    # Concurrent transaction changes DB to inactive and bumps auth_version
    User.objects.filter(pk=user.pk).update(is_active=False, auth_version=2)

    # Calling save() on stale instance must detect auth_version mismatch
    with pytest.raises(ConflictError) as exc_info:
        serializer.save()

    assert exc_info.value.status_code == 409
    assert "auth_version mismatch" in str(exc_info.value)

    # Ensure DB record was not overwritten and remains inactive with auth_version=2
    user.refresh_from_db()
    assert user.is_active is False
    assert user.auth_version == 2


# =========================================================================
# 4. D1 SQLite Trigger tests (protect_last_admin_before_update)
# =========================================================================

def test_d1_sqlite_trigger_protect_last_admin_before_update():
    """
    Validates D1 SQLite trigger 'protect_last_admin_before_update' defined in schema.sql.
    Verifies that the database engine aborts updates demoting or deactivating the last active Admin.
    """
    # Locate schema.sql
    schema_path = Path(settings.BASE_DIR).parent / "cloudflare-worker" / "bridge" / "schema.sql"
    if not schema_path.exists():
        schema_path = Path(settings.BASE_DIR) / "assethub_api" / "backends" / "d1" / "sql" / "schema.sql"

    assert schema_path.exists(), f"schema.sql not found at {schema_path}"

    conn = sqlite3.connect(":memory:")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn.executescript(schema_sql)

    # Insert single active admin
    conn.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (101, 'd1_sole_admin_sqlite', 'hash', 'sole@sqlite.test', 'ADMIN', 1)"
    )
    conn.commit()

    # Attempt to demote sole admin -> trigger must abort
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        conn.execute("UPDATE authentication_user SET role = 'USER' WHERE id = 101")

    # Attempt to deactivate sole admin -> trigger must abort
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        conn.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 101")

    # Insert second active admin
    conn.execute(
        "INSERT INTO authentication_user (id, username, password, email, role, is_active) "
        "VALUES (102, 'd1_second_admin_sqlite', 'hash', 'second@sqlite.test', 'ADMIN', 1)"
    )
    conn.commit()

    # Now deactivating or demoting the first admin succeeds
    conn.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 101")
    conn.commit()

    # Second admin is now sole active admin; demoting second admin must abort
    with pytest.raises(sqlite3.IntegrityError, match="Cannot demote or deactivate the last active Admin"):
        conn.execute("UPDATE authentication_user SET role = 'USER' WHERE id = 102")

    conn.close()
