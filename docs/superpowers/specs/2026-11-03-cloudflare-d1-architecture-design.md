# Đặc Tả Kiến Trúc Triển Khai Cloudflare D1 & Chuyển Đổi CSDL AssetHub (Tuần 4)

- **Ngày ban hành:** 2026-11-03 (Bản sửa đổi lần 3)
- **Tác giả:** Antigravity (AGY) & Đội ngũ Kỹ thuật AssetHub
- **Trạng thái:** Approved for Planning — còn implementation blockers (Chấp thuận lập kế hoạch — chưa nghiệm thu implementation hoặc chuyển dữ liệu thật)
- **Nhánh thực hiện:** `feat/cloudflare-d1`
- **Phiên bản mã nguồn cơ sở:** Commit `f1c1ce7` (nhánh `dev`)
- **Tài khoản Cloudflare:** `hnhoa494@gmail.com` (Account ID: `5e76c2bf4e09587a010d31ea416ec8d3`)
- **Tài nguyên thử nghiệm được phê duyệt:**
  - D1 Database: `assethub-test-d1` (`3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`, Region: APAC)
  - Worker Bridge: `https://assethub-test-worker.hnhoa494-assethub.workers.dev`

---

## 1. Bối Cảnh, Mục Tiêu & Ranh Giới Kiến Trúc

### 1.1. Mục tiêu (Goals)
1. **Chuyển đổi toàn bộ CSDL nghiệp vụ chính sang Cloudflare D1:** D1 là kho lưu trữ dữ liệu chính thức duy nhất của hệ thống khi chạy bản cuối (users, RBAC, departments, categories, assets, requests, audit logs, identifier counters).
2. **Bảo toàn an ninh mật khẩu & Framework nghiệp vụ:** Giữ nguyên Django 5.2.18, Django REST Framework (DRF), SimpleJWT, và cơ chế băm mật khẩu chuẩn `PBKDF2PasswordHasher` với **1.000.000 vòng lặp** (tuyệt đối không giảm số vòng để ép chạy trên môi trường hạn chế).
3. **Bảo tồn toàn bộ giao diện và hợp đồng API:** Giao diện React Vite, Tailwind CSS, biểu đồ Dashboard và các API contract hiện tại giữ nguyên 100%.
4. **Tích hợp hệ sinh thái Cloudflare gói Free:**
   - **Frontend:** Triển khai tĩnh trên **Cloudflare Pages**.
   - **Tệp tin & Ảnh:** Lưu trữ bảo mật trên **Cloudflare R2** (Zero Egress Fees).
   - **AI Microservice:** **Cloudflare Workers AI** (`@cf/meta/llama-3-8b-instruct` / `@cf/meta/llama-3.1-8b-instruct`) gợi ý danh mục tài sản.
   - **CSDL:** **Cloudflare D1** (Serverless SQL tại Edge).
   - **Ingress:** **Cloudflare Tunnel (`cloudflared`)** kết nối an toàn backend container ra Edge.

### 1.2. Ranh giới kiến trúc & Tính minh bạch (Non-Goals & Transparency)
- **Kiến trúc có Backend bên ngoài Cloudflare:** Kết quả khảo sát thực nghiệm chứng minh rằng việc đóng gói toàn bộ Django + DRF + SimpleJWT lên Cloudflare Python Workers bị chặn đứng bởi giới hạn trần CPU **10ms / request** của gói Workers Free (xác thực PBKDF2 1.000.000 vòng tốn ~140ms CPU trong Pyodide). Do đó, Django/DRF được vận hành trong **Linux Container (Docker)** nằm ngoài Cloudflare Workers.
- **Ràng buộc vận hành Ingress:** Container Django kết nối ra Internet và giao tiếp với Cloudflare Edge thông qua **Cloudflare Tunnel (`cloudflared`)** hoặc container cloud (Fly.io/Render/VPS). Nếu chạy trên máy cục bộ qua Tunnel, máy trạm phải luôn bật và kết nối mạng để hệ thống hoạt động.
- **Không tự tuyên bố đáp ứng:** Không tự tuyên bố phương án này đã đáp ứng yêu cầu “toàn bộ backend chạy trên Cloudflare”. Đây là giải pháp kiến trúc lai (Hybrid: Edge Database D1 + Edge Static Pages + Containerized Backend Engine) nhằm bảo toàn bảo mật doanh nghiệp trên gói Free.
- **Bảo tồn CSDL PostgreSQL:** CSDL PostgreSQL 16 hiện tại được đóng băng nguyên vẹn làm nguồn đối chiếu dữ liệu đối soát và là phương án phục hồi tức thì (pre-cutover rollback plan). Tuyệt đối không dual-write song song sau chuyển đổi.

---

## 2. Báo Cáo Thực Nghiệm & Đo Đạc Kỹ Thuật (Đã Chuẩn Hóa Theo Bằng Chứng)

Đợt kiểm thử kỹ thuật cô lập được thực hiện trên tài khoản Cloudflare thật (`hnhoa494@gmail.com`) với CSDL D1 `assethub-test-d1` (`3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`) và Worker `assethub-test-worker`.

### 2.1. Đối chiếu ranh giới tài nguyên Cloudflare Workers Free Plan
- **Kích thước gói nén và Bundle dependencies:** Đóng gói toàn bộ dependencies của AssetHub backend (`django`, `drf`, `simplejwt`, `pillow`, `pypdf`, `boto3`, `django-storages`) tạo ra bundle 53.7 MB (gzip 23.1 MB, uncompressed thư mục ~83 MB). Khi deploy lên Cloudflare Workers Free, Cloudflare API từ chối với lỗi: `multipart: message too large [code: 10021]`.
- **CPU Time vs. Wall-Clock Latency:**
  - `time.perf_counter()` đo wall-clock latency (tổng thời gian thực tế gồm I/O và CPU). Trong container Linux, một phép băm PBKDF2 1M vòng tốn ~141–145 ms wall-clock.
  - Runtime Cloudflare Workers Free giám sát thời gian CPU thực tế (CPU time) bằng cơ chế ngắt thread isolate của V8 với trần cứng **10ms CPU**.
  - Khi triển khai thử nghiệm endpoint đăng nhập trên Python Workers thật, lời gọi xác thực mật khẩu bị runtime Cloudflare ngắt ngay lập tức với HTTP 503, mã lỗi Cloudflare `1102`: `Worker exceeded CPU time limit`.
  - Thao tác phát hành và giải mã JWT (`RefreshToken.for_user()`) chỉ tốn < 1ms CPU time. Điểm nghẽn CPU thực sự nằm ở thuật toán băm mật khẩu chuẩn `PBKDF2PasswordHasher` với 1.000.000 vòng lặp.

### 2.2. Kết quả kiểm chứng đường kết nối: Django Container → Cloudflare D1
Đã triển khai một **D1 Worker Bridge** bảo mật cao (JS native, CPU < 0.2ms) và adapter `assethub_api.backends.d1` trong Django container. Bộ kiểm thử tích hợp thực tế `test_d1_orm_integration.py` đã vượt qua 100%:
1. **Bảo mật không dùng fallback:** Token `BRIDGE_SECRET_TOKEN` được cấu hình bắt buộc (tối thiểu 32 ký tự ngẫu nhiên), loại bỏ hoàn toàn fallback bí mật mặc định trong code. Header `X-Bridge-Token` được kiểm tra bằng thuật toán so sánh hằng số thời gian `crypto.subtle.timingSafeEqual`. Token không hợp lệ bị từ chối với `HTTP 401 Unauthorized`.
2. **Ngăn chặn Double-Write khi Retry mạng (Atomic Idempotency Protocol):**
   - Bảng `_idempotency_log` trên D1 lưu vết `(idempotency_key, payload_hash, response_body)`.
   - Thao tác ghi (`/api/v1/query` và `/api/v1/batch`) thực hiện **nguyên tử thực sự**:
     - Câu lệnh ghi nhận idempotency được chèn trực tiếp trong cùng một mảng gọi `env.DB.batch([userStmt, logStmt])` với câu lệnh mutation.
     - **Cơ chế Khôi phục Dữ liệu Tại Thời Điểm Commit (Commit-Time Persistence) & Giới hạn Kỹ thuật:**
       - Với truy vấn đơn (`/api/v1/query`): Khi câu lệnh chứa mệnh đề `RETURNING id` hoặc các biến thể định danh có dấu trích dẫn do Django ORM sinh ra (ví dụ: `RETURNING "id"`, `RETURNING "table"."id"`), Worker nhúng trực tiếp biểu thức SQLite `json_array(json_object('id', last_insert_rowid()))` vào bản ghi khởi tạo của `_idempotency_log`.
       - **Giới hạn 1 (Django Quoted Identifiers):** Regex phân tích SQL hiện tại cần bao phủ toàn bộ cú pháp định danh có dấu ngoặc kép chuẩn SQL của Django (`"id"`, `"app_table"."id"`). Nếu câu lệnh dùng cú pháp chưa nhận diện, Worker tuyệt đối không trả kết quả sai lệch mà phải trả mã trạng thái rõ ràng để client quyết định.
       - **Giới hạn 2 (Batch Fallback không giả định số liệu):** Với `/api/v1/batch`, tuyệt đối không được tự ý điền sẵn các giá trị thay đổi giả định (như `meta.changes: 1` hay kết quả giả tạo) vào bản ghi khởi tạo khi Worker sập trước UPDATE. Nếu Worker gặp sự cố trước khi lưu trữ đầy đủ kết quả thực thi của từng câu lệnh, trạng thái bản ghi phải phản ánh đúng mức độ hoàn tất hoặc trả về lỗi giao thức rõ ràng (`500 Incomplete State`), không được ngụy tạo kết quả thành công nhằm đánh lừa tầng nghiệp vụ.
       - **Không cam kết bảo toàn replay 100% cho mọi SQL tổng quát:** Hệ thống chỉ cam kết an toàn double-write (CSDL chỉ ghi một lần) và khôi phục khóa chính `id` cho các thao tác INSERT đơn bảng chuẩn của ORM. Toàn bộ các trường hợp SQL phức tạp, multi-column returning, hoặc batch không thể phục hồi trọn vẹn kết quả chi tiết khi gặp lỗi giữa commit và update đều phải được xử lý thận trọng theo Task 0 trước khi mở rộng.
     - **Quy chuẩn Validation Độc Lập:** Phân định kiểm tra cấu trúc riêng biệt cho từng endpoint: `/api/v1/query` kiểm tra `success`, mảng `results` và đối tượng `meta` cấp cao nhất; `/api/v1/batch` kiểm tra mảng kết quả từng phần tử mang cấu trúc `{ results: [...], meta: {...} }`.
     - **Fault Injection Resilience:** Điểm chèn lỗi kiểm thử (`X-Test-Fault-Injection: crash-after-commit`) được đặt chính xác ngay sau lệnh commit `env.DB.batch()` và **trước** lệnh `UPDATE _idempotency_log` để kiểm chứng sát sườn khoảng lỗi thực tế.
     - Khi request mutation bị gián đoạn mạng và gửi lại: cùng `Idempotency-Key` và cùng `payload_hash` sẽ nhận lại kết quả đã lưu trước đó kèm header `X-Idempotent-Replay: true` mà không chạy lại SQL.
     - Cùng `Idempotency-Key` nhưng khác payload bị từ chối ngay lập tức với `HTTP 422 Conflict: Idempotency-Key already used with different payload`.
     - **Xử lý Race Condition Cạnh tranh:** Hai request đồng thời cùng `Idempotency-Key` được giải quyết an toàn: request chiến thắng commit trước, request còn lại bắt lỗi ràng buộc UNIQUE trên `_idempotency_log`, bóc tách đối tượng người chiến thắng (`winnerObj = JSON.parse(winnerLog.response_body)`), kiểm tra tính hợp lệ và trả về `HTTP 200` theo đúng protocol. Cả 2 request đều nhận phản hồi thành công hợp lệ và dữ liệu chỉ ghi 1 lần.
     - **Phân loại Read/Write thận trọng (Conservative Classification):** `D1Client` chỉ xem là truy vấn đọc (`is_mutation = False`) khi câu lệnh bắt đầu rõ ràng bằng `SELECT`, `EXPLAIN`, hoặc các `PRAGMA` đọc đã duyệt (`PRAGMA table_info`, `PRAGMA index_list`, `PRAGMA foreign_key_list`). Toàn bộ các câu lệnh CTE (`WITH`), DDL, DML hay cú pháp không xác định đều được mặc định coi là mutation (`is_mutation = True`) và **không được tự động retry** nếu thiếu `Idempotency-Key`.
   - **Cơ chế Cách Ly & Bảo Vệ Môi Trường Kiểm Thử (Test Isolation Guard):**
     - Không dùng so khớp chuỗi lỏng lẻo (`startswith`). Endpoint được bóc tách và chuẩn hóa chính xác bằng `urllib.parse.urlsplit` (scheme, hostname, port, path) đối chiếu tập hợp `ALLOWED_TEST_BRIDGE_ENDPOINTS`.
     - Chặn đứng mọi hình thức tấn công/sai sót cấu hình: Giả mạo tên miền đuôi (`*.workers.dev.evil.com`), sai scheme (`http` thay vì `https`), sai port mạng, và truyền thông tin xác thực nhúng trong URL (`userinfo` như `user:pass@host`).
     - Yêu cầu cấu hình bắt buộc: Từ chối ngay lập tức nếu thiếu URL hoặc token bảo mật không đạt chuẩn (`< 32` ký tự).
     - **Chặn trước tầng Transport:** Lệnh mutation vi phạm bị chặn đứng tại client trước khi khởi tạo kết nối mạng (đảm bảo không phát tán token hay nội dung SQL ra Internet).
     - Guard chỉ kích hoạt trong môi trường test (`is_test_environment() == True`), kiểm chứng mock cho thấy đường ORM và client hoàn toàn không bị chặn nhầm trên môi trường production runtime.
3. **Phân loại mã lỗi chính xác:** Lỗi vi phạm ràng buộc D1 (`SQLITE_CONSTRAINT`, `CHECK`, `UNIQUE`, `TRIGGER`) được ánh xạ thành `HTTP 409 Conflict` (và `django.db.IntegrityError` tại client). Lỗi hạ tầng mạng trả về 502/503/504 (`OperationalError`). Không coi chung lỗi hạ tầng là conflict.
4. **Kiểm chứng thực tế Django ORM (ORM Integration Gate):**
   - Đã chạy thành công các model Django ORM thực tế (`Department`, `Category`, `User`, `Asset`) trên CSDL D1 thông qua `connections['d1']`.
   - Truy vấn `filter()`, `get()`, `create()`, `save()`, `update()`, `delete()` và điều hướng quan hệ khóa ngoại (Foreign Key) hoạt động chính xác.
   - Hàm `check_password` trên model `User` đọc từ D1 xác thực mật khẩu PBKDF2 1.000.000 vòng chính xác trong môi trường container.
   - SimpleJWT phát hành token, `CustomJWTAuthentication.get_user()` đọc và xác thực thành công người dùng từ D1.
   - Kiểm chứng thu hồi phiên: Tăng `auth_version` làm token cũ bị từ chối ngay lập tức (`token_revoked`). Vô hiệu hóa tài khoản (`is_active=0`) làm token bị từ chối ngay lập tức (`user_inactive`).
   - Đảm bảo cách ly: Dữ liệu kiểm thử ghi trên D1 hoàn toàn không xuất hiện trên CSDL PostgreSQL `default`.

---

## 3. Sơ Đồ Kiến Trúc Hệ Thống

```
[ Trình duyệt Người dùng (Client Browser) ]
              │
              ├─── (1) Tải Giao diện Tĩnh (SPA Assets) ───► [ Cloudflare Pages ] (React + Vite)
              │
              └─── (2) Gọi API Nghiệp vụ (/api/*) ───────► [ Cloudflare Tunnel / Edge ]
                                                                     │
                                                            (HTTPS Ingress qua cloudflared)
                                                                     ▼
                                                        ┌──────────────────────────────┐
                                                        │   Django Origin Server       │
                                                        │   (Docker Container Linux)   │
                                                        │                              │
                                                        │  - Django 5.2 / DRF          │
                                                        │  - PBKDF2 Hasher (1M rounds) │
                                                        │  - SimpleJWT Authentication  │
                                                        │  - RBAC & Business Services  │
                                                        │  - D1 Custom Database Engine │
                                                        └──────────────┬───────────────┘
                                                                       │
                                                (3) SQL Query / Atomic Batch (X-Bridge-Token + Idempotency)
                                                                       ▼
                                                        ┌──────────────────────────────┐
                                                        │ Cloudflare D1 Bridge Worker  │
                                                        │ (Edge Gateway / env.DB API)  │
                                                        └──────────────┬───────────────┘
                                                                       │
                                                       Edge SQLite Native Binding
                                                                       ▼
                                                        ┌──────────────────────────────┐
                                                        │      Cloudflare D1 SQL       │
                                                        │   (Primary Serverless DB)    │
                                                        └──────────────────────────────┘
```

---

## 4. Đặc Tả Tích Hợp Chi Tiết

### 4.1. Bộ Điều Hướng CSDL Django (`assethub_api.backends.d1`)

#### Bảng phạm vi hỗ trợ adapter (Feature Matrix):
| Thành phần | Mức độ hỗ trợ trên D1 Backend | Cơ chế thực hiện |
| :--- | :--- | :--- |
| **Placeholders** | Hỗ trợ đầy đủ | Tự động chuyển đổi định dạng Django `%s` sang SQLite `?` binding. |
| **Data Types** | Hỗ trợ chuẩn SQLite | Boolean (`0/1`), Integer, Text/VarChar, ISO8601 UTC Datetime, JSONField text. |
| **Cursor Description** | Hỗ trợ chuẩn DB-API 2.0 | `description` trả về danh sách 7-tuple `(name, None, None, None, None, None, None)` cho phép `RawQuerySet` và `inspectdb` hoạt động. |
| **Rowcount & Lastrowid** | Hỗ trợ chuẩn | Trích xuất từ metadata của D1 (`meta.changes`, `meta.last_row_id`). |
| **Transactions & Savepoints** | Không hỗ trợ interactive transaction | `atomic_transactions = False`, `supports_transactions = False`, `uses_savepoints = False`. Các thao tác atomic nhiều bảng được gom lại qua cơ chế `d1_batch`. |
| **Select for Update** | Không hỗ trợ | SQLite D1 hoạt động đơn luồng ghi tại Edge; sử dụng Optimistic Locking (`version` + điều kiện trạng thái) thay thế cho row-level lock. |
| **Date Truncation** | Hỗ trợ chuyển đổi | Thay thế các hàm `django_date_trunc` bằng biểu thức SQLite `STRFTIME` và `DATE`. |
| **Foreign Keys** | Bật mặc định trên D1 | D1 tự động kích hoạt foreign keys. Không giả định có thể tùy ý bật/tắt PRAGMA trên từng session HTTP độc lập. |
| **Schema Migrations** | Quản lý qua DDL scripts | Sử dụng file DDL SQL phiên bản hóa áp dụng trực tiếp qua `wrangler d1 execute --remote`. Django migrations dùng để đồng bộ metadata models. |

### 4.2. Bảo Vệ Quản Trị Viên Cuối Cùng (Admin Protection Triggers)
Hệ thống cài đặt hai Database Triggers nguyên tử trên D1 để ngăn chặn tuyệt đối việc hệ thống rơi vào trạng thái không còn Admin nào:
1. **Trigger bảo vệ khi UPDATE (`protect_last_admin_before_update`):**
   - Kích hoạt khi có thao tác cập nhật cột `role` hoặc `is_active` trên bảng `authentication_user`.
   - Điều kiện kiểm tra: `OLD.role = 'ADMIN' AND OLD.is_active = 1 AND (NEW.role != 'ADMIN' OR NEW.is_active != 1)`.
   - Nếu số lượng Admin active còn lại trong hệ thống `= 0`, kích hoạt `RAISE(ABORT, 'Cannot demote or deactivate the last active Admin')`.
2. **Trigger bảo vệ khi DELETE (`protect_last_admin_before_delete`):**
   - Kích hoạt trước khi xóa bản ghi trên bảng `authentication_user`.
   - Điều kiện kiểm tra: `OLD.role = 'ADMIN' AND OLD.is_active = 1`.
   - Nếu số lượng Admin active còn lại trong hệ thống `= 0`, kích hoạt `RAISE(ABORT, 'Cannot delete the last active Admin')`.

### 4.3. Bảo Toàn Hợp Đồng Nghiệp Vụ Hiện Tại
Toàn bộ tên bảng, tên trường, trạng thái và quy tắc nghiệp vụ tuân thủ chính xác 100% hợp đồng hiện hành của AssetHub:
1. **Trạng thái tài sản (`Asset.Status`):**
   - Sử dụng đúng 6 trạng thái chuẩn: `AVAILABLE`, `IN_USE`, `DAMAGED_PENDING_REPAIR`, `UNDER_MAINTENANCE`, `PENDING_DISPOSAL`, `DISPOSED`.
   - Tuyệt đối không dùng tên lạ (`ALLOCATED`).
   - Tên trường người giữ tài sản: `assigned_to_user_id`.
2. **Phân tách Đổi Mật Khẩu và Khóa Tài Khoản:**
   - **Đổi mật khẩu người dùng:** Cập nhật hash `password` mới và tăng `auth_version += 1`, giữ nguyên `is_active = 1`.
   - **Admin khóa tài khoản:** Đặt `is_active = 0` và tăng `auth_version += 1`.
   - **Đổi phòng ban / vai trò:** Tăng `auth_version += 1` để thu hồi tức thì các phiên đăng nhập cũ mang quyền hạn cũ.
3. **Sinh mã định danh tự động (`IdentifierSequence`):**
   - Định dạng mã: `AST-YYYY-NNN` (Tài sản, ví dụ: `AST-2026-001`) và `REQ-YYYY-NNN` (Phiếu yêu cầu, ví dụ: `REQ-2026-001`).
   - Tăng giá trị `value` trong bảng `assets_identifiersequence` trong cùng batch nguyên tử với thao tác tạo tài sản/phiếu, ngăn chặn đọc nhầm counter giữa hai request cạnh tranh.
4. **Phạm vi Mutation toàn diện & Phân định rõ Danh mục Action:**
   - **Loại yêu cầu (`AssetRequest.RequestType`):** `ALLOCATE` (Cấp phát), `RETURN` (Thu hồi/Trả lại), `DAMAGE_REPORT` (Báo hỏng).
   - **Hành động kiểm toán (`AssetHistoryLog.ActionType`):** `CREATE`, `ALLOCATE`, `RETURN`, `DAMAGE`, `MAINTENANCE_START`, `MAINTENANCE_END`, `TRANSFER`, `DISPOSAL_PROPOSE`, `DISPOSAL_CONFIRM`.
   - **Phân biệt rõ ràng:** `DAMAGE_REPORT` là loại phiếu yêu cầu của người dùng; khi được duyệt hoặc xác nhận hỏng sẽ ghi nhận hành động kiểm toán tài sản là `DAMAGE`.
   - **Hành vi từ chối phiếu (`reject_request`):** Khi Admin/Manager từ chối phiếu yêu cầu, trạng thái phiếu chuyển sang `REJECTED` kèm `rejection_reason`. Tài sản liên quan giữ nguyên hiện trạng và **tuyệt đối không tạo bản ghi lịch sử `AssetHistoryLog`** (bảo toàn 100% logic hiện hữu trong `RequestService.reject_request`).
   - Mỗi mutation chuyển đổi tài sản hợp lệ ghi nhận đúng 1 bản ghi lịch sử `AssetHistoryLog` tương ứng.

---

## 5. Danh Mục Bảng & Kế Hoạch Chuyển Đổi Dữ Liệu

### 5.1. Bảng Kiểm Kê Toàn Bộ CSDL (Full Table Inventory - 18 Bảng)

| STT | Tên Bảng | Vai Trò & Ứng Dụng Quản Lý | Nguồn Dữ Liệu Chuyển Đổi |
| :--- | :--- | :--- | :--- |
| 1 | `departments_department` | Danh mục phòng ban (`apps.departments`) | PostgreSQL `departments_department` |
| 2 | `departments_category` | Danh mục loại tài sản (`apps.departments`) | PostgreSQL `departments_category` |
| 3 | `authentication_user` | Tài khoản người dùng, RBAC, auth_version (`apps.authentication`) | PostgreSQL `authentication_user` |
| 4 | `assets_identifiersequence` | Bộ đếm sinh mã tuần tự AST/REQ (`apps.assets`) | PostgreSQL `assets_identifiersequence` |
| 5 | `assets_asset` | Thông tin tài sản vật lý (`apps.assets`) | PostgreSQL `assets_asset` |
| 6 | `requests_assetrequest` | Phiếu yêu cầu cấp phát, thu hồi, báo hỏng (`apps.requests`) | PostgreSQL `requests_assetrequest` |
| 7 | `assets_assethistorylog` | Sổ nhật ký kiểm toán tài sản append-only (`apps.assets`) | PostgreSQL `assets_assethistorylog` |
| 8 | `django_content_type` | Metadata model của Django (`django.contrib.contenttypes`) | Sinh tự động / Export |
| 9 | `django_admin_log` | Lịch sử thao tác quản trị Django (`django.contrib.admin`) | PostgreSQL `django_admin_log` |
| 10 | `auth_permission` | Phân quyền chi tiết Django (`django.contrib.auth`) | Sinh tự động qua migrations |
| 11 | `auth_group` | Nhóm người dùng Django (`django.contrib.auth`) | PostgreSQL `auth_group` |
| 12 | `auth_group_permissions` | Liên kết nhóm - quyền (`django.contrib.auth`) | PostgreSQL `auth_group_permissions` |
| 13 | `authentication_user_groups` | Liên kết user - nhóm (`django.contrib.auth`) | PostgreSQL `authentication_user_groups` |
| 14 | `authentication_user_user_permissions` | Quyền riêng của user (`django.contrib.auth`) | PostgreSQL `authentication_user_user_permissions` |
| 15 | `django_migrations` | Lịch sử áp dụng migrations của Django | Export từ PostgreSQL |
| 16 | `django_session` | Phiên làm việc web (nếu có dùng session) | Export / Khởi tạo rỗng |
| 17 | `_assert_guard` | Bảng kiểm soát số dòng bị ảnh hưởng cho atomic batch | D1 Native Infrastructure |
| 18 | `_idempotency_log` | Bảng lưu cache phản hồi chống double-write retry | D1 Native Infrastructure |

### 5.2. Kế Hoạch Phục Hồi & Rollback Hai Giai Đoạn (RPO / RTO)

Phân biệt rõ ràng giữa hai trạng thái chuyển đổi:

#### Giai đoạn 1: Rollback trước khi mở ghi lên D1 (Pre-Cutover Rollback)
- **Áp dụng:** Khi đang trong cửa sổ bảo trì (`MAINTENANCE_MODE=True`), đang chạy trích xuất dữ liệu, nạp D1 hoặc đối soát dữ liệu nhưng phát hiện sai lệch số liệu, lỗi migration hoặc kiểm thử smoke test thất bại.
- **Hành động:** 
  1. Hủy bỏ phiên nạp D1.
  2. Giữ nguyên cấu hình `DATABASE_ENGINE=django.db.backends.postgresql`.
  3. Tắt `MAINTENANCE_MODE=False` và mở lại ứng dụng trên CSDL PostgreSQL gốc.
- **RPO (Mất mát dữ liệu):** **0 giây** (PostgreSQL được đóng băng nguyên vẹn trong suốt quá trình).
- **RTO (Thời gian phục hồi):** **< 5 phút**.

#### Giai đoạn 2: Phục hồi sau khi D1 đã nhận dữ liệu mới (Post-Cutover Disaster Recovery)
- **Bối cảnh:** Ứng dụng đã mở lại ghi cho người dùng trên CSDL D1. Người dùng đã tạo tài sản mới, duyệt phiếu mới và đổi mật khẩu trên D1. Không thể đơn giản quay về PostgreSQL cũ vì các dữ liệu mới này sẽ bị biến mất.
- **Phương án phục hồi:**
  1. **Cloudflare D1 Time Travel:** Sử dụng tính năng Time Travel của D1 để khôi phục trạng thái CSDL về bất kỳ bookmark hoặc mốc thời gian nào trong vòng tối đa **7 ngày** (giới hạn chính thức của gói Cloudflare Workers Free; 30 ngày yêu cầu gói Paid):
     `npx wrangler d1 time-travel restore assethub-d1 --timestamp="2026-11-03T10:00:00Z"`
  2. **Sao lưu định kỳ (Scheduled D1 Dumps):** Cronjob sao lưu định kỳ chạy lệnh `npx wrangler d1 export assethub-d1 --remote --output=backup.sql` mỗi ngày lưu trữ trên Cloudflare R2 hoặc máy chủ lưu trữ độc lập.
  3. **Mục tiêu thiết kế (Design Targets - chưa phải kết quả diễn tập khôi phục thực tế):**
     - **RPO (Mất mát dữ liệu tối đa thiết kế):** **< 15 phút** (theo bookmark Time Travel của Cloudflare).
     - **RTO (Thời gian khôi phục dịch vụ thiết kế):** **< 30 phút**.

### 5.3. Tiêu Chí Đối Soát Dữ Liệu 1:1 (Reconciliation Checklist)
Trước khi mở ghi D1, script đối soát tự động phải xác nhận:
1. `COUNT(*)` khớp 100% trên toàn bộ các bảng nghiệp vụ.
2. `ID` và khóa chính (Primary Keys) bảo toàn chính xác từng bản ghi.
3. Liên kết khóa ngoại (`department_id`, `category_id`, `assigned_to_user_id`, `requester_id`, `asset_id`) khớp 100%.
4. Chuỗi băm mật khẩu `password` khớp chính xác từng ký tự (đặc biệt các tham số PBKDF2 `$pbkdf2_sha256$1000000$...`).
5. Giá trị bộ đếm sinh mã `assets_identifiersequence` tại D1 phải lớn hơn hoặc bằng giá trị lớn nhất hiện hữu trên PostgreSQL.
6. Toàn bộ định dạng ngày giờ UTC được chuẩn hóa theo chuẩn ISO 8601.

---

## 6. Dịch Vụ Cloudflare Mở Rộng: CLI, R2, AI, Pages & Ingress

### 6.1. Bảng Phân Định Vai Trò Các Công Cụ Dòng Lệnh (CLI Tools)
Tuần 4 yêu cầu nắm vững và sử dụng các công cụ dòng lệnh chính thức:
- **`wrangler` (Cloudflare Developer Platform CLI):** Quản lý vòng đời Worker Bridge, thiết lập bí mật (`wrangler secret put`), thực thi DDL và truy vấn CSDL Cloudflare D1 (`wrangler d1 execute`).
- **`cloudflared` (Cloudflare Tunnel CLI):** Thiết lập đường hầm bảo mật mã hóa HTTPS giữa container Django nội bộ và Cloudflare Edge mà không cần mở port public hay cấu hình NAT/firewall phức tạp.
- **`cf CLI` (Cloudflare v4 REST API CLI / wrapper):** Công cụ dòng lệnh quản lý tài nguyên cấp tài khoản, zone DNS và token xác thực theo tài liệu chính thức Cloudflare.

### 6.2. Cloudflare R2 Storage (Lưu trữ ảnh & tài liệu)
- **Cấu hình S3 Client:** Sử dụng `boto3` và `django-storages` với endpoint cấu hình theo Account ID:
  `ENDPOINT_URL = https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com`
  `AWS_STORAGE_BUCKET_NAME = assethub-media`
- **Chính sách Bucket:** Bucket ở chế độ riêng tư (Private). Truy cập tải ảnh và tài liệu qua Presigned URLs có hạn dùng (15 phút).
- **Hạn mức & Lưu ý chi phí:** Gói Free của R2 cấp miễn phí 10 GB lưu trữ, 1.000.000 lượt thao tác Class A (write/list), 10.000.000 lượt thao tác Class B (read)/tháng. Lưu ý: Kích hoạt R2 cần thẻ thanh toán quốc tế xác minh tài khoản nhưng không bị trừ tiền nếu nằm trong hạn mức miễn phí. Không tự ý kích hoạt nếu người dùng chưa cho phép.

### 6.3. Cloudflare Workers AI Microservice
- **Model được hỗ trợ chính thức:** Sử dụng model mã nguồn mở hoạt động ổn định trên Workers AI: `@cf/meta/llama-3-8b-instruct` (hoặc `@cf/meta/llama-3.1-8b-instruct`).
- **Nhiệm vụ:** Tiếp nhận mô tả tài sản từ backend Django, phân tích đặc tính kỹ thuật và gợi ý mã danh mục (`Category`) phù hợp nhất.
- **Hạn mức gói Free:** ~10.000 Neurons/ngày miễn phí, hoàn toàn đáp ứng nhu cầu chạy thử nghiệm và demo đồ án.

### 6.4. Triển Khai Cloudflare Pages, Ingress & Cấu Hình Mạng (Domain / CORS)
- **Frontend SPA:** Triển khai bản build tĩnh của React (`dist/`) lên **Cloudflare Pages** với domain mặc định `assethub.pages.dev` (hoặc domain sinh viên tự gắn).
- **Cấu hình API Routing:**
  - Frontend gọi API backend thông qua URL cấu hình: `VITE_API_BASE_URL=https://api.assethub.vn` (hoặc hostname Tunnel Cloudflare).
- **Cấu hình CORS & CSRF trên Django:**
  - `CORS_ALLOWED_ORIGIN_REGEXES = [r"^https:\/\/.*\.pages\.dev$"]` và origin localhost dev.
  - `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` để Django nhận biết kết nối HTTPS an toàn từ Cloudflare Edge.
  - `ALLOWED_HOSTS` bao gồm hostname Tunnel và domain production được chỉ định.

---

## 7. Kế Hoạch Triển Khai Gói Công Việc & Điều Kiện Chặn Cứng (Gating Architecture)

> [!IMPORTANT]
> **Task 0 (Implementation Blockers Resolution Gate):** Phải được thực thi và nghiệm thu 100% trước khi bắt đầu bất kỳ task nào phụ thuộc vào giao thức D1 Bridge hoặc ORM mutations.
> 
> **Hai blocker kỹ thuật Codex xác nhận cần giải quyết trong Task 0:**
> 1. **Mệnh đề RETURNING có Quoted Identifiers do Django sinh:**
>    - Biểu thức phân tích trong Worker Bridge phải nhận diện chính xác các biến thể: `RETURNING "id"`, `RETURNING "departments_department"."id"`, `RETURNING id`, `RETURNING departments_department.id`.
>    - Thu hẹp rõ phạm vi: Chỉ trích xuất `id` (primary key). Nếu câu lệnh chứa RETURNING đa cột hoặc biểu thức tính toán tùy ý mà sập trước UPDATE, Worker trả về mã lỗi giao thức rõ ràng (`500 Incomplete State`), không được giả mạo trả kết quả thiếu cột thành công.
> 2. **Batch Fallback không giả định số liệu (No Assumed Results):**
>    - Tuyệt đối loại bỏ việc hardcode `meta.changes: 1` hoặc kết quả vỏ giả tạo trong `initialBatchResults`.
>    - Nếu Worker sập trước UPDATE, bản ghi ban đầu chỉ chứa thông tin trạng thái transaction (`COMMITTED_PENDING_RESOLUTION`). Khi client replay, nếu kết quả chi tiết từng câu lệnh chưa thể tái dựng an toàn, Worker từ chối với HTTP 500 kèm mã lỗi giao thức cụ thể, buộc client hoặc retry hoặc xử lý lỗi giao dịch, không được trả HTTP 200 với kết quả rỗng giả vờ thành công.
> 
> **Tiêu chí nghiệm thu Task 0:**
> - Bộ regression tests độc lập (chạy trên Node test runner và Pytest integration) bao phủ:
>   - INSERT với `RETURNING "id"` (quoted) do Django sinh.
>   - INSERT với `RETURNING "table"."id"` (quoted & table-prefixed).
>   - Batch fault injection không trả về kết quả giả định.
> - Toàn bộ test của Task 0 phải PASS 100% thì mới mở khóa cho Gói 1 và Gói 4.

### Danh mục các Gói Công Việc Dự Kiến:
- **Task 0: Xử lý dứt điểm Implementation Blockers của D1 Bridge & Giao thức Idempotency** (Chặn cứng Gói 1, Gói 4).
- **Gói 1: Hoàn thiện D1 Database Backend Adapter & Client** (`assethub_api.backends.d1`).
- **Gói 2: Hoàn thiện Schema DDL D1 (18 bảng), Constraints & Triggers bảo vệ**.
- **Gói 3: Lệnh di chuyển dữ liệu `migrate_pg_to_d1` & Kịch bản đối soát dữ liệu 1:1**.
- **Gói 4: Chuyển đổi các Business Services (`AssetService`, `RequestService`) sang Atomic Batch D1**.
- **Gói 5: Tích hợp Cloudflare R2 Storage & Workers AI Proxy Microservice**.
- **Gói 6: Cấu hình Cloudflare Pages Build, Ingress Tunnel & E2E Verification hoàn tất Tuần 4**.

---

## 8. Nguồn Tài Liệu Đối Chiếu Chính Thức (Cập Nhật Tháng 11/2026)

- [Cloudflare D1 Worker API Reference](https://developers.cloudflare.com/d1/worker-api/d1-database/)
- [Cloudflare D1 Foreign Keys Documentation](https://developers.cloudflare.com/d1/sql-api/foreign-keys/)
- [Cloudflare D1 Platform Limits](https://developers.cloudflare.com/d1/platform/limits/)
- [Cloudflare Workers Platform Limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Cloudflare R2 Storage Overview & Pricing](https://developers.cloudflare.com/r2/get-started/)
- [Cloudflare Workers AI Llama Models](https://developers.cloudflare.com/workers-ai/models/)
- [Cloudflare Tunnel (cloudflared) Guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [Cloudflare cf CLI Documentation](https://developers.cloudflare.com/cf/)
