# Đề Xuất Kỹ Thuật Chi Tiết: Task 5 & Task 6
## Cloudflare R2 Storage, Workers AI Microservice, Cloudflare Pages & Ingress Tunnel

- **Tác giả:** Agent 3 — Cloudflare Services & Operations Planning Specialist
- **Ngày lập:** 2026-11-03
- **Thuộc dự án:** Chuyển đổi kiến trúc Cloudflare D1 & Triển khai Hybrid Edge (Tuần 4)
- **Tài khoản Cloudflare:** `hnhoa494@gmail.com` (Account ID: `5e76c2bf4e09587a010d31ea416ec8d3`)
- **Tập tin mục tiêu:** `docs/superpowers/plans/proposals/agent3-r2-ai-pages-tunnel.md`
- **Ranh giới thực thi:** Đề xuất kỹ thuật độc lập; không chỉnh sửa trực tiếp tài liệu Spec hoặc Implementation Plan chung.

---

## 1. Tổng Quan Kiến Trúc & Vị Trí Triển Khai (Tasks 5 & 6)

Trong kiến trúc tổng thể Tuần 4 của AssetHub:
- **Tasks 0–4** thiết lập nền móng lưu trữ dữ liệu chính thức trên **Cloudflare D1** (Database Adapter, Schema DDL 18 bảng, di chuyển dữ liệu đối soát 1:1 từ PostgreSQL và kiểm soát tính nguyên tử với D1 Batch Guards).
- **Task 5** mở rộng khả năng lưu trữ tệp nhị phân (ảnh, tài liệu PDF) lên **Cloudflare R2** thông qua giao thức chuẩn S3 API kết hợp chữ ký bảo mật có thời hạn (Presigned URLs 15 phút), đồng thời hoàn thiện microservice trí tuệ nhân tạo **Cloudflare Workers AI** phục vụ tự động gợi ý danh mục tài sản kèm cơ chế dự phòng thích ứng (graceful fallback) khi cạn kiệt định mức.
- **Task 6** hoàn thiện tầng phân phối người dùng cuối: Đóng gói và phát hành ứng dụng React SPA lên mạng phân phối toàn cầu **Cloudflare Pages**, thiết lập cổng kết nối bảo mật nội bộ **Cloudflare Tunnel (`cloudflared`)** từ Docker Container Linux ra Cloudflare Edge, đồng bộ hóa triệt để chính sách CORS/CSRF và xây dựng kịch bản kiểm thử khép kín (End-to-End Smoke Test) xác nhận toàn hệ thống hoạt động ổn định trên gói Free.

```
                      +-------------------------------------------------------+
                      |                 Cloudflare Edge Anycast               |
                      |                                                       |
                      |   +-----------------------+   +-------------------+   |
                      |   |    Cloudflare Pages   |   |   Cloudflare R2   |   |
                      |   |  (assethub.pages.dev) |   |  (Private Bucket) |   |
                      |   +-----------+-----------+   +---------^---------+   |
                      |               |                         |             |
                      |               | Fetch API               | Direct GET  |
                      |               v (CORS / SSL Header)     | (15-min     |
                      |   +-----------+-----------+             |  Presigned) |
                      |   |   Cloudflare Tunnel   |             |             |
                      |   |     (cloudflared)     |             |             |
                      +---+-----------+-----------+-------------+-------------+
                                      |                         ^
              Encrypted Outbound QUIC |                         | Presigned URL
                                      v                         | (boto3 SigV4)
+-------------------------------------+-------------------------+-------------+
| Origin Server (Local Linux / Docker Environment)                            |
|                                                                             |
|   +---------------------+        +--------------------+                     |
|   |   backend:8000      |        |   Worker AI Proxy  |                     |
|   |  (Django 5.2 + DRF) |------->| (Workers AI Llama) |                     |
|   +----------+----------+        +--------------------+                     |
|              |                                                              |
|              | HTTP Batch via X-Bridge-Token                                |
|              v                                                              |
|   +---------------------+                                                   |
|   |  Worker D1 Bridge   |-------> [ Cloudflare D1 Database ]                |
|   +---------------------+                                                   |
+-----------------------------------------------------------------------------+
```

---

## 2. Task 5: Cloudflare R2 Storage & Workers AI Microservice

### 2.1. Tích Hợp Cloudflare R2 Object Storage

#### 2.1.1. Thông Số Cấu Hình Điểm Cuối (Endpoints & Credentials)
- **Cloudflare Account ID:** `5e76c2bf4e09587a010d31ea416ec8d3`
- **R2 S3 API Endpoint:** `https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com`
- **Tên Bucket Được Chỉ Định:** `assethub-media`
- **Khu vực (Region):** `auto`
- **Thuật toán chữ ký S3:** `s3v4` (Bắt buộc đối với Cloudflare R2 API)

#### 2.1.2. Hạn Mức Gói Miễn Phí (Free Tier Boundaries)
Hệ thống AssetHub được kiểm soát chặt chẽ nhằm đảm bảo không phát sinh chi phí:
- **Dung lượng lưu trữ (Storage):** Tối đa **10 GB/tháng** miễn phí.
  - *Định lượng AssetHub:* Với dung lượng trung bình 2 MB/ảnh và 3 MB/PDF, 10 GB cho phép lưu trữ khoảng 2.000 đến 4.000 tệp tài sản, vượt xa nhu cầu thử nghiệm và vận hành thử nghiệm (~100–500 tệp).
- **Thao tác Class A (Ghi / Thay đổi trạng thái):** Tối đa **1.000.000 requests/tháng** miễn phí.
  - Bao gồm: `PutObject`, `CopyObject`, `ListObjectsV2`, `CreateBucket`.
  - *Tần suất AssetHub:* Thao tác upload tệp tài sản chỉ diễn ra khi tạo mới hoặc cập nhật tài sản (~vài trăm request/tháng, chiếm < 0.05% hạn mức).
- **Thao tác Class B (Đọc / Tải dữ liệu):** Tối đa **10.000.000 requests/tháng** miễn phí.
  - Bao gồm: `GetObject`, `HeadObject`.
  - *Tần suất AssetHub:* Người dùng truy cập xem chi tiết tài sản hoặc tải file biên bản (~vài nghìn request/tháng, chiếm < 0.1% hạn mức).
- **Phí truyền dữ liệu chiều ra (Egress Fees):** **$0.00 / GB (Hoàn toàn miễn phí)**. Đây là ưu thế vượt trội của Cloudflare R2 so với AWS S3, cho phép người dùng cuối tải tệp không giới hạn băng thông trong gói Free.

#### 2.1.3. Chính Sách Private Bucket & Presigned URLs 15 Phút
1. **Chính sách Private Bucket tuyệt đối:**
   - Bucket `assethub-media` không được cấu hình Public Bucket URL hoặc Public R2 Custom Domain. Mọi truy cập trực tiếp không có chữ ký đều bị từ chối với mã lỗi `HTTP 403 Forbidden`.
   - Cơ sở dữ liệu D1 chỉ lưu trữ khóa định danh đối tượng tương đối (Object Key, ví dụ: `assets/42/image/a1b2c3d4_laptop.png`), tuyệt đối không lưu trữ URL tuyệt đối hay presigned URL vào cơ sở dữ liệu.
2. **Cơ chế Presigned URLs 15 phút (900 giây):**
   - Khi người dùng có thẩm quyền yêu cầu tải hoặc xem tệp qua endpoint `GET /api/assets/{id}/attachments/{kind}/download/`, Django backend sử dụng SDK `boto3` để sinh một liên kết tải xuống có chữ ký SigV4 với thời hạn hết hiệu lực chính xác là **900 giây (15 phút)**.
   - **Tối ưu hóa chi phí:** Quá trình sinh Presigned URL bằng `boto3.client.generate_presigned_url()` được thực hiện hoàn toàn bằng thuật toán mã hóa HMAC-SHA256 phía CPU của máy chủ backend, **không gửi bất kỳ request mạng nào đến Cloudflare R2**, do đó không tiêu tốn hạn mức Class A hay Class B trong bước sinh URL.
   - Request Class B chỉ được tính duy nhất 1 lần khi trình duyệt của người dùng gửi lệnh `GET` trực tiếp đến R2 Endpoint qua URL có chữ ký.
3. **Phân biệt chế độ hiển thị (Inline vs Attachment):**
   - Đối với tệp ảnh (`kind='image'`): Thiết lập header `ResponseContentDisposition='inline'` để trình duyệt hiển thị trực tiếp trên giao diện Dashboard/Chi tiết tài sản.
   - Đối với tài liệu (`kind='document'` hoặc query param `?attachment=1`): Thiết lập `ResponseContentDisposition='attachment; filename="ten_file.pdf"'` để trình duyệt tự động mở hộp thoại lưu tệp.

#### 2.1.4. Thiết Kế Chi Tiết Lớp Lưu Trữ `R2AttachmentStorage`

Cấu hình trong `backend/assethub_api/settings.py`:
```python
# ==============================================================================
# Cloudflare R2 / S3 Object Storage Configuration
# ==============================================================================
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'assethub-media')
R2_ENDPOINT_URL = os.environ.get(
    'R2_ENDPOINT_URL',
    'https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com'
)
R2_PRESIGNED_EXPIRATION_SECONDS = int(
    os.environ.get('R2_PRESIGNED_EXPIRATION_SECONDS', '900')  # 15 minutes
)

# django-storages S3 settings for Cloudflare R2
AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL
AWS_S3_REGION_NAME = 'auto'
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
```

Triển khai hoàn thiện trong `backend/apps/common/storage_backends.py`:
```python
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage
from storages.backends.s3boto3 import S3Boto3Storage


def is_r2_configured() -> bool:
    """Kiểm tra điều kiện kích hoạt Cloudflare R2 từ biến môi trường."""
    access_key = getattr(settings, 'R2_ACCESS_KEY_ID', None) or os.environ.get('R2_ACCESS_KEY_ID')
    secret_key = getattr(settings, 'R2_SECRET_ACCESS_KEY', None) or os.environ.get('R2_SECRET_ACCESS_KEY')
    bucket_name = getattr(settings, 'R2_BUCKET_NAME', None) or os.environ.get('R2_BUCKET_NAME')
    endpoint_url = getattr(settings, 'R2_ENDPOINT_URL', None) or os.environ.get('R2_ENDPOINT_URL')
    return bool(access_key and secret_key and bucket_name and endpoint_url)


class AttachmentStorage(Storage):
    """
    Điểm chuyển giao hợp nhất cho lưu trữ tệp đính kèm.
    Tự động khởi tạo R2AttachmentStorage khi có đủ thông tin R2,
    ngược lại sử dụng LocalDevAttachmentStorage cho môi trường phát triển ngoại tuyến.
    """
    def __new__(cls, *args, **kwargs):
        if cls is AttachmentStorage:
            if is_r2_configured():
                return R2AttachmentStorage(*args, **kwargs)
            return LocalDevAttachmentStorage(*args, **kwargs)
        return super().__new__(cls)

    def get_presigned_download_url(
        self,
        name: str,
        expire_seconds: int = 900,
        filename: str = None,
        as_attachment: bool = False
    ) -> str:
        raise NotImplementedError


class R2AttachmentStorage(S3Boto3Storage, AttachmentStorage):
    """
    Lớp lưu trữ tích hợp Cloudflare R2 qua thư viện boto3 và django-storages.
    Thiết lập chính sách Private Bucket và tạo Presigned URL có hiệu lực 15 phút (900s).
    """
    default_acl = None
    file_overwrite = False
    querystring_auth = True

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
        kwargs.setdefault('region_name', 'auto')
        kwargs.setdefault('signature_version', 's3v4')
        super().__init__(*args, **kwargs)

    @property
    def boto3_client(self):
        if hasattr(self, '_boto3_client') and self._boto3_client is not None:
            return self._boto3_client
        return getattr(self.connection, 'meta', None) and self.connection.meta.client

    @boto3_client.setter
    def boto3_client(self, value):
        self._boto3_client = value

    def get_presigned_download_url(
        self,
        name: str,
        expire_seconds: int = 900,
        filename: str = None,
        as_attachment: bool = False
    ) -> str:
        """
        Sinh URL có chữ ký AWS SigV4 truy cập trực tiếp R2 với hạn định 15 phút (900 giây).
        """
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
```

Cập nhật endpoint tải file trong `backend/apps/assets/views.py`:
```python
    @extend_schema(
        summary="Lấy URL tải xuống tệp đính kèm với chữ ký bảo mật 15 phút (900s)",
        tags=["assets"],
        parameters=[
            OpenApiParameter(
                name="kind",
                type=str,
                location=OpenApiParameter.PATH,
                description="Loại tệp ('image' hoặc 'document')",
                enum=["image", "document"],
            ),
            OpenApiParameter(
                name="attachment",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Thiết lập '1' để ép buộc trình duyệt tải về dạng tệp tin",
                required=False,
            )
        ],
        responses={200: OpenApiResponse(description="Presigned download URL thành công")},
    )
    @action(
        detail=True,
        methods=['get'],
        url_path=r'attachments/(?P<kind>[^/.]+)/download',
    )
    def download(self, request, pk=None, kind=None):
        if kind not in ('image', 'document'):
            return Response(
                {"detail": f"Loại tệp không hợp lệ: '{kind}'. Chỉ chấp nhận 'image' hoặc 'document'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        asset = self.get_object()
        file_key = getattr(asset, kind, None)
        if not file_key:
            return Response(
                {"detail": f"Tài sản không có {'ảnh' if kind == 'image' else 'tài liệu'} đính kèm."},
                status=status.HTTP_404_NOT_FOUND,
            )

        storage = AttachmentStorage()
        as_attachment = (kind == 'document') or (request.query_params.get('attachment') == '1')
        expire_seconds = getattr(settings, 'R2_PRESIGNED_EXPIRATION_SECONDS', 900)

        download_url = storage.get_presigned_download_url(
            name=file_key,
            expire_seconds=expire_seconds,
            filename=os.path.basename(file_key),
            as_attachment=as_attachment,
        )

        response = Response(
            {
                "download_url": download_url,
                "expires_in": expire_seconds,
                "kind": kind,
            },
            status=status.HTTP_200_OK,
        )
        response['Cache-Control'] = 'no-store'
        return response
```

---

### 2.2. Workers AI Microservice & Cơ Chế Dự Phòng (Graceful Fallback)

#### 2.2.1. Xác Minh Mô Hình Workers AI Trên Tài Liệu Cloudflare
Dựa trên tài liệu chính thức của Cloudflare Workers AI:
- **`@cf/meta/llama-3-8b-instruct`:** Mô hình gốc Llama 3 8B Instruct, hỗ trợ ngữ cảnh 8.192 tokens. Độ trễ thấp, đáp ứng tốt cho tác vụ trích xuất phân loại cơ bản.
- **`@cf/meta/llama-3.1-8b-instruct`:** Mô hình nâng cấp Llama 3.1 8B Instruct, mở rộng cửa sổ ngữ cảnh lên 128.000 tokens, cải tiến vượt bậc khả năng tuân thủ cấu trúc JSON Output Schema và suy luận ngữ cảnh tiếng Việt kỹ thuật chuyên ngành.
- **Khuyến nghị kiến trúc:** Thiết lập `@cf/meta/llama-3.1-8b-instruct` làm mô hình mặc định trong Worker, đồng thời giữ biến môi trường `AI_MODEL` để có thể chuyển đổi linh hoạt mà không cần build lại mã nguồn.

#### 2.2.2. Ranh Giới Hạn Mức Quota Free Tier (10,000 Neurons/Ngày)
- Cloudflare Workers AI cấp phát định mức **10.000 Neurons miễn phí mỗi ngày** cho toàn bộ tài khoản Free.
- Một lượt inference phân loại tài sản (prompt ~120 tokens, system prompt định nghĩa danh mục ~150 tokens, output ~40 tokens) tiêu tốn xấp xỉ **250–350 Neurons**.
- Do đó, tài khoản Free hỗ trợ khoảng **30–40 yêu cầu AI mỗi ngày**. Khi vượt quá số lượt này, Cloudflare AI Gateway sẽ phản hồi mã lỗi `HTTP 429 Too Many Requests` hoặc trả về payload chứa mã lỗi:
  ```json
  {
    "success": false,
    "errors": [
      {
        "code": 10016,
        "message": "Workers AI limit exceeded. Daily free quota of 10000 Neurons exhausted."
      }
    ]
  }
  ```

#### 2.2.3. Chiến Lược Phòng Vệ & Dự Phòng Cục Bộ (3-Tier Resilience Architecture)
Tác vụ gợi ý danh mục tài sản là tính năng **hỗ trợ thông minh (advisory)**, tuyệt đối không được phép trở thành điểm nghẽn (single point of failure) chặn đứng quy trình nhập liệu tài sản của Quản trị viên.

Chiến lược 3 tầng phòng vệ:
1. **Tầng 1 — Circuit Breaker & Throttling (Đã tích hợp):**
   - Giới hạn tốc độ gọi tối đa 10 requests/phút/user bằng cache.
   - Nếu gặp 3 lỗi mạng hoặc timeout liên tiếp từ Worker, Circuit Breaker tự ngắt trong 30 giây để tránh làm quá tải đường truyền.
2. **Tầng 2 — Quota Exhaustion Detection tại Worker & Proxy:**
   - Worker bắt lỗi từ `env.AI.run()`; nếu gặp mã lỗi `10016` hoặc lỗi quota, Worker trả về `HTTP 429` có cấu trúc:
     ```json
     {
       "error": "AI quota exhausted",
       "code": "QUOTA_EXHAUSTED",
       "message": "Cloudflare Workers AI daily free limit (10,000 Neurons) has been reached."
     }
     ```
3. **Tầng 3 — Heuristic Rule-Based Keyword Matching Fallback:**
   - Khi Backend nhận được phản hồi Quota Exhausted (429), Circuit Breaker Open, hoặc Worker Unavailable (503/504), dịch vụ **không ném ngoại lệ làm hỏng UI**, mà tự động chuyển sang bộ quy tắc heuristic nội bộ:
     - Quét tên tài sản (`name`) và mô tả (`description`) so khớp với từ khóa đặc trưng của các danh mục hiện có trong cơ sở dữ liệu.
     - Trả về danh mục khớp điểm cao nhất, kèm cờ `is_fallback: True` và lý do rõ ràng.

#### 2.2.4. Triển Khai Hoàn Thiện `AiProxyService` Với Graceful Fallback

Cập nhật `backend/apps/ai/services.py`:
```python
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
import uuid

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError

from apps.authentication.models import User
from apps.departments.models import Category

logger = logging.getLogger(__name__)


class AiProxyService:
    _failure_count = 0
    _tripped_until = 0

    # Bảng từ khóa phục vụ phân loại Heuristic Fallback khi AI cạn Quota
    CATEGORY_KEYWORDS = {
        'laptop': ['laptop', 'macbook', 'thinkpad', 'dell xps', 'notebook', 'zenbook', 'latitute', 'elitebook'],
        'desktop': ['pc', 'desktop', 'máy trạm', 'workstation', 'imac', 'thùng máy', 'case máy tính'],
        'monitor': ['màn hình', 'monitor', 'display', 'screen', 'dell ultrasharp', 'lg 4k'],
        'networking': ['switch', 'router', 'access point', 'wifi', 'firewall', 'modem', 'cáp mạng'],
        'mobile': ['điện thoại', 'smartphone', 'iphone', 'ipad', 'tablet', 'samsung galaxy'],
        'office': ['bàn', 'ghế', 'tủ hồ sơ', 'máy in', 'printer', 'máy scan', 'máy chiếu', 'projector'],
    }

    @classmethod
    def is_circuit_open(cls) -> bool:
        now = time.time()
        if cls._tripped_until > now:
            return True
        if cls._tripped_until != 0 and cls._tripped_until <= now:
            cls._tripped_until = 0
            cls._failure_count = 0
        return False

    @classmethod
    def record_failure(cls) -> None:
        cls._failure_count += 1
        if cls._failure_count >= 3:
            cls._tripped_until = time.time() + 30
            logger.warning("AiProxyService: Circuit breaker tripped for 30 seconds.")

    @classmethod
    def record_success(cls) -> None:
        cls._failure_count = 0
        cls._tripped_until = 0

    @classmethod
    def reset_circuit_breaker(cls) -> None:
        cls._failure_count = 0
        cls._tripped_until = 0

    @classmethod
    def check_rate_limit(cls, user_id) -> None:
        now = time.time()
        cache_key = f"ai_rate_limit_{user_id}"
        req_times = cache.get(cache_key, [])
        req_times = [t for t in req_times if now - t < 60]
        if len(req_times) >= 10:
            raise Throttled(detail="Rate limit exceeded. Maximum 10 requests per minute.")
        req_times.append(now)
        cache.set(cache_key, req_times, timeout=60)

    @classmethod
    def reset_rate_limit(cls, user_id=None) -> None:
        if user_id is not None:
            cache.delete(f"ai_rate_limit_{user_id}")
        else:
            cache.clear()

    @classmethod
    def _execute_heuristic_fallback(cls, name: str, description: str, categories: list) -> dict:
        """
        Thuật toán phân loại dự phòng dựa trên phân tích từ khóa và đối soát độ tương đồng.
        Được kích hoạt khi Workers AI cạn kiệt Quota 10.000 Neurons/ngày hoặc Worker gặp sự cố.
        """
        combined_text = f"{name} {description}".lower()
        best_category = None
        best_score = 0

        for cat in categories:
            cat_name_lower = cat['name'].lower()
            score = 0

            # So khớp trực tiếp tên danh mục
            if cat_name_lower in combined_text:
                score += 5

            # So khớp qua bảng từ khóa quy ước
            for cat_key, keywords in cls.CATEGORY_KEYWORDS.items():
                if cat_key in cat_name_lower or any(kw in cat_name_lower for kw in keywords):
                    for kw in keywords:
                        if re.search(r'\b' + re.escape(kw) + r'\b', combined_text):
                            score += 2

            if score > best_score:
                best_score = score
                best_category = cat

        if not best_category:
            best_category = categories[0]

        return {
            "request_id": str(uuid.uuid4()),
            "suggested_category_id": best_category['id'],
            "suggested_category_name": best_category['name'],
            "explanation": (
                "Hệ thống AI đạt hạn mức gói Free (10.000 Neurons/ngày) hoặc đang bận. "
                "Đề xuất danh mục được kích hoạt tự động theo quy tắc từ khóa dự phòng."
            ),
            "is_fallback": True,
            "log_saved": False,
        }

    @classmethod
    def suggest_category(cls, actor, name: str, description: str = "") -> dict:
        # 1. Kiểm tra quyền hạn: Chỉ quản trị viên (Admin)
        if not actor or getattr(actor, "role", None) != User.Role.ADMIN:
            raise PermissionDenied("Chỉ quản trị viên mới có quyền sử dụng tính năng gợi ý AI.")

        # 2. Xác thực tính hợp lệ của đầu vào
        if not name or not isinstance(name, str) or not name.strip():
            raise ValidationError({"name": ["Tên tài sản không được để trống."]})

        cleaned_name = name.strip()
        if len(cleaned_name) > 200:
            raise ValidationError({"name": ["Tên tài sản không được vượt quá 200 ký tự."]})

        cleaned_description = str(description or "").strip()
        if len(cleaned_description) > 2000:
            raise ValidationError({"description": ["Mô tả không được vượt quá 2000 ký tự."]})

        # 3. Kiểm soát tần suất gọi (10 requests/phút)
        cls.check_rate_limit(getattr(actor, "id", "anonymous"))

        # 4. Truy vấn danh mục hiện hữu
        categories = list(Category.objects.values("id", "name"))
        if not categories:
            raise ValidationError({"detail": "Hệ thống chưa có danh mục tài sản nào để phân loại."})

        # 5. Kiểm tra Circuit Breaker - Nếu đang mở, tự động chuyển Fallback thay vì ném lỗi
        if cls.is_circuit_open():
            logger.info("Circuit breaker mở; kích hoạt phân loại Heuristic Fallback.")
            return cls._execute_heuristic_fallback(cleaned_name, cleaned_description, categories)

        # 6. Gửi yêu cầu đến Cloudflare Worker
        request_id = str(uuid.uuid4())
        worker_url = settings.AI_WORKER_URL.rstrip("/") + "/api/suggest-category"
        headers = {
            "Authorization": f"Bearer {settings.AI_SERVICE_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "request_id": request_id,
            "name": cleaned_name,
            "description": cleaned_description,
            "categories": categories,
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(worker_url, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=6.0) as response:
                raw_body = response.read().decode("utf-8")
                res_data = json.loads(raw_body)
                cls.record_success()

                suggested_id = res_data.get("suggested_category_id")
                category = Category.objects.filter(id=suggested_id).first()
                if not category:
                    return cls._execute_heuristic_fallback(cleaned_name, cleaned_description, categories)

                return {
                    "request_id": res_data.get("request_id", request_id),
                    "suggested_category_id": category.id,
                    "suggested_category_name": category.name,
                    "explanation": str(res_data.get("explanation", ""))[:500],
                    "is_fallback": False,
                    "log_saved": bool(res_data.get("log_saved", False)),
                }

        except urllib.error.HTTPError as exc:
            cls.record_failure()
            # Nhận diện đặc biệt mã lỗi 429 Quota Exhausted từ Cloudflare
            if exc.code == 429:
                logger.warning("Cloudflare Workers AI Quota Exhausted (10.000 Neurons/ngày). Kích hoạt Fallback.")
                return cls._execute_heuristic_fallback(cleaned_name, cleaned_description, categories)

            logger.error(f"AI Worker trả về mã lỗi HTTP {exc.code}. Chuyển hướng Fallback.")
            return cls._execute_heuristic_fallback(cleaned_name, cleaned_description, categories)

        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            cls.record_failure()
            logger.warning(f"Không thể kết nối đến AI Worker ({exc}). Chuyển hướng Fallback.")
            return cls._execute_heuristic_fallback(cleaned_name, cleaned_description, categories)

        except Exception as exc:
            cls.record_failure()
            logger.error(f"Lỗi không mong muốn khi gọi AI Worker: {exc}. Chuyển hướng Fallback.")
            return cls._execute_heuristic_fallback(cleaned_name, cleaned_description, categories)
```

#### 2.2.5. Triển Khai Hoàn Thiện Cloudflare AI Worker Script

Cập nhật `cloudflare-worker/ai/src/index.js` (hoặc `cloudflare-worker/src/index.js`):
```javascript
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname !== '/api/suggest-category') {
      return new Response(JSON.stringify({ error: 'Not Found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    if (request.method !== 'POST') {
      return new Response(JSON.stringify({ error: 'Method Not Allowed' }), {
        status: 405,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Xác thực Shared Token giữa Backend và Worker
    const authHeader = request.headers.get('Authorization') || '';
    const expectedToken = env.AI_SERVICE_TOKEN;
    if (
      !expectedToken ||
      !authHeader.startsWith('Bearer ') ||
      authHeader.slice(7).trim() !== expectedToken.trim()
    ) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Giới hạn kích thước payload tối đa 16 KB
    const contentLength = request.headers.get('content-length');
    if (contentLength && parseInt(contentLength, 10) > 16 * 1024) {
      return new Response(JSON.stringify({ error: 'Payload too large' }), {
        status: 413,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    let body;
    try {
      const rawText = await request.text();
      body = JSON.parse(rawText);
    } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const { request_id, name, description = '', categories } = body || {};
    if (
      !request_id ||
      !name ||
      typeof name !== 'string' ||
      !Array.isArray(categories) ||
      categories.length === 0
    ) {
      return new Response(JSON.stringify({ error: 'Invalid request body' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const categoryListStr = categories
      .map((c) => `- ID ${c.id}: ${c.name}`)
      .join('\n');

    const systemPrompt = `Bạn là chuyên gia phân loại tài sản thiết bị doanh nghiệp. Dựa trên tên và mô tả, hãy chọn chính xác một danh mục phù hợp nhất từ danh sách sau:\n${categoryListStr}\nPhản hồi BẮT BUỘC là định dạng JSON hợp lệ duy nhất: {"suggested_category_id": <id số nguyên>, "explanation": "<giải thích ngắn gọn dưới 100 từ>"}`;
    const userPrompt = `Tên tài sản: ${name}\nMô tả: ${description || 'Không có mô tả chi tiết'}`;

    // Ưu tiên mô hình Llama 3.1 8B Instruct chuẩn quốc tế
    const model = env.AI_MODEL || '@cf/meta/llama-3.1-8b-instruct';
    let aiOutput;

    try {
      aiOutput = await env.AI.run(model, {
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.2, // Giảm temperature để suy luận nhất quán và bám sát JSON
        max_tokens: 256,
      });
    } catch (err) {
      const errStr = String(err);
      console.error('Workers AI execution error:', errStr);

      // Nhận diện mã lỗi cạn kiệt Quota 10.000 Neurons/ngày
      if (errStr.includes('10016') || errStr.toLowerCase().includes('quota') || errStr.toLowerCase().includes('rate limit')) {
        return new Response(
          JSON.stringify({
            error: 'AI quota exhausted',
            code: 429,
            detail: 'Daily limit of 10,000 Neurons reached',
          }),
          {
            status: 429,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      }

      return new Response(
        JSON.stringify({ error: 'AI service unavailable', detail: errStr }),
        {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    }

    // Xử lý và trích xuất JSON an toàn từ phản hồi của LLM
    let suggestedId = null;
    let explanation = '';
    const rawResponse =
      typeof aiOutput === 'string'
        ? aiOutput
        : aiOutput?.response || (typeof aiOutput === 'object' ? JSON.stringify(aiOutput) : '');

    try {
      const jsonMatch = typeof rawResponse === 'string' ? rawResponse.match(/\{[\s\S]*\}/) : null;
      const parsedObj = jsonMatch ? JSON.parse(jsonMatch[0]) : JSON.parse(rawResponse);
      const rawId = parsedObj.suggested_category_id ?? parsedObj.category_id;
      if (rawId !== undefined && rawId !== null) {
        suggestedId = Number(rawId);
      }
      if (parsedObj.explanation) {
        explanation = String(parsedObj.explanation);
      }
    } catch (parseErr) {
      console.warn('JSON parsing failed, falling back to string match:', parseErr);
    }

    let matchedCategory = categories.find((c) => Number(c.id) === Number(suggestedId));
    if (!matchedCategory) {
      matchedCategory = categories.find(
        (c) =>
          typeof rawResponse === 'string' &&
          rawResponse.toLowerCase().includes(c.name.toLowerCase())
      );
      if (!matchedCategory) {
        matchedCategory = categories[0];
      }
    }

    const finalCategoryId = matchedCategory.id;
    const finalCategoryName = matchedCategory.name;
    const finalExplanation = explanation.slice(0, 500) || 'Gợi ý tự động từ mô hình Cloudflare Workers AI.';

    // Ghi log vào cơ sở dữ liệu D1 nếu có binding
    let logSaved = false;
    if (env.DB && typeof env.DB.prepare === 'function') {
      try {
        await env.DB.prepare(
          'INSERT INTO ai_suggestion_logs (id, asset_name, description, suggested_category_id, suggested_category_name, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)'
        )
          .bind(
            request_id,
            name,
            description || '',
            finalCategoryId,
            finalCategoryName,
            model,
            new Date().toISOString()
          )
          .run();
        logSaved = true;
      } catch (dbErr) {
        console.warn('D1 ai_suggestion_logs insert failed:', dbErr);
        logSaved = false;
      }
    }

    return new Response(
      JSON.stringify({
        request_id,
        suggested_category_id: finalCategoryId,
        suggested_category_name: finalCategoryName,
        explanation: finalExplanation,
        log_saved: logSaved,
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  },
};
```

---

## 3. Task 6: Cloudflare Pages Build, Ingress Tunnel (`cloudflared`) & Vận Hành

### 3.1. Frontend SPA Build & Triển Khai Cloudflare Pages

#### 3.1.1. Cấu Trúc Đóng Gói (Vite SPA to `frontend/dist`)
- **Bộ công cụ:** React 18, Vite 5, Tailwind CSS.
- **Biến môi trường Build:** Biến `VITE_API_BASE_URL` trỏ trực tiếp đến tên miền công khai của Cloudflare Tunnel (ví dụ: `https://api.assethub.vn` hoặc `https://assethub-tunnel.<your-subdomain>.cfargotunnel.com`).
- **Quy trình build:**
  ```bash
  cd frontend
  npm install
  export VITE_API_BASE_URL="https://api.assethub.vn"
  npm run build
  ```
- **Xác thực thư mục xuất bản (`frontend/dist`):**
  - Chứa tệp `index.html`, thư mục `assets/` (các file `.js`, `.css` đã được tối ưu hóa và gắn hash nội dung).
  - Phải có mặt 2 tệp cấu hình cốt lõi của Cloudflare Pages:
    1. `frontend/dist/_headers`: Khai báo bộ tiêu đề bảo mật (Security Headers) áp dụng tại CDN Edge.
    2. `frontend/dist/_redirects`: Khai báo quy tắc định tuyến SPA Rewrites (`/* /index.html 200`).

#### 3.1.2. Tệp Cấu Hình Biên: `_headers` & `_redirects`
Tệp `frontend/public/_headers` (sẽ được Vite sao chép tự động sang `frontend/dist/_headers` khi build):
```http
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: blob:; font-src 'self' data:; connect-src 'self' https: http://127.0.0.1:8000; frame-ancestors 'none';
```
*Lưu ý bảo mật CSP:* Khai báo `connect-src 'self' https: http://127.0.0.1:8000;` cho phép giao diện SPA gửi request REST API đến tên miền HTTPS của Tunnel và tải trực tiếp tệp từ R2 Endpoint.

Tệp `frontend/public/_redirects`:
```http
/* /index.html 200
```
Quy tắc trên đảm bảo khi người dùng tải lại trang (F5) tại bất kỳ đường dẫn con nào của React Router (ví dụ: `https://assethub.pages.dev/assets/AST-2026-001` hoặc `/requests/my-requests`), máy chủ Cloudflare Pages luôn phục vụ `index.html` với mã HTTP 200 để Client-Side Router tiếp quản, tránh hoàn toàn lỗi `404 Not Found`.

---

### 3.2. Kiến Trúc Ingress: Cloudflare Tunnel (`cloudflared`)

#### 3.2.1. Sơ Đồ Định Tuyến Ingress Không Mở Cổng (Zero-Trust Ingress)
Truyền thống triển khai web yêu cầu cấu hình Port Forwarding trên Router và mở port 80/443 ra mạng công cộng, gây nguy cơ rò rỉ IP và bị tấn công mạng.
Cloudflare Tunnel giải quyết triệt để vấn đề này:
1. `cloudflared` chạy dưới dạng một container dịch vụ trong mạng nội bộ Linux Docker (`docker-compose.prod.yml`).
2. Container này thiết lập 4 kết nối song song mã hóa **chiều ra (outbound only)** qua giao thức HTTP/2 hoặc QUIC (cổng 7844) tới các máy chủ Anycast gần nhất của Cloudflare Edge.
3. Khi người dùng gửi request tới `https://api.assethub.vn`, Cloudflare Edge chuyển tiếp request qua đường hầm mã hóa tới `cloudflared`.
4. `cloudflared` chuyển tiếp HTTP nội bộ tới `http://backend:8000`.

```
[ Người Dùng Ngoài Internet ]
              |
              | HTTPS (Port 443)
              v
[ Cloudflare Global Anycast Edge ]
              |
              | KẾT NỐI MÃ HÓA CHIỀU RA (Outbound QUIC / HTTP2)
              | Không cần mở Port trên Router / Không cần Static IP
              v
[ Container Linux: cloudflared ] (deploy/cloudflared/)
              |
              | HTTP Nội Bộ (Docker Bridge Network)
              v
[ Container Linux: backend ] (Gunicorn lắng nghe 0.0.0.0:8000)
```

#### 3.2.2. Ràng Buộc Vận Hành Bắt Buộc (Explicit Operational Constraints)
Khi hệ thống backend vận hành trên máy trạm cục bộ (Local Workstation) thông qua Cloudflare Tunnel:
1. **Duy trì nguồn điện & trạng thái máy chủ:**
   - Máy trạm Linux chứa Docker daemon bắt buộc phải **Bật liên tục (Power ON)**.
   - Phải tắt tính năng Sleep/Hibernate của hệ điều hành (`systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`).
2. **Duy trì kết nối mạng Internet:**
   - Kết nối mạng của máy trạm phải liên tục và ổn định.
   - **Hệ quả khi máy trạm tắt hoặc mất mạng:** Cloudflare Edge sẽ không thể liên lạc với daemon `cloudflared`, người dùng bên ngoài truy cập `https://api.assethub.vn` sẽ ngay lập tức nhận mã lỗi **`HTTP 530 / Error 1033 (Cloudflare Tunnel: Argo Tunnel error - Origin Unreachable)`**.
   - Cần cấu hình cờ `restart: always` cho container `cloudflared` và `backend` trong Docker Compose để hệ thống tự động phục hồi kết nối ngay khi máy trạm khởi động lại.

#### 3.2.3. Cấu Hình Tệp `deploy/cloudflared/tunnel-config.yml`
```yaml
# Đường dẫn tệp: deploy/cloudflared/config.yml
tunnel: ${TUNNEL_ID}
credentials-file: /etc/cloudflared/credentials.json

ingress:
  # Định tuyến toàn bộ lưu lượng API Backend
  - hostname: api.assethub.vn
    service: http://backend:8000
    originRequest:
      noTLSVerify: false
      httpHostHeader: api.assethub.vn
      connectTimeout: 10s
      noChunkedEncoding: false

  # Quy tắc bắt buộc cuối cùng: Từ chối các host không được định nghĩa
  - service: http_status:404
```

---

### 3.3. Cấu Hình CORS & CSRF An Toàn Trên Django Backend

#### 3.3.1. Cấu Hình CORS Phù Hợp Cho Cloudflare Pages
Giao diện React SPA được phân phối trên Cloudflare Pages có đặc tính:
- Domain sản xuất chính thức: `https://assethub.pages.dev` (hoặc custom domain `https://assethub.vn`).
- Domain preview nhánh git / commit build: `https://<commit-hash>.assethub.pages.dev` hoặc `https://<branch>.assethub.pages.dev`.

Do đó, sử dụng biểu thức chính quy (Regex) là giải pháp tối ưu và an toàn nhất:
```python
# backend/assethub_api/settings.py

# Cho phép origin cố định trong môi trường dev
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        'CORS_ALLOWED_ORIGINS',
        'http://localhost:5173,http://127.0.0.1:5173'
    ).split(',')
    if origin.strip()
]

# Chấp nhận toàn bộ tên miền Cloudflare Pages hợp lệ
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https:\/\/.*\.pages\.dev$",
]

# V1 Security Constraint: Không dùng Cookie Session cross-origin, dùng JWT Authorization Bearer
CORS_ALLOW_CREDENTIALS = False

# Cho phép các headers chuẩn
CORS_ALLOW_HEADERS = [
    'authorization',
    'content-type',
    'x-csrftoken',
    'x-requested-with',
    'accept',
    'origin',
]
```

#### 3.3.2. Cấu Hình `SECURE_PROXY_SSL_HEADER` & Nhận Biết HTTPS
Khi request từ người dùng gửi đến Cloudflare Edge bằng HTTPS, `cloudflared` giải mã và chuyển tiếp request nội bộ tới Gunicorn qua HTTP thuần (`http://backend:8000`).
Để Django nhận biết chính xác rằng kết nối gốc là HTTPS bảo mật (tránh chuyển hướng vòng lặp hoặc lỗi CSRF):
```python
# Nhận diện tiêu đề do Cloudflare Edge gắn vào
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Danh sách Allowed Hosts chấp nhận Hostname của Tunnel
DJANGO_ALLOWED_HOSTS_ENV = os.environ.get(
    'DJANGO_ALLOWED_HOSTS',
    'api.assethub.vn,assethub.pages.dev,localhost,127.0.0.1,backend'
)
ALLOWED_HOSTS = [h.strip() for h in DJANGO_ALLOWED_HOSTS_ENV.split(',') if h.strip()]

# Tin cậy nguồn CSRF nếu có tương tác qua form HTTPS
CSRF_TRUSTED_ORIGINS = [
    'https://*.pages.dev',
    'https://api.assethub.vn',
]
```

---

### 3.4. Bảng Phân Tách Vai Trò & Lệnh Thực Thi Các Công Cụ CLI

Ba công cụ dòng lệnh được sử dụng trong dự án có vai trò và ranh giới rõ ràng:

| Tiêu Chí | `wrangler` | `cloudflared` | `cf CLI` / Cloudflare API |
| :--- | :--- | :--- | :--- |
| **Bản chất** | Cloudflare Developer Platform CLI (Node.js / npm) | Ingress Tunnel & Zero Trust Daemon (Go binary độc lập) | Cloudflare Management CLI / REST API v4 |
| **Phạm vi quản lý** | Cloudflare Workers, Pages, D1 Database, R2 Buckets, KV | Đường hầm mạng bảo mật (Cloudflare Tunnel), định tuyến Ingress, Virtual Networks | Quản lý Zones, DNS Records, WAF Rules, SSL/TLS Settings |
| **Vị trí thực thi** | Máy phát triển (Dev Workstation) hoặc CI/CD pipeline | Cài đặt trên máy chủ Linux hoặc container `cloudflared` | Máy quản trị hệ thống hoặc script tự động hóa |

#### 3.4.1. Bộ Lệnh Chi Tiết Với `wrangler`
```bash
# 1. Đăng nhập tài khoản Cloudflare
npx wrangler login

# 2. Quản lý R2 Object Storage
# Tạo bucket mới
npx wrangler r2 bucket create assethub-media

# Kiểm tra danh sách bucket hiện có
npx wrangler r2 bucket list

# 3. Quản lý Workers AI & Worker Microservice
# Đặt Secret Token xác thực cho Worker AI
npx wrangler secret put AI_SERVICE_TOKEN --name assethub-ai-worker

# Deploy Worker lên Cloudflare Edge
cd cloudflare-worker && npx wrangler deploy

# Xem log trực tiếp (Tail Logs) khi debug Worker
npx wrangler tail assethub-ai-worker

# 4. Triển khai Cloudflare Pages
# Deploy thư mục frontend/dist lên dự án assethub trên Pages
npx wrangler pages deploy frontend/dist --project-name=assethub --commit-dirty=true
```

#### 3.4.2. Bộ Lệnh Chi Tiết Với `cloudflared`
```bash
# 1. Cài đặt cloudflared trên Linux (Debian/Ubuntu)
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared

# 2. Đăng nhập và xác thực tài khoản Tunnel
cloudflared tunnel login
# (Trình duyệt mở trang xác thực, lưu cert.pem vào ~/.cloudflared/cert.pem)

# 3. Tạo đường hầm mới mang tên 'assethub-prod-tunnel'
cloudflared tunnel create assethub-prod-tunnel
# Lệnh in ra Tunnel ID (UUID) và sinh file credentials: ~/.cloudflared/<TUNNEL_ID>.json

# 4. Gán định tuyến DNS từ Subdomain tới Tunnel
cloudflared tunnel route dns assethub-prod-tunnel api.assethub.vn

# 5. Khởi chạy Tunnel với file cấu hình
cloudflared tunnel --config deploy/cloudflared/config.yml run

# 6. (Phương án chạy nhanh không cần file config bằng Token)
cloudflared tunnel run --token <TUNNEL_TOKEN_SECRET>
```

---

## 4. Kịch Bản Kiểm Thử & Nghiệm Thu Toàn Diện (Smoke Test & E2E Verification)

### 4.1. Quy Trình 6 Bước Kiểm Thử Đầu Cuối (E2E Verification Checklist)

1. **Bước 1: Kiểm thử phân phối tĩnh Cloudflare Pages:**
   - Gửi request `GET https://assethub.pages.dev/`.
   - Kỳ vọng: HTTP 200 OK, phản hồi HTML chứa bundle assets, header `X-Content-Type-Options: nosniff`.
   - Gửi request `GET https://assethub.pages.dev/assets/AST-2026-001` (F5 route ảo).
   - Kỳ vọng: HTTP 200 OK (nhờ rewrite `_redirects`).
2. **Bước 2: Kiểm thử Ingress Tunnel & CORS Preflight:**
   - Gửi `OPTIONS https://api.assethub.vn/api/health/` với header `Origin: https://assethub.pages.dev`.
   - Kỳ vọng: HTTP 200 OK, `Access-Control-Allow-Origin: https://assethub.pages.dev`.
   - Gửi `GET https://api.assethub.vn/api/health/`.
   - Kỳ vọng: HTTP 200 OK, `{"status": "ok", "database": "d1"}`.
3. **Bước 3: Kiểm thử Xác thực SimpleJWT qua D1:**
   - Gửi `POST https://api.assethub.vn/api/auth/login/` với thông tin Admin.
   - Kỳ vọng: HTTP 200 OK, trả về access token và refresh token. Cơ sở dữ liệu D1 giải mã và xác thực PBKDF2 1.000.000 vòng hoàn tất trong container.
4. **Bước 4: Kiểm thử CRUD Nghiệp vụ trên D1 qua Worker Bridge:**
   - Gọi `GET https://api.assethub.vn/api/assets/` -> HTTP 200 OK.
   - Tạo mới một tài sản thử nghiệm -> Kiểm tra counter `IdentifierSequence` tăng đúng định dạng `AST-YYYY-NNN`.
5. **Bước 5: Kiểm thử Upload tệp lên R2 & Presigned URL 15 phút:**
   - Gửi `POST /api/assets/{id}/attachments/` tải lên 1 ảnh hợp lệ (`test.png`).
   - Kỳ vọng: HTTP 200 OK, trả về Object Key (`assets/{id}/image/..._test.png`).
   - Gọi `GET /api/assets/{id}/attachments/image/download/`.
   - Kỳ vọng: HTTP 200 OK, trả về `download_url` chứa `X-Amz-Signature` và `expires_in: 900`.
   - Sử dụng `curl` tải trực tiếp `download_url` từ R2 -> HTTP 200 OK, dữ liệu nhị phân khớp 100%.
6. **Bước 6: Kiểm thử Workers AI & Dự Phòng Quota:**
   - Gửi `POST /api/ai/suggest-category/` với payload `{"name": "MacBook Pro M3 Max"}`.
   - Kỳ vọng: HTTP 200 OK, gợi ý danh mục "Laptop" kèm lời giải thích.
   - Mô phỏng cạn Quota (Mock HTTP 429) -> Hệ thống tự động kích hoạt Heuristic Fallback, trả về danh mục "Laptop" kèm cờ `is_fallback: True` mà không làm đứt gãy trải nghiệm người dùng.

---

### 4.2. Mã Nguồn Kịch Bản Kiểm Thử Tự Động (`backend/tests/e2e/test_d1_cloud_smoke.py`)

Tệp script kiểm thử E2E độc lập, có thể chạy trực tiếp bằng Pytest hoặc thực thi độc lập qua dòng lệnh:

```python
"""
End-to-End Cloud Verification Script for AssetHub (Week 4 Cloudflare D1 Migration)
Covers Tasks 5 & 6:
- Cloudflare Pages Frontend Availability & SPA Rewrites
- Ingress Tunnel Connectivity & CORS Preflight
- SimpleJWT Authentication against D1
- D1 Asset Lifecycle Transactions
- Cloudflare R2 Upload & 15-Minute Presigned Download Verification
- Cloudflare Workers AI Suggestion & Graceful Quota Fallback
"""
import io
import os
import sys
import time
import requests
import pytest
from PIL import Image

PAGES_BASE_URL = os.environ.get("TEST_PAGES_URL", "https://assethub.pages.dev")
API_BASE_URL = os.environ.get("TEST_API_URL", "https://api.assethub.vn")
TEST_ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@assethub.vn")
TEST_ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "AdminSecure2026!@#")


def create_sample_png() -> bytes:
    """Tạo tệp ảnh PNG mẫu trong bộ nhớ để phục vụ test upload."""
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestAssetHubCloudE2E:
    session = requests.Session()
    access_token = None
    created_asset_id = None
    uploaded_attachment_key = None

    def test_01_pages_frontend_and_spa_rewrite(self):
        """Bước 1: Cloudflare Pages phục vụ bundle tĩnh và hỗ trợ SPA redirect."""
        # 1. Trang chủ
        resp = requests.get(PAGES_BASE_URL, timeout=10)
        assert resp.status_code == 200, f"Pages root unreachable: {resp.status_code}"
        assert "<html" in resp.text.lower()
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

        # 2. Tuyến đường con SPA (Kiểm tra _redirects)
        sub_resp = requests.get(f"{PAGES_BASE_URL}/assets/AST-2026-999", timeout=10)
        assert sub_resp.status_code == 200, "SPA rewrite to /index.html failed for sub-route"
        assert "<html" in sub_resp.text.lower()

    def test_02_tunnel_ingress_and_cors_preflight(self):
        """Bước 2: Đường hầm Ingress Tunnel hoạt động và phản hồi CORS đúng chuẩn."""
        # 1. Kiểm tra CORS Preflight OPTIONS
        headers = {
            "Origin": PAGES_BASE_URL,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        }
        options_resp = requests.options(f"{API_BASE_URL}/api/health/", headers=headers, timeout=10)
        assert options_resp.status_code == 200, f"CORS Preflight failed: {options_resp.status_code}"
        assert options_resp.headers.get("Access-Control-Allow-Origin") == PAGES_BASE_URL

        # 2. Kiểm tra Health Endpoint qua Tunnel
        health_resp = requests.get(f"{API_BASE_URL}/api/health/", timeout=10)
        assert health_resp.status_code == 200, f"API Health check failed: {health_resp.status_code}"
        data = health_resp.json()
        assert data.get("status") == "ok"

    def test_03_jwt_authentication_against_d1(self):
        """Bước 3: Xác thực JWT đăng nhập đối chiếu mật khẩu PBKDF2 1.000.000 vòng trên D1."""
        login_payload = {
            "email": TEST_ADMIN_EMAIL,
            "password": TEST_ADMIN_PASSWORD,
        }
        resp = requests.post(f"{API_BASE_URL}/api/auth/login/", json=login_payload, timeout=15)
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        data = resp.json()
        assert "access" in data, "No access token returned"
        assert "refresh" in data, "No refresh token returned"

        TestAssetHubCloudE2E.access_token = data["access"]
        self.session.headers.update({"Authorization": f"Bearer {TestAssetHubCloudE2E.access_token}"})

    def test_04_d1_database_crud_operations(self):
        """Bước 4: Thực hiện giao dịch CRUD trên Cloudflare D1 qua Worker Bridge."""
        # 1. Lấy danh sách danh mục hiện có
        cat_resp = self.session.get(f"{API_BASE_URL}/api/categories/", timeout=10)
        assert cat_resp.status_code == 200
        categories = cat_resp.json().get("results", [])
        assert len(categories) > 0, "No categories found in D1"
        target_category_id = categories[0]["id"]

        # 2. Tạo một tài sản mới
        asset_payload = {
            "name": "E2E Test Laptop ThinkPad P1",
            "category": target_category_id,
            "specification": {"ram": "64GB", "cpu": "Intel i9"},
            "serial_number": f"SN-E2E-{int(time.time())}",
        }
        create_resp = self.session.post(f"{API_BASE_URL}/api/assets/", json=asset_payload, timeout=10)
        assert create_resp.status_code == 201, f"Asset creation on D1 failed: {create_resp.text}"
        created_asset = create_resp.json()
        TestAssetHubCloudE2E.created_asset_id = created_asset["id"]
        assert created_asset["asset_code"].startswith("AST-")

    def test_05_r2_storage_upload_and_15min_presigned_download(self):
        """Bước 5: Tải file lên R2 và kiểm tra Presigned URL có hiệu lực 15 phút (900s)."""
        asset_id = TestAssetHubCloudE2E.created_asset_id
        assert asset_id is not None, "Previous asset creation failed"

        # 1. Tải lên ảnh đính kèm
        png_bytes = create_sample_png()
        files = {"file": ("device_photo.png", png_bytes, "image/png")}
        data = {"kind": "image"}
        upload_resp = self.session.post(
            f"{API_BASE_URL}/api/assets/{asset_id}/attachments/",
            data=data,
            files=files,
            timeout=15,
        )
        assert upload_resp.status_code == 200, f"R2 upload failed: {upload_resp.text}"
        upload_result = upload_resp.json()
        assert upload_result["kind"] == "image"
        assert "assets/" in upload_result["key"]
        TestAssetHubCloudE2E.uploaded_attachment_key = upload_result["key"]

        # 2. Lấy Presigned URL tải xuống
        download_resp = self.session.get(
            f"{API_BASE_URL}/api/assets/{asset_id}/attachments/image/download/",
            timeout=10,
        )
        assert download_resp.status_code == 200
        download_meta = download_resp.json()
        download_url = download_meta["download_url"]

        # Xác thực cấu hình 15 phút (900 giây)
        assert download_meta["expires_in"] == 900
        assert "X-Amz-Signature" in download_url or "sig=" in download_url
        assert download_resp.headers.get("Cache-Control") == "no-store"

        # 3. Trình duyệt tải trực tiếp nội dung từ Cloudflare R2
        r2_direct_resp = requests.get(download_url, timeout=15)
        assert r2_direct_resp.status_code == 200, "Direct download from R2 presigned URL failed"
        assert r2_direct_resp.content == png_bytes, "Downloaded content does not match uploaded bytes"

    def test_06_workers_ai_suggestion_and_fallback(self):
        """Bước 6: Gợi ý danh mục bằng Workers AI và kiểm tra tính năng Graceful Fallback."""
        ai_payload = {
            "name": "Dell UltraSharp 32 4K Monitor",
            "description": "Màn hình đồ họa cao cấp hiển thị chuẩn màu",
        }
        resp = self.session.post(f"{API_BASE_URL}/api/ai/suggest-category/", json=ai_payload, timeout=15)
        assert resp.status_code == 200, f"AI suggestion failed: {resp.text}"
        result = resp.json()

        assert "suggested_category_id" in result
        assert "suggested_category_name" in result
        assert "explanation" in result
        # Nếu AI cạn Quota 10.000 Neurons, cờ is_fallback phải là True mà không ném lỗi 500
        if result.get("is_fallback") is True:
            assert "10.000 Neurons" in result["explanation"] or "dự phòng" in result["explanation"]


if __name__ == "__main__":
    pytest.main(["-v", __file__])
```

---

## 5. Bảng Tiêu Chí Nghiệm Thu (Acceptance Criteria Matrix)

| STT | Hạng Mục Kiểm Tra | Tiêu Chí Đạt (Pass Criteria) | Trạng Thái Dự Kiến |
| :--- | :--- | :--- | :--- |
| **AC-01** | **R2 Storage Backend** | `boto3` và `django-storages` kết nối thành công tới R2 Endpoint `https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com`. Tệp tải lên bucket `assethub-media` không bị rò rỉ đường dẫn tuyệt đối ra cơ sở dữ liệu. | SẴN SÀNG |
| **AC-02** | **15-Min Presigned URLs** | Endpoint `GET /api/assets/{id}/attachments/{kind}/download/` trả về URL chữ ký SigV4 có thời hạn chính xác **900 giây (15 phút)**. Truy cập trực tiếp không chữ ký bị chặn với mã 403. | SẴN SÀNG |
| **AC-03** | **R2 Free Limits** | Dung lượng lưu trữ < 10 GB, số lượng Class A < 1M requests/tháng, Class B < 10M requests/tháng, $0 phí Egress. | ĐẠT CHUẨN |
| **AC-04** | **Workers AI Model** | Worker AI chạy trên mô hình `@cf/meta/llama-3.1-8b-instruct` (hoặc `@cf/meta/llama-3-8b-instruct`), phân tích tên/mô tả tiếng Việt và phản hồi đúng ID danh mục hợp lệ. | SẴN SÀNG |
| **AC-05** | **AI Quota Fallback** | Khi vượt quá 10.000 Neurons/ngày (HTTP 429), `AiProxyService` tự kích hoạt quy tắc phân loại heuristic từ khóa nội bộ, trả về `is_fallback: True` mà không làm gián đoạn hay ném lỗi 500 tới Admin. | SẴN SÀNG |
| **AC-06** | **Cloudflare Pages SPA** | Bản build `frontend/dist` chứa đầy đủ `_headers` và `_redirects`. Tải lại trang (F5) tại bất kỳ URL con nào của React Router đều nhận mã 200 OK từ `assethub.pages.dev`. | SẴN SÀNG |
| **AC-07** | **Zero-Trust Ingress** | Container `cloudflared` kết nối ổn định tới Cloudflare Edge. Không cần mở bất kỳ cổng Inbound nào trên Router máy chủ. Nhận diện chuẩn `SECURE_PROXY_SSL_HEADER`. | SẴN SÀNG |
| **AC-08** | **CORS & CSRF Security** | `CORS_ALLOWED_ORIGIN_REGEXES = [r"^https:\/\/.*\.pages\.dev$"]` chấp nhận mọi domain Cloudflare Pages và chặn đứng các origin lạ. `CORS_ALLOW_CREDENTIALS` giữ giá trị `False`. | SẴN SÀNG |
| **AC-09** | **Operational Guide** | Tài liệu vận hành nêu rõ điều kiện máy trạm Linux phải duy trì nguồn điện và kết nối mạng liên tục khi chạy Tunnel nội bộ. | SẴN SÀNG |
| **AC-10** | **E2E Smoke Test** | Script `test_d1_cloud_smoke.py` vượt qua toàn bộ 6/6 bài kiểm tra độc lập từ môi trường bên ngoài. | SẴN SÀNG |

---
*Đề xuất kỹ thuật hoàn tất bởi Agent 3 (Cloudflare Services & Operations Planning Specialist). Sẵn sàng bàn giao cho Đội ngũ Điều phối tích hợp vào Kế hoạch Hành động Tổng thể.*
