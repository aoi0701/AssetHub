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

---

### Task 4: State machine, quyền action và audit service

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
- **Kết quả:** Schema OpenAPI 3.0.3 tạo thành công cho 8 detail actions và endpoint lịch sử với 0 cảnh báo và 0 lỗi.

#### Lệnh 3: Chạy kiểm thử tự động pytest cho State Machine, Permissions và Assets
```bash
docker compose exec -T backend pytest tests/test_assets_state_machine.py tests/test_asset_permissions.py tests/test_assets.py -v
```
- **Exit code:** 0
- **Kết quả:** `31 passed in 9.30s`
- **Danh sách kiểm thử chính:**
  - **Vòng đời toàn diện máy trạng thái (`test_assets_state_machine.py` - 10 tests):**
    - `test_full_asset_lifecycle_transitions_and_history_logs`: Mô phỏng chuỗi 8 bước hoàn chỉnh: CREATE AVAILABLE -> allocate IN_USE -> revoke GOOD AVAILABLE -> transfer AVAILABLE -> mark-damaged DAMAGED_PENDING_REPAIR -> send-maintenance UNDER_MAINTENANCE -> propose-disposal PENDING_DISPOSAL -> confirm-disposal DISPOSED. Xác nhận đúng mã HTTP từng bước và chính xác 8 bản ghi `AssetHistoryLog` với đầy đủ snapshot và trạng thái trước/sau.
    - `test_available_cannot_skip_damage_confirmation`: AVAILABLE gọi send-maintenance nhận HTTP 409 Conflict, không thay đổi DB/log.
    - `test_invalid_state_transitions_return_409`: Cố gắng chuyển trạng thái trái quy tắc máy trạng thái đều trả HTTP 409.
    - `test_disposed_asset_is_strictly_terminal`: Tài sản DISPOSED chặn toàn bộ các action và PATCH với HTTP 409.
    - `test_complete_maintenance_return_to_stock`: Hoàn tất bảo trì về kho chuyển sang AVAILABLE và xóa người giữ.
    - `test_complete_maintenance_return_to_user_success`: Hoàn tất bảo trì trả người cũ chuyển sang IN_USE khi người cũ còn hoạt động và cùng phòng.
    - `test_complete_maintenance_return_to_user_rejects_missing_or_invalid_holder`: Trả về HTTP 409 nếu người cũ bị vô hiệu hóa hoặc chuyển phòng.
    - `test_propose_disposal_with_holder_requires_received_confirmation`: Đề xuất thanh lý tài sản có người giữ bắt buộc `received_confirmed=True`, xóa người giữ có lưu log.
    - `test_revoke_damaged_transitions_to_damaged_pending_repair`: Thu hồi đánh giá DAMAGED chuyển sang DAMAGED_PENDING_REPAIR.
    - `test_actions_require_non_empty_reason_and_valid_inputs`: Kiểm tra mọi action đều bắt buộc trường `reason` không rỗng (HTTP 400).
  - **Phân quyền và Phạm vi Action (`test_asset_permissions.py` - 5 tests):**
    - `test_user_cannot_call_any_action_or_view_history`: User gọi bất kỳ action nào hoặc xem full history đều nhận HTTP 403.
    - `test_manager_cannot_transfer_or_confirm_disposal`: Manager cố gọi transfer hoặc confirm-disposal nhận HTTP 403 Forbidden.
    - `test_manager_accessing_other_department_asset_returns_404_for_all_actions_and_history`: Manager thao tác trên tài sản phòng ban khác trả về HTTP 404 Not Found.
    - `test_company_pool_assets_admin_access_manager_404`: Tài sản kho công ty (`department=None`) do Admin quản lý; Manager truy cập nhận HTTP 404.
    - `test_manager_can_perform_permitted_actions_in_own_department`: Manager thực hiện thành công các action được phép trong phạm vi phòng mình.
  - **Hồi quy CRUD Asset (`test_assets.py` - 16 tests):**
    - Toàn bộ 16 tests của Task 3 tiếp tục PASS ổn định.

---

### Task 5: Phiếu, transaction, rollback và cạnh tranh

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
- **Kết quả:** Schema OpenAPI 3.0.3 tạo thành công cho các endpoints requests (List/Create, Detail, Approve, Reject) với 0 cảnh báo và 0 lỗi.

#### Lệnh 3: Chạy kiểm thử tự động pytest cho Requests, Concurrency và User Admin
```bash
docker compose exec -T backend pytest tests/test_asset_requests.py tests/test_concurrency.py tests/test_user_admin.py -v
```
- **Exit code:** 0
- **Kết quả:** `50 passed in 15.92s`
- **Danh sách kiểm thử chính:**
  - **Vòng đời phiếu yêu cầu (`test_asset_requests.py` - 26 tests):**
    - `test_user_can_create_allocate_request_success`: User tạo phiếu ALLOCATE thành công với `requested_category_id` và lý do, mã sinh tự động dạng `REQ-YYYY-NNN`, trạng thái `PENDING`.
    - `test_user_create_allocate_rejects_client_asset_id`: Chặn client gửi `asset_id` khi tạo ALLOCATE (HTTP 400).
    - `test_user_create_request_requires_non_empty_reason`: Chặn lý do rỗng khi tạo phiếu (HTTP 400).
    - `test_user_cannot_tamper_request_server_managed_fields`: Ngăn chặn giả mạo trạng thái, phòng ban, requester hoặc mã phiếu.
    - `test_user_can_create_return_request_success`: User tạo phiếu RETURN thành công cho tài sản đang giữ (IN_USE), không thay đổi trạng thái tài sản lúc gửi.
    - `test_user_cannot_create_return_for_available_asset`: Chặn tạo phiếu RETURN cho tài sản kho (HTTP 400).
    - `test_user_cannot_create_return_for_asset_assigned_to_another_user`: Chặn tạo phiếu RETURN cho tài sản của người khác (HTTP 400).
    - `test_user_cannot_create_duplicate_pending_return_request`: Chặn tạo trùng lặp phiếu RETURN đang chờ duyệt trên cùng tài sản (DB partial unique constraint & serializer validation).
    - `test_user_can_create_damage_report_success`: User gửi báo hỏng tài sản đang giữ thành công.
    - `test_user_cannot_create_duplicate_pending_damage_report`: Chặn tạo trùng lặp phiếu DAMAGE_REPORT đang chờ duyệt trên cùng tài sản.
    - `test_request_scoping_for_user_manager_and_admin`: Phân quyền phạm vi: User chỉ thấy phiếu cá nhân; Manager chỉ thấy phiếu trong phòng ban; Admin thấy toàn bộ công ty; xem chi tiết ngoài phạm vi trả về HTTP 404.
    - `test_manager_can_approve_allocate_request_success`: Manager duyệt ALLOCATE với `selected_asset_id`, tài sản chuyển sang IN_USE giao cho requester, lưu asset vào phiếu, cập nhật trạng thái APPROVED, tạo 1 log ALLOCATE.
    - `test_approve_allocate_requires_selected_asset_id`: Duyệt ALLOCATE bắt buộc chọn tài sản (HTTP 400).
    - `test_approve_allocate_rejects_asset_with_mismatched_category`: Chặn cấp tài sản khác danh mục yêu cầu (HTTP 400).
    - `test_approve_allocate_rejects_asset_not_available`: Tài sản không AVAILABLE trả về HTTP 409 Conflict, phiếu vẫn giữ nguyên PENDING.
    - `test_approve_return_success_condition_good`: Duyệt trả tài sản với condition GOOD chuyển về AVAILABLE, xóa người giữ, tạo log RETURN.
    - `test_approve_return_success_condition_damaged`: Duyệt trả tài sản với condition DAMAGED chuyển về DAMAGED_PENDING_REPAIR, xóa người giữ, tạo log RETURN.
    - `test_approve_return_requires_received_confirmed_and_valid_condition`: Duyệt trả bắt buộc `received_confirmed=True` và `condition` hợp lệ (HTTP 400).
    - `test_approve_damage_report_success_retains_holder`: Duyệt báo hỏng chuyển tài sản sang DAMAGED_PENDING_REPAIR và GIỮ NGUYÊN người chịu trách nhiệm, tạo log DAMAGE.
    - `test_approve_damage_report_fails_if_asset_not_in_use`: Tài sản không còn IN_USE trả về HTTP 409, phiếu giữ nguyên PENDING.
    - `test_manager_can_reject_request_with_reason`: Manager từ chối phiếu với lý do, cập nhật REJECTED, tài sản giữ nguyên trạng thái.
    - `test_reject_request_requires_non_empty_rejection_reason`: Từ chối bắt buộc có lý do (HTTP 400).
    - `test_user_cannot_approve_or_reject`: User gọi duyệt/từ chối nhận HTTP 403 Forbidden.
    - `test_manager_cannot_approve_or_reject_other_department_request`: Manager phòng ban khác duyệt/từ chối nhận HTTP 404 Not Found.
    - `test_repeated_approve_or_reject_returns_conflict`: Thao tác duyệt/từ chối lại phiếu đã xử lý trả về HTTP 409 Conflict.
    - `test_fault_injection_log_creation_failure_rolls_back_asset_and_request`: Fault injection khi ghi log trong giao dịch duyệt đảm bảo rollback hoàn toàn cả tài sản và phiếu, không để xảy ra trạng thái APPROVED một phần.
  - **Kiểm thử cạnh tranh dữ liệu trên PostgreSQL thật (`test_concurrency.py` - 3 tests):**
    - `test_concurrent_allocate_one_winner`: Hai luồng đồng thời duyệt cấp cùng một tài sản AVAILABLE cho hai phiếu khác nhau -> chính xác một luồng thành công (200), một luồng nhận HTTP 409 Conflict, chính xác một log ALLOCATE được ghi.
    - `test_concurrent_approve_same_request_once`: Hai luồng đồng thời duyệt cùng một phiếu -> một luồng thành công (200), một luồng nhận HTTP 409 Conflict, phiếu chỉ được duyệt đúng một lần.
    - `test_concurrent_identifier_creation`: 8 luồng đồng thời sinh mã định danh khi counter trong DB chưa tồn tại -> toàn bộ 8 mã đều duy nhất, theo đúng chuẩn REQ-YYYY-NNN, không phát sinh lỗi duplicate key.
  - **Kiểm thử Guard bảo vệ tài khoản User (`test_user_admin.py`):**
    - `test_real_service_guard_with_pending_request_blocks_department_or_deactivation`: Tích hợp bảo vệ tài khoản khi User có phiếu đang PENDING -> Admin cố tình đổi phòng ban hoặc vô hiệu hóa tài khoản sẽ bị chặn với HTTP 409 Conflict; sau khi phiếu được giải quyết thì cho phép thay đổi bình thường.

#### Lệnh 4: Chạy toàn bộ test suite dự án (Hồi quy toàn diện)
```bash
docker compose exec -T backend pytest -q
```
- **Exit code:** 0
- **Kết quả:** `130 passed in 36.67s` (100% passed, 0 failures, 0 regressions)

---

### Task 6: Lịch sử cá nhân và báo cáo đúng phạm vi (Hoàn thành: 2026-10-29)

#### Lệnh 1: Kiểm thử TDD lịch sử cá nhân và báo cáo thống kê
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/test_personal_history.py tests/test_reports.py -v
```
- **Exit code:** 0
- **Kết quả:** `15 passed in 3.69s`
- **Nội dung kiểm thử chi tiết:**
  - **Lịch sử cá nhân (`test_personal_history.py` - 6 tests):**
    - `test_personal_history_survives_return_and_transfer`: Tài sản cấp phát cho User A, A hoàn trả, Admin chuyển sang phòng ban khác và cấp cho User B. User A chỉ thấy đúng 2 sự kiện cá nhân của mình (`ALLOCATE` và `RETURN`), không thể thấy thông tin về User B hay các sự kiện sau đó. Serialization an toàn: chỉ chứa 9 trường (`id`, `asset_id`, `asset_code_snapshot`, `asset_name_snapshot`, `action_type`, `from_status`, `to_status`, `created_at`, `message`), hoàn toàn không chứa lý do nhạy cảm, actor hay tên người khác.
    - `test_user_cannot_access_full_asset_history`: User truy cập timeline quản trị `/api/assets/{id}/history/` bị từ chối với HTTP 403 Forbidden.
    - `test_user_cannot_query_personal_history_of_other_users`: User gửi query param `user` hoặc `user_id` để xem lịch sử người khác bị từ chối với HTTP 403 Forbidden.
    - `test_personal_history_unauthenticated`: Người dùng chưa xác thực truy cập nhận HTTP 401 Unauthorized.
    - `test_personal_history_pagination`: Phân trang chuẩn `PageNumberPagination` (20 mục/trang), kiểm tra chính xác `count`, `next`, `results`.
    - `test_personal_history_survives_asset_disposal`: Lịch sử cá nhân tồn tại bền vững ngay cả sau khi tài sản đã bị thanh lý (`DISPOSED`).
  - **Báo cáo và thống kê (`test_reports.py` - 9 tests):**
    - `test_reports_user_forbidden`: User thông thường bị chặn truy cập `/api/reports/kpis/` và `/api/reports/charts/` với HTTP 403.
    - `test_manager_scoping_and_forbidden_on_other_department`: Manager bị khóa chặt trong phòng ban của mình; truy vấn phòng ban khác hoặc `unallocated` nhận HTTP 403.
    - `test_manager_department_distribution_is_empty`: Manager nhận mảng rỗng `[]` đối với biểu đồ phân bố toàn công ty `department_distribution`.
    - `test_kpi_counts_and_exclusion_of_disposed`: 6 chỉ số KPI (`total_active`, `available`, `in_use`, `damaged_under_maintenance`, `pending_disposal`, `pending_requests`), loại trừ hoàn toàn tài sản đã thanh lý (`DISPOSED`), mỗi tài sản chỉ được đếm một lần theo ID.
    - `test_admin_reports_department_filter_and_unallocated`: Admin có thể lọc theo từng phòng ban hoặc nhóm tài sản chưa phân bổ `unallocated` (kho công ty có `pending_requests = 0`).
    - `test_reports_category_filter`: Bộ lọc danh mục áp dụng chuẩn xác cho tài sản và các loại phiếu yêu cầu (`ALLOCATE` theo `requested_category_id`, `RETURN`/`DAMAGE_REPORT` theo `asset__category_id`).
    - `test_admin_charts_distributions`: Kiểm tra đúng cấu trúc phân bố trạng thái (loại trừ `DISPOSED`), phân bố danh mục, và phân bố phòng ban gồm cả nhóm `Chưa phân bổ`.
    - `test_monthly_trend_zero_padding_and_validation`: Biểu đồ biến động 12 tháng tự động bù 0 cho các tháng không phát sinh; chặn các khoảng thời gian > 12 tháng hoặc sai định dạng với HTTP 400.
    - `test_monthly_trend_asia_bangkok_boundary_and_snapshots`: Thống kê tháng theo múi giờ `Asia/Bangkok` (+07:00), kiểm tra ranh giới UTC/BKK; số liệu nhập mới (`CREATE`) và thanh lý (`DISPOSAL_CONFIRM`) căn cứ theo snapshot `to_department` trong log lịch sử chứ không phụ thuộc vào phòng ban hiện tại của tài sản.

#### Lệnh 2: Chạy toàn bộ test suite dự án (Hồi quy toàn diện)
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest
```
- **Exit code:** 0
- **Kết quả:** `145 passed in 40.39s` (100% passed, 0 failures, 0 regressions)

---

### Task 7: OpenAPI và Postman/Newman thực chạy (Hoàn thành: 2026-10-29)

#### Lệnh 1: Validate OpenAPI Schema (DRF Spectacular)
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py spectacular --file docs/openapi-schema.yml --validate --fail-on-warn
```
- **Exit code:** 0
- **Kết quả:** Schema OpenAPI 3.0 được sinh tại `backend/docs/openapi-schema.yml` (73KB) và validate thành công, 0 lỗi, 0 cảnh báo. Đầy đủ các paths API (`/api/auth/*`, `/api/users/*`, `/api/departments/*`, `/api/categories/*`, `/api/assets/*`, `/api/requests/*`, `/api/me/asset-history/`, `/api/reports/*`) và security scheme `jwtAuth` (HTTP Bearer).

#### Lệnh 2: Kiểm thử tự động kiểm tra tài liệu và Schema bằng Pytest
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/test_schema.py -v
```
- **Exit code:** 0
- **Kết quả:** `2 passed in 0.35s` (`test_swagger_ui_endpoint` và `test_schema_endpoint` đều trả HTTP 200).

#### Lệnh 3: Chạy thực tế Newman chống Backend Docker
```bash
npm --prefix tools/postman run test:api
```
- **Exit code:** 0
- **File báo cáo raw:** `logs/newman/report.json` (gitignored)
- **File môi trường local:** `tools/postman/environment.local.json` (`chmod 600`, gitignored)
- **File môi trường mẫu:** `backend/docs/postman/AssetHub.postman_environment.json` (chỉ chứa placeholder, không lộ mật khẩu)
- **Thống kê Newman thực chạy:**
  - Iterations: 1
  - Requests: 48
  - Test Scripts: 47
  - Prerequest Scripts: 6
  - **Assertions: 86 passed / 0 failed**
  - **Thời gian chạy:** 2.6s
  - **Modules kiểm thử:**
    - `00_HealthCheck`: Kiểm tra health status DB và API.
    - `01_Auth`: Đăng ký tài khoản (PENDING_APPROVAL) → Admin kích hoạt → Đăng nhập → Lấy profile → Cập nhật profile → Đổi mật khẩu → Đăng nhập lại với mật khẩu mới → Thu hồi phiên đăng xuất.
    - `02_Departments_and_Categories`: CRUD phòng ban và danh mục.
    - `03_User_Admin`: Quản lý danh sách người dùng, cập nhật vai trò/phòng ban, chặn vô hiệu hóa user đang giữ tài sản (HTTP 409).
    - `04_Assets_Lifecycle`: Toàn bộ vòng đời tài sản (Tạo mới AVAILABLE → Cấp phát IN_USE → Thu hồi AVAILABLE → Báo hỏng DAMAGED_PENDING_REPAIR → Đi bảo trì UNDER_MAINTENANCE → Hoàn thành bảo trì AVAILABLE → Chuyển phòng ban → Đề xuất thanh lý PENDING_DISPOSAL → Xác nhận thanh lý DISPOSED).
    - `05_Requests`: Tạo phiếu cấp phát, tạo phiếu hoàn trả, duyệt phiếu cấp phát (chuyển trạng thái tài sản tự động), duyệt/từ chối phiếu hoàn trả, chặn tạo trùng phiếu PENDING (HTTP 409).
    - `06_History_and_Reports`: Lịch sử quản trị tài sản (Admin), lịch sử tài sản cá nhân (`/api/me/asset-history/` an toàn cho User), báo cáo KPIs và các biểu đồ phân bố/biến động 12 tháng.
    - `07_Security_and_Negative_Tests`: User cố truy cập lịch sử quản trị (HTTP 403), User cố truy cập báo cáo KPIs (HTTP 403), Manager cố truy cập báo cáo kho chưa phân bổ (HTTP 403), duyệt lại phiếu đã đóng (HTTP 409).

#### Lệnh 4: Chạy toàn bộ test suite dự án (Hồi quy toàn diện)
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
- **Exit code:** 0
- **Kết quả:** `147 passed in 40.54s` (100% passed, 0 failures, 0 regressions)

---

### Task 8: React nền tảng, auth và quản trị (Hoàn thành: 2026-10-29)

#### Lệnh 1: Kiểm thử tự động Frontend bằng Vitest
```bash
npm --prefix frontend test
```
- **Exit code:** 0
- **Kết quả:** `12 passed in 2.04s` (2 test suites, 12 tests passed, 0 failures)
- **Nội dung kiểm thử chi tiết:**
  - **Auth & Kiến trúc Token in-memory (`Auth.test.jsx` - 7 tests):**
    - `login success stores tokens in memory and strictly not in localStorage`: Đăng nhập thành công lưu token trong memory module, kiểm tra triệt để không ghi bất kỳ khóa token nào vào `localStorage` hay `sessionStorage`.
    - `login failure with invalid credentials shows Vietnamese error alert`: Đăng nhập sai mật khẩu hiển thị thông báo lỗi tiếng Việt thân thiện.
    - `register user shows pending approval message on 201 Created`: Đăng ký tài khoản thành công hiển thị banner tài khoản ở trạng thái chờ Admin kích hoạt trước khi có thể đăng nhập.
    - `profile update and change password work as expected`: Cập nhật thông tin cá nhân (họ tên, email, sđt) và đổi mật khẩu thành công.
    - `logout clears memory state and tokens`: Đăng xuất dọn sạch token trong bộ nhớ và xóa state user.
    - `handles concurrent 401 requests with a single refresh call queue`: Khi có nhiều request đồng thời nhận 401 Unauthorized, cơ chế queueing trong `axiosClient.js` đảm bảo chỉ thực hiện duy nhất 1 request refresh token và retry lại toàn bộ các request đang chờ mà không gây vòng lặp 401.
    - `ProtectedRoute guards roles correctly`: Bọc route theo vai trò (Admin vào được trang quản trị, User thường bị chặn với thông báo 403 Forbidden).
  - **Trang Quản trị Admin/Manager (`AdminPages.test.jsx` - 5 tests):**
    - `UserAdminPage renders users and allows Admin to toggle active and change department`: Admin hiển thị danh sách người dùng, kích hoạt tài khoản và điều chuyển phòng ban.
    - `UserAdminPage shows friendly alert on 409 Conflict`: Khi Admin cố vô hiệu hóa nhân viên đang giữ tài sản hoặc có phiếu chờ xử lý, giao diện hiển thị alert tiếng Việt cảnh báo HTTP 409 Conflict rõ ràng.
    - `UserAdminPage: Manager sees read-only view without edit/activate buttons`: Manager chỉ xem nhân viên trong phòng ban của mình ở chế độ đọc, không hiển thị các nút thao tác chỉnh sửa vai trò hay kích hoạt.
    - `DepartmentPage: Admin can create and edit; verify absence of DELETE buttons`: Admin tạo mới và chỉnh sửa phòng ban; kiểm tra tuyệt đối không có bất kỳ nút xóa (DELETE) nào.
    - `CategoryPage: Admin can create and edit; verify absence of DELETE buttons`: Admin tạo mới và chỉnh sửa danh mục tài sản; kiểm tra tuyệt đối không có nút xóa nào.

#### Lệnh 2: Build Production Frontend (Vite)
```bash
npm --prefix frontend run build
```
- **Exit code:** 0
- **Kết quả:** `dist/index.html` (0.47 kB), `dist/assets/index.css` (20.69 kB), `dist/assets/index.js` (291.30 kB). Build sạch sẽ trong 1.86s, không lỗi bundle.

#### Lệnh 3: Kiểm tra cấu hình Docker Compose Dev Override
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
```
- **Exit code:** 0
- **Kết quả:** Cấu hình compose dev hợp lệ, service frontend được ánh xạ đúng cổng `127.0.0.1:5173:5173` và biến môi trường `VITE_API_BASE_URL=http://127.0.0.1:8000`.

---

### Task 9: React nghiệp vụ và dashboard (Hoàn thành: 2026-10-29)

#### Lệnh 1: Kiểm thử tự động Frontend bằng Vitest
```bash
npm --prefix frontend test
```
- **Exit code:** 0
- **Kết quả:** `28 passed in 2.89s` (6 test suites, 28 tests passed, 0 failures)
- **Nội dung kiểm thử chi tiết:**
  - **Luồng Tài sản (`AssetFlows.test.jsx` - 5 tests):**
    - `AssetListPage renders assets with search and status badges`: Hiển thị danh sách tài sản, tìm kiếm, lọc theo trạng thái và hiển thị badge màu đúng chuẩn.
    - `Admin creates asset, edits asset`: Admin tạo tài sản mới theo strict allowlist và chỉnh sửa thông tin tài sản (chặn sửa trường bất biến).
    - `Manager cannot see create asset button`: Nút tạo tài sản bị ẩn hoàn toàn với Manager.
    - `AssetDetailPage allows Admin to edit asset and allocate asset`: Chi tiết tài sản cho phép thực hiện thao tác cấp phát, chuyển trạng thái.
    - `AssetActionModal handles 409 Conflict gracefully with alert`: Xử lý xung đột trạng thái (HTTP 409) bằng thông báo tiếng Việt rõ ràng, yêu cầu tải lại dữ liệu.
  - **Luồng Phiếu Yêu Cầu (`RequestFlows.test.jsx` - 4 tests):**
    - `User creates ALLOCATE request`: Nhân viên gửi yêu cầu cấp phát tài sản chọn danh mục và nhập lý do.
    - `User creates RETURN request and duplicate is prevented with 409`: Nhân viên tạo phiếu hoàn trả và xử lý chặn tạo trùng phiếu PENDING (HTTP 409).
    - `Manager approves ALLOCATE request selecting available asset`: Quản lý duyệt cấp phát bắt buộc chọn tài sản đang khả dụng (AVAILABLE) trong phòng ban.
    - `Manager rejects request with non-empty reason`: Quản lý từ chối phiếu bắt buộc nhập lý do từ chối.
  - **Lịch sử Tài sản Cá nhân (`PersonalHistory.test.jsx` - 2 tests):**
    - `Loads /api/me/asset-history/ and renders event snapshots and messages`: Tải lịch sử cá nhân an toàn, hiển thị đúng thông điệp thân thiện và snapshot mã/tên tài sản, không chứa link sang timeline toàn công ty.
    - `Persists historical events after asset is returned and no longer held`: Lịch sử cá nhân vẫn được bảo toàn sau khi tài sản đã hoàn trả hoặc chuyển đi phòng ban khác.
  - **Dashboard & Báo cáo Recharts (`Dashboard.test.jsx` - 5 tests):**
    - `Renders 6 KPI cards with metric values`: 6 thẻ KPI tổng thể (`total_active`, `available`, `in_use`, `damaged_under_maintenance`, `pending_disposal`, `pending_requests`).
    - `Admin sees all 4 charts including department distribution`: Admin quan sát đủ 4 biểu đồ (Trạng thái, Danh mục, Phòng ban kèm Chưa phân bổ, Biến động 12 tháng).
    - `Manager sees 3 charts and department distribution is hidden`: Manager bị ẩn hoàn toàn biểu đồ phân bố phòng ban toàn công ty.
    - `Category and department filters update request queries`: Thay đổi bộ lọc đồng bộ cập nhật tham số truy vấn dữ liệu.
    - `Enforces date range validation (start <= end, max 12 months)`: Cảnh báo lỗi giao diện khi chọn khoảng thời gian không hợp lệ hoặc vượt quá 12 tháng liên tiếp.

#### Lệnh 2: Build Production Frontend (Vite)
```bash
npm --prefix frontend run build
```
- **Exit code:** 0
- **Kết quả:** `dist/index.html` (0.47 kB), `dist/assets/index.css` (27.08 kB), `dist/assets/index.js` (785.83 kB). Bundle hoàn tất trong 3.21s, không lỗi bundle.

---

### Task 10: R2 private và quyền upload/download (Hoàn thành: 2026-10-29)

#### Lệnh 1: Kiểm thử Backend Storage & Permissions bằng Pytest
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/test_storage.py -v
```
- **Exit code:** 0
- **Kết quả:** `13 passed in 3.04s`
- **Nội dung kiểm thử chi tiết:**
  - `test_valid_real_images_upload_succeeds`: Tải lên ảnh thật định dạng JPEG, PNG, WEBP (dung lượng dưới 5 MiB) thành công (HTTP 200).
  - `test_valid_real_pdf_upload_succeeds`: Tải lên tài liệu PDF thật (dung lượng dưới 10 MiB) thành công (HTTP 200).
  - `test_reject_oversized_image_with_400`: Chặn ảnh vượt quá 5 MiB với HTTP 400.
  - `test_reject_oversized_pdf_with_400`: Chặn PDF vượt quá 10 MiB với HTTP 400.
  - `test_reject_fake_image_disguised_with_extension`: Chặn tệp văn bản giả mạo đuôi `.png` bằng Pillow `Image.verify()` thật (HTTP 400).
  - `test_reject_fake_pdf_disguised_with_extension`: Chặn tệp văn bản giả mạo đuôi `.pdf` bằng pypdf `PdfReader` thật (HTTP 400).
  - `test_upload_on_disposed_asset_rejected_with_400`: Chặn tải lên tệp đính kèm trên tài sản đã thanh lý (`DISPOSED`) với HTTP 400.
  - `test_admin_can_upload_manager_and_user_forbidden`: Chỉ Admin có quyền tải lên tệp đính kèm; Manager và User bị từ chối với HTTP 403 Forbidden.
  - `test_download_permissions_admin_manager_user`: Phân quyền tải tệp đính kèm nghiêm ngặt: Admin được tải toàn công ty; Manager chỉ được tải tài sản trong phòng ban; User chỉ được tải tài sản đang được giao cho chính mình, ngay sau khi thu hồi tài sản thì User lập tức bị chặn với HTTP 403 Forbidden.
  - `test_orphan_file_cleaned_up_if_database_save_fails`: Dọn dẹp tệp mồ côi (compensating cleanup) trên storage nếu quá trình lưu DB xảy ra lỗi.
  - `test_old_file_cleaned_up_when_replacing_attachment`: Khi thay thế tệp đính kèm mới, tệp cũ được dọn sạch sau khi giao dịch commit (`transaction.on_commit`).
  - `test_local_signed_download_flow_and_expiration`: Tạo URL ký HMAC hết hạn sau 60 giây và kiểm tra header `Cache-Control: no-store`.
  - `test_r2_storage_presigned_url_generation`: Kiểm tra khả năng tương thích tạo presigned URL với Cloudflare R2 / S3 Boto3 API.

#### Lệnh 2: Kiểm thử hồi quy toàn bộ Backend
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
- **Exit code:** 0
- **Kết quả:** `160 passed in 43.89s` (100% passed, 0 failures, 0 regressions)

#### Lệnh 3: Kiểm thử tự động Frontend bằng Vitest
```bash
npm --prefix frontend test
```
- **Exit code:** 0
- **Kết quả:** `33 passed in 2.95s` (7 test suites, 33 tests passed, 0 failures)
- **Nội dung kiểm thử chi tiết (`Attachments.test.jsx` - 5 tests):**
  - `Renders attachments component with preview and download buttons`: Hiển thị ảnh xem trước và nút tải tài liệu đính kèm.
  - `Admin can upload attachments`: Admin tải tệp đính kèm thành công với phản hồi cập nhật.
  - `User can download attachments of assigned assets`: Nhân viên tải tài liệu của tài sản mình đang giữ.
  - `Upload error alert maintains form state`: Khi tải lên gặp lỗi, form giữ nguyên dữ liệu đã chọn để người dùng không phải nhập lại.
  - `AssetDetailPage integrates AssetAttachments`: Tích hợp mượt mà vào trang chi tiết tài sản.

#### Lệnh 4: Build Production Frontend (Vite)
```bash
npm --prefix frontend run build
```
- **Exit code:** 0
- **Kết quả:** Build sạch sẽ, 0 lỗi bundle.








