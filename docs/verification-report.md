# Báo cáo kiểm chứng triển khai AssetHub

Ngày bắt đầu: 2026-10-29.

Tài liệu này lưu lại các bằng chứng kiểm thử thực tế và phiên bản môi trường sau mỗi task triển khai. Không ghi nhận hoàn thành nếu chưa có kết quả chạy thực tế với exit code 0.

---

## 1. Môi trường và công cụ phát triển

| Công cụ | Phiên bản ghi nhận | Ghi chú |
| :--- | :--- | :--- |
| **Git** | `2.51.2` | Host |
| **Docker** | `29.9.2, build a82e528` | Host daemon |
| **Docker Compose** | `v5.6.1` | Compose v2 |
| **Python (Host)** | `3.13.13` | Host runtime |
| **Python (Container)** | `3.12.16` | Container base `python:3.12-slim` |
| **Node.js** | `v22.20.0` | Host runtime |
| **npm** | `10.9.3` | Host package manager |
| **PostgreSQL** | `16.15` | Container `postgres:16-alpine` |
| **Django** | `5.2.18` | Container pinned dependencies |
| **Django REST Framework** | `3.18.3` | Container pinned dependencies |
| **drf-spectacular** | `0.30.0` | OpenAPI 3 schema generator |
| **SimpleJWT** | `5.5.1` | JWT authentication |
| **psycopg** | `3.3.6` (binary) | PostgreSQL DB adapter |
| **pytest** | `9.2.0` | Test runner |
| **pytest-django** | `4.14.0` | Django test plugin |

---

## 2. Kiểm chứng theo Task

### Task 0: Bootstrap, dependency, Compose và runner

- **Ngày thực hiện:** 2026-10-29
- **Trạng thái:** HOÀN THÀNH (Exit 0)

#### Lệnh 1: Xác thực cú pháp Docker Compose
```bash
docker compose config --quiet
```
- **Exit code:** 0
- **Kết quả:** Cấu hình base `docker-compose.yml` hợp lệ.

#### Lệnh 2: Khởi chạy cụm dịch vụ dev
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d db backend
```
- **Exit code:** 0
- **Kết quả:** Build container backend thành công; khởi chạy `assethub-db-1` và `assethub-backend-1`. Cả hai container đều đạt trạng thái `healthy`.

#### Lệnh 3: Chạy database migrations
```bash
docker compose exec -T backend python manage.py migrate --noinput
```
- **Exit code:** 0
- **Kết quả:**
```text
Operations to perform:
  Apply all migrations: admin, auth, authentication, contenttypes, departments, sessions
Running migrations:
  Applying contenttypes.0001_initial... OK
  Applying departments.0001_initial... OK
  Applying contenttypes.0002_remove_content_type_name... OK
  Applying auth.0001_initial... OK
  Applying auth.0002_alter_permission_name_max_length... OK
  Applying auth.0003_alter_user_email_max_length... OK
  Applying auth.0004_alter_user_username_opts... OK
  Applying auth.0005_alter_user_last_login_null... OK
  Applying auth.0006_require_contenttypes_0002... OK
  Applying auth.0007_alter_validators_add_error_messages... OK
  Applying auth.0008_alter_user_username_max_length... OK
  Applying auth.0009_alter_user_last_name_max_length... OK
  Applying auth.0010_alter_group_name_max_length... OK
  Applying auth.0011_update_proxy_permissions... OK
  Applying auth.0012_alter_user_first_name_max_length... OK
  Applying authentication.0001_initial... OK
  Applying admin.0001_initial... OK
  Applying admin.0002_logentry_remove_auto_add... OK
  Applying admin.0003_logentry_add_action_flag_choices... OK
  Applying sessions.0001_initial... OK
```

#### Lệnh 4: Kiểm tra cấu hình Django
```bash
docker compose exec -T backend python manage.py check
```
- **Exit code:** 0
- **Kết quả:** `System check identified no issues (0 silenced).`

#### Lệnh 5: Kiểm tra tính toàn vẹn migrations
```bash
docker compose exec -T backend python manage.py makemigrations --check --dry-run
```
- **Exit code:** 0
- **Kết quả:** `No changes detected` (Không có migration nào bị thiếu hoặc lệch schema).

#### Lệnh 6: Chạy kiểm thử tự động pytest
```bash
docker compose exec -T backend pytest tests/test_bootstrap.py -q
```
- **Exit code:** 0
- **Kết quả:** `7 passed in 0.86s`
- **Danh sách tests:**
  - `test_health_check_endpoint`: Kiểm tra GET `/api/health/` trả về HTTP 200, json `{"status": "ok", "db": "ok"}`, không để lộ thông tin cấu hình nhạy cảm.
  - `test_health_check_database_failure`: Kiểm tra GET `/api/health/` trả về HTTP 503 khi mất kết nối database.
  - `test_custom_user_model_configured`: Xác nhận `AUTH_USER_MODEL` trỏ đúng vào `authentication.User`.
  - `test_user_model_defaults`: Xác nhận các giá trị mặc định của User model (`role=USER`, `auth_version=1`, `department=None`).
  - `test_user_roles_and_department_fixtures`: Xác nhận fixture hai phòng ban và 3 vai trò (Admin, Manager, User), quan hệ khóa ngoại hai chiều.
  - `test_department_unique_code`: Xác nhận ràng buộc duy nhất trên mã phòng ban `code`.
  - `test_user_department_set_null_on_delete`: Xác nhận khi xóa phòng ban, trường `department` của User chuyển thành `null` (`SET_NULL`).

#### Lệnh 7: Kiểm tra truy cập endpoint từ host
```bash
curl -i http://127.0.0.1:8000/api/health/
```
- **Exit code:** 0
- **Kết quả:** HTTP 200 OK với body `{"status":"ok","db":"ok"}`.

#### Lệnh 8: Xác nhận review fix
- Đã cập nhật `CORS_ALLOW_CREDENTIALS = False` trong `settings.py` theo đúng Spec §10.
- Đã thêm ràng buộc `email = models.EmailField(unique=True, blank=False)` trên User model theo đúng Spec §4.

---

### Task 1: Đăng ký, JWT, hồ sơ và thu hồi phiên

- **Ngày thực hiện:** 2026-10-29
- **Trạng thái:** HOÀN THÀNH (Exit 0)

#### Lệnh 1: Kiểm tra tính toàn vẹn migrations
```bash
docker compose exec -T backend python manage.py makemigrations --check --dry-run
```
- **Exit code:** 0
- **Kết quả:** `No changes detected`

#### Lệnh 2: Xác thực OpenAPI Schema với drf-spectacular
```bash
docker compose exec -T backend python manage.py spectacular --validate --fail-on-warn
```
- **Exit code:** 0
- **Kết quả:** Schema OpenAPI 3.0.3 tạo thành công không có cảnh báo hoặc lỗi (`Warnings: 0, Errors: 0`). Đã đăng ký `CustomJWTScheme` OpenAPI extension cho `CustomJWTAuthentication`.

#### Lệnh 3: Chạy kiểm thử tự động pytest cho authentication và hồi quy
```bash
docker compose exec -T backend pytest tests/test_auth.py -v
```
- **Exit code:** 0
- **Kết quả:** `10 passed in 6.73s`
- **Danh sách tests:**
  - `test_register_pending_user`: Kiểm tra POST `/api/auth/register/` tạo user `is_active=False`, `role=USER`, `department=None`, trả về HTTP 201 kèm thông báo, không cấp JWT.
  - `test_register_rejects_privileged_fields`: Kiểm tra POST `/api/auth/register/` từ chối các trường đặc quyền (`role`, `is_active`, `department`) và trường ngoài allowlist với HTTP 400.
  - `test_register_validation_errors`: Kiểm tra validation email/username trùng lặp và mật khẩu yếu theo Django password validators.
  - `test_inactive_cannot_login`: Kiểm tra tài khoản chưa kích hoạt hoặc sai credentials bị từ chối với HTTP 401.
  - `test_login_refresh_and_expiry`: Kiểm tra POST `/api/auth/login/` trả về access (10 phút), refresh (1 ngày) và thông tin user; POST `/api/auth/refresh/` kiểm tra DB user và `auth_version`, trả access token mới hợp lệ; refresh token giả/hết hạn bị từ chối với HTTP 401.
  - `test_profile_rejects_role_and_department`: Kiểm tra GET `/api/auth/profile/` và PATCH `/api/auth/profile/` chỉ cho phép cập nhật `full_name`, `email`, `phone`; từ chối các trường `role`, `department`, `is_active`, trường lạ với HTTP 400.
  - `test_change_password_revokes_old_access_and_refresh`: Kiểm tra POST `/api/auth/change-password/` xác minh mật khẩu cũ, validate mật khẩu mới, tăng `auth_version`, trả về HTTP 204. Thu hồi ngay lập tức access token cũ và refresh token cũ (HTTP 401), từ chối đăng nhập mật khẩu cũ (HTTP 401), cho phép đăng nhập mật khẩu mới (HTTP 200).
  - `test_logout_revokes_all_sessions`: Kiểm tra POST `/api/auth/logout/` tăng `auth_version`, thu hồi toàn bộ token trên mọi phiên đăng nhập (Session A và Session B đều nhận HTTP 401 trên access và refresh).
  - `test_deactivated_user_token_rejected_immediately`: Kiểm tra người dùng bị vô hiệu hóa trong DB (`is_active=False`) bị từ chối ngay lập tức khi dùng token cũ (HTTP 401) hoặc refresh (HTTP 401).
  - `test_throttle_login_and_register`: Kiểm tra rate limiting theo IP cho login và register (10 requests/phút), vượt quá giới hạn nhận HTTP 429 Too Many Requests.

#### Lệnh 4: Chạy toàn bộ test suite
```bash
docker compose exec -T backend pytest -v
```
- **Exit code:** 0
- **Kết quả:** `17 passed in 7.42s` (toàn bộ 17 tests của Task 0 và Task 1 đều pass).

---

### Task 2: Quản trị tài khoản, phòng ban, danh mục và RBAC

- **Ngày thực hiện:** 2026-10-29
- **Trạng thái:** HOÀN THÀNH (Exit 0)

#### Lệnh 1: Kiểm tra tính toàn vẹn migrations
```bash
docker compose exec -T backend python manage.py makemigrations --check --dry-run
```
- **Exit code:** 0
- **Kết quả:** `No changes detected` (Schema DB khớp 100% với models hiện hành).

#### Lệnh 2: Xác thực OpenAPI Schema với drf-spectacular
```bash
docker compose exec -T backend python manage.py spectacular --validate --fail-on-warn
```
- **Exit code:** 0
- **Kết quả:** Tạo schema OpenAPI 3.0.3 thành công cho các endpoints mới `/api/users/`, `/api/departments/`, `/api/categories/` với 0 cảnh báo và 0 lỗi.

#### Lệnh 3: Chạy kiểm thử tự động pytest cho RBAC, User Admin và hồi quy Auth
```bash
docker compose exec -T backend pytest tests/test_rbac.py tests/test_user_admin.py tests/test_auth.py -v
```
- **Exit code:** 0
- **Kết quả:** `50 passed in 16.30s`
- **Danh sách kiểm thử chính:**
  - **RBAC Departments & Categories (`test_rbac.py` - 21 tests):**
    - Admin toàn quyền CRUD trên danh mục và phòng ban.
    - Cấm thao tác DELETE và PUT trên Departments và Categories (trả về HTTP 405 Method Not Allowed).
    - Manager và User chỉ được đọc thông tin phòng ban của chính mình (`get_queryset` lọc theo `department_id`); truy cập phòng ban khác trả về HTTP 404.
    - Manager và User chỉ có quyền đọc danh mục (`GET /api/categories/`), không được tạo/sửa (nhận HTTP 403).
    - Người dùng chưa đăng nhập bị chặn toàn bộ (HTTP 401).
  - **User Admin (`test_user_admin.py` - 19 tests):**
    - Admin liệt kê toàn bộ tài khoản, hỗ trợ tìm kiếm và lọc theo `is_active`, `role`, `department_id`.
    - Admin tạo tài khoản có gán vai trò và phòng ban; bắt buộc phải có phòng ban nếu `role` là USER hoặc MANAGER khi `is_active=True`.
    - Admin kích hoạt pending user và chỉ định phòng ban. Chặn kích hoạt nếu thiếu phòng ban (HTTP 400).
    - Cấm vô hiệu hóa hoặc hạ quyền tài khoản Admin hoạt động cuối cùng trong hệ thống (HTTP 400).
    - Đổi `role`, `department`, hoặc `is_active` thực hiện tăng `auth_version` trong transaction atomic, làm mất hiệu lực token ngay lập tức (token cũ nhận HTTP 401).
    - Các endpoints quản trị người dùng không bao giờ trả về trường `password`, `password_hash` hay `auth_version`.
    - Manager chỉ xem được danh sách và chi tiết nhân viên trong phòng ban của mình; không được xem nhân viên phòng khác (HTTP 404); không được tạo, sửa, xóa tài khoản (HTTP 403/405).
    - User bị chặn hoàn toàn khỏi `/api/users/` (HTTP 403).
    - Service guard `check_can_modify_user_department_or_status` trả về HTTP 409 Conflict khi tài khoản đang giữ tài sản hoặc có phiếu chờ xử lý.
  - **Regression Auth (`test_auth.py` - 10 tests):**
    - Toàn bộ 10 tests của Task 1 đều PASS ổn định, không có hồi quy.

---

### Task 3: Hồ sơ tài sản, mã tự sinh, allowlist và seed data

- **Ngày thực hiện:** 2026-10-29
- **Trạng thái:** HOÀN THÀNH (Exit 0)

#### Lệnh 1: Kiểm tra tính toàn vẹn migrations
```bash
docker compose exec -T backend python manage.py makemigrations --check --dry-run
```
- **Exit code:** 0
- **Kết quả:** `No changes detected` (Schema DB khớp 100% với model Asset, IdentifierSequence, AssetHistoryLog).

#### Lệnh 2: Xác thực OpenAPI Schema với drf-spectacular
```bash
docker compose exec -T backend python manage.py spectacular --validate --fail-on-warn
```
- **Exit code:** 0
- **Kết quả:** Schema OpenAPI 3.0.3 tạo thành công cho các endpoints `/api/assets/` với 0 cảnh báo và 0 lỗi.

#### Lệnh 3: Chạy kiểm thử tự động pytest cho Asset, Identifiers, Seed Data và Service Guard
```bash
docker compose exec -T backend pytest tests/test_assets.py tests/test_identifiers.py tests/test_seed_data.py tests/test_user_admin.py -v
```
- **Exit code:** 0
- **Kết quả:** `47 passed in 18.52s`
- **Danh sách kiểm thử chính:**
  - **Asset CRUD & Permissions (`test_assets.py` - 24 tests):**
    - Admin tạo tài sản mới: bắt buộc trạng thái `AVAILABLE`, `assigned_to_user=None`; tự động sinh mã định dạng `AST-YYYY-NNN`; tạo 1 bản ghi `CREATE` `AssetHistoryLog` có snapshot mã và tên.
    - POST từ chối khi client gửi trường mã tài sản, trạng thái, hoặc người giữ (HTTP 400).
    - Cấm thao tác DELETE trên Asset (HTTP 405 Method Not Allowed).
    - PATCH chỉ cho phép các trường allowlist: `name`, `category_id`, `serial_number`, `specification`, `purchase_date`. Gửi các trường trạng thái, phòng ban, người giữ, mã tài sản trả về HTTP 400 kèm thông báo lỗi rõ ràng.
    - Phân quyền theo phạm vi: Admin xem toàn bộ tài sản và lọc theo phòng ban (kể cả nhóm chưa phân bổ `unallocated`); Manager chỉ xem tài sản thuộc phòng ban mình; User chỉ xem tài sản được cấp cho chính mình.
    - Manager và User bị cấm tạo hoặc sửa tài sản (HTTP 403).
    - Model check constraints: chặn tài sản ở trạng thái AVAILABLE, PENDING_DISPOSAL, DISPOSED có người giữ; chặn tài sản kho công ty (`department=null`) có người giữ.
    - Bảng `AssetHistoryLog` là append-only: chặn thao tác sửa hoặc xóa trên bản ghi đã lưu.
  - **Mã tự sinh (`test_identifiers.py` - 5 tests):**
    - Kiểm tra định dạng `AST-YYYY-001`, `REQ-YYYY-001`, tự động reset/tách biệt theo từng năm.
    - Hỗ trợ số thứ tự >= 1000 mà không bị lỗi độ dài (`AST-2026-1000`).
    - Kiểm tra cạnh tranh tranh chấp (concurrency race condition) khi tạo dòng sequence đầu tiên trong năm bằng `select_for_update()` và retry logic.
  - **Demo Seed Command (`test_seed_data.py` - 6 tests):**
    - Bắt buộc cờ `--demo`, yêu cầu biến môi trường `ALLOW_DEMO_SEED=true` và `DEMO_SEED_PASSWORD`.
    - Tạo chuẩn: 1 Admin, 2 Managers, 4 Users, 2 Phòng ban, 5 Danh mục, 15 Tài sản phân bổ đủ 6 trạng thái nghiệp vụ.
    - Tính bất biến (idempotent): Chạy lệnh 2 lần liên tiếp không sinh trùng tài sản, không nhân bản người dùng, không reset mật khẩu.
    - Không in mật khẩu hoặc secret ra console stdout.
  - **Service Guard User Admin (`test_user_admin.py`):**
    - Tích hợp bảo vệ: Không cho phép đổi phòng ban hoặc vô hiệu hóa tài khoản đang giữ tài sản (trả về HTTP 409 Conflict).

