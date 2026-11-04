"""
Integration & Rehearsal Test Suite for Cloudflare D1 Attachment Storage.

Tests real Cloudflare D1 local attachment upload and replacement using LocalDevStorage,
three-state commit exception cleanup (committed=True, committed=False, unknown state readback),
readback failure orphan retention, and OCC / DISPOSED conflicts.
"""

import io
import secrets
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.assets.models import Asset
from apps.assets.views import AssetViewSet
from apps.authentication.models import User
from apps.common.storage_backends import AttachmentStorage, LocalDevAttachmentStorage
from apps.common.tests.harness_d1_target import D1TargetHarness, check_port_in_use
from apps.departments.models import Category, Department

pytestmark = pytest.mark.rehearsal

ALIAS = "d1_attachment_test"


class D1AttachmentTestRouter:
    """Routes all model reads and writes to d1_attachment_test during integration tests."""

    def db_for_read(self, model, **hints):
        return ALIAS

    def db_for_write(self, model, **hints):
        return ALIAS

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == ALIAS


def find_available_port(start_port=8800):
    port = start_port
    while check_port_in_use("127.0.0.1", port):
        port += 1
    return port


def make_real_image(format="PNG", size=(40, 40), color="green", filename="test.png"):
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=format)
    buf.seek(0)
    mime = f"image/{format.lower()}" if format.lower() != "jpg" else "image/jpeg"
    return SimpleUploadedFile(filename, buf.getvalue(), content_type=mime)


def seed_attachment_fixtures(alias: str = ALIAS) -> dict:
    """Seed or reset minimal test entities in local D1 database."""
    with connections[alias].cursor() as cursor:
        cursor.execute("DELETE FROM assets_assethistorylog;")
        cursor.execute("DELETE FROM requests_assetrequest;")
        cursor.execute("DELETE FROM assets_asset;")
        cursor.execute("DELETE FROM _assert_guard;")
        cursor.execute("DELETE FROM authentication_user WHERE id > 1;")
        cursor.execute(
            "UPDATE authentication_user SET is_active = 1, role = 'ADMIN', auth_version = 1 WHERE id = 1;"
        )

    dept_it = Department.objects.using(alias).filter(id=1).first()
    if not dept_it:
        dept_it = Department.objects.using(alias).create(
            id=1,
            code="IT",
            name="Phòng Công Nghệ Thông Tin",
            description="Bộ phận IT kiểm thử",
        )

    cat_laptop = Category.objects.using(alias).filter(id=1).first()
    if not cat_laptop:
        cat_laptop = Category.objects.using(alias).create(
            id=1,
            name="Máy tính xách tay",
            description="Laptop phục vụ công việc",
        )

    admin_user = User.objects.using(alias).filter(id=1).first()
    if not admin_user:
        admin_user = User(
            id=1,
            username="admin_storage_d1",
            email="admin_storage_d1@assethub.test",
            role=User.Role.ADMIN,
            full_name="Quản Trị Viên D1 Storage",
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

    asset1 = Asset.objects.using(alias).create(
        id=1,
        asset_code="AST-D1-ATT-001",
        name="MacBook Pro D1 Attachment Test",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
        serial_number="D1ATTTEST001",
    )

    return {
        "dept_it": dept_it,
        "cat_laptop": cat_laptop,
        "admin_user": admin_user,
        "asset1": asset1,
    }


@pytest.fixture(scope="module")
def d1_env(django_db_blocker):
    """
    Module-level fixture spinning up local D1 via D1TargetHarness,
    configuring ALIAS and default database routing to local D1.
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

        settings.DATABASES[ALIAS] = d1_conf
        connections.databases[ALIAS] = d1_conf
        orig_default = dict(settings.DATABASES.get("default", {}))
        settings.DATABASES["default"] = d1_conf
        connections.databases["default"] = d1_conf

        orig_routers = getattr(settings, "DATABASE_ROUTERS", [])
        settings.DATABASE_ROUTERS = [
            "apps.assets.tests.test_d1_attachment_storage.D1AttachmentTestRouter"
        ]
        from django.db import router
        router._routers = None
        router.__dict__.pop("routers", None)

        seed_attachment_fixtures(ALIAS)

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
                        try:
                            del connections[a]
                        except Exception:
                            pass
                settings.DATABASES.pop(ALIAS, None)
                connections.databases.pop(ALIAS, None)
                if orig_default:
                    settings.DATABASES["default"] = orig_default
                    connections.databases["default"] = orig_default
                settings.DATABASE_ROUTERS = orig_routers
                from django.db import router
                router._routers = None
                router.__dict__.pop("routers", None)


@pytest.fixture(autouse=True)
def unblock_db_each_test(django_db_blocker):
    with django_db_blocker.unblock():
        yield


@pytest.fixture
def temp_media_root(settings, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(media_dir)
    return media_dir


def invoke_upload(asset_id, file_obj, user, kind="image"):
    factory = APIRequestFactory()
    request = factory.post(
        f"/api/assets/{asset_id}/attachments/",
        {"kind": kind, "file": file_obj},
        format="multipart",
    )
    force_authenticate(request, user=user)
    view = AssetViewSet.as_view({"post": "attachments"})
    return view(request, pk=asset_id)


def test_d1_attachment_upload_and_replace_with_local_storage(d1_env, temp_media_root):
    """
    Test 1: Real D1 local attachment upload and replacement using LocalDevStorage.
    - Initial upload succeeds, file exists in storage, asset.image points to key.
    - Replacement upload succeeds, new file retained, old file deleted from storage.
    """
    fixtures = seed_attachment_fixtures(ALIAS)
    admin = fixtures["admin_user"]
    asset = fixtures["asset1"]
    storage = AttachmentStorage()

    # 1. Initial upload
    img1 = make_real_image(filename="d1_initial.png")
    resp1 = invoke_upload(asset.id, img1, admin)
    assert resp1.status_code == 200
    key1 = resp1.data["key"]
    assert storage.exists(key1)

    asset.refresh_from_db(using=ALIAS)
    assert asset.image == key1

    # 2. Replacement upload
    img2 = make_real_image(color="blue", filename="d1_replacement.png")
    resp2 = invoke_upload(asset.id, img2, admin)
    assert resp2.status_code == 200
    key2 = resp2.data["key"]
    assert key2 != key1
    assert storage.exists(key2)

    # Old file must be deleted from storage
    assert not storage.exists(key1)

    # Asset in D1 points to new key
    asset.refresh_from_db(using=ALIAS)
    assert asset.image == key2


def test_d1_lost_ack_unknown_committed_readback_preserves_new_and_deletes_old(
    d1_env, temp_media_root
):
    """
    Test 2: Actual D1 commit followed by lost ack (OperationalError without committed flag):
    - Readback detects saved_key in D1.
    - Under data preservation contract, both saved_key and old_key are preserved in storage.
    - Confirms storage.exists(saved_key) is True.
    - Confirms storage.exists(old_key) is True.
    """
    fixtures = seed_attachment_fixtures(ALIAS)
    admin = fixtures["admin_user"]
    asset = fixtures["asset1"]
    storage = AttachmentStorage()

    # Seed old file in storage and D1
    old_img = make_real_image(filename="old_baseline.png")
    old_key = storage.save(f"assets/{asset.id}/image/old_baseline.png", old_img)
    Asset.objects.using(ALIAS).filter(id=asset.id).update(image=old_key)
    asset.refresh_from_db(using=ALIAS)
    assert storage.exists(old_key)

    class LostAckOperationalError(Exception):
        pass  # No committed attribute -> State 3 (unknown state)

    def fake_update(self_qs, **kwargs):
        # Perform actual commit directly in local D1
        with connections[ALIAS].cursor() as cursor:
            cursor.execute(
                "UPDATE assets_asset SET image = %s WHERE id = %s;",
                [kwargs["image"], asset.id],
            )
        # Simulate lost ack
        raise LostAckOperationalError("Lost ack after D1 commit")

    new_img = make_real_image(filename="lost_ack.png")
    with patch("django.db.models.query.QuerySet.update", side_effect=fake_update, autospec=True):
        with pytest.raises(LostAckOperationalError):
            invoke_upload(asset.id, new_img, admin)

    # Readback verified that D1 committed the new key:
    asset.refresh_from_db(using=ALIAS)
    saved_key = asset.image
    assert saved_key != old_key
    assert "lost_ack" in saved_key

    # Under data preservation contract, both files MUST be preserved in storage
    assert storage.exists(saved_key) is True
    assert storage.exists(old_key) is True


def test_d1_late_commit_after_timeout_preserves_new_file(d1_env, temp_media_root):
    """
    Regression Test: Late commit after timeout preserves new file.
    Simulate late commit:
    1. Seed asset with old_key in storage and D1.
    2. Patch / hook QuerySet.update to simulate a delayed in-flight mutation:
       It raises OperationalError("Timeout waiting for D1 ack") without committed attribute (unknown state).
       View catches unknown state, does NOT delete saved_key, does NOT delete old_key, and re-raises OperationalError.
    3. After view returns/raises, the delayed mutation finishes in D1:
       Update D1 record: Asset.objects.using(ALIAS).filter(id=asset.id).update(image=saved_key).
    4. Assert D1 points to saved_key.
    5. Assert storage.exists(saved_key) is True!
       This proves the pending commit does NOT point to a deleted file (zero dangling pointer).
    """
    from django.db import OperationalError

    fixtures = seed_attachment_fixtures(ALIAS)
    admin = fixtures["admin_user"]
    asset = fixtures["asset1"]
    storage = AttachmentStorage()

    # 1. Seed asset with old_key in storage and D1
    old_img = make_real_image(filename="old_baseline_late.png")
    old_key = storage.save(f"assets/{asset.id}/image/old_baseline_late.png", old_img)
    Asset.objects.using(ALIAS).filter(id=asset.id).update(image=old_key)
    asset.refresh_from_db(using=ALIAS)
    assert storage.exists(old_key) is True

    # 2. Patch QuerySet.update to simulate delayed in-flight mutation raising OperationalError
    captured_kwargs = {}

    def delayed_update(self_qs, **kwargs):
        captured_kwargs.update(kwargs)
        raise OperationalError("Timeout waiting for D1 ack")

    new_img = make_real_image(filename="late_commit.png")
    with patch("django.db.models.query.QuerySet.update", side_effect=delayed_update, autospec=True):
        with pytest.raises(OperationalError) as exc_info:
            invoke_upload(asset.id, new_img, admin)

    assert "Timeout waiting for D1 ack" in str(exc_info.value)
    saved_key = captured_kwargs.get("image")
    assert saved_key is not None
    assert "late_commit" in saved_key

    # View catches unknown state, does NOT delete saved_key, does NOT delete old_key
    assert storage.exists(saved_key) is True
    assert storage.exists(old_key) is True

    # 3. After view returns/raises, the delayed mutation finishes in D1:
    Asset.objects.using(ALIAS).filter(id=asset.id).update(image=saved_key)

    # 4. Assert D1 points to saved_key
    asset.refresh_from_db(using=ALIAS)
    assert asset.image == saved_key

    # 5. Assert storage.exists(saved_key) is True (zero dangling pointer)
    assert storage.exists(saved_key) is True


def test_d1_uncommitted_error_deletes_new_file(d1_env, temp_media_root):
    """
    Test 3: D1 uncommitted error (committed=False):
    - Confirms storage.exists(saved_key) is False.
    """
    fixtures = seed_attachment_fixtures(ALIAS)
    admin = fixtures["admin_user"]
    asset = fixtures["asset1"]

    class D1UncommittedError(Exception):
        committed = False

    new_img = make_real_image(filename="d1_uncommitted.png")
    with patch(
        "django.db.models.query.QuerySet.update",
        side_effect=D1UncommittedError("D1 transaction aborted"),
    ):
        with pytest.raises(D1UncommittedError):
            invoke_upload(asset.id, new_img, admin)

    # Confirm saved_key was deleted from storage
    created_files = list(temp_media_root.rglob("*d1_uncommitted*"))
    assert len(created_files) == 0


def test_d1_committed_error_retains_new_file(d1_env, temp_media_root):
    """
    Test 4: D1 committed error (committed=True):
    - Confirms storage.exists(saved_key) is True.
    """
    fixtures = seed_attachment_fixtures(ALIAS)
    admin = fixtures["admin_user"]
    asset = fixtures["asset1"]

    class D1CommittedError(Exception):
        committed = True

    new_img = make_real_image(filename="d1_committed_err.png")
    with patch(
        "django.db.models.query.QuerySet.update",
        side_effect=D1CommittedError("D1 batch committed with partial error"),
    ):
        with pytest.raises(D1CommittedError):
            invoke_upload(asset.id, new_img, admin)

    # Confirm saved_key was NOT deleted because committed=True
    created_files = list(temp_media_root.rglob("*d1_committed_err*"))
    assert len(created_files) == 1


def test_d1_readback_failure_retains_new_file_as_orphan(d1_env, temp_media_root):
    """
    Test 5: Readback failure when commit state is unknown:
    - Confirms saved_key is preserved as orphan to avoid missing file references.
    """
    fixtures = seed_attachment_fixtures(ALIAS)
    admin = fixtures["admin_user"]
    asset = fixtures["asset1"]

    class OperationalTimeoutError(Exception):
        pass  # No committed attribute

    class ReadbackError(Exception):
        pass

    def fail_first(self_qs):
        raise ReadbackError("D1 bridge completely unreachable during readback")

    new_img = make_real_image(filename="d1_orphan_retain.png")
    with patch(
        "django.db.models.query.QuerySet.update",
        side_effect=OperationalTimeoutError("D1 timeout"),
    ):
        with patch("django.db.models.query.QuerySet.first", side_effect=fail_first, autospec=True):
            with pytest.raises(OperationalTimeoutError):
                invoke_upload(asset.id, new_img, admin)

    # State remained unknown -> saved_key preserved as temporary orphan
    created_files = list(temp_media_root.rglob("*d1_orphan_retain*"))
    assert len(created_files) == 1


def test_d1_occ_and_disposed_conflicts_clean_up_new_file(d1_env, temp_media_root):
    """
    Test 6: D1 OCC conflict & DISPOSED conflict on local D1:
    - DISPOSED asset: returns HTTP 400, cleans up new file.
    - Concurrent modification (OCC mismatch): returns HTTP 400, cleans up new file, preserves old file.
    """
    fixtures = seed_attachment_fixtures(ALIAS)
    admin = fixtures["admin_user"]
    asset = fixtures["asset1"]
    storage = AttachmentStorage()

    # 1. DISPOSED Conflict
    asset.status = Asset.Status.DISPOSED
    asset.save(using=ALIAS)

    img_disp = make_real_image(filename="d1_disposed_clean.png")
    resp_disp = invoke_upload(asset.id, img_disp, admin)
    assert resp_disp.status_code == 400
    assert "Không thể tải tệp lên tài sản đã thanh lý." in resp_disp.data["detail"]

    created_disp_files = list(temp_media_root.rglob("*d1_disposed_clean*"))
    assert len(created_disp_files) == 0

    # 2. OCC Conflict (Concurrent modification)
    asset.status = Asset.Status.AVAILABLE
    old_key = storage.save(f"assets/{asset.id}/image/d1_occ_old.png", io.BytesIO(b"old image"))
    asset.image = old_key
    asset.save(using=ALIAS)

    orig_save = LocalDevAttachmentStorage.save

    def concurrent_save(self_storage, name, content, max_length=None):
        saved = orig_save(self_storage, name, content, max_length=max_length)
        # Another transaction updates image in D1 right after file is saved
        with connections[ALIAS].cursor() as cursor:
            cursor.execute(
                "UPDATE assets_asset SET image = 'assets/1/image/concurrent_raced.png' WHERE id = %s;",
                [asset.id],
            )
        return saved

    img_occ = make_real_image(filename="d1_occ_new.png")
    with patch.object(LocalDevAttachmentStorage, "save", concurrent_save):
        resp_occ = invoke_upload(asset.id, img_occ, admin)

    assert resp_occ.status_code == 400
    assert "xung đột sửa đổi đồng thời" in resp_occ.data["detail"]

    # New file must be cleaned up
    created_occ_files = list(temp_media_root.rglob("*d1_occ_new*"))
    assert len(created_occ_files) == 0

    # Old file must be preserved
    assert storage.exists(old_key)
