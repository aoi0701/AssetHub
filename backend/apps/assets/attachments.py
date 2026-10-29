import os
from PIL import Image
import pypdf
from rest_framework.exceptions import ValidationError

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MiB
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024  # 10 MiB

ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
ALLOWED_IMAGE_MIMES = {'image/jpeg', 'image/png', 'image/webp', 'image/x-png', 'image/pjpeg'}

ALLOWED_DOCUMENT_EXTENSIONS = {'.pdf'}
ALLOWED_DOCUMENT_MIMES = {'application/pdf', 'application/x-pdf'}


def validate_attachment(file_obj, kind: str):
    """
    Strict validation of file size, MIME type, extension, and binary content.
    Uses Pillow for images and pypdf for PDFs.
    Raises rest_framework.exceptions.ValidationError on any mismatch or corruption.
    """
    if kind not in ('image', 'document'):
        raise ValidationError(
            {"kind": f"Loại tệp không hợp lệ: '{kind}'. Chỉ chấp nhận 'image' hoặc 'document'."}
        )

    if not file_obj:
        raise ValidationError({"file": "Vui lòng chọn tệp để tải lên."})

    filename = getattr(file_obj, 'name', '') or ''
    _, ext = os.path.splitext(filename.lower())

    if kind == 'image':
        if file_obj.size > MAX_IMAGE_SIZE:
            raise ValidationError(
                {"file": f"Kích thước ảnh vượt quá giới hạn tối đa 5MB (kích thước hiện tại: {file_obj.size} bytes)."}
            )

        if ext and ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValidationError(
                {"file": f"Phần mở rộng tệp '{ext}' không được hỗ trợ. Chỉ chấp nhận .jpg, .jpeg, .png, .webp."}
            )

        content_type = getattr(file_obj, 'content_type', None)
        if content_type and content_type.lower() not in ALLOWED_IMAGE_MIMES:
            # Also check if it's octet-stream before outright rejecting
            if content_type.lower() != 'application/octet-stream':
                raise ValidationError(
                    {"file": f"MIME type '{content_type}' không hợp lệ cho ảnh. Chỉ chấp nhận JPEG, PNG, WEBP."}
                )

        try:
            file_obj.seek(0)
            img = Image.open(file_obj)
            img.verify()
            fmt = (img.format or '').upper()
            if fmt not in ALLOWED_IMAGE_FORMATS:
                raise ValidationError(
                    {"file": f"Định dạng ảnh '{fmt}' không được hỗ trợ. Chỉ chấp nhận JPEG, PNG, WEBP."}
                )
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                {"file": f"Tệp ảnh không hợp lệ hoặc bị lỗi nội dung: {str(e)}"}
            )
        finally:
            file_obj.seek(0)

    elif kind == 'document':
        if file_obj.size > MAX_DOCUMENT_SIZE:
            raise ValidationError(
                {"file": f"Kích thước tài liệu vượt quá giới hạn tối đa 10MB (kích thước hiện tại: {file_obj.size} bytes)."}
            )

        if ext and ext not in ALLOWED_DOCUMENT_EXTENSIONS:
            raise ValidationError(
                {"file": f"Phần mở rộng tệp '{ext}' không được hỗ trợ. Chỉ chấp nhận .pdf."}
            )

        content_type = getattr(file_obj, 'content_type', None)
        if content_type and content_type.lower() not in ALLOWED_DOCUMENT_MIMES:
            if content_type.lower() != 'application/octet-stream':
                raise ValidationError(
                    {"file": f"MIME type '{content_type}' không hợp lệ cho tài liệu. Chỉ chấp nhận PDF."}
                )

        try:
            file_obj.seek(0)
            header = file_obj.read(5)
            if header != b'%PDF-':
                raise ValidationError(
                    {"file": "Tệp không phải là định dạng PDF hợp lệ (thiếu header %PDF-)."}
                )
            file_obj.seek(0)
            reader = pypdf.PdfReader(file_obj)
            # Verify pages can be inspected without error
            _ = len(reader.pages)
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                {"file": f"Tệp PDF không hợp lệ hoặc bị lỗi cấu trúc: {str(e)}"}
            )
        finally:
            file_obj.seek(0)
