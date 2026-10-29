import io
import time
from unittest.mock import MagicMock, patch

import pytest
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
    generate_local_presigned_url,
    verify_local_presigned_url,
)
from apps.departments.models import Category, Department


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


def make_real_image(format='PNG', size=(60, 60), color='blue', filename='test.png'):
    buf = io.BytesIO()
    img = Image.new('RGB', size, color=color)
    img.save(buf, format=format)
    buf.seek(0)
    mime = f"image/{format.lower()}" if format.lower() != 'jpg' else 'image/jpeg'
    return SimpleUploadedFile(filename, buf.getvalue(), content_type=mime)


def make_real_pdf(filename='test.pdf'):
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buf)
    buf.seek(0)
    return SimpleUploadedFile(filename, buf.getvalue(), content_type='application/pdf')


@pytest.fixture
def category(db):
    return Category.objects.create(name='Laptops & Electronics', description='Hardware')


@pytest.fixture
def available_asset(db, dept_it, category):
    return Asset.objects.create(
        asset_code='AST-TEST-ATTACH-001',
        name='ThinkPad X1 Carbon',
        category=category,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )


# ---------------------------------------------------------------------------
# Upload Validation Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_valid_real_images_upload_succeeds(admin_user, available_asset, temp_media_root):
    """Real JPEG, PNG, and WebP images upload successfully with 200 OK."""
    client = auth_client_for(admin_user)

    for fmt, ext in [('JPEG', 'jpg'), ('PNG', 'png'), ('WEBP', 'webp')]:
        img_file = make_real_image(format=fmt, filename=f"photo.{ext}")
        res = client.post(
            f"/api/assets/{available_asset.id}/attachments/",
            data={'kind': 'image', 'file': img_file},
            format='multipart',
        )
        assert res.status_code == 200
        assert res.data['kind'] == 'image'
        assert 'assets/' in res.data['key']

        available_asset.refresh_from_db()
        assert available_asset.image == res.data['key']
        # Verify presigned URL is NOT stored in DB, only object key
        assert not available_asset.image.startswith('http')
        assert available_asset.image.endswith(f"photo.{ext}")


@pytest.mark.django_db
def test_valid_real_pdf_upload_succeeds(admin_user, available_asset, temp_media_root):
    """Real PDF file uploads successfully with 200 OK."""
    client = auth_client_for(admin_user)
    pdf_file = make_real_pdf('manual.pdf')
    res = client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'document', 'file': pdf_file},
        format='multipart',
    )
    assert res.status_code == 200
    assert res.data['kind'] == 'document'
    assert 'assets/' in res.data['key']

    available_asset.refresh_from_db()
    assert available_asset.document == res.data['key']
    assert not available_asset.document.startswith('http')


@pytest.mark.django_db
def test_reject_oversized_image_with_400(admin_user, available_asset, temp_media_root):
    """Images exceeding 5 MiB are rejected with 400 Bad Request."""
    client = auth_client_for(admin_user)
    # > 5 MiB dummy data
    oversized_data = b"x" * (5 * 1024 * 1024 + 10)
    oversized_img = SimpleUploadedFile("big.png", oversized_data, content_type="image/png")

    res = client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'image', 'file': oversized_img},
        format='multipart',
    )
    assert res.status_code == 400
    assert "vượt quá giới hạn" in str(res.data)


@pytest.mark.django_db
def test_reject_oversized_pdf_with_400(admin_user, available_asset, temp_media_root):
    """Documents exceeding 10 MiB are rejected with 400 Bad Request."""
    client = auth_client_for(admin_user)
    oversized_data = b"%PDF-" + b"x" * (10 * 1024 * 1024 + 10)
    oversized_pdf = SimpleUploadedFile("big.pdf", oversized_data, content_type="application/pdf")

    res = client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'document', 'file': oversized_pdf},
        format='multipart',
    )
    assert res.status_code == 400
    assert "vượt quá giới hạn" in str(res.data)


@pytest.mark.django_db
def test_reject_fake_image_disguised_with_extension(admin_user, available_asset, temp_media_root):
    """Text disguised as .png/.jpg is detected by Pillow and rejected with 400 Bad Request."""
    client = auth_client_for(admin_user)
    fake_img = SimpleUploadedFile(
        "photo.png",
        b"<html><body>Not a real image bytes</body></html>",
        content_type="image/png",
    )
    res = client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'image', 'file': fake_img},
        format='multipart',
    )
    assert res.status_code == 400
    assert "không hợp lệ hoặc bị lỗi" in str(res.data)


@pytest.mark.django_db
def test_reject_fake_pdf_disguised_with_extension(admin_user, available_asset, temp_media_root):
    """Text file disguised as .pdf is detected by pypdf and rejected with 400 Bad Request."""
    client = auth_client_for(admin_user)
    fake_pdf = SimpleUploadedFile(
        "document.pdf",
        b"This is plain text with no PDF headers or structure.",
        content_type="application/pdf",
    )
    res = client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'document', 'file': fake_pdf},
        format='multipart',
    )
    assert res.status_code == 400
    assert "không hợp lệ hoặc bị lỗi" in str(res.data) or "không phải là định dạng PDF" in str(res.data)


@pytest.mark.django_db
def test_upload_on_disposed_asset_rejected_with_400(admin_user, dept_it, category, temp_media_root):
    """Upload on DISPOSED asset is strictly rejected with 400 Bad Request."""
    disposed_asset = Asset.objects.create(
        asset_code='AST-DISPOSED-001',
        name='Scrapped Laptop',
        category=category,
        department=dept_it,
        status=Asset.Status.DISPOSED,
    )
    client = auth_client_for(admin_user)
    img_file = make_real_image()
    res = client.post(
        f"/api/assets/{disposed_asset.id}/attachments/",
        data={'kind': 'image', 'file': img_file},
        format='multipart',
    )
    assert res.status_code == 400
    assert "Không thể tải tệp lên tài sản đã thanh lý" in str(res.data)


@pytest.mark.django_db
def test_admin_can_upload_manager_and_user_forbidden(admin_user, manager_user, regular_user, available_asset, temp_media_root):
    """Only Admin can upload attachments; Manager and User receive 403 Forbidden."""
    img_admin = make_real_image(filename="admin_img.png")
    admin_client = auth_client_for(admin_user)
    assert admin_client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'image', 'file': img_admin},
        format='multipart',
    ).status_code == 200

    img_mgr = make_real_image(filename="mgr_img.png")
    mgr_client = auth_client_for(manager_user)
    assert mgr_client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'image', 'file': img_mgr},
        format='multipart',
    ).status_code == 403

    img_user = make_real_image(filename="user_img.png")
    user_client = auth_client_for(regular_user)
    assert user_client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'image', 'file': img_user},
        format='multipart',
    ).status_code == 403


# ---------------------------------------------------------------------------
# Download Permissions and Scoping Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_download_permissions_admin_manager_user(
    admin_user, manager_user, regular_user, dept_it, dept_hr, category, temp_media_root
):
    """
    Download scoping:
    - Admin: allowed for all.
    - Manager: allowed for own department, 403/404 for other department.
    - User: allowed while holding asset, 403 after revoked or when not assigned.
    - Nonexistent attachment returns 404.
    """
    storage = AttachmentStorage()
    key_it = storage.save("assets/it/image/test.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfakecontent"))
    key_hr = storage.save("assets/hr/image/test.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfakecontent"))

    asset_it = Asset.objects.create(
        asset_code="AST-DOWN-IT",
        name="IT Laptop",
        category=category,
        department=dept_it,
        status=Asset.Status.IN_USE,
        assigned_to_user=regular_user,
        image=key_it,
    )

    asset_hr = Asset.objects.create(
        asset_code="AST-DOWN-HR",
        name="HR Laptop",
        category=category,
        department=dept_hr,
        status=Asset.Status.AVAILABLE,
        image=key_hr,
    )

    admin_client = auth_client_for(admin_user)
    mgr_client = auth_client_for(manager_user)
    user_client = auth_client_for(regular_user)

    # 1. Admin receives 200 and Cache-Control: no-store
    admin_res = admin_client.get(f"/api/assets/{asset_it.id}/attachments/image/download/")
    assert admin_res.status_code == 200
    assert "download_url" in admin_res.data
    assert admin_res.data["expires_in"] == 60
    assert admin_res.headers.get("Cache-Control") == "no-store"

    # 2. Manager receives 200 for own dept, 403/404 for other dept
    mgr_own_res = mgr_client.get(f"/api/assets/{asset_it.id}/attachments/image/download/")
    assert mgr_own_res.status_code == 200

    mgr_other_res = mgr_client.get(f"/api/assets/{asset_hr.id}/attachments/image/download/")
    assert mgr_other_res.status_code in (403, 404)

    # 3. User receives 200 while holding asset
    user_held_res = user_client.get(f"/api/assets/{asset_it.id}/attachments/image/download/")
    assert user_held_res.status_code == 200

    # 4. User receives 403 after asset is revoked / no longer holding it
    asset_it.assigned_to_user = None
    asset_it.status = Asset.Status.AVAILABLE
    asset_it.save()

    user_revoked_res = user_client.get(f"/api/assets/{asset_it.id}/attachments/image/download/")
    assert user_revoked_res.status_code == 403

    # 5. Nonexistent attachment returns 404
    no_doc_res = admin_client.get(f"/api/assets/{asset_it.id}/attachments/document/download/")
    assert no_doc_res.status_code == 404


# ---------------------------------------------------------------------------
# Orphan & Old File Cleanup Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_orphan_file_cleaned_up_if_database_save_fails(admin_user, available_asset, temp_media_root):
    """If database save fails, newly uploaded file is deleted from storage immediately."""
    client = auth_client_for(admin_user)
    storage = AttachmentStorage()
    img_file = make_real_image(filename="orphan_test.png")

    with patch.object(Asset, 'save', side_effect=RuntimeError("Database crashed during save")):
        with pytest.raises(RuntimeError):
            client.post(
                f"/api/assets/{available_asset.id}/attachments/",
                data={'kind': 'image', 'file': img_file},
                format='multipart',
            )

    # Check that no orphan files remain in storage directory
    media_path = temp_media_root
    created_files = list(media_path.rglob("orphan_test.png"))
    assert len(created_files) == 0


@pytest.mark.django_db(transaction=True)
def test_old_file_cleaned_up_when_replacing_attachment(admin_user, available_asset, temp_media_root):
    """When replacing an existing file, old file is deleted inside transaction.on_commit."""
    client = auth_client_for(admin_user)
    storage = AttachmentStorage()

    # First upload
    first_img = make_real_image(color='red', filename="first.png")
    res1 = client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'image', 'file': first_img},
        format='multipart',
    )
    assert res1.status_code == 200
    first_key = res1.data['key']
    assert storage.exists(first_key)

    # Second upload replaces first
    second_img = make_real_image(color='blue', filename="second.png")
    res2 = client.post(
        f"/api/assets/{available_asset.id}/attachments/",
        data={'kind': 'image', 'file': second_img},
        format='multipart',
    )
    assert res2.status_code == 200
    second_key = res2.data['key']
    assert storage.exists(second_key)

    # First key should now be deleted from storage
    assert not storage.exists(first_key)


# ---------------------------------------------------------------------------
# Storage Backend Presigned URLs & Security Tests
# ---------------------------------------------------------------------------

def test_local_signed_download_flow_and_expiration(temp_media_root, settings):
    """Local presigned URLs allow downloading with signature; expired or tampered URLs return 403."""
    storage = LocalDevAttachmentStorage()
    test_key = storage.save("test_file.txt", io.BytesIO(b"Confidential asset data"))

    # Valid URL expiring in 60 seconds
    valid_url = storage.get_presigned_download_url(test_key, expire_seconds=60)
    assert "/api/media/download/" in valid_url

    client = APIClient()
    res = client.get(valid_url)
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "no-store"
    assert b"Confidential asset data" in res.getvalue()

    # Expired URL test
    expired_url = storage.get_presigned_download_url(test_key, expire_seconds=-10)
    res_expired = client.get(expired_url)
    assert res_expired.status_code == 403
    assert "đã hết hạn" in str(res_expired.data)

    # Tampered signature test
    tampered_url = valid_url.replace("sig=", "sig=tampered_hex_")
    res_tampered = client.get(tampered_url)
    assert res_tampered.status_code == 403
    assert "không hợp lệ" in str(res_tampered.data)


def test_r2_storage_presigned_url_generation(monkeypatch):
    """When R2 is configured, R2AttachmentStorage calls boto3 generate_presigned_url."""
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "dummy_key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "dummy_secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "assethub-bucket")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://fake.r2.cloudflarestorage.com")

    storage = AttachmentStorage()
    assert isinstance(storage, R2AttachmentStorage)

    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "https://fake.r2.cloudflarestorage.com/signed?sig=abc"
    storage.boto3_client = mock_client

    url = storage.get_presigned_download_url(
        name="assets/1/image/photo.jpg",
        expire_seconds=60,
        filename="photo.jpg",
        as_attachment=False,
    )
    assert url == "https://fake.r2.cloudflarestorage.com/signed?sig=abc"
    mock_client.generate_presigned_url.assert_called_once_with(
        'get_object',
        Params={
            'Bucket': 'assethub-bucket',
            'Key': 'assets/1/image/photo.jpg',
            'ResponseContentDisposition': 'inline',
        },
        ExpiresIn=60,
    )
