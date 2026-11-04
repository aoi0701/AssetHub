import io
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from pypdf import PdfWriter
from rest_framework.test import APIClient

from apps.assets.models import Asset
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.common.storage_backends import (
    AttachmentStorage,
    LocalDevAttachmentStorage,
    R2AttachmentStorage,
)
from apps.departments.models import Category, Department


# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


def make_real_image(format='PNG', size=(40, 40), color='green', filename='test.png'):
    buf = io.BytesIO()
    img = Image.new('RGB', size, color=color)
    img.save(buf, format=format)
    buf.seek(0)
    mime = f"image/{format.lower()}" if format.lower() != 'jpg' else 'image/jpeg'
    return SimpleUploadedFile(filename, buf.getvalue(), content_type=mime)


def make_real_pdf(filename='doc.pdf'):
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buf)
    buf.seek(0)
    return SimpleUploadedFile(filename, buf.getvalue(), content_type='application/pdf')


@pytest.fixture
def temp_media_root(settings, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(media_dir)
    return media_dir


@pytest.fixture
def dept_it(db):
    return Department.objects.create(
        name="Information Technology",
        code="IT_R2",
        description="IT and Systems Infrastructure",
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username="admin_user_r2",
        email="admin_r2@assethub.local",
        password="AdminPassword123!",
        role=User.Role.ADMIN,
        full_name="System Administrator R2",
        phone="0901234567",
        department=None,
        is_active=True,
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def asset_category(db):
    return Category.objects.create(name='Test Category', description='Category for R2 storage tests')


@pytest.fixture
def test_asset(db, dept_it, asset_category):
    return Asset.objects.create(
        asset_code='AST-R2-TEST-001',
        name='ThinkPad X1 R2 Test',
        category=asset_category,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )


# ===========================================================================
# Unit Tests (Mock Storage & Mock S3 Client - No Real DB Needed for S3 logic)
# ===========================================================================

def test_r2_presigned_url_default_900s_ttl(monkeypatch):
    """
    [UNIT TEST - Mock S3 Client]
    R2AttachmentStorage generates presigned URL with default 900s TTL from settings.
    """
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "mock_key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "mock_secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "mock-assethub-bucket")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://mock.r2.cloudflarestorage.com")

    storage = AttachmentStorage()
    assert isinstance(storage, R2AttachmentStorage)

    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "https://mock.r2.cloudflarestorage.com/download?token=xyz"
    storage.boto3_client = mock_client

    url = storage.get_presigned_download_url("assets/1/image/photo.png")
    assert url == "https://mock.r2.cloudflarestorage.com/download?token=xyz"

    mock_client.generate_presigned_url.assert_called_once_with(
        'get_object',
        Params={
            'Bucket': 'mock-assethub-bucket',
            'Key': 'assets/1/image/photo.png',
            'ResponseContentDisposition': 'inline',
        },
        ExpiresIn=900,
    )


def test_r2_presigned_url_override_ttl_parameter(monkeypatch):
    """
    [UNIT TEST - Mock S3 Client]
    Explicit expire_seconds overrides the default 900s TTL.
    """
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "mock_key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "mock_secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "mock-assethub-bucket")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://mock.r2.cloudflarestorage.com")

    storage = AttachmentStorage()
    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "https://mock.r2.cloudflarestorage.com/override?token=xyz"
    storage.boto3_client = mock_client

    url = storage.get_presigned_download_url("assets/1/image/photo.png", expire_seconds=300)
    assert url == "https://mock.r2.cloudflarestorage.com/override?token=xyz"

    mock_client.generate_presigned_url.assert_called_once_with(
        'get_object',
        Params={
            'Bucket': 'mock-assethub-bucket',
            'Key': 'assets/1/image/photo.png',
            'ResponseContentDisposition': 'inline',
        },
        ExpiresIn=300,
    )


def test_r2_presigned_url_inline_for_images_and_attachment_for_documents(monkeypatch):
    """
    [UNIT TEST - Mock S3 Client]
    Enforces 'inline' for images and 'attachment; filename="doc.pdf"' for documents.
    """
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "mock_key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "mock_secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "mock-assethub-bucket")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://mock.r2.cloudflarestorage.com")

    storage = AttachmentStorage()
    mock_client = MagicMock()
    storage.boto3_client = mock_client

    # 1. Image -> inline
    storage.get_presigned_download_url("assets/1/image/photo.png", kind="image")
    assert mock_client.generate_presigned_url.call_args[1]['Params']['ResponseContentDisposition'] == 'inline'

    # 2. Document -> attachment
    storage.get_presigned_download_url("assets/1/document/specs.pdf", kind="document")
    assert mock_client.generate_presigned_url.call_args[1]['Params']['ResponseContentDisposition'] == 'attachment; filename="specs.pdf"'

    # 3. Explicit as_attachment override on image -> attachment
    storage.get_presigned_download_url("assets/1/image/photo.png", as_attachment=True)
    assert mock_client.generate_presigned_url.call_args[1]['Params']['ResponseContentDisposition'] == 'attachment; filename="photo.png"'


# ===========================================================================
# Integration Tests (Real Database & LocalDevAttachmentStorage)
# ===========================================================================

@pytest.mark.django_db
def test_download_view_returns_900_default_and_respects_override(admin_user, test_asset, temp_media_root):
    """
    [INTEGRATION TEST - Real DB & LocalDevStorage]
    Download endpoint returns 'expires_in': 900 by default and respects ?expires_in=300 query param.
    """
    storage = AttachmentStorage()
    key = storage.save(f"assets/{test_asset.id}/image/sample.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfakeimage"))
    test_asset.image = key
    test_asset.save()

    client = auth_client_for(admin_user)

    # 1. Default request -> 900 seconds TTL
    res_default = client.get(f"/api/assets/{test_asset.id}/attachments/image/download/")
    assert res_default.status_code == 200
    assert res_default.data["expires_in"] == 900
    assert "download_url" in res_default.data
    assert res_default.headers.get("Cache-Control") == "no-store"

    # 2. Override via ?expires_in=300 -> 300 seconds TTL
    res_override = client.get(f"/api/assets/{test_asset.id}/attachments/image/download/?expires_in=300")
    assert res_override.status_code == 200
    assert res_override.data["expires_in"] == 300

    # 3. Override via ?expire_seconds=120 -> 120 seconds TTL
    res_alias = client.get(f"/api/assets/{test_asset.id}/attachments/image/download/?expire_seconds=120")
    assert res_alias.status_code == 200
    assert res_alias.data["expires_in"] == 120


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query_param,expected_error",
    [
        ("expires_in=0", "Thời gian hết hạn URL phải nằm trong khoảng từ 1 đến 604800 giây."),
        ("expires_in=-1", "Thời gian hết hạn URL phải nằm trong khoảng từ 1 đến 604800 giây."),
        ("expires_in=604801", "Thời gian hết hạn URL phải nằm trong khoảng từ 1 đến 604800 giây."),
        ("expires_in=abc", "Thời gian hết hạn URL (expires_in/expire_seconds) phải là số nguyên hợp lệ."),
        ("expire_seconds=0", "Thời gian hết hạn URL phải nằm trong khoảng từ 1 đến 604800 giây."),
    ],
)
def test_download_view_invalid_ttl_returns_400(admin_user, test_asset, temp_media_root, query_param, expected_error):
    """
    [INTEGRATION TEST - Strict Integer TTL Validation]
    Download endpoint rejects invalid, non-integer, or out-of-bounds TTL query params with HTTP 400.
    """
    storage = AttachmentStorage()
    key = storage.save(f"assets/{test_asset.id}/image/sample.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfakeimage"))
    test_asset.image = key
    test_asset.save()

    client = auth_client_for(admin_user)
    res = client.get(f"/api/assets/{test_asset.id}/attachments/image/download/?{query_param}")
    assert res.status_code == 400
    assert res.data["detail"] == expected_error


@pytest.mark.django_db
def test_upload_on_disposed_asset_fails_and_cleans_up_new_file(admin_user, asset_category, dept_it, temp_media_root):
    """
    [INTEGRATION TEST - Real DB & LocalDevStorage]
    Upload on DISPOSED asset fails with HTTP 400 and immediately cleans up the uploaded file from storage.
    """
    disposed = Asset.objects.create(
        asset_code='AST-DISP-CLEANUP-001',
        name='Disposed Server',
        category=asset_category,
        department=dept_it,
        status=Asset.Status.DISPOSED,
    )
    client = auth_client_for(admin_user)
    img_file = make_real_image(filename="disposed_clean.png")

    res = client.post(
        f"/api/assets/{disposed.id}/attachments/",
        data={'kind': 'image', 'file': img_file},
        format='multipart',
    )
    assert res.status_code == 400
    assert "Không thể tải tệp lên tài sản đã thanh lý" in res.data["detail"]

    # Verify new file was cleaned up from storage
    media_path = temp_media_root
    created_files = list(media_path.rglob("*disposed_clean*"))
    assert len(created_files) == 0

    # Verify asset remains untouched in DB
    disposed.refresh_from_db()
    assert disposed.image == ''


@pytest.mark.django_db
def test_upload_concurrent_modification_cleans_up_new_file_and_preserves_old(admin_user, test_asset, temp_media_root):
    """
    [INTEGRATION TEST - Real DB & LocalDevStorage]
    When concurrent modification occurs (OCC mismatch, rows == 0),
    the new file is cleaned up and the existing old file is preserved.
    """
    client = auth_client_for(admin_user)
    storage = AttachmentStorage()

    # 1. First upload sets baseline file
    initial_img = make_real_image(filename="initial.png")
    res1 = client.post(
        f"/api/assets/{test_asset.id}/attachments/",
        data={'kind': 'image', 'file': initial_img},
        format='multipart',
    )
    assert res1.status_code == 200
    old_key = res1.data['key']
    assert storage.exists(old_key)

    # 2. Simulate concurrent modification during the second upload:
    # Right after the new file is saved to storage, another transaction changes the asset's image in DB.
    orig_save = LocalDevAttachmentStorage.save
    def concurrent_save(self_storage, name, content, max_length=None):
        saved = orig_save(self_storage, name, content, max_length=max_length)
        Asset.objects.filter(id=test_asset.id).update(image="assets/999/image/concurrent_replaced.png")
        return saved

    new_img = make_real_image(filename="second_concurrent.png")
    with patch.object(LocalDevAttachmentStorage, 'save', concurrent_save):
        res2 = client.post(
            f"/api/assets/{test_asset.id}/attachments/",
            data={'kind': 'image', 'file': new_img},
            format='multipart',
        )

    assert res2.status_code == 400
    assert "xung đột sửa đổi đồng thời" in res2.data["detail"]

    # 3. Verify new file was cleaned up
    created_new_files = list(temp_media_root.rglob("*second_concurrent*"))
    assert len(created_new_files) == 0

    # 4. Verify old file is preserved
    assert storage.exists(old_key)


@pytest.mark.django_db
def test_d1_incomplete_batch_error_committed_true_does_not_delete_new_file(admin_user, test_asset, temp_media_root):
    """
    [INTEGRATION TEST - Real DB & Mocked D1 IncompleteBatchError]
    If database raises an exception with committed=True (e.g. D1IncompleteBatchError),
    the saved file must NOT be deleted because the DB write actually committed.
    """
    class D1IncompleteBatchError(Exception):
        committed = True

    client = auth_client_for(admin_user)
    img_file = make_real_image(filename="d1_committed.png")

    with patch('django.db.models.query.QuerySet.update', side_effect=D1IncompleteBatchError("D1 incomplete batch")):
        with pytest.raises(D1IncompleteBatchError):
            client.post(
                f"/api/assets/{test_asset.id}/attachments/",
                data={'kind': 'image', 'file': img_file},
                format='multipart',
            )

    # Verify new file was NOT deleted because committed=True
    created_files = list(temp_media_root.rglob("*d1_committed*"))
    assert len(created_files) == 1


@pytest.mark.django_db
def test_database_exception_committed_false_deletes_new_file(admin_user, test_asset, temp_media_root):
    """
    [INTEGRATION TEST - Real DB & Mocked DB Error]
    If database raises an exception where committed is not True,
    the saved file MUST be deleted immediately to prevent orphans.
    """
    class UncommittedError(Exception):
        committed = False

    client = auth_client_for(admin_user)
    img_file = make_real_image(filename="uncommitted.png")

    with patch('django.db.models.query.QuerySet.update', side_effect=UncommittedError("DB write failed")):
        with pytest.raises(UncommittedError):
            client.post(
                f"/api/assets/{test_asset.id}/attachments/",
                data={'kind': 'image', 'file': img_file},
                format='multipart',
            )

    # Verify new file was deleted
    created_files = list(temp_media_root.rglob("*uncommitted*"))
    assert len(created_files) == 0


@pytest.mark.django_db
def test_database_exception_unknown_committed_readback_matches_retains_new_and_deletes_old(
    admin_user, test_asset, temp_media_root
):
    """
    [INTEGRATION TEST - State 3: Unknown Commit State with Successful Readback Match]
    If update raises an exception without 'committed' attr (e.g. timeout after commit),
    readback finds saved_key in DB -> retains new file and deletes old file.
    """
    from django.db import connection

    client = auth_client_for(admin_user)
    storage = AttachmentStorage()

    # Seed old file
    old_key = storage.save(f"assets/{test_asset.id}/image/old_file.png", io.BytesIO(b"\x89PNG\r\n\x1a\nold"))
    test_asset.image = old_key
    test_asset.save()

    class OperationalTimeoutError(Exception):
        pass  # No committed attribute

    def fake_update(self_qs, **kwargs):
        # Directly update DB without calling QuerySet.update recursively
        with connection.cursor() as cursor:
            cursor.execute("UPDATE assets_asset SET image = %s WHERE id = %s", [kwargs['image'], test_asset.id])
        raise OperationalTimeoutError("Lost network connection after mutation commit")

    img_file = make_real_image(filename="unknown_state_match.png")
    with patch('django.db.models.query.QuerySet.update', side_effect=fake_update, autospec=True):
        with pytest.raises(OperationalTimeoutError):
            client.post(
                f"/api/assets/{test_asset.id}/attachments/",
                data={'kind': 'image', 'file': img_file},
                format='multipart',
            )

    # Verify new file is retained
    new_files = list(temp_media_root.rglob("*unknown_state_match*"))
    assert len(new_files) == 1
    # Verify old file was deleted
    assert not storage.exists(old_key)


@pytest.mark.django_db
def test_database_exception_unknown_committed_readback_mismatch_deletes_new(
    admin_user, test_asset, temp_media_root
):
    """
    [INTEGRATION TEST - State 3: Unknown Commit State with Readback Mismatch]
    If update raises an exception without 'committed' attr, and readback shows
    saved_key was NOT saved in DB -> cleans up new file.
    """
    client = auth_client_for(admin_user)

    class OperationalError(Exception):
        pass

    img_file = make_real_image(filename="unknown_state_mismatch.png")
    with patch('django.db.models.query.QuerySet.update', side_effect=OperationalError("Network drop before commit")):
        with pytest.raises(OperationalError):
            client.post(
                f"/api/assets/{test_asset.id}/attachments/",
                data={'kind': 'image', 'file': img_file},
                format='multipart',
            )

    # Verify new file was cleaned up
    new_files = list(temp_media_root.rglob("*unknown_state_mismatch*"))
    assert len(new_files) == 0


@pytest.mark.django_db
def test_database_exception_unknown_committed_readback_failure_retains_new_orphan(
    admin_user, test_asset, temp_media_root
):
    """
    [INTEGRATION TEST - State 3: Readback Failure Retains Orphan]
    If readback itself raises an exception, the state remains unknown.
    The new file is retained as temporary orphan to avoid broken asset references.
    """
    client = auth_client_for(admin_user)

    class NetworkError(Exception):
        pass

    def fail_first(self_qs):
        raise NetworkError("Readback timeout")

    img_file = make_real_image(filename="unknown_state_orphan.png")
    with patch('django.db.models.query.QuerySet.update', side_effect=NetworkError("Update error")):
        with patch('django.db.models.query.QuerySet.first', side_effect=fail_first, autospec=True):
            with pytest.raises(NetworkError):
                client.post(
                    f"/api/assets/{test_asset.id}/attachments/",
                    data={'kind': 'image', 'file': img_file},
                    format='multipart',
                )

    # Verify new file was retained as temporary orphan
    new_files = list(temp_media_root.rglob("*unknown_state_orphan*"))
    assert len(new_files) == 1


@pytest.mark.django_db(transaction=True)
def test_successful_replacement_cleans_up_old_file_and_retains_new_file(admin_user, test_asset, temp_media_root):
    """
    [INTEGRATION TEST - Real DB & LocalDevStorage]
    Replacing an attachment succeeds: old file deleted from storage, new file retained,
    and DB stores relative object key (NEVER a full presigned URL).
    """
    client = auth_client_for(admin_user)
    storage = AttachmentStorage()

    # 1. Upload first file
    img1 = make_real_image(color='red', filename="first_replace.png")
    res1 = client.post(
        f"/api/assets/{test_asset.id}/attachments/",
        data={'kind': 'image', 'file': img1},
        format='multipart',
    )
    assert res1.status_code == 200
    key1 = res1.data['key']
    assert storage.exists(key1)

    # 2. Upload second file (replacement)
    img2 = make_real_image(color='blue', filename="second_replace.png")
    res2 = client.post(
        f"/api/assets/{test_asset.id}/attachments/",
        data={'kind': 'image', 'file': img2},
        format='multipart',
    )
    assert res2.status_code == 200
    key2 = res2.data['key']
    assert key2 != key1

    # 3. Old file deleted, new file exists
    assert not storage.exists(key1)
    assert storage.exists(key2)

    # 4. Verify DB stores relative key only, not presigned URL
    test_asset.refresh_from_db()
    assert test_asset.image == key2
    assert not test_asset.image.startswith("http://")
    assert not test_asset.image.startswith("https://")
    assert not test_asset.image.startswith("/api/")
    assert test_asset.image.startswith(f"assets/{test_asset.id}/image/")
