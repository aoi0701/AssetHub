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
| `cloudflare-worker/src/index.js` | **Task 5** | Worker AI Proxy service (Llama 3.1 8B Instruct FP8) & DB Logging |
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

- [x] **Step 5: Commit Task 4 Baseline**
  ```bash
  git add backend/apps/assets/ backend/apps/requests/ backend/apps/authentication/ backend/assethub_api/backends/d1/ docs/
  git commit -m "feat(services): implement d1 atomic guarded batch occ for asset and request workflows"
  ```

- [x] **Step 6: Sửa các lỗi hồi quy Task 4 theo review 458c3a5**
  - [x] Agent A: Giữ holder trong `confirm_damage_report`; sửa predicate nullable department ở `propose_disposal` và `confirm_disposal` cho kho chung (`department_id IS NULL`).
  - [x] Agent B: Bổ sung guard requester active/cùng phòng cho cả RETURN và DAMAGE_REPORT; bổ sung guard category của asset khớp requested_category tại write trong batch ALLOCATE.
  - [x] Agent C: Bỏ hack sửa state giữa workflow trong `test_d1_business_integrity.py`; test disposal kho chung; test interleaving requester/category; test rollback thực của `approve_request` qua D1 trigger test-only.

- [x] **Step 7: Verification và Commit Task 4 Regressions**
  - Chạy đầy đủ: unit mock, PG test baseline, và D1 local integration. Codex đã nghiệm thu độc lập commit `bd1fcd2` (21/21 D1 integration tests 44.25s, 250/250 backend + domain unit tests 73.90s).
  - Commit: `bd1fcd2` `fix(services): retain holder on damage confirm, support pool disposal, and guard write-time requester and category`.

---

### Task 5: Cloudflare R2 Storage & Workers AI Category Suggester (Local/Mock First)

> **Mục tiêu:** Mở rộng `backend/apps/common/storage_backends.py` hiện có, cấu hình Presigned URL 15 phút (900s qua `R2_PRESIGNED_URL_EXPIRE_SECONDS`), `expires_in` metadata trả về khớp TTL thực; xử lý an toàn upload/replace trên D1 bằng conditional write `exclude(status=DISPOSED)` + OCC, không xóa file cũ trước khi DB commit thành công, dọn file mới khi DB thất bại rõ ràng và không xóa mù khi `D1IncompleteBatchError(committed=True)`. Tích hợp Workers AI tại `cloudflare-worker/src/index.js` (dùng model `@cf/meta/llama-3.1-8b-instruct-fp8`, phân biệt 3036 Quota và 3040 Capacity theo tài liệu Cloudflare chính thức, ghi log `ai_suggestion_logs`), kèm backend `AiProxyService` có heuristic keyword fallback (chỉ chọn category ID tồn tại trong DB, không chọn bừa `categories[0]`, trả `is_fallback: True/False`).
> Ranh giới nghiêm ngặt: Kiểm thử local/mock trước; không checkout R2 trả phí, không gọi AI cloud tiêu quota, không sửa DNS/Pages domain.
> 
> **Điều kiện tiên quyết:** Task 4 đã được Codex nghiệm thu.

**Files:**
- Modify: `backend/assethub_api/settings.py` & `.env.example`
- Modify: `backend/apps/common/storage_backends.py`
- Modify: `backend/apps/assets/views.py`
- Create/Modify: `backend/apps/assets/tests/test_r2_storage.py` & `backend/tests/test_storage.py`
- Modify: `backend/apps/ai/services.py`, `backend/apps/ai/serializers.py`, `backend/apps/ai/views.py`
- Modify: `backend/tests/test_ai_proxy.py`
- Modify: `cloudflare-worker/src/index.js`, `cloudflare-worker/wrangler.toml`, `cloudflare-worker/tests/worker.test.js`

**Interfaces:**
- Consumes: Cấu hình R2 S3 credentials, binding `env.AI` và `env.DB` trong Worker.
- Produces: Endpoint tải/upload file presigned URL SigV4 900s, endpoint gợi ý danh mục AI kèm `is_fallback: True/False` và Worker logging `ai_suggestion_logs`.

- [x] **Step 1: Cấu hình Settings và Phân công Song song A / B / C**
  - Coordinator: Khai báo `R2_PRESIGNED_URL_EXPIRE_SECONDS = 900` và `AI_MODEL = '@cf/meta/llama-3.1-8b-instruct-fp8'` trong `settings.py` và `.env.example`.
  - Phân công file độc lập:
    - Agent A: Storage & Attachments (`storage_backends.py`, `assets/views.py`, `test_r2_storage.py`, `test_storage.py`).
    - Agent B: AI Backend & Heuristic Fallback (`apps/ai/services.py`, `serializers.py`, `views.py`, `test_ai_proxy.py`).
    - Agent C: Cloudflare Worker AI & Logging (`cloudflare-worker/src/index.js`, `wrangler.toml`, `cloudflare-worker/tests/worker.test.js`).

- [x] **Step 2: Agent A — R2 Presigned URLs, TTL 900s & D1 Safe Upload/Replace**
  - `storage_backends.py`: Đọc `R2_PRESIGNED_URL_EXPIRE_SECONDS` (default 900s), SigV4, inline cho ảnh, attachment cho tài liệu. Giữ `LocalDevAttachmentStorage` fallback và tham số override.
  - `apps/assets/views.py`: Action `download` trả `expires_in: 900` khớp TTL. Action `attachments` dùng conditional write `exclude(status=DISPOSED)` + OCC; chỉ dọn file mới nếu DB fail chắc chắn; chỉ xóa file cũ sau khi DB commit thành công; không xóa mù khi `D1IncompleteBatchError` báo đã committed.
  - Viết unit tests mock S3 client và D1 integration tests: verify 900s TTL, inline vs attachment, conditional update, dọn rác đúng ngữ nghĩa.

- [x] **Step 3: Agent B — AI Backend, Error Mapping & Heuristic Fallback**
  - `apps/ai/services.py`: Nhận diện mã 3036 (Quota) và 3040 (Capacity) từ Worker; circuit breaker 3 lỗi -> 30s; rate limit 10 req/min/user (HTTP 429).
  - Khi upstream lỗi hoặc circuit breaker mở: Heuristic keyword fallback trên danh mục thực tế trong DB; không chọn `categories[0]`; nếu không khớp trả controlled error.
  - `serializers.py`: Thêm `is_fallback: bool` và `explanation: str`.
  - Viết unit tests: test success (`is_fallback=False`), quota/capacity fallback (`is_fallback=True`), breaker trip & recovery, auth/validation preservation.

- [x] **Step 4: Agent C — Cloudflare Worker AI & DB Logging**
  - `cloudflare-worker/src/index.js`: Model `@cf/meta/llama-3.1-8b-instruct-fp8`; bọc `env.AI.run()` bắt lỗi 3036 (Quota) và 3040 (Capacity) trả HTTP 429 rõ ràng; validate gợi ý thuộc danh mục hợp lệ (không fallback về item đầu tiên); log vào `ai_suggestion_logs` kèm `request_id`.
  - `wrangler.toml`: Cập nhật `AI_MODEL`.
  - `cloudflare-worker/tests/worker.test.js`: Mock `env.AI` và `env.DB` test auth, input validation, quota, capacity, invalid output, logging success/failure.

- [x] **Step 5: Review Độc Lập D & Coordinator Tích Hợp Verification**
  - Agent D độc lập review diff và kết nối production code: R2 cleanup lifecycle, B↔C contract, không tạo file dư thừa.
  - Coordinator chạy kiểm thử tổng hợp (storage, AI backend, worker tests, D1 business integrity regression).
  - Cập nhật `docs/progress-report.md`, commit local `feat(r2-ai): implement r2 private presigned storage and workers ai with heuristic fallback`. (Commit: `feff816`).

- [x] **Step 6: Sửa 5 lỗi hồi quy Task 5 theo review feff816**
  - [x] Agent A: Xử lý 3 trạng thái commit (chắc chắn chưa ghi / đã ghi / chưa biết có readback an toàn, giữ orphan khi unknown tránh mất file), validate strict integer TTL (1 <= ttl <= 604800, trả 400 rõ), test trên D1 local thật.
  - [x] Agent B: Bỏ `record_failure()` khi gặp lỗi auth 401/403 của Worker (không mở breaker sang 200 giả), validate kiểu/giá trị `suggested_category_id` trước khi ORM filter (tránh 500 ValueError).
  - [x] Agent C: Bỏ tìm kiếm substring tên category trong rawResponse/explanation (trả 502 `INVALID_SUGGESTION`, không ghi audit log khi output lỗi).

- [x] **Step 7: Verification và Commit Task 5 Fixes**
  - Chạy đầy đủ: unit mock, PG test baseline, D1 local rehearsal attachments, Worker vitest.
  - Reviewer độc lập D thẩm định diff và 5 ca hồi quy.
  - Commit: `ac29b29` `fix(r2-ai): resolve upload unknown commit cleanup, download ttl validation, ai auth breaker and category parsing`.

- [x] **Step 8: Hoàn Tất Sửa Task 5 Theo Review ac29b29 (Unknown Commit Cleanup, Strict Integer ID & D1 Fixture Isolation)**
  - [x] Agent A: Bảo vệ pending/late commit khi trạng thái commit chưa biết (unknown state) trong `views.py` — giữ cả tệp mới và tệp cũ, không xóa mù khi readback thấy key cũ; cập nhật unit tests trong `test_r2_storage.py` tuân thủ hợp đồng bảo toàn dữ liệu.
  - [x] Agent B: Validate số nguyên dương trước mọi ép kiểu số trong `cloudflare-worker/src/index.js` và `backend/apps/ai/services.py`; từ chối float (`1.9`), non-finite (`Infinity`), boolean (`true`), array (`[1]`); Worker trả 502 `INVALID_SUGGESTION` (không ghi log); Backend kích hoạt heuristic fallback an toàn không gây `OverflowError`/500; bổ sung tests trong `worker.test.js` và `test_ai_proxy.py`.
  - [x] Agent C: Khôi phục trọn vẹn router lifecycle (`router._routers = None`, `router.__dict__.pop('routers', None)`) và connection cache trong `test_d1_attachment_storage.py` & `test_d1_business_integrity.py`; bổ sung regression test late commit với timing hook; chứng minh 2 test suite pass trong cùng tiến trình ở cả 2 thứ tự chạy.

- [x] **Step 9: Review Độc Lập (Agent D) & Verification Nghiệm Thu Cuối Task 5**
  - Thẩm định độc lập bằng ponytail-review và requesting-code-review: Verdict SHIP.
  - Verification toàn diện: PG baseline (227 tests), storage/AI tests (61 tests), Worker vitest (21 tests), D1 rehearsal song hành cả 2 chiều (28 tests mỗi chiều).
  - Cập nhật progress report và tạo commit local trên `feat/cloudflare-d1` (commit `8076992`).

- [x] **Step 10: Sửa Điểm Nhỏ DatabaseError Lookup AI Theo Review 8076992**
  - [x] Agent A: Viết regression test đỏ cho `DatabaseError` tại primary lookup category trong `backend/tests/test_ai_proxy.py`; bỏ catch-all `except (ValueError, OverflowError, Exception)` trong `backend/apps/ai/services.py`; bỏ alias `suggest_category_for_asset` không dùng; chạy suite test AI 32/32 passed.
  - [x] Agent B: Review độc lập diff và contract của Agent A qua ponytail-review: Verdict SHIP.

---

### Task 6: Chuẩn Bị Bản Demo Cloudflare & Kiểm Thử Trước Deploy (Pre-Deployment Package)

> **Mục tiêu:** Hoàn thiện toàn bộ mã nguồn, cấu hình runtime D1 opt-in (`USE_D1_DATABASE`), siết chặt bảo mật CORS/ALLOWED_HOSTS, bản build tĩnh React SPA cho Cloudflare Pages (Headers CSP & Redirects SPA), cấu hình Cloudflare Tunnel (hỗ trợ Quick Tunnel demo qua binary host `cloudflared`), bộ test khói E2E có gating an toàn (mặc định không gọi cloud), và cập nhật tài liệu vận hành chính xác để Codex duyệt trước khi publish lên cloud.
> 
> **Ranh giới an toàn nghiêm ngặt:**
> - Chưa tự deploy/tạo/xóa/ghi tài nguyên cloud, mở public Tunnel, bật R2 billing, gọi AI cloud, đổi DNS, chuyển dữ liệu thật hoặc đổi runtime đang phục vụ.
> - Không sửa `.env` đang dùng, không restart các container người dùng đang chạy (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
> - Không cài đặt/thêm/nâng cấp thư viện/công cụ, không kéo Docker image mới, không cài Llama local.
> - 1 tab AGY, nhánh `feat/cloudflare-d1`, 0 worktree, 0 push/merge.
> 
> **Điều kiện tiên quyết:** Toàn bộ Tasks 0..5 đã được Codex nghiệm thu.

**Phân công tệp tin:**
- **Agent A (Backend Runtime & Settings):**
  - `backend/assethub_api/settings.py`
  - `docker-compose.yml`
  - `backend/tests/test_settings_runtime.py`
- **Agent B (Frontend Pages Build & API Integration):**
  - `frontend/vite.config.js`
  - `frontend/public/_headers`
  - `frontend/public/_redirects`
- **Agent C (Tunnel Config & E2E Smoke Harness):**
  - `deploy/cloudflared/config.yml`
  - `backend/tests/e2e/test_d1_cloud_smoke.py`
  - `backend/tests/e2e/__init__.py`
- **Coordinator:**
  - `.env.example`
  - `deploy/.env.demo.example`
  - `deploy/docker-compose.demo.yml`
  - `docs/deployment-guide.md`
  - `README.md`
  - `docs/progress-report.md`
  - Điều phối và độc lập review qua Agent D.

- [x] **Step 1: Khởi động song song Agent A, B, C theo contract thống nhất**
  - Contract biến môi trường: `USE_D1_DATABASE`, `D1_BRIDGE_URL`, `D1_BRIDGE_SECRET_TOKEN`, `D1_DATABASE_NAME`, `D1_TIMEOUT`, `VITE_API_BASE_URL`, `CORS_ALLOWED_ORIGINS`, `DJANGO_ALLOWED_HOSTS`, `R2_*`, `AI_*`.
  - Agent A: Cài đặt D1 opt-in runtime, cấu hình CORS/ALLOWED_HOSTS chuẩn, kiểm tra lỗi thiếu config khi bật D1, viết test settings.
  - Agent B: Kiểm tra Vite build với `VITE_API_BASE_URL`, cấu hình CSP `_headers` và SPA `_redirects`, kiểm chứng không rò rỉ secret trong bundle.
  - Agent C: Cấu hình `deploy/cloudflared/config.yml`, hướng dẫn Quick Tunnel qua binary host; viết `test_d1_cloud_smoke.py` có cờ opt-in gating `@pytest.mark.cloud_smoke`.

- [x] **Step 2: Coordinator chuẩn bị cấu hình demo cách ly và tài liệu**
  - Tạo `deploy/.env.demo.example` và `deploy/docker-compose.demo.yml` tách biệt khỏi môi trường dev.
  - Cập nhật `.env.example`.
  - Cập nhật `README.md` và `docs/deployment-guide.md` phản ánh kiến trúc D1 là CSDL nghiệp vụ chính cho demo, PostgreSQL làm nguồn đối chiếu/rollback.

- [x] **Step 3: Thẩm định độc lập (Agent D) & Verification tổng hợp**
  - Thẩm định chéo diff qua `ponytail:ponytail-review` và `superpowers:requesting-code-review`.
  - Build frontend Vite `npm --prefix frontend run build`.
  - Chạy backend test settings và smoke gating test.
  - Cập nhật progress-report.md và commit local Task 6 preparation (`39ec16c`).

- [x] **Step 4: Khắc Phục Triệt Để 6 Điểm Review Từ Codex Tại 39ec16c (Agent A, B, C)**
  - **Agent A (P1/P2):**
    - Sửa alias `postgres` trong `backend/assethub_api/settings.py`: không fallback sang `DB_USER`/`DB_PASSWORD`; nếu không có `POSTGRES_RO_USER`/`PASSWORD` thì không tạo alias `postgres`; nếu có thì cấu hình connection read-only (`OPTIONS: {'options': '-c default_transaction_read_only=on'}`); viết unit test kiểm chứng SELECT được nhưng INSERT/UPDATE bị từ chối; giữ PG dev mode không đổi.
    - Sửa CORS regex trong `settings.py`: loại bỏ prefix `assethub-*` cho phép attacker project; chỉ cho phép origin từ env hoặc project Pages chính xác; kiểm thử bằng test client/middleware thật từ chối `https://assethub-attacker.pages.dev`.
  - **Agent B (P1/P2):**
    - Sửa Compose demo isolation & paths: tạo cấu hình demo riêng `deploy/docker-compose.demo.yml` hoặc chuẩn hóa paths trong `deploy/docker-compose.prod.yml` không bị resolve sai đường dẫn cha; dùng project name riêng `assethub-demo`, port riêng (8001:8000), không phụ thuộc `db` healthy khi D1 mode; validate config bằng env giả.
    - Đồng bộ API origin & AI Worker: tách bạch URL AI Worker riêng khỏi D1 Bridge Worker trong `deploy/.env.demo.example`; siết chặt CSP `connect-src` trong `frontend/public/_headers` (bỏ `https:` wildcard); ghi rõ hướng dẫn rebuild frontend với API origin thực.
  - **Agent C (P1/P2):**
    - Smoke safety: xóa bỏ hoàn toàn `ALLOWED_TEST_BRIDGE_ENDPOINTS.add(...)` tại runtime; chặn mọi endpoint ngoài allowlist trước khi gửi network request; tuyệt đối không sửa hay deactive Admin có sẵn; teardown chỉ xóa dữ liệu do run sở hữu (theo run_id/tracking IDs), kiểm tra xác nhận sau cleanup.
    - Nâng cấp smoke test: kiểm thử HTTP thực tế tới API_BASE_URL (thay vì in-process factory); test R2 upload/download byte thật; test AI với category thật; duy trì cờ opt-in `RUN_CLOUD_SMOKE=1` mặc định skip cleanly khi chạy local.

- [x] **Step 5: Thẩm Định Độc Lập Ponytail Review (Agent D) & Verification**
  - Review độc lập chéo diff và contract qua `ponytail:ponytail-review` + `superpowers:requesting-code-review`.
  - Kiểm tra frontend build, test runtime settings, CORS, storage, smoke gating và isolation safety.

- [x] **Step 6: Coordinator Tích Hợp Tài Liệu & Commit Local**
  - Cập nhật `README.md`, `docs/deployment-guide.md`, `docs/progress-report.md`.
  - Commit local chuẩn Conventional Commits trên nhánh `feat/cloudflare-d1` (`cda507f`).

- [x] **Step 7: Hoàn Tất Đóng Gói Demo Freshness & Positive HTTP Smoke (Review cda507f)**
  - **Agent A (Demo Runtime Freshness & Source Mount):**
    - Cập nhật `deploy/docker-compose.demo.yml`: gắn volume `./backend:/app:ro` và `pull_policy: never` để container `assethub-backend:latest` chạy trực tiếp mã nguồn đã review mà không cần rebuild Docker image; kiểm chứng trong container test cô lập.
  - **Agent B (Smoke Fixture DB Unblock, Cleanup Ownership & AI Classification):**
    - Sửa `backend/tests/e2e/test_d1_cloud_smoke.py`: bọc `django_db_blocker.unblock()` trong fixture `smoke_context`; bọc `try/finally` từ trước mutation đầu tiên; lưu trữ ID thực và mã sinh từ API (`AST-2026-xxx`, `REQ-2026-xxx`); xóa tuần tự theo quan hệ FK và xác nhận không còn dữ liệu thuộc run; dọn dẹp R2 theo key thực tế; phân loại AI nghiêm ngặt (lỗi 503 auth phải FAIL dứt điểm); bổ sung unit regression phân loại AI.
  - **Agent C (Positive Local HTTP Smoke Subprocess Rehearsal):**
    - Tạo `backend/tests/e2e/test_smoke_local_http.py`: khởi chạy backend WSGI thật với `USE_D1_DATABASE=True` trên loopback port ngẫu nhiên; kích hoạt `test_d1_cloud_smoke.py` qua `pytest` subprocess với `RUN_CLOUD_SMOKE=1`; xác nhận toàn bộ luồng HTTP thật, D1 readback và cleanup PASS 100%.
  - **Coordinator & Agent D:**
    - Review độc lập chéo diff qua `ponytail:ponytail-review`.
    - Cập nhật tài liệu runbook và tiến độ; commit local trên `feat/cloudflare-d1` (`19ca834`).

- [x] **Step 8: Sửa An Toàn Cleanup Smoke & Zero-Cloud Rehearsal (Review 19ca834)**
  - **Agent A (Smoke Cleanup Ownership & Preflight Guard):**
    - Trong `backend/tests/e2e/test_d1_cloud_smoke.py`:
      - Thêm preflight check trước mutation đầu tiên: kiểm tra D1 có ít nhất một Admin active nền; nếu không có thì dừng/skip rõ ràng, không seed trước để tránh vướng last-admin trigger.
      - Xác minh quyền sở hữu (`run_id` trong department, category, user, asset, request, R2 key) trước khi xóa; nếu ID không thuộc run (sentinel), giữ nguyên và báo mismatch, không dùng raw SQL xóa trần theo ID.
      - Xóa theo thứ tự FK nghiêm ngặt và xác nhận `COUNT(*) == 0`.
  - **Agent B (Rehearsal Opt-In Marker, Zero-Cloud Env & Cleanup Regressions):**
    - Trong `backend/tests/e2e/test_smoke_local_http.py`:
      - Đánh dấu `@pytest.mark.rehearsal` để opt-in theo `conftest.py` (chạy khi có `--rehearsal`).
      - Vô hiệu hóa triệt để biến môi trường R2/AI cloud cả ở server process lẫn subprocess env (`R2_*="", AI_*=""`).
      - Bổ sung regression tests kiểm chứng: Negative A (sentinel asset được bảo toàn, báo mismatch), Negative B (D1 trống dừng ở preflight, bảng vẫn trống), và dọn dẹp partial failure.
  - **Coordinator & Agent C:**
    - Thẩm định độc lập qua `superpowers:requesting-code-review` + `ponytail:ponytail-review`.
    - Cập nhật tài liệu tiến độ và commit local trên `feat/cloudflare-d1`.

- [x] **Step 9: Sửa 3 Đường Cô Lập Test Rehearsal (Review 7cbd2ad)**
  - **Agent A (Isolate Rehearsal Server, Port Guard & Child Zero-PG):**
    - Trong `backend/tests/e2e/test_smoke_local_http.py`:
      - Xóa triệt để nhánh `else DELETE/INSERT` khi port 8787 đã bận; từ chối mutation và không đụng listener lạ.
      - Chuyển `live_rehearsal_server` sang `scope="function"` để giải phóng port ngay sau test.
      - Cô lập R2/AI env và settings thành chuỗi rỗng `""` trước server startup; try/finally bao trọn toàn bộ fixture.
      - Bỏ `DB_PORT: 5433` và mọi biến PG khỏi child env; đặt `USE_D1_DATABASE=True`; chạy subprocess với `-m cloud_smoke`.
  - **Agent B (Negative Proofs & Lifecycle Verification):**
    - Kiểm chứng kịch bản port bận: xác nhận 0 SQL mutation gửi tới listener lạ.
    - Kiểm chứng `is_r2_configured() == False` và AI disabled khi có fake credentials trong môi trường trước fixture.
    - Kiểm chứng child subprocess không phụ thuộc PostgreSQL container.
    - Kiểm chứng thứ tự chạy xuôi và ngược độc lập vòng đời.
  - **Coordinator & Agent C:**
    - Thẩm định độc lập qua `superpowers:requesting-code-review` + `ponytail:ponytail-review`.
    - Cập nhật Section 23 trong `docs/progress-report.md` và commit local trên `feat/cloudflare-d1`.

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
