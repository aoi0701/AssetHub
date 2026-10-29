import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage
from django.http import FileResponse, Http404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from storages.backends.s3boto3 import S3Boto3Storage


def is_r2_configured():
    """
    Check if Cloudflare R2 / S3 credentials and endpoint are fully configured.
    """
    access_key = getattr(settings, 'R2_ACCESS_KEY_ID', None) or os.environ.get('R2_ACCESS_KEY_ID')
    secret_key = getattr(settings, 'R2_SECRET_ACCESS_KEY', None) or os.environ.get('R2_SECRET_ACCESS_KEY')
    bucket_name = getattr(settings, 'R2_BUCKET_NAME', None) or os.environ.get('R2_BUCKET_NAME')
    endpoint_url = getattr(settings, 'R2_ENDPOINT_URL', None) or os.environ.get('R2_ENDPOINT_URL')
    return bool(access_key and secret_key and bucket_name and endpoint_url)


def generate_local_presigned_url(name, expire_seconds=60, filename=None, as_attachment=False):
    """
    Generate an HMAC-signed local download URL expiring in expire_seconds.
    Points to /api/media/download/.
    """
    expires_at = int(time.time()) + int(expire_seconds)
    secret = str(getattr(settings, 'SECRET_KEY', 'assethub-secret')).encode('utf-8')
    raw = f"{name}:{expires_at}:{int(as_attachment)}:{filename or ''}"
    sig = hmac.new(secret, raw.encode('utf-8'), hashlib.sha256).hexdigest()
    params = {
        'path': name,
        'expires': str(expires_at),
        'sig': sig,
    }
    if filename:
        params['filename'] = filename
    if as_attachment:
        params['as_attachment'] = '1'
    return f"/api/media/download/?{urlencode(params)}"


def verify_local_presigned_url(name, expires_str, sig, filename=None, as_attachment=False):
    """
    Verify the timestamp and HMAC signature of a local signed download URL.
    """
    try:
        expires_at = int(expires_str)
    except (ValueError, TypeError):
        return False, "Invalid expires parameter"

    if time.time() > expires_at:
        return False, "URL has expired"

    secret = str(getattr(settings, 'SECRET_KEY', 'assethub-secret')).encode('utf-8')
    raw = f"{name}:{expires_at}:{int(as_attachment)}:{filename or ''}"
    expected_sig = hmac.new(secret, raw.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False, "Invalid signature"

    return True, None


class AttachmentStorage(Storage):
    """
    Unified AttachmentStorage interface.
    Dispatches to R2AttachmentStorage when R2 is configured,
    otherwise uses LocalDevAttachmentStorage.
    """
    def __new__(cls, *args, **kwargs):
        if cls is AttachmentStorage:
            if is_r2_configured():
                return R2AttachmentStorage(*args, **kwargs)
            return LocalDevAttachmentStorage(*args, **kwargs)
        return super().__new__(cls)

    def get_presigned_download_url(self, name, expire_seconds=60, filename=None, as_attachment=False):
        raise NotImplementedError


class LocalDevAttachmentStorage(FileSystemStorage, AttachmentStorage):
    """
    Local file storage for development and testing.
    Generates HMAC-signed presigned URLs pointing to /api/media/download/.
    """
    def __init__(self, location=None, base_url=None, *args, **kwargs):
        if location is None:
            location = getattr(settings, 'MEDIA_ROOT', None)
        super().__init__(location=location, base_url=base_url, *args, **kwargs)

    def get_presigned_download_url(self, name, expire_seconds=60, filename=None, as_attachment=False):
        return generate_local_presigned_url(
            name=name,
            expire_seconds=expire_seconds,
            filename=filename,
            as_attachment=as_attachment,
        )


class R2AttachmentStorage(S3Boto3Storage, AttachmentStorage):
    """
    Private storage backend using Cloudflare R2 / S3-compatible object storage.
    Enforces private ACL and generates presigned S3 URLs expiring in 60s.
    """
    default_acl = 'private'

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            'access_key',
            getattr(settings, 'R2_ACCESS_KEY_ID', None) or os.environ.get('R2_ACCESS_KEY_ID')
        )
        kwargs.setdefault(
            'secret_key',
            getattr(settings, 'R2_SECRET_ACCESS_KEY', None) or os.environ.get('R2_SECRET_ACCESS_KEY')
        )
        kwargs.setdefault(
            'bucket_name',
            getattr(settings, 'R2_BUCKET_NAME', None) or os.environ.get('R2_BUCKET_NAME')
        )
        kwargs.setdefault(
            'endpoint_url',
            getattr(settings, 'R2_ENDPOINT_URL', None) or os.environ.get('R2_ENDPOINT_URL')
        )
        kwargs.setdefault('default_acl', 'private')
        kwargs.setdefault('querystring_auth', True)
        super().__init__(*args, **kwargs)

    @property
    def boto3_client(self):
        if hasattr(self, '_boto3_client'):
            return self._boto3_client
        return getattr(self.connection, 'meta', None) and self.connection.meta.client

    @boto3_client.setter
    def boto3_client(self, value):
        self._boto3_client = value

    def get_presigned_download_url(self, name, expire_seconds=60, filename=None, as_attachment=False):
        fn = filename or os.path.basename(name)
        disposition = f'attachment; filename="{fn}"' if as_attachment else 'inline'
        params = {
            'Bucket': self.bucket_name,
            'Key': name,
            'ResponseContentDisposition': disposition,
        }
        client = self.boto3_client
        return client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=expire_seconds,
        )


class MediaDownloadView(APIView):
    """
    Public signed download endpoint for LocalDevAttachmentStorage.
    Validates HMAC signature and timestamp expiry (60s).
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        path = request.query_params.get('path')
        expires = request.query_params.get('expires')
        sig = request.query_params.get('sig')
        filename = request.query_params.get('filename')
        as_attachment = request.query_params.get('as_attachment') == '1'

        if not path or not expires or not sig:
            return Response(
                {"detail": "Thiếu tham số xác thực URL tải xuống."},
                status=status.HTTP_403_FORBIDDEN,
            )

        valid, err_msg = verify_local_presigned_url(
            name=path,
            expires_str=expires,
            sig=sig,
            filename=filename,
            as_attachment=as_attachment,
        )
        if not valid:
            return Response(
                {"detail": f"Liên kết tải xuống không hợp lệ hoặc đã hết hạn: {err_msg}"},
                status=status.HTTP_403_FORBIDDEN,
            )

        storage = LocalDevAttachmentStorage()
        if not storage.exists(path):
            raise Http404("Tệp không tồn tại.")

        file_obj = storage.open(path, 'rb')
        response = FileResponse(
            file_obj,
            as_attachment=as_attachment,
            filename=filename or os.path.basename(path),
        )
        response['Cache-Control'] = 'no-store'
        return response
