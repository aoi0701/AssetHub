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
