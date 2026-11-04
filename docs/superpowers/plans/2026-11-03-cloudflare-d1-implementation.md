# Kế Hoạch Triển Khai Chuyển Đổi Cloudflare D1 & Hệ Sinh Thái Edge AssetHub

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển đổi toàn bộ cơ sở dữ liệu vận hành chính của AssetHub từ PostgreSQL 16 sang Cloudflare D1 (SQLite tại Edge) trên tài khoản Cloudflare Free (`hnhoa494@gmail.com`), bảo toàn 100% logic Django 5.2/DRF, PBKDF2 1.000.000 vòng băm mật khẩu, SimpleJWT, frontend React, lưu trữ Cloudflare R2 và AI microservice, không làm gián đoạn hoặc mất mát dữ liệu.

**Architecture:** Kiến trúc lai (Hybrid Edge + Containerized Origin Engine). Django/DRF vận hành trong Linux Docker Container kết nối bảo mật ra Cloudflare Edge qua Cloudflare Tunnel (`cloudflared`). Giao tiếp với Cloudflare D1 thông qua Worker Bridge bảo mật cao (`assethub-test-worker`). Giao diện React SPA triển khai tĩnh trên Cloudflare Pages. Tệp tin đa phương tiện lưu trữ tại Cloudflare R2 (Private Bucket + Presigned URL 15 phút). Workers AI gợi ý danh mục tài sản kèm 3 tầng dự phòng (Heuristic Fallback). PostgreSQL được giữ nguyên làm nguồn đối chiếu dữ liệu và phương án phục hồi tức thì.

**Tech Stack:** Python 3.12, Django 5.2.18, Django REST Framework, SimpleJWT, SQLite 3.45 / Cloudflare D1, Cloudflare Workers (JavaScript V8 native), Cloudflare Pages, Cloudflare R2 (`boto3`, `django-storages`), Cloudflare Workers AI (`@cf/meta/llama-3.1-8b-instruct-fp8`), Cloudflare Tunnel (`cloudflared`), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-03-cloudflare-d1-architecture-design.md`

---

## Global Constraints

- **CSDL PostgreSQL gốc:** Tuyệt đối không xóa, không sửa, không đột biến dữ liệu trên PostgreSQL hiện tại; giữ nguyên vẹn 100% làm nguồn đối chiếu và phương án rollback.
- **Bảo mật mật khẩu:** Tuyệt đối giữ nguyên thuật toán `PBKDF2PasswordHasher` với **1.000.000 vòng lặp**; không hạ số vòng lặp.
- **Hợp đồng giao diện & API:** Giữ nguyên 100% cấu trúc giao diện React Vite, Tailwind CSS, API contracts và mã phản hồi nghiệp vụ.
- **Môi trường & Git:** Thực hiện trên một tab AGY duy nhất, nhánh `feat/cloudflare-d1`, **không tạo git worktree**, **không git push/merge** lên remote khi chưa có lệnh.
- **Chi phí Cloudflare:** 100% nằm trong gói Free (`Workers Free`, `D1 Free`, `Pages Free`, `R2 Free 10GB`, `Workers AI 10K Neurons/ngày`); không kích hoạt bất kỳ tính năng trả phí nào.
- **Bảo mật liên dịch vụ:** Mọi liên lạc giữa Django Container và Worker Bridge bắt buộc xác thực qua `X-Bridge-Token` bằng thuật toán so sánh hằng số thời gian `timingSafeEqual`.
- **Ranh giới cam kết Replay:** Không cam kết bảo toàn replay 100% cho các biểu thức SQL tùy ý phức tạp. Chỉ cam kết tại commit-time cho các thao tác INSERT lấy khóa chính `id` (`last_insert_rowid()`) và các mutation đơn giản; các trường hợp sập trước update ở batch hoặc multi-column returning phải trả về mã lỗi giao thức rõ ràng (`HTTP 500 INCOMPLETE_BATCH_STATE`), tuyệt đối không trả số liệu giả định.

---

## Chiến Lược Phục Hồi & Rollback Hai Giai Đoạn (Two-Stage Disaster Recovery)

Nhằm bảo đảm an toàn dữ liệu tuyệt đối và khả năng khôi phục tức thì khi xảy ra sự cố:

1. **Giai đoạn 1: Rollback Trước Cutover (Pre-Cutover Revert)**
   - **Tình huống kích hoạt:** Trong quá trình nạp dữ liệu hoặc đối soát 1:1 (`migrate_pg_to_d1`), nếu phát hiện sai lệch số liệu, lỗi hash PBKDF2, hoặc gián đoạn mạng không thể khắc phục.
   - **Hành động rollback:**
     1. Hủy bỏ phiên nạp D1; ngắt toàn bộ kết nối Django tới D1.
     2. Giữ nguyên vẹn 100% CSDL PostgreSQL gốc (`assethub-db-1`).
     3. Khôi phục cấu hình Django: `DATABASES['default']` trỏ về PostgreSQL (`django.db.backends.postgresql`).
     4. Tắt `MAINTENANCE_MODE=False`, ứng dụng tiếp tục vận hành bình thường trên PostgreSQL.
   - **Chỉ số an toàn:** **RPO = 0 giây** (không mất mát bất kỳ dữ liệu nào); **RTO < 5 phút**.

2. **Giai đoạn 2: Phục Hồi Thảm Họa Sau Cutover (Post-Cutover Disaster Recovery)**
   - **Tình huống kích hoạt:** Hệ thống đã chuyển đổi sang D1 làm CSDL chính và đã phát sinh giao dịch mới tại Edge, nhưng xảy ra sự cố logic nghiêm trọng hoặc hỏng dữ liệu.
   - **Hành động phục hồi:**
     1. **Cloudflare D1 Time Travel:** Phục hồi CSDL D1 về trạng thái điểm thời gian trước sự cố (hạn mức gói Free tối đa 7 ngày):
        ```bash
        npx wrangler d1 time-travel restore assethub-test-d1 --timestamp="2026-11-03T10:00:00Z"
        ```
     2. **Sao lưu định kỳ (Daily SQL Backup):** Tự động xuất bản dump D1 SQL hàng ngày lưu trữ an toàn trên Cloudflare R2:
        ```bash
        npx wrangler d1 export assethub-test-d1 --remote --output="./backups/d1-backup-$(date +%Y%m%d).sql"
        ```
     3. **Kiểm tra tính nhất quán:** Chạy script đối soát kiểm tra lại toàn vẹn dữ liệu sau khi khôi phục.

---

## Review Focus (5 Failure Modes Cốt Lõi)

1. **Quoted Identifiers do Django sinh trong RETURNING:** Regex phân tích của Worker Bridge phải nhận diện chính xác `RETURNING "id"` và `RETURNING "departments_department"."id"`. Nếu không, ID khóa chính bị rơi rụng thành `[]` khi Worker gặp sự cố sau commit.
2. **Số liệu giả định trong Batch Fallback:** Khi Worker sập trước `UPDATE _idempotency_log`, tuyệt đối không được trả về `meta.changes: 1` hoặc kết quả vỏ giả tạo. Replay phải trả `HTTP 500 INCOMPLETE_BATCH_STATE` với `committed: true`, ngăn chặn client tự ý retry làm double-write.
3. **Mất Admin hoạt động cuối cùng khi đồng thời cập nhật:** Hai request đồng thời hạ quyền hoặc vô hiệu hóa Admin phải bị chặn đứng bằng Database Triggers tại CSDL Edge (`Cannot demote or deactivate the last active Admin`).
4. **Ghi nhật ký kiểm toán mồ côi (Orphan Audit Logs) khi UPDATE 0 dòng:** Trong atomic batch, nếu câu lệnh cập nhật trạng thái có điều kiện không ảnh hưởng dòng nào (`changes() == 0`), bảng `_assert_guard` phải kích hoạt `CHECK(rows_affected > 0)` làm D1 abort toàn bộ batch transaction.
5. **Ghi đè trạng thái cũ & Tranh chấp truy cập (Stale Instance Overwrite & Concurrency Race):** Loại bỏ hoàn toàn giả định `refresh_from_db()` đủ chống race condition vì TOCTOU window vẫn tồn tại giữa refresh và save, và D1 không hỗ trợ `select_for_update()`. Các API PATCH (`AssetUpdateSerializer`, `ProfileUpdateSerializer`, `UserAdminUpdateSerializer`) chỉ được phép cập nhật danh sách trường cho phép (`update_fields`), tuyệt đối không lưu toàn bộ instance. Toàn bộ chuyển đổi trạng thái nghiệp vụ và bảo vệ dữ liệu phải sử dụng Optimistic Concurrency Control (OCC) / Conditional Writes (`WHERE id = ? AND status = ?`) kết hợp `_assert_guard` trong cùng atomic batch. Thao tác bảo mật tài khoản (khóa tài khoản, đổi vai trò, đổi mật khẩu) phải cập nhật nguyên tử `auth_version` bằng CAS (`WHERE id = ? AND auth_version = ?`). Sinh mã tuần tự dùng SQLite UPSERT RETURNING nguyên tử.

---

## Sơ Đồ Phụ Thuộc & Các Nhóm Task Song Song

```
┌────────────────────────────────────────────────────────────────────────┐
│  TASK 0: Implementation Blockers Resolution Gate (Chốt Chặn Cứng)      │
│  - Hỗ trợ Quoted Identifiers: RETURNING "id", "table"."id"             │
│  - Loại bỏ hoàn toàn Batch Fallback giả định số liệu (HTTP 500 Error)   │
│  - Kiểm chứng bằng SQL thực tế do Django ORM sinh                      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Chỉ mở khóa khi Task 0 đạt 100%)
                                    ▼
┌───────────────────────────────────┴────────────────────────────────────┐
│ NHÓM 1: TẦNG NỀN TẢNG CƠ SỞ DỮ LIỆU (Tuần tự tích hợp)                 │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Task 1: D1 Database Adapter & Client (assethub_api.backends.d1)  │  │
│  └──────────────────────────────────┬───────────────────────────────┘  │
│                                     ▼                                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Task 2: Schema DDL & Triggers Bảo Vệ (18 bảng, Check, Admin)     │  │
│  │ (DDL độc lập; kiểm thử pytest tích hợp phụ thuộc adapter Task 1) │  │
│  └──────────────────────────────────┬───────────────────────────────┘  │
└─────────────────────────────────────┼──────────────────────────────────┘
                                      │
                   ┌──────────────────┴──────────────────┐
                   ▼                                     ▼
┌──────────────────┴──────────────────┐┌─────────────────┴──────────────┐
│ NHÓM 2: DI CHUYỂN DỮ LIỆU & NGHIỆP  ││ NHÓM 3: HỆ SINH THÁI EDGE       │
│ VỤ BATCH (Tuần tự theo dữ liệu)     ││ (Có thể triển khai song song)   │
│                                     ││                                 │
│ ┌─────────────────────────────────┐ ││ ┌─────────────────────────────┐ │
│ │ Task 3: Lệnh Di Chuyển          │ ││ │ Task 5: Cloudflare R2       │ │
│ │ migrate_pg_to_d1 & Đối Soát 1:1 │ ││ │ Storage & Workers AI Proxy  │ │
│ └────────────────┬────────────────┘ ││ └──────────────┬──────────────┘ │
│                  ▼                  ││                │                │
│ ┌─────────────────────────────────┐ ││                │                │
│ │ Task 4: Chuyển Đổi Dịch Vụ      │ ││                │                │
│ │ Nghiệp Vụ sang D1 Atomic OCC    │ ││                │                │
│ └────────────────┬────────────────┘ ││                │                │
└──────────────────┼──────────────────┘└────────────────┼────────────────┘
                   │                                    │
                   └──────────────────┬─────────────────┘
                                      ▼
┌─────────────────────────────────────┴──────────────────────────────────┐
│ CAPSTONE GATE: TRIỂN KHAI HOÀN TẤT & KIỂM THỬ KHÓI E2E                 │
│                                                                        │
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │ Task 6: Cloudflare Pages Build, Ingress Tunnel & E2E Cloud Smoke   │ │
│ │ (Chốt chặn tuần tự cuối cùng — chỉ chạy khi Tasks 0..5 hoàn tất)   │ │
│ └────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Bảng Phân Quyền Tệp Tin (File Ownership Matrix)

Để tránh tuyệt đối việc các subagent sửa đè tệp tin của nhau khi làm việc:

| Tệp Tin / Thư Mục Phụ Trách | Task Sở Hữu Độc Quyền | Vai Trò & Trách Nhiệm |
| :--- | :--- | :--- |
| `spikes/cf-d1-spike/bridge-worker/src/index.js` | **Task 0** | Worker Bridge staging logic, token auth, commit idempotency |
| `backend/assethub_api/backends/d1/client.py` | **Task 0** | D1Client protocol exception mapping (`D1IncompleteBatchError`), non-retry on committed |
| `spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs` | **Task 0** | Node test runner regression test suite |
| `backend/assethub_api/backends/d1/tests/test_bridge_atomicity.py` | **Task 0** | Test atomicity, quoted RETURNING, client protocol & urlopen calls |
| `backend/assethub_api/backends/d1/base.py` | **Task 1** | DB-API 2.0 cursor, `replace_date_trunc_in_sql`, DatabaseWrapper |
| `backend/assethub_api/backends/d1/__init__.py` | **Task 1** | Export `d1_batch` public helper |
| `backend/assethub_api/backends/d1/tests/test_isolation_guard.py` | **Task 1** | Test URL guard RFC 3986 và chặn trước transport |
| `cloudflare-worker/bridge/schema.sql` | **Task 2** | Authoritative DDL 19 bảng an toàn (`IF NOT EXISTS`, không tự động DROP), triggers bảo vệ Admin, CHECK constraints |
| `cloudflare-worker/bridge/wrangler.toml` | **Task 2** | Cấu hình triển khai Worker Bridge sản xuất lên Cloudflare |
| `backend/assethub_api/backends/d1/sql/schema.sql` | **Task 2** | Bản sao đồng bộ DDL schema trong backend |
| `scripts/reset_d1_test_db.sh` & `.sql` | **Task 2** | Công cụ reset database test có guard `--confirm-destructive` và check target |
| `backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py`| **Task 2** | Test kiểm chứng 19 bảng, constraints, triggers và tính bất biến khi chạy lại |
| `backend/apps/common/management/commands/migrate_pg_to_d1.py` | **Task 3** | Lệnh di chuyển dữ liệu chunked 4-Tier và đối soát 1:1 |
| `backend/apps/common/tests/test_d1_migration.py` | **Task 3** | Test kiểm chứng lệnh di chuyển dữ liệu và đối soát |
| `scripts/sync_d1_schema.sh` | **Task 3** | Script đồng bộ và kiểm tra byte-level giữa 2 bản DDL schema |
| `backend/apps/assets/services.py` | **Task 4** | 9 phương thức nghiệp vụ tài sản chuyển sang D1 atomic batch OCC + `_assert_guard` |
| `backend/apps/requests/services.py` | **Task 4** | 2 phương thức nghiệp vụ phiếu yêu cầu chuyển sang D1 atomic batch OCC |
| `backend/apps/assets/serializers.py` | **Task 4** | `AssetUpdateSerializer` cập nhật allowlist `update_fields`, loại bỏ full `save()` |
| `backend/apps/authentication/serializers.py` | **Task 4** | `ProfileUpdateSerializer` cập nhật allowlist `full_name`, `email`, `phone`, guard `is_active` |
| `backend/apps/authentication/admin_api.py` | **Task 4** | `UserAdminUpdateSerializer` CAS bảo vệ quyền/khóa và tăng `auth_version` nguyên tử |
| `backend/apps/authentication/views.py` | **Task 4** | `ProfileView` và `ChangePasswordView` thực hiện CAS `auth_version` |
| `backend/apps/assets/identifiers.py` | **Task 4** | Sinh mã tuần tự `AST`/`REQ` nguyên tử bằng SQLite UPSERT RETURNING |
| `backend/apps/assets/tests/test_d1_business_integrity.py` | **Task 4** | Test OCC, `_assert_guard`, sinh mã, concurrency races, chống orphan log |
| `backend/apps/common/storage_backends.py` | **Task 5 (Modify)** | Sửa backend R2 S3 hiện tại, cấu hình 900s, bảo toàn 13 passing unit tests |
| `backend/apps/ai/services.py` | **Task 5** | AI Proxy service kết nối Workers AI kèm 3 tầng Fallback |
| `cloudflare-worker/ai/src/index.js` | **Task 5** | Worker AI Proxy service (Llama 3.1 8B Instruct FP8) |
| `backend/apps/assets/tests/test_r2_storage.py` | **Task 5** | Test R2 upload, presigned download và fallback AI |
| `frontend/public/_headers` & `frontend/public/_redirects` | **Task 6** | Cấu hình bảo mật CDN và SPA rewrite cho Pages |
| `frontend/vite.config.js` | **Task 6** | Cấu hình build Vite cho Cloudflare Pages |
| `deploy/cloudflared/tunnel-config.yml` | **Task 6** | Cấu hình ingress Cloudflare Tunnel an toàn |
| `docker-compose.yml` & `.env.example` | **Task 6** | Cấu hình biến môi trường D1, R2, AI và ingress |
| `backend/assethub_api/settings.py` | **Task 1 & 6** | Task 1 khai báo D1 DB engine; Task 6 bật `USE_D1_DATABASE=True`, CORS Pages regex, ALLOWED_HOSTS |
| `docs/deployment-guide.md` | **Task 6** | Tài liệu hóa ràng buộc máy trạm chạy Docker Tunnel |
| `backend/tests/e2e/test_d1_cloud_smoke.py` | **Task 6** | Kịch bản kiểm thử E2E Cloudflare toàn diện 4 tầng |

---

## Chi Tiết Các Gói Công Việc (Task-by-Task Implementation)

### Task 0: Implementation Blockers Resolution Gate (Chốt Chặn Cứng Toàn Tuyến)

> **Mục tiêu:** Xử lý triệt để 2 blocker kỹ thuật và bảo đảm giao thức toàn tuyến từ Worker tới Client:
> 1. Worker Bridge: Regex bóc tách `RETURNING` hỗ trợ quoted identifiers do Django ORM sinh (`"id"`, `"table"."id"`). Chỉ dùng `last_insert_rowid()` phục hồi INSERT đơn dòng/PK trong phạm vi đã xác nhận; không áp dụng cho UPDATE/DELETE RETURNING hoặc multi-column.
> 2. Worker Bridge: Loại bỏ hoàn toàn số liệu giả định (`changes: 1`, results rỗng) trong batch fallback khi Worker gặp sự cố trước UPDATE; trả về `HTTP 500` với `code: "INCOMPLETE_BATCH_STATE"` và `committed: true`.
> 3. D1Client Protocol & Exception Mapping: Bổ sung lớp biệt lệ `D1IncompleteBatchError` kế thừa `DatabaseError`. Khi nhận HTTP 500 với `code == 'INCOMPLETE_BATCH_STATE'` hoặc `committed: true`, client **tuyệt đối không tự ý retry** mutation và không nuốt thành lỗi rollback thông thường; bảo toàn `operation`, `idempotency_key`, `committed: true`, và `response_payload` để phục vụ đối soát.
> 4. Rõ ràng ngữ nghĩa Retry: Phân biệt rõ giữa (a) retry truy vấn đọc trạng thái cùng Idempotency-Key và (b) chạy lại nghiệp vụ bằng Idempotency-Key mới.
> 
> **Đường dẫn Worker Bridge:** Mã nguồn Worker Bridge đang vận hành và kiểm chứng kỹ thuật đặt tại thư mục chuẩn `spikes/cf-d1-spike/bridge-worker/` (chứa `src/index.js` và `wrangler.jsonc`). Thư mục này chịu trách nhiệm cho toàn bộ các bản sửa đổi, test Node, và lệnh deploy lên Cloudflare trong Task 0. (Thư mục `cloudflare-worker/bridge/` được tạo trong Task 2 để lưu trữ DDL `schema.sql` và cấu hình triển khai sản xuất).
> 
> **Điều kiện tiên quyết:** Không có. Task 0 là chốt chặn bắt buộc phải hoàn thành 100% trước khi bắt đầu Task 1, 2, 4.

**Files:**
- Modify: `spikes/cf-d1-spike/bridge-worker/src/index.js`
- Modify: `backend/assethub_api/backends/d1/client.py`
- Modify: `spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs`
- Modify: `backend/assethub_api/backends/d1/tests/test_bridge_atomicity.py`

**Interfaces:**
- Consumes: `env.DB`, `env.BRIDGE_SECRET_TOKEN`, raw SQL mutation requests.
- Produces: Worker bridge an toàn nhận diện quoted RETURNING và trả về lỗi giao thức tường minh (`HTTP 500 INCOMPLETE_BATCH_STATE`); `D1Client` xử lý biệt lệ `D1IncompleteBatchError` chặn double-write.

- [ ] **Step 1: Viết failing regression tests cho Quoted RETURNING, No-Fake Batch và D1Client Protocol**
  - Trong `spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs`:
    - `RETURNING "id"` (quoted) do Django sinh: sập sau commit, replay phải trả đúng ID.
    - `RETURNING "departments_department"."id"` (quoted & table-prefixed): replay phải trả đúng ID.
    - Batch crash before UPDATE: replay phải trả `HTTP 500` với `code: "INCOMPLETE_BATCH_STATE"` và `committed: true`, tuyệt đối không trả `changes: 1` giả tạo.
    - Câu lệnh ngoài phạm vi (UPDATE/DELETE RETURNING hoặc multi-column): không giả mạo kết quả phục hồi.
  - Trong `backend/assethub_api/backends/d1/tests/test_bridge_atomicity.py`:
    - Test `D1Client` với mock `urllib.request.urlopen`:
      - Khi nhận `HTTP 500` có `code == 'INCOMPLETE_BATCH_STATE'`, client ném `D1IncompleteBatchError`, số lần gọi `urlopen` chính xác là 1 (chặn auto-retry), bảo toàn `idempotency_key`, `committed=True`.
      - Khi nhận `HTTP 500` thông thường trước commit (không có `committed: true`), client thực hiện retry theo đúng chính sách an toàn.
      - Phân biệt replay cùng key nhận kết quả hoặc báo trạng thái với việc mutation bằng key mới.

- [ ] **Step 2: Chạy kiểm thử để xác nhận lỗi (Red)**
  ```bash
  node --test spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs
  docker compose exec backend pytest assethub_api/backends/d1/tests/test_bridge_atomicity.py -v -k "incomplete or returning"
  ```
  Mong đợi: FAIL ở các ca kiểm thử quoted identifiers, batch no-fake fallback và `D1IncompleteBatchError`.

- [ ] **Step 3: Cải tiến logic phân tích SQL trong Worker và Exception Mapping trong Client**
  - Trong `spikes/cf-d1-spike/bridge-worker/src/index.js`:
    - Cài đặt hàm `analyzeReturningClause(sql)` nhận diện identifier có dấu ngoặc kép:
      `const singleIdPattern = /^(?:(?:"?[a-zA-Z0-9_]+"?)?\.)?"?id"?$/i;`
    - Với batch: Thay thế mảng khởi tạo giả tạo bằng đối tượng giao thức chưa hoàn tất:
      `const initialPayload = JSON.stringify({ status: 'COMMITTED_PENDING_RESOLUTION', batch_size: statements.length });`
    - Trong nhánh replay của batch: Nếu `cachedObj.status === 'COMMITTED_PENDING_RESOLUTION'`, trả về `HTTP 500` với `{ error: "Incomplete batch state after commit", code: "INCOMPLETE_BATCH_STATE", committed: true }`.
    - Giữ nguyên hook `X-Test-Fault-Injection: crash-after-commit` đặt ngay sau commit và trước UPDATE.
  - Trong `backend/assethub_api/backends/d1/client.py`:
    - Định nghĩa lớp `D1IncompleteBatchError(DatabaseError)` chứa các trường: `code`, `committed`, `idempotency_key`, `payload`.
    - Trong `_post()`, khi gặp `urllib.error.HTTPError`: phân tích JSON body. Nếu `parsed.get("code") == "INCOMPLETE_BATCH_STATE"` hoặc `parsed.get("committed") is True`, lập tức ném `D1IncompleteBatchError`, không thực hiện vòng lặp retry.

- [ ] **Step 4: Chạy lại kiểm thử cục bộ và deploy lên Worker thật (Green)**
  ```bash
  node --test spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs
  cd spikes/cf-d1-spike/bridge-worker && npx wrangler deploy
  docker compose exec backend pytest assethub_api/backends/d1/tests/test_bridge_atomicity.py -v
  ```
  Mong đợi: PASS 100% trên cả Node mock runner và pytest tích hợp với Worker thật.

- [ ] **Step 5: Commit Task 0**
  ```bash
  git add spikes/cf-d1-spike/bridge-worker/src/index.js backend/assethub_api/backends/d1/client.py spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs backend/assethub_api/backends/d1/tests/test_bridge_atomicity.py .gitignore
  git commit -m "fix(bridge): complete end-to-end task 0 protocol for quoted returning and incomplete batch state"
  ```

---

### Task 1: D1 Database Backend Adapter & Client (`assethub_api.backends.d1`)

> **Mục tiêu:** Hoàn thiện adapter CSDL tùy biến cho Django kết nối Cloudflare D1 qua Worker Bridge, chuẩn DB-API 2.0, bảo vệ URL Guard RFC 3986, bảo toàn siêu dữ liệu ngoại lệ qua `D1DatabaseErrorWrapper`, và bảo toàn trọn vẹn thứ tự/tên cột trùng khi JOIN (`select_related`) bằng giao thức D1 `raw({ columnNames: true })`.
> 
> **Điều kiện tiên quyết:** Task 0 đã PASS 100%.

**Files:**
- Create: `backend/assethub_api/backends/d1/__init__.py`
- Modify: `backend/assethub_api/backends/d1/client.py`
- Modify: `backend/assethub_api/backends/d1/base.py`
- Modify: `spikes/cf-d1-spike/bridge-worker/src/index.js`
- Modify: `backend/assethub_api/backends/d1/tests/test_isolation_guard.py`
- Test: `backend/assethub_api/backends/d1/tests/test_d1_orm_integration.py`

**Interfaces:**
- Consumes: Cấu hình `DATABASES['d1']` hoặc `DATABASES['default']` với `ENGINE = 'assethub_api.backends.d1'`.
- Produces: `DatabaseWrapper`, `D1Cursor`, `D1Client`, `D1DatabaseErrorWrapper`, và hàm tiện ích `d1_batch(statements)`.

- [ ] **Step 1: Viết test cho chuyển đổi placeholders, types, date truncation và JOIN select_related**
  Tạo test trong `test_d1_orm_integration.py` kiểm tra:
  - Cursor description trả về 7-tuple chuẩn DB-API 2.0.
  - Boolean `True/False` chuyển thành `1/0`.
  - Date truncation `django_date_trunc` được viết lại thành `STRFTIME`.
  - `parse_and_normalize_endpoint` chặn đứng URL sai scheme, userinfo, hoặc port không hợp lệ trước khi gọi mạng.
  - Ngoại lệ `D1DatabaseErrorWrapper` bảo toàn 5 thuộc tính (`committed`, `code`, `idempotency_key`, `payload`, `raw_response`) khi qua cursor, `d1_batch` và ORM mutation.
  - ORM `select_related("category", "department", "assigned_to_user")` bảo toàn đúng giá trị từng model, không bị đè cột trùng tên `id` hay `name`.

- [ ] **Step 2: Chạy kiểm thử để xác nhận thất bại (Red)**
  ```bash
  docker compose exec backend pytest assethub_api/backends/d1/tests/test_isolation_guard.py assethub_api/backends/d1/tests/test_d1_orm_integration.py -v
  ```

- [ ] **Step 3: Hoàn thiện `base.py`, `client.py`, `index.js` và `__init__.py`**
  - Trong Worker Bridge `index.js`: Sử dụng `stmt.raw({ columnNames: true })` cho query để trả về `columns` và `rows` nguyên bản từ SQLite engine, bảo toàn thứ tự cột và tên trùng khi JOIN.
  - Trong `base.py`: Dựng `D1Cursor._rows` trực tiếp từ `rows` tuple thay vì object keys.
  - Cài đặt `D1DatabaseErrorWrapper` sao chép thuộc tính lỗi khi re-raise.
  - Khai báo cấu hình `D1_DATABASE_CONFIG` trong `backend/assethub_api/settings.py` (giữ cờ `USE_D1_DATABASE=False` ở giai đoạn này).
  - Cài đặt `replace_date_trunc_in_sql(sql)` thay thế các UDF của Django bằng SQLite native STRFTIME.
  - Export `d1_batch(statements, using='default')` trong `assethub_api/backends/d1/__init__.py`.
  - Đảm bảo `is_read_only_query()` chỉ cho phép `SELECT`, `EXPLAIN`, và các `PRAGMA` đọc đã duyệt.

- [ ] **Step 4: Chạy kiểm thử xác nhận thành công (Green)**
  ```bash
  docker compose exec backend pytest assethub_api/backends/d1/tests/ -v
  ```
  Mong đợi: Toàn bộ tests trong thư mục tests của D1 backend PASSED 100%.

- [ ] **Step 5: Commit Task 1**
  ```bash
  git add backend/assethub_api/backends/d1/ spikes/cf-d1-spike/bridge-worker/src/index.js
  git commit -m "feat(db): complete django d1 database backend adapter and client with db-api 2.0 compliance"
  ```

---

### Task 2: Schema DDL (19 Bảng An Toàn), Constraints & Triggers Bảo Vệ

> **Mục tiêu:** Xây dựng tệp DDL chính thức 19 bảng cho D1 (14 bảng ứng dụng/auth/system, 2 bảng hạ tầng `_assert_guard`/`_idempotency_log`, 1 bảng `ai_suggestion_logs`, 2 bảng hệ thống Django) bảo đảm tính idempotent re-entrant (`CREATE TABLE IF NOT EXISTS`, tuyệt đối không tự động DROP/xóa dữ liệu), cài đặt 2 Triggers bảo vệ Admin cuối cùng (UPDATE và DELETE), ràng buộc CHECK trạng thái tài sản (đúng 6 trạng thái nghiệp vụ chuẩn), tách công cụ reset database test riêng biệt có guard bảo mật, và đồng bộ 100% byte giữa 2 bản DDL.
> 
> **Điều kiện tiên quyết:** Task 0 đã PASS 100%. (Lưu ý: Việc biên soạn DDL có thể thực hiện trước, nhưng Step 4 chạy kiểm thử `test_d1_schema_constraints.py` qua pytest phụ thuộc vào adapter kết nối D1 từ Task 1).

**Files:**
- Create: `cloudflare-worker/bridge/schema.sql`
- Create: `cloudflare-worker/bridge/wrangler.toml`
- Create: `backend/assethub_api/backends/d1/sql/schema.sql`
- Create: `scripts/sync_d1_schema.sh`
- Create: `scripts/reset_d1_test_db.sh` & `scripts/reset_d1_test_db.sql`
- Create: `backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py`

**Interfaces:**
- Consumes: CSDL Cloudflare D1 `assethub-test-d1` qua `wrangler d1 execute`.
- Produces: 19 bảng hoàn chỉnh idempotent, 2 triggers `protect_last_admin_before_update` và `protect_last_admin_before_delete`, bảng `_assert_guard`, `_idempotency_log`, bảng `ai_suggestion_logs`, công cụ reset an toàn.

- [ ] **Step 1: Viết failing test kiểm tra schema constraints, admin triggers và re-entrant safety**
  Tạo `backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py`:
  - Test kiểm chứng ràng buộc CHECK trạng thái tài sản bắt buộc thuộc đúng 6 trạng thái chuẩn:
    `CHECK(status IN ('AVAILABLE', 'IN_USE', 'DAMAGED_PENDING_REPAIR', 'UNDER_MAINTENANCE', 'PENDING_DISPOSAL', 'DISPOSED'))`.
  - Test vi phạm trạng thái tài sản lạ (`'INVALID_STATUS'`) bị từ chối bởi CHECK constraint với `IntegrityError`.
  - Test tài sản `AVAILABLE` có người giữ (`assigned_to_user_id IS NOT NULL`) bị từ chối bởi CHECK constraint.
  - Test de-activate hoặc giáng quyền Admin duy nhất bị từ chối bởi trigger (`Cannot demote or deactivate the last active Admin`).
  - Test xóa Admin duy nhất bị từ chối bởi trigger (`Cannot delete the last active Admin`).
  - Test kiểm tra tính đồng nhất 100% từng byte giữa `cloudflare-worker/bridge/schema.sql` và `backend/assethub_api/backends/d1/sql/schema.sql`.
  - Test bảo toàn dữ liệu khi chạy lại schema (Re-entrant execution): chèn dữ liệu mẫu rồi chạy lại `schema.sql`, bảo đảm dữ liệu cũ không bị DROP hay xóa mất.
  - Test công cụ reset `reset_d1_test_db.sh` có guard: từ chối nếu thiếu `--confirm-destructive` hoặc truyền sai database target.

- [ ] **Step 2: Chạy kiểm thử để xác nhận thất bại (Red)**
  ```bash
  docker compose exec backend pytest assethub_api/backends/d1/tests/test_d1_schema_constraints.py -v
  ```
  Mong đợi: FAIL (chưa có tệp test hoặc schema chưa có đủ triggers/constraints).

- [ ] **Step 3: Biên soạn `schema.sql`, công cụ reset và đồng bộ**
  - Biên soạn `cloudflare-worker/bridge/schema.sql` với đủ 19 bảng sử dụng `CREATE TABLE IF NOT EXISTS`, loại bỏ hoàn toàn các lệnh `DROP TABLE`.
  - Tạo `scripts/reset_d1_test_db.sh` và `scripts/reset_d1_test_db.sql` phục vụ riêng việc dọn dẹp testbed có guard nghiêm ngặt.
  - Cấu hình `cloudflare-worker/bridge/wrangler.toml` (database_name = "assethub-test-d1", database_id = "3850e3a1-7c57-42f4-afd6-1fa1655bbcf1").
  - Đồng bộ bản sao tệp DDL vào `backend/assethub_api/backends/d1/sql/schema.sql` qua `scripts/sync_d1_schema.sh`.

- [ ] **Step 4: Chạy kiểm thử xác nhận thành công (Green)**
  ```bash
  docker compose exec backend pytest assethub_api/backends/d1/tests/test_d1_schema_constraints.py -v
  ```
  Mong đợi: PASS 100%. Các ràng buộc 6 trạng thái, trigger và tính bất biến an toàn hoạt động chính xác.

- [ ] **Step 5: Commit Task 2**
  ```bash
  git add cloudflare-worker/bridge/ backend/assethub_api/backends/d1/sql/ scripts/ backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py
  git commit -m "feat(schema): define official d1 schema with 19 tables, 6 asset statuses and admin protection triggers"
  ```

---

### Task 3: Công Cụ Di Chuyển Dữ Liệu & Đối Soát 1:1 (`migrate_pg_to_d1`)

> **Mục tiêu:** Hoàn thiện lệnh quản trị Django `migrate_pg_to_d1` trích xuất dữ liệu từ nguồn test/cloned dataset độc lập (tuyệt đối không đưa CSDL production lên cloud trong giai đoạn này), nạp lên D1 theo 4 Tier topo bảo toàn 100% khóa chính gốc, preflight kiểm tra schema nguồn (15 bảng bắt buộc) và schema đích (gồm bảng AI mới) trước mọi ghi, phân loại canonicalization theo đúng kiểu schema `(table, column)` không đoán kiểu từ chuỗi, xử lý batch SQLite atomic an toàn trong nested transaction bằng `transaction.atomic()` và loại bỏ mock-detection trong production, loại bỏ upsert ghi đè âm thầm, bảo toàn `sqlite_sequence` và đối soát 1:1 theo 7 tiêu chí kỹ thuật.
> 
> **Điều kiện tiên quyết:** Task 2 đã hoàn tất (D1 đã có cấu trúc DDL hoàn chỉnh gồm 19 bảng và triggers).

**Files:**
- Modify: `backend/apps/common/management/commands/migrate_pg_to_d1.py`
- Modify: `backend/apps/common/tests/test_d1_migration.py`
- Reference: `scripts/sync_d1_schema.sh`

**Interfaces:**
- Consumes: CSDL PostgreSQL `default` hoặc alias test chỉ định rõ, CSDL D1 `d1` hoặc SQLite testbed.
- Produces: Dữ liệu di chuyển lên CSDL đích, báo cáo đối soát 1:1 (Row count, Key set, Row content canonicalized theo schema, FKs, PBKDF2 verbatim, Datetime/bool, Identifier sequence counters theo `(kind, year)`).

- [ ] **Step 1: Viết failing regression tests cho 4 lỗi followup Task 3**
  Trong `backend/apps/common/tests/test_d1_migration.py`:
  - Test 1 (Preflight & Nguồn cũ không AI): Nguồn cũ không có `ai_suggestion_logs` di chuyển thành công sang đích có bảng AI rỗng hợp lệ; thiếu bất kỳ bảng/cột bắt buộc nào ở nguồn hoặc đích (kể cả thiếu cả hai phía) đều bị chặn ngay tại preflight trước mọi ghi.
  - Test 2 (Canonicalization theo kiểu schema table+column): Text trông giống JSON (như `assets_assethistorylog.reason`) hoặc text trông giống datetime (như `assets_asset.name`) bị phát hiện khác biệt khi thay đổi khoảng trắng/định dạng vì được so nguyên văn; JSON thực (`specification`), datetime thực (`created_at`) và bool thực mới canonicalize.
  - Test 3 (SQLite batch trong nested transaction & backend guard): Batch SQLite lồng trong `transaction.atomic` ngoài khi lỗi được rollback hoàn toàn (0 bản ghi sót lại); backend không hỗ trợ (không phải D1 hoặc SQLite testbed) bị từ chối; không có logic `unittest.mock` trong production.
  - Test 4 (Clean import & không upsert âm thầm): Không sử dụng `ON CONFLICT DO UPDATE`; đích có dữ liệu xung đột PK bị từ chối, không được ghi đè âm thầm password, role, auth_version, audit logs.

- [ ] **Step 2: Chạy kiểm thử để xác nhận lỗi (Red)**
  ```bash
  docker compose exec backend pytest apps/common/tests/test_d1_migration.py -v -k "ai_table or canonicalize_by_schema or nested_transaction or no_silent_upsert"
  ```
  Mong đợi: Các test regression mới FAIL trên mã nguồn hiện tại.

- [ ] **Step 3: Cải tiến `migrate_pg_to_d1.py`**
  - Tách bạch `SOURCE_MANDATORY_TABLES` (15 bảng) và `DESTINATION_NEW_TABLES` (`ai_suggestion_logs`).
  - Thêm hàm `run_preflight_schema_checks(conn_src, conn_tgt)` kiểm tra đầy đủ bảng và cột bắt buộc trước khi ghi bất kỳ byte nào.
  - Sửa `canonicalize_for_reconciliation`: nhận `table` và `column`, tra cứu theo bảng thuộc tính schema (`JSON_COLUMNS`, `DATETIME_COLUMNS`, `DATE_COLUMNS`, `BOOL_COLUMNS`, `PASSWORD_COLUMNS`). Cột text so sánh chuỗi nguyên văn.
  - Sửa `execute_batch_statements`: kiểm tra backend hợp lệ (`is_d1` hoặc `is_sqlite`), dùng `transaction.atomic(using=target_db)` cho SQLite để tự động hỗ trợ savepoints trong nested transaction; xóa bỏ hoàn toàn `from unittest.mock import Mock, MagicMock` và `is_mock`.
  - Sửa câu lệnh INSERT: dùng `INSERT INTO` chuẩn (clean import), loại bỏ `ON CONFLICT DO UPDATE`.

- [ ] **Step 4: Chạy kiểm thử xác nhận thành công (Green)**
  ```bash
  docker compose exec backend pytest apps/common/tests/test_d1_migration.py -v
  ```
  Mong đợi: PASS 100% tất cả các unit và regression tests.

- [x] **Step 5: Thẩm định chéo độc lập & Commit Task 3 Followup**
  - Reviewer độc lập và ponytail-review kiểm tra diff, bảo đảm an toàn dữ liệu và tuân thủ ranh giới.
  - Commit local riêng cho Task 3 Followup: `a16aa8d`
    `git commit -m "fix(migration): enforce schema-driven canonicalization, preflight validation and clean atomic import"`

---

### Task 3b: Diễn Tập Local End-to-End: PostgreSQL Test Độc Lập -> D1 Local Qua Worker Bridge

> **Mục tiêu:** Chứng minh `migrate_pg_to_d1` chạy thành công từ CSDL PostgreSQL test độc lập (schema tạo bằng Django migrations thật, dữ liệu giả) sang D1 local qua Worker bridge thật (`http://127.0.0.1:8787`) và Django adapter backend `assethub_api.backends.d1`. Kiểm chứng trọn vẹn kiểu dữ liệu (JSON scalar/object, Datetime aware/microseconds, PBKDF2 1,000,000 iterations, bool, date), quan hệ M2M, sequence counters và non-empty guard.

**Phân quyền và phối hợp:**
- **Agent A (Nguồn PostgreSQL Test):** Tạo container PostgreSQL `assethub-pg-rehearsal-<id>` trên port 5434 độc lập, migrate Django schema thật (15 bảng, không có bảng AI), seed dữ liệu giả đa dạng.
- **Agent B (Đích D1 Local & Worker Bridge):** Chạy `wrangler dev` với thư mục persist tạm, port 8787, nạp DDL chuẩn (16 bảng gồm `ai_suggestion_logs`), chứng minh đích rỗng.
- **Agent C (Kiểm thử & Reviewer Độc Lập):** Soạn kịch bản diễn tập, chạy xác nhận tiền kiểm, `--dry-run`, migration thật, đối soát 1:1, kiểm tra mật khẩu qua Django auth, kiểm tra non-empty guard, và chạy kịch bản đối chứng cố tình gây lỗi.
- **Coordinator:** Điều phối, cấu hình settings test, thực thi end-to-end, cleanup tài nguyên và commit.

- [x] **Step 1: Khởi động Agent A & Agent B song song (Dựng 2 đầu harness)**
- [x] **Step 2: Nạp schema & seed dữ liệu giả vào PostgreSQL test; nạp DDL vào D1 local**
- [x] **Step 3: Thực hiện kiểm thử tiền kiểm & `--dry-run` (0 ghi)**
- [x] **Step 4: Chạy migration thật từ PostgreSQL test sang D1 local (không `--allow-non-empty`)**
- [x] **Step 5: Đối soát 1:1, kiểm tra mật khẩu bằng Django auth, kiểm tra non-empty guard & negative tests**
- [x] **Step 6: Dọn dẹp tài nguyên diễn tập local (container/volume/persist dir tạm) & commit kết quả**
  - Commit 83f95c1: 11/11 bài diễn tập PG -> D1 local PASS.

---

### Task 3c: Chuẩn Hóa Lifecycle, Process Ownership & Gating Diễn Tập (P1/P2 Fixes)

> **Mục tiêu:** Khắc phục triệt để các rủi ro cleanup và lifecycle được Codex phát hiện trong review 83f95c1:
> 1. (P1) Gỡ bỏ hoàn toàn việc kill process theo port / lsof / pkill / fuser; không leo cây process cha; chỉ quản lý process Popen thuộc run context. Port bận thì fail trước khi thực hiện mutation.
> 2. (P1) Thư mục persist, env file và PID file cô lập theo run_id với marker ownership; cleanup kiểm tra marker trước khi xóa.
> 3. (P1) Test suite mặc định không tự tạo Docker/Worker; cấu hình `@pytest.mark.rehearsal` và flag opt-in rõ ràng (`--rehearsal` hoặc `ASSETHUB_RUN_REHEARSAL=1`). Khi opt-in, fixture tự động setup và teardown trọn vẹn cả PG và D1 trong `finally` / teardown hook kể cả khi assertion fail.
> 4. (P2) Nguồn PostgreSQL chỉ bind `127.0.0.1`, container gán docker label ownership, credentials ngẫu nhiên per-run không in log. Token D1 local ngẫu nhiên độc lập với cloud token, env file chmod 0600.
> 5. Thêm regression tests với mock/sentinel kiểm chứng an toàn process ownership và cleanup.

**Phân quyền file:**
- **Agent A:** `backend/apps/common/tests/harness_pg_source.py`, `backend/apps/common/tests/test_harness_pg_source.py`.
- **Agent B:** `backend/apps/common/tests/harness_d1_target.py`, `backend/apps/common/tests/test_harness_d1_target.py`.
- **Agent C:** `backend/apps/common/tests/test_pg_to_d1_rehearsal.py`, `backend/pytest.ini`, `conftest.py`.
- **Coordinator:** Điều phối, hợp nhất kế hoạch, chạy verify độc lập và ponytail review.

- [x] **Step 1: Agent B sửa P1 Process Ownership & Cleanup trong `harness_d1_target.py`**
- [x] **Step 2: Agent A sửa P2 Run ID, Labels, 127.0.0.1 Binding & Random Credentials trong `harness_pg_source.py`**
- [x] **Step 3: Agent C cấu hình Opt-in Gating, tự động Fixture Lifecycle & Mock Sentinel Tests**
- [x] **Step 4: Chạy kiểm thử an toàn ownership (sentinel servers không bị kill, cleanup chỉ xóa đúng marker)**
- [x] **Step 5: Chạy full rehearsal opt-in xác nhận 11 bài migration đạt với lifecycle tự động**
- [x] **Step 6: Thẩm định độc lập (Ponytail Review), dọn dẹp tài nguyên và commit**
  ```

---

### Task 4: Chuyển Đổi Dịch Vụ Nghiệp Vụ Sang D1 Atomic Batch & OCC Chống Cạnh Tranh

> **Mục tiêu:** Chuyển đổi toàn bộ 11 phương thức mutation nghiệp vụ (`AssetService` 9 phương thức, `RequestService` 2 phương thức) sang D1 Atomic Guarded Batch OCC, loại bỏ triệt để phụ thuộc `select_for_update()`, giải quyết triệt để race condition khi cập nhật PATCH bằng allowlist và CAS `auth_version`, sinh mã định danh nguyên tử bằng SQLite UPSERT RETURNING, và bảo vệ chống orphan audit log bằng `_assert_guard`.
> 
> **Điều kiện tiên quyết:** Task 0, Task 1, Task 2 đã hoàn tất.

**Files:**
- Modify: `backend/apps/assets/services.py`
- Modify: `backend/apps/requests/services.py`
- Modify: `backend/apps/assets/serializers.py`
- Modify: `backend/apps/authentication/serializers.py`
- Modify: `backend/apps/authentication/admin_api.py`
- Modify: `backend/apps/authentication/views.py`
- Modify: `backend/apps/assets/identifiers.py`
- Create: `backend/apps/assets/tests/test_d1_business_integrity.py`

**Interfaces:**
- Consumes: `d1_batch` từ `assethub_api.backends.d1`.
- Produces: 11 phương thức mutation an toàn concurrency, serializers dùng allowlist `update_fields`, CAS `auth_version`.

- [x] **Step 1: Viết failing regression tests cho Concurrency & Race Conditions**
  Tạo `backend/apps/assets/tests/test_d1_business_integrity.py`:
  - Test 1 (`test_concurrent_asset_allocation_race`): Hai request đồng thời gọi `AssetService.allocate` trên cùng tài sản `AVAILABLE` -> 1 thành công, 1 thất bại qua `_assert_guard`, không sinh orphan audit log.
  - Test 2 (`test_interleaved_asset_patch_and_disposal`): Request `PATCH /api/assets/{id}/` chạy xen giữa lúc tài sản bị chuyển trạng thái (`confirm_disposal` hoặc `allocate`) -> không làm đảo ngược trạng thái tài sản, chỉ cập nhật trường allowlist.
  - Test 3 (`test_profile_patch_vs_admin_deactivation_race`): Request `PATCH /api/auth/profile/` chạy xen giữa lúc Admin vô hiệu hóa tài khoản (`is_active=False`) -> tài khoản vẫn giữ `is_active=False`, không bị kích hoạt lại.
  - Test 4 (`test_change_password_auth_version_cas`): Request Admin cập nhật quyền/khóa tài khoản hoặc User đổi mật khẩu sử dụng CAS trên `auth_version` -> phát hiện xung đột và tăng `auth_version` nguyên tử.
  - Test 5: Sinh mã `generate_identifier('AST')` và `generate_identifier('REQ')` đồng thời không bị trùng lặp hoặc lỗi khóa qua SQLite UPSERT RETURNING.
  - Test 6: Kiểm tra đầy đủ 9 phương thức của `AssetService` (`allocate`, `revoke`, `mark_damaged`, `confirm_damage_report`, `send_maintenance`, `complete_maintenance`, `transfer`, `propose_disposal`, `confirm_disposal`) và 2 phương thức của `RequestService` (`approve_request`, `reject_request`).
  - Test 7: Từ chối phiếu (`reject_request`) đổi trạng thái `REJECTED`, cập nhật `rejection_reason` và không tạo bất kỳ bản ghi `AssetHistoryLog` nào.

- [x] **Step 2: Chạy kiểm thử để xác nhận thất bại (Red)**
  ```bash
  docker compose exec backend pytest apps/assets/tests/test_d1_business_integrity.py -v
  ```
  Mong đợi: FAIL (xác nhận lỗi khi chưa chuyển đổi sang D1 batch OCC).

- [x] **Step 3: Tái cấu trúc Services, Serializers và Views sang D1 Concurrency Guard**
  - Cập nhật `AssetUpdateSerializer`: Sử dụng `update_fields` hoặc `.filter(id=..., status!=DISPOSED).update(...)`. Loại bỏ hoàn toàn `instance.save()` cả cụm.
  - Cập nhật `ProfileUpdateSerializer`: Chỉ cập nhật `full_name`, `email`, `phone` với điều kiện `is_active=True`.
  - Cập nhật `UserAdminUpdateSerializer` & `ChangePasswordView`: Thực hiện cập nhật có điều kiện trên `auth_version` (`auth_version = auth_version + 1`).
  - Cập nhật `apps/assets/identifiers.py`: Thay thế vòng lặp `select_for_update` bằng câu lệnh SQLite UPSERT RETURNING nguyên tử (`ON CONFLICT(kind, year) DO UPDATE SET value = value + 1 RETURNING value`).
  - Tái cấu trúc 9 phương thức `AssetService` và 2 phương thức `RequestService` sử dụng `d1_batch` kèm `_assert_guard`.

- [x] **Step 4: Chạy kiểm thử xác nhận thành công (Green)**
  ```bash
  backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_business_integrity.py --rehearsal -v
  ```
  Kết quả: 8/8 PASS 100%. Concurrency và tính toàn vẹn nghiệp vụ được bảo đảm tuyệt đối.

- [x] **Step 5: Commit Task 4**
  ```bash
  git add backend/apps/assets/ backend/apps/requests/ backend/apps/authentication/ backend/assethub_api/backends/d1/ docs/
  git commit -m "feat(services): implement d1 atomic guarded batch occ for asset and request workflows"
  ```

---

### Task 5: Cloudflare R2 Storage & Workers AI Category Suggester

> **Mục tiêu:** Tùy biến `backend/apps/common/storage_backends.py` hiện có (bảo toàn 13 passing unit tests và local fallback), cấu hình Presigned URL 15 phút (900s), tích hợp Workers AI với model `@cf/meta/llama-3.1-8b-instruct-fp8` (fallback `@cf/meta/llama-3-8b-instruct`) kèm kiến trúc dự phòng 3 tầng (3-Tier Resilience Architecture) bảo đảm không làm gián đoạn UI khi cạn kiệt định mức 10.000 Neurons/ngày. Ranh giới nghiêm ngặt: kiểm thử mock/local song song; không checkout R2 trả phí, không đột biến bucket production, không sửa bản ghi DNS.
> 
> **Điều kiện tiên quyết:** Có thể thực hiện độc lập hoặc song song với Task 3 & 4.

**Files:**
- Modify: `backend/apps/common/storage_backends.py`
- Modify: `backend/apps/assets/views.py`
- Modify: `backend/apps/ai/services.py`
- Create: `cloudflare-worker/ai/src/index.js`
- Create: `backend/apps/assets/tests/test_r2_storage.py`

**Interfaces:**
- Consumes: Cấu hình R2 S3 credentials, binding `env.AI` trong Worker.
- Produces: Endpoint tải file có chữ ký SigV4, endpoint gợi ý danh mục AI kèm `is_fallback: True`.

- [ ] **Step 1: Viết failing test cho R2 Presigned URLs và AI 3-Tier Fallback**
  Trong `backend/apps/assets/tests/test_r2_storage.py`:
  - Test sinh presigned URL 15 phút (mặc định 900s, cho phép override tham số) với tham số `ResponseContentDisposition` tương ứng cho ảnh (`inline`) và tài liệu (`attachment`).
  - Test bảo toàn 13 tests hiện có trong `backend/tests/test_storage.py` (LocalDev fallback, validation Pillow/pypdf).
  - Test AI Proxy Service kích hoạt dự phòng 3 tầng:
    - **Tầng 1 (Circuit Breaker & Throttling):** Giới hạn tốc độ 10 requests/phút/user; ngắt tạm thời 30 giây khi gặp 3 lỗi mạng liên tiếp.
    - **Tầng 2 (Quota Exhaustion Trap):** Worker bắt lỗi `env.AI.run()` (code 10016 / limit exceeded) trả về `HTTP 429 QUOTA_EXHAUSTED`.
    - **Tầng 3 (Heuristic Keyword Matching):** Khi gặp 429 hoặc lỗi mạng, tự động phân tích từ khóa theo `CATEGORY_KEYWORDS` và trả về kết quả kèm `is_fallback: True`, không ném exception làm hỏng UI.

- [ ] **Step 2: Chạy kiểm thử để xác nhận thất bại (Red)**
  ```bash
  docker compose exec backend pytest apps/assets/tests/test_r2_storage.py -v
  ```
  Mong đợi: FAIL.

- [ ] **Step 3: Cập nhật `storage_backends.py` và `AiProxyService` với 3 Tầng Dự Phòng**
  - Cập nhật `backend/apps/common/storage_backends.py`: bổ sung `R2_PRESIGNED_EXPIRATION_SECONDS` (mặc định 900s), giữ nguyên `AttachmentStorage` factory và `LocalDevAttachmentStorage`.
  - Cập nhật action `download` trong `apps/assets/views.py` sinh URL có chữ ký qua `boto3`.
  - Cập nhật `AiProxyService` trong `apps/ai/services.py`: Cài đặt bộ đếm lỗi circuit breaker, bắt mã lỗi HTTP 429 từ Worker AI Proxy, và phân loại theo bảng từ khóa `CATEGORY_KEYWORDS`.
  - Tạo `cloudflare-worker/ai/src/index.js` sử dụng model `@cf/meta/llama-3.1-8b-instruct-fp8`, bọc `env.AI.run()` trong try/catch để chuyển đổi mã lỗi 10016 thành HTTP 429.

- [ ] **Step 4: Chạy kiểm thử xác nhận thành công (Green)**
  ```bash
  docker compose exec backend pytest apps/assets/tests/test_r2_storage.py tests/test_storage.py -v
  ```
  Mong đợi: PASS 100%.

- [ ] **Step 5: Commit Task 5**
  ```bash
  git add backend/apps/common/storage_backends.py backend/apps/assets/views.py backend/apps/ai/services.py cloudflare-worker/ai/ backend/apps/assets/tests/test_r2_storage.py
  git commit -m "feat(edge): integrate cloudflare r2 private presigned storage and workers ai with 3-tier heuristic fallback"
  ```

---

### Task 6: Cloudflare Pages Build, Ingress Tunnel (`cloudflared`) & E2E Verification

> **Mục tiêu:** Kích hoạt chuyển đổi runtime `USE_D1_DATABASE=True` (giữ `DATABASES['postgres']` làm alias đối soát chỉ đọc, tuyệt đối không silent fallback), cấu hình biến môi trường toàn diện trong `docker-compose.yml` và `.env.example`, siết chặt bảo mật CORS Pages domain cụ thể (`^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$`) và `ALLOWED_HOSTS`, cấu hình build tĩnh React SPA cho Cloudflare Pages (`_headers`, `_redirects`), cấu hình ingress Cloudflare Tunnel (`cloudflared`), tài liệu hóa ràng buộc vận hành và kiểm thử khói End-to-End toàn hệ thống 4 tầng.
> 
> **Điều kiện tiên quyết:** Chốt chặn tuần tự cuối cùng (Capstone Gate) — chỉ thực hiện khi toàn bộ Tasks 0, 1, 2, 3, 4, 5 đã hoàn tất 100%.

**Files:**
- Modify: `backend/assethub_api/settings.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Verify/Modify: `frontend/public/_headers`
- Verify/Modify: `frontend/public/_redirects`
- Modify: `frontend/vite.config.js`
- Create: `deploy/cloudflared/tunnel-config.yml`
- Modify: `docs/deployment-guide.md`
- Create: `backend/tests/e2e/test_d1_cloud_smoke.py`

**Interfaces:**
- Consumes: Bản build `frontend/dist`, Docker backend container port 8000.
- Produces: Ứng dụng chạy thực tế trên Cloudflare Pages kết nối backend qua Tunnel, vượt qua bộ smoke test E2E 4 tầng.

- [ ] **Step 1: Viết kịch bản kiểm thử E2E Smoke Test**
  Tạo `backend/tests/e2e/test_d1_cloud_smoke.py`:
  - Bước 1: Kiểm tra Worker Bridge health check (`/health`) trên `assethub-test-worker`.
  - Bước 2: Kiểm tra xác thực đăng nhập qua Tunnel (PBKDF2 1M vòng, SimpleJWT).
  - Bước 3: Kiểm tra CRUD tài sản và phiếu yêu cầu trên CSDL D1 thật (không silent fallback PostgreSQL).
  - Bước 4: Kiểm tra sinh Presigned URL tải file từ R2.
  - Bước 5: Kiểm tra gợi ý danh mục AI trên model `@cf/meta/llama-3.1-8b-instruct-fp8` (hoặc fallback khi cạn quota).
  - Bước 6: Kiểm tra bảo vệ Admin Trigger không cho phép xóa Admin cuối cùng.

- [ ] **Step 2: Chạy kiểm thử để xác nhận thất bại (Red)**
  ```bash
  docker compose exec backend pytest tests/e2e/test_d1_cloud_smoke.py -v
  ```
  Mong đợi: FAIL (chưa có cấu hình Pages, Tunnel hoặc runtime settings).

- [ ] **Step 3: Cấu hình Runtime D1, Cloudflare Pages, Cloudflare Tunnel và Settings**
  - Trong `backend/assethub_api/settings.py`:
    - Bật `USE_D1_DATABASE=True`: chuyển `DATABASES['default']` sang D1, giữ `DATABASES['postgres']` làm alias đối soát chỉ đọc.
    - Siết chặt `ALLOWED_HOSTS`: loại bỏ wildcard `*`, mặc định `localhost,127.0.0.1,backend`.
    - Siết chặt CORS regex: `r"^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$"`, từ chối `*.pages.dev` tùy tiện.
  - Trong `docker-compose.yml` và `.env.example`: tiêm đầy đủ các biến `D1_BRIDGE_URL`, `D1_BRIDGE_SECRET_TOKEN`, `R2_*`, `AI_*`.
  - Kiểm tra `frontend/public/_headers` (khai báo CSP, HSTS, X-Frame-Options) và `frontend/public/_redirects` (`/* /index.html 200`).
  - Chạy thử nghiệm build frontend: `cd frontend && npm run build` -> kiểm tra thư mục `dist/`.
  - Tạo cấu hình mẫu `deploy/cloudflared/tunnel-config.yml` chuyển tiếp HTTPS Ingress về `http://backend:8000`.
  - Bổ sung tài liệu vận hành trong `docs/deployment-guide.md`: Nêu rõ ràng buộc máy trạm chạy Docker container qua Tunnel phải luôn bật nguồn và kết nối mạng.

- [ ] **Step 4: Chạy kịch bản kiểm thử khói E2E (Green)**
  ```bash
  docker compose exec backend pytest tests/e2e/test_d1_cloud_smoke.py -v
  ```
  Mong đợi: PASS 100%. Toàn bộ 6 bước nghiệp vụ xuyên suốt hoạt động ổn định.

- [ ] **Step 5: Commit Task 6**
  ```bash
  git add frontend/public/ frontend/vite.config.js deploy/cloudflared/ backend/assethub_api/settings.py docker-compose.yml .env.example docs/deployment-guide.md backend/tests/e2e/test_d1_cloud_smoke.py
  git commit -m "chore(deploy): finalize cloudflare pages spa build, tunnel ingress config and e2e smoke verification"
  ```

---

## Tiêu Chí Nghiệm Thu Toàn Diện (End-of-Plan Verification Gate)

Trước khi nghiệm thu toàn bộ Tuần 4, hệ thống phải vượt qua toàn bộ 4 tầng kiểm thử:

1. **Tier 1: Unit & Protocol Gate (D1 Backend & Bridge):**
   - Node tests: `node --test spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs` (6 tests PASS 100%).
   - Pytest D1 backend: `docker compose exec backend pytest assethub_api/backends/d1/tests/ -v` (26 tests PASS 100%).
   - Zero fake batch data, nhận diện trọn vẹn quoted identifiers của Django ORM.
2. **Tier 2: Storage & AI Unit/Integration:**
   - Storage tests: `docker compose exec backend pytest tests/test_storage.py apps/assets/tests/test_r2_storage.py -v` (13+ tests PASS 100%).
   - AI proxy tests: `docker compose exec backend pytest apps/ai/tests/ -v` (PASS 100%).
3. **Tier 3: Core Business & RBAC trên D1:**
   - Nghiệp vụ tài sản & OCC: `docker compose exec backend pytest apps/assets/tests/ -v`.
   - Phiếu yêu cầu & quy trình duyệt: `docker compose exec backend pytest apps/requests/tests/ -v`.
   - Phân quyền & RBAC: `docker compose exec backend pytest apps/authentication/tests/ -v`.
   - Database Triggers bảo vệ Admin ngăn chặn tuyệt đối tình huống không còn Admin active; 6 trạng thái tài sản được bảo vệ bằng CHECK constraint.
4. **Tier 4: Frontend SPA Build & Live Cloud Smoke Verification:**
   - Frontend build: `npm --prefix frontend run build` (dist/ sinh thành công kèm `_headers`, `_redirects`, exit 0).
   - E2E Cloud smoke: `docker compose exec backend pytest tests/e2e/test_d1_cloud_smoke.py -v` chạy trên tài nguyên test đã duyệt (`assethub-test-worker`, `assethub-test-d1`).
   - Data Integrity & Rollback Readiness: Đối soát 1:1 xác nhận `COUNT(*)`, PKs, FKs, và chuỗi băm PBKDF2 khớp 100% với PostgreSQL; quy trình Rollback 2 giai đoạn (Pre-cutover PG instant revert và Post-cutover D1 Time Travel 7 ngày) được kiểm chứng sẵn sàng.
   - Zero Leakage & Zero Cost: PostgreSQL `default` giữ nguyên vẹn 100%; 100% tài nguyên chạy trong hạn mức Free Cloudflare.
   - Git Safety: 0 worktrees, 0 push/merge trái phép; toàn bộ commits tuân thủ Conventional Commits trên nhánh `feat/cloudflare-d1`.
