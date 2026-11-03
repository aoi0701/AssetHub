# Kế Hoạch Triển Khai Chi Tiết: Task 2, Task 3 & Task 4 (Cloudflare D1 Migration)

- **Người lập kế hoạch:** Agent 2 — Schema, Migration & Business Domain Planning Specialist
- **Ngày lập:** 2026-11-03
- **Tài liệu tham chiếu cơ sở:**
  - `docs/superpowers/specs/2026-11-03-cloudflare-d1-architecture-design.md`
  - `docs/superpowers/plans/2026-11-03-cloudflare-d1-implementation.md`
- **Mục tiêu:** Cung cấp phương án kỹ thuật sản xuất (production-grade), tuân thủ nghiêm ngặt nguyên tắc Test-Driven Development (TDD), không gây suy thoái (zero-regression) cho hệ thống AssetHub khi chuyển đổi tầng dữ liệu từ PostgreSQL 16 sang Cloudflare D1.

---

## Mục Lục Tổng Quan

1. [Tổng Quan & Ma Trận Phụ Thuộc (Dependency Matrix)](#1-tổng-quan--ma-trận-phụ-thuộc)
2. [Task 2: Schema DDL (18 Bảng), Ràng Buộc & Triggers Bảo Vệ Quản Trị Viên](#2-task-2-schema-ddl-18-bảng-ràng-buộc--triggers-bảo-vệ-quản-trị-viên)
   - 2.1. Danh mục 18 Bảng & Cú pháp DDL Chuẩn SQLite/D1
   - 2.2. Admin Protection Triggers: UPDATE & DELETE
   - 2.3. Ràng buộc toàn vẹn CHECK Constraints
   - 2.4. Hạ tầng Đảm bảo Nguyên tử & Chống Double-Write (`_assert_guard`, `_idempotency_log`)
   - 2.5. Quy trình & Lệnh Triển khai qua `wrangler d1 execute --remote`
   - 2.6. Quy trình TDD, File Ranh giới, Chữ ký Hàm & Kiểm chứng Test
3. [Task 3: Công Cụ Di Chuyển Dữ Liệu & Đối Soát 1:1 (`migrate_pg_to_d1`)](#3-task-3-công-cụ-di-chuyển-dữ-liệu--đối-soát-11-migrate_pg_to_d1)
   - 3.1. Kiến trúc Lệnh Quản trị `migrate_pg_to_d1`
   - 3.2. Thứ tự Khai thác Dữ liệu theo Đồ thị Phụ thuộc (Topological Sort)
   - 3.3. Chuẩn hóa Định dạng Dữ liệu (PBKDF2 Hashes, Booleans, UTC Datetime, JSON)
   - 3.4. Kịch bản Đối soát Dữ liệu Tự động 1:1 (Reconciliation Script)
   - 3.5. Kế hoạch Khôi phục & Rollback Hai Giai Đoạn (RPO / RTO)
   - 3.6. Quy trình TDD, Chữ ký Mã & Kiểm chứng Thực tế
4. [Task 4: Chuyển Đổi Dịch Vụ Nghiệp Vụ (`AssetService`, `RequestService`) Sang D1 Atomic Batch](#4-task-4-chuyển-đổi-dịch-vụ-nghiệp-vụ-assetservice-requestservice-sang-d1-atomic-batch)
   - 4.1. Chuyển dịch Cơ chế Khóa: Từ `select_for_update()` sang D1 Atomic Guarded Batch OCC
   - 4.2. Chống Ghi Lịch sử Mồ côi (Orphan Audit Logs) bằng `_assert_guard`
   - 4.3. Sinh Mã Tuần tự Nguyên tử (`AST-YYYY-NNN`, `REQ-YYYY-NNN`) trong Cùng Batch
   - 4.4. Phân định Chuẩn: `DAMAGE_REPORT` (Phiếu Yêu Cầu) vs `DAMAGE` (Hành Động Kiểm Toán)
   - 4.5. Xử lý Từ Chối Phiếu (`reject_request`): Đổi Trạng Thái, Không Ghi `AssetHistoryLog`
   - 4.6. Phòng Chống Suy Thoái (Regression Guards): Stale Instance Overwrite & Profile Deactivation
   - 4.7. File Ranh giới, Giao tiếp Consumes/Produces & Quy trình TDD Chi tiết
5. [Bảng Tổng Hợp Kiểm Thử & Tiêu Chí Nghiệm Thu Tuần 4](#5-bảng-tổng-hợp-kiểm-thử--tiêu-chí-nghiệm-thu-tuần-4)

---

## 1. Tổng Quan & Ma Trận Phụ Thuộc

### 1.1. Bối cảnh Kỹ thuật
Hệ thống AssetHub chuyển đổi kho dữ liệu vận hành từ PostgreSQL 16 sang Cloudflare D1 (SQLite tại Edge). Vì D1 không hỗ trợ interactive transactions (`atomic_transactions = False`, `supports_transactions = False`) và không hỗ trợ row-level locking (`select_for_update`), toàn bộ cơ chế bảo toàn toàn vẹn dữ liệu phải dịch chuyển sang:
1. **Schema DDL Native:** Ràng buộc CHECK constraint, FOREIGN KEY enforced và Database Triggers cấp CSDL tại Edge.
2. **Batch Mutations Nguyên tử:** Đóng gói mutation và audit log vào cùng một lời gọi `env.DB.batch([...])`.
3. **Optimistic Concurrency Control (OCC) & Assertion Guard:** Cập nhật có điều kiện trạng thái kết hợp với `_assert_guard` để hủy toàn bộ batch nếu bản ghi đã bị thay đổi bởi request cạnh tranh.
4. **Idempotency Protocol:** Chống ghi đúp (double-write) khi client gặp sự cố mạng hoặc timeout và gửi lại request.

### 1.2. Ma Trận Phụ Thuộc (Dependency Matrix)

```
[ Task 0: Bridge Blockers Resolution Gate ]
                    │
                    ▼
[ Task 1: D1 Database Backend Adapter & D1Client ]
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│                 AGENT 2 WORK PACKAGES                       │
│                                                             │
│  [ Task 2: Schema DDL, Constraints & Triggers ]             │
│                    │                                        │
│                    ▼                                        │
│  [ Task 3: Data Migration & 1:1 Reconciliation Script ]     │
│                    │                                        │
│                    ▼                                        │
│  [ Task 4: Business Services Conversion (Asset & Request) ] │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
[ Task 5: R2 File Storage & Workers AI Suggester ]
                    │
                    ▼
[ Task 6: Pages Build, Tunnel Ingress & Final Verification ]
```

- **Điều kiện tiên quyết của Task 2:** Task 0 hoàn tất (Worker Bridge xử lý trích xuất `RETURNING "id"` có dấu trích dẫn và batch fault injection không giả định kết quả); Task 1 cung cấp `D1Client` và `DatabaseWrapper`.
- **Điều kiện tiên quyết của Task 3:** Task 2 hoàn tất (18 bảng và triggers đã triển khai trên D1 từ xa và vượt qua kiểm thử DDL).
- **Điều kiện tiên quyết của Task 4:** Task 2 và Task 3 hoàn tất (D1 đã có cấu trúc dữ liệu và dữ liệu migrated sẵn sàng để chuyển đổi logic nghiệp vụ).

---

## 2. Task 2: Schema DDL (18 Bảng), Ràng Buộc & Triggers Bảo Vệ Quản Trị Viên

### 2.1. Danh mục 18 Bảng & Cú pháp DDL Chuẩn SQLite/D1

Tệp DDL chính thức: `cloudflare-worker/bridge/schema.sql` (và tệp nguồn trong backend `backend/assethub_api/backends/d1/sql/schema.sql`).

Toàn bộ cú pháp SQLite tương thích 100% với Cloudflare D1:

```sql
-- ============================================================================
-- AssetHub Cloudflare D1 Production-Aligned Schema (18 Tables)
-- ============================================================================

-- Dọn dẹp an toàn các triggers và bảng theo thứ tự khóa ngoại ngược
DROP TRIGGER IF EXISTS protect_last_admin_before_update;
DROP TRIGGER IF EXISTS protect_last_admin_before_delete;
DROP TABLE IF EXISTS _idempotency_log;
DROP TABLE IF EXISTS _assert_guard;
DROP TABLE IF EXISTS django_session;
DROP TABLE IF EXISTS django_migrations;
DROP TABLE IF EXISTS authentication_user_user_permissions;
DROP TABLE IF EXISTS authentication_user_groups;
DROP TABLE IF EXISTS auth_group_permissions;
DROP TABLE IF EXISTS auth_group;
DROP TABLE IF EXISTS auth_permission;
DROP TABLE IF EXISTS django_admin_log;
DROP TABLE IF EXISTS django_content_type;
DROP TABLE IF EXISTS assets_assethistorylog;
DROP TABLE IF EXISTS requests_assetrequest;
DROP TABLE IF EXISTS assets_asset;
DROP TABLE IF EXISTS assets_identifiersequence;
DROP TABLE IF EXISTS authentication_user;
DROP TABLE IF EXISTS departments_category;
DROP TABLE IF EXISTS departments_department;

-- ----------------------------------------------------------------------------
-- 1. Phòng ban (apps.departments.models.Department)
-- ----------------------------------------------------------------------------
CREATE TABLE departments_department (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- ----------------------------------------------------------------------------
-- 2. Danh mục tài sản (apps.departments.models.Category)
-- ----------------------------------------------------------------------------
CREATE TABLE departments_category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- ----------------------------------------------------------------------------
-- 3. Người dùng & RBAC (apps.authentication.models.User)
-- Tương thích hoàn toàn AbstractUser và PBKDF2 1.000.000 vòng
-- ----------------------------------------------------------------------------
CREATE TABLE authentication_user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    password TEXT NOT NULL,
    last_login TEXT,
    is_superuser INTEGER NOT NULL DEFAULT 0 CHECK(is_superuser IN (0, 1)),
    username TEXT UNIQUE NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT UNIQUE NOT NULL,
    is_staff INTEGER NOT NULL DEFAULT 0 CHECK(is_staff IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    date_joined TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    role TEXT NOT NULL CHECK(role IN ('ADMIN', 'MANAGER', 'USER')),
    full_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    department_id INTEGER REFERENCES departments_department(id),
    auth_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- ----------------------------------------------------------------------------
-- 4. Django Content Types (Metadata bắt buộc của Django ORM)
-- ----------------------------------------------------------------------------
CREATE TABLE django_content_type (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_label TEXT NOT NULL,
    model TEXT NOT NULL,
    CONSTRAINT django_content_type_app_label_model_uniq UNIQUE (app_label, model)
);

-- ----------------------------------------------------------------------------
-- 5. Django Admin Log (Nhật ký quản trị Django Admin)
-- ----------------------------------------------------------------------------
CREATE TABLE django_admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_time TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    user_id INTEGER NOT NULL REFERENCES authentication_user(id),
    content_type_id INTEGER REFERENCES django_content_type(id),
    object_id TEXT,
    object_repr TEXT NOT NULL DEFAULT '',
    action_flag INTEGER NOT NULL,
    change_message TEXT NOT NULL DEFAULT ''
);

-- ----------------------------------------------------------------------------
-- 6. Phân quyền Django (django.contrib.auth.models.Permission)
-- ----------------------------------------------------------------------------
CREATE TABLE auth_permission (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    content_type_id INTEGER NOT NULL REFERENCES django_content_type(id),
    codename TEXT NOT NULL,
    CONSTRAINT unique_content_type_codename UNIQUE (content_type_id, codename)
);

-- ----------------------------------------------------------------------------
-- 7. Nhóm người dùng (django.contrib.auth.models.Group)
-- ----------------------------------------------------------------------------
CREATE TABLE auth_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- ----------------------------------------------------------------------------
-- 8. Liên kết Nhóm - Quyền (Group Permissions)
-- ----------------------------------------------------------------------------
CREATE TABLE auth_group_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES auth_group(id),
    permission_id INTEGER NOT NULL REFERENCES auth_permission(id),
    CONSTRAINT unique_group_permission UNIQUE (group_id, permission_id)
);

-- ----------------------------------------------------------------------------
-- 9. Liên kết Người dùng - Nhóm (User Groups)
-- ----------------------------------------------------------------------------
CREATE TABLE authentication_user_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES authentication_user(id),
    group_id INTEGER NOT NULL REFERENCES auth_group(id),
    CONSTRAINT unique_user_group UNIQUE (user_id, group_id)
);

-- ----------------------------------------------------------------------------
-- 10. Quyền riêng của người dùng (User Permissions)
-- ----------------------------------------------------------------------------
CREATE TABLE authentication_user_user_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES authentication_user(id),
    permission_id INTEGER NOT NULL REFERENCES auth_permission(id),
    CONSTRAINT unique_user_permission UNIQUE (user_id, permission_id)
);

-- ----------------------------------------------------------------------------
-- 11. Bảng theo dõi Migrations của Django
-- ----------------------------------------------------------------------------
CREATE TABLE django_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    name TEXT NOT NULL,
    applied TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- 12. Bảng lưu trữ Session của Django (Session Storage)
-- ----------------------------------------------------------------------------
CREATE TABLE django_session (
    session_key TEXT PRIMARY KEY,
    session_data TEXT NOT NULL,
    expire_date TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS django_session_expire_date_idx ON django_session(expire_date);

-- ----------------------------------------------------------------------------
-- 13. Bộ đếm sinh mã tuần tự (apps.assets.models.IdentifierSequence)
-- Hỗ trợ sinh mã định danh AST-YYYY-NNN và REQ-YYYY-NNN
-- ----------------------------------------------------------------------------
CREATE TABLE assets_identifiersequence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('AST', 'REQ')),
    year INTEGER NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT unique_identifier_sequence_kind_year UNIQUE (kind, year)
);

-- ----------------------------------------------------------------------------
-- 14. Tài sản vật lý (apps.assets.models.Asset)
-- Đảm bảo đúng 6 trạng thái nghiệp vụ và ràng buộc người giữ
-- ----------------------------------------------------------------------------
CREATE TABLE assets_asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES departments_category(id),
    serial_number TEXT NOT NULL DEFAULT '',
    specification TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(status IN ('AVAILABLE', 'IN_USE', 'DAMAGED_PENDING_REPAIR', 'UNDER_MAINTENANCE', 'PENDING_DISPOSAL', 'DISPOSED')),
    department_id INTEGER REFERENCES departments_department(id),
    assigned_to_user_id INTEGER REFERENCES authentication_user(id),
    image TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL DEFAULT '',
    purchase_date TEXT,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    CONSTRAINT chk_asset_no_holder_when_available_or_disposal 
        CHECK(NOT (status IN ('AVAILABLE', 'PENDING_DISPOSAL', 'DISPOSED') AND assigned_to_user_id IS NOT NULL)),
    CONSTRAINT chk_asset_no_holder_when_unallocated 
        CHECK(NOT (department_id IS NULL AND assigned_to_user_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_assets_asset_status ON assets_asset(status);
CREATE INDEX IF NOT EXISTS idx_assets_asset_department ON assets_asset(department_id);
CREATE INDEX IF NOT EXISTS idx_assets_asset_assigned_user ON assets_asset(assigned_to_user_id);

-- ----------------------------------------------------------------------------
-- 15. Phiếu yêu cầu tài sản (apps.requests.models.AssetRequest)
-- Đúng 3 loại request, 3 trạng thái và Partial Unique Index chống trùng request PENDING
-- ----------------------------------------------------------------------------
CREATE TABLE requests_assetrequest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_code TEXT UNIQUE NOT NULL,
    request_type TEXT NOT NULL CHECK(request_type IN ('ALLOCATE', 'RETURN', 'DAMAGE_REPORT')),
    requester_id INTEGER NOT NULL REFERENCES authentication_user(id),
    asset_id INTEGER REFERENCES assets_asset(id),
    requested_category_id INTEGER REFERENCES departments_category(id),
    department_id INTEGER NOT NULL REFERENCES departments_department(id),
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'APPROVED', 'REJECTED')),
    reviewed_by_id INTEGER REFERENCES authentication_user(id),
    reviewed_at TEXT,
    rejection_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE INDEX IF NOT EXISTS idx_requests_requester ON requests_assetrequest(requester_id);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests_assetrequest(status);
CREATE INDEX IF NOT EXISTS idx_requests_department ON requests_assetrequest(department_id);

-- Partial Unique Index: Không thể tạo 2 request RETURN hoặc DAMAGE_REPORT đồng thời cho 1 tài sản khi còn PENDING
CREATE UNIQUE INDEX IF NOT EXISTS unique_pending_return_or_damage_per_user_asset 
ON requests_assetrequest(requester_id, request_type, asset_id) 
WHERE status = 'PENDING' AND request_type IN ('RETURN', 'DAMAGE_REPORT') AND asset_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 16. Sổ nhật ký kiểm toán tài sản append-only (apps.assets.models.AssetHistoryLog)
-- Đúng 9 loại hành động kiểm toán
-- ----------------------------------------------------------------------------
CREATE TABLE assets_assethistorylog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets_asset(id),
    actor_id INTEGER NOT NULL REFERENCES authentication_user(id),
    action_type TEXT NOT NULL CHECK(action_type IN (
        'CREATE', 
        'ALLOCATE', 
        'RETURN', 
        'DAMAGE', 
        'MAINTENANCE_START', 
        'MAINTENANCE_END', 
        'TRANSFER', 
        'DISPOSAL_PROPOSE', 
        'DISPOSAL_CONFIRM'
    )),
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL,
    from_user_id INTEGER REFERENCES authentication_user(id),
    to_user_id INTEGER REFERENCES authentication_user(id),
    from_department_id INTEGER REFERENCES departments_department(id),
    to_department_id INTEGER REFERENCES departments_department(id),
    reason TEXT NOT NULL DEFAULT '',
    asset_code_snapshot TEXT NOT NULL,
    asset_name_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE INDEX IF NOT EXISTS idx_history_asset ON assets_assethistorylog(asset_id);
CREATE INDEX IF NOT EXISTS idx_history_actor ON assets_assethistorylog(actor_id);
CREATE INDEX IF NOT EXISTS idx_history_action ON assets_assethistorylog(action_type);

-- ----------------------------------------------------------------------------
-- 17. Bảng kiểm soát số dòng bị ảnh hưởng cho Atomic Batch (_assert_guard)
-- Nếu UPDATE không đổi dòng nào (0 rows), lệnh INSERT vi phạm CHECK(rows_affected > 0)
-- làm D1 abort toàn bộ batch transaction
-- ----------------------------------------------------------------------------
CREATE TABLE _assert_guard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rows_affected INTEGER NOT NULL CHECK(rows_affected > 0)
);

-- ----------------------------------------------------------------------------
-- 18. Bảng lưu cache phản hồi chống Double-Write khi Retry mạng (_idempotency_log)
-- ----------------------------------------------------------------------------
CREATE TABLE _idempotency_log (
    idempotency_key TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    response_body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_created ON _idempotency_log(created_at);
```

---

### 2.2. Admin Protection Triggers: UPDATE & DELETE

Hệ thống bảo vệ quyền quản trị tối cao thông qua hai Database Triggers nguyên tử trên D1. Triggers này ngăn ngừa triệt để tình huống vô tình hoặc cố ý hạ quyền, vô hiệu hóa hoặc xóa quản trị viên duy nhất còn lại:

```sql
-- Trigger 1: Ngăn chặn giáng quyền (demote) hoặc vô hiệu hóa (deactivate) Admin hoạt động cuối cùng
CREATE TRIGGER IF NOT EXISTS protect_last_admin_before_update
BEFORE UPDATE OF role, is_active ON authentication_user
FOR EACH ROW
WHEN OLD.role = 'ADMIN' AND OLD.is_active = 1
     AND (NEW.role != 'ADMIN' OR NEW.is_active != 1)
BEGIN
    SELECT RAISE(ABORT, 'Cannot demote or deactivate the last active Admin')
    WHERE (
        SELECT COUNT(*) FROM authentication_user 
        WHERE role = 'ADMIN' AND is_active = 1 AND id != OLD.id
    ) = 0;
END;

-- Trigger 2: Ngăn chặn xóa (delete) Admin hoạt động cuối cùng
CREATE TRIGGER IF NOT EXISTS protect_last_admin_before_delete
BEFORE DELETE ON authentication_user
FOR EACH ROW
WHEN OLD.role = 'ADMIN' AND OLD.is_active = 1
BEGIN
    SELECT RAISE(ABORT, 'Cannot delete the last active Admin')
    WHERE (
        SELECT COUNT(*) FROM authentication_user 
        WHERE role = 'ADMIN' AND is_active = 1 AND id != OLD.id
    ) = 0;
END;
```

#### Ma trận Hành vi & Trường Hợp Biên (Behavioral Matrix):
| Số lượng Admin đang Active | Thao tác thực hiện | Dữ liệu cập nhật | Kết quả thực thi |
| :--- | :--- | :--- | :--- |
| 1 Admin (Admin A) | `UPDATE authentication_user SET is_active = 0 WHERE id = A` | `is_active: 0` | **ABORT:** `Cannot demote or deactivate the last active Admin` |
| 1 Admin (Admin A) | `UPDATE authentication_user SET role = 'USER' WHERE id = A` | `role: 'USER'` | **ABORT:** `Cannot demote or deactivate the last active Admin` |
| 1 Admin (Admin A) | `DELETE FROM authentication_user WHERE id = A` | Delete row | **ABORT:** `Cannot delete the last active Admin` |
| 2 Admin (Admin A, Admin B) | `UPDATE authentication_user SET is_active = 0 WHERE id = A` | `is_active: 0` | **SUCCESS:** Admin B vẫn active. Còn 1 Admin active. |
| 1 Admin (Admin B còn lại) | `UPDATE authentication_user SET is_active = 0 WHERE id = B` | `is_active: 0` | **ABORT:** Đã chặn đứng thao tác vô hiệu hóa Admin cuối cùng. |
| 1 Admin (Admin A) | `UPDATE authentication_user SET full_name = 'New Name' WHERE id = A` | Tên thay đổi | **SUCCESS:** Trigger không kích hoạt vì `role` và `is_active` không đổi. |

---

### 2.3. Ràng buộc Toàn vẹn CHECK Constraints

1. **6 Trạng thái Tài sản (`Asset.Status`):**
   ```sql
   CHECK(status IN ('AVAILABLE', 'IN_USE', 'DAMAGED_PENDING_REPAIR', 'UNDER_MAINTENANCE', 'PENDING_DISPOSAL', 'DISPOSED'))
   ```
   Bất kỳ giá trị nào ngoài danh sách trên (ví dụ: `ALLOCATED`, `BROKEN`) đều bị D1 từ chối với lỗi `SQLITE_CONSTRAINT: CHECK constraint failed`.

2. **Ràng buộc Người giữ Tài sản:**
   - Khi tài sản ở trạng thái sẵn sàng hoặc chuẩn bị/đã thanh lý thì tuyệt đối không được có người giữ:
     ```sql
     CONSTRAINT chk_asset_no_holder_when_available_or_disposal 
         CHECK(NOT (status IN ('AVAILABLE', 'PENDING_DISPOSAL', 'DISPOSED') AND assigned_to_user_id IS NOT NULL))
     ```
   - Khi tài sản thuộc kho chung công ty (chưa phân bổ phòng ban, `department_id IS NULL`) thì tuyệt đối không được gán cho nhân viên:
     ```sql
     CONSTRAINT chk_asset_no_holder_when_unallocated 
         CHECK(NOT (department_id IS NULL AND assigned_to_user_id IS NOT NULL))
     ```

3. **3 Loại Phiếu Yêu Cầu (`AssetRequest.RequestType`):**
   ```sql
   CHECK(request_type IN ('ALLOCATE', 'RETURN', 'DAMAGE_REPORT'))
   ```

4. **3 Trạng thái Phiếu Yêu Cầu (`AssetRequest.Status`):**
   ```sql
   CHECK(status IN ('PENDING', 'APPROVED', 'REJECTED'))
   ```

5. **9 Hành Động Kiểm Toán Nhật Ký (`AssetHistoryLog.ActionType`):**
   ```sql
   CHECK(action_type IN (
       'CREATE', 'ALLOCATE', 'RETURN', 'DAMAGE', 
       'MAINTENANCE_START', 'MAINTENANCE_END', 
       'TRANSFER', 'DISPOSAL_PROPOSE', 'DISPOSAL_CONFIRM'
   ))
   ```

---

### 2.4. Hạ tầng Đảm bảo Nguyên tử & Chống Double-Write

1. **Bảng `_assert_guard`:**
   ```sql
   CREATE TABLE _assert_guard (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       rows_affected INTEGER NOT NULL CHECK(rows_affected > 0)
   );
   ```
   - Mục đích: Dùng trong các câu lệnh batch chuyển trạng thái. Khi chạy câu lệnh cập nhật có điều kiện trạng thái:
     `UPDATE assets_asset SET status = 'IN_USE', assigned_to_user_id = ? WHERE id = ? AND status = 'AVAILABLE';`
     ngay sau đó batch thực thi:
     `INSERT INTO _assert_guard (rows_affected) VALUES (changes());`
   - Nếu điều kiện `status = 'AVAILABLE'` không thỏa mãn (ví dụ tài sản đã bị người khác lấy), số dòng thay đổi `changes()` bằng 0.
   - Thao tác chèn số 0 vi phạm `CHECK(rows_affected > 0)`, khiến D1 hủy bỏ toàn bộ batch. Toàn bộ các câu lệnh đi kèm (như chèn log kiểm toán) đều bị rollback 100%.

2. **Bảng `_idempotency_log`:**
   ```sql
   CREATE TABLE _idempotency_log (
       idempotency_key TEXT PRIMARY KEY,
       payload_hash TEXT NOT NULL,
       response_body TEXT NOT NULL,
       created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
   );
   ```
   - Lưu trữ bản ghi kết quả của các mutation kèm khóa định danh duy nhất `idempotency_key`.
   - Khi client gặp ngắt kết nối mạng hoặc timeout và gửi lại cùng key: Worker Bridge nhận diện khóa, đối soát hash nội dung và trả lại phản hồi đã lưu kèm header `X-Idempotent-Replay: true` mà không chạy lại câu lệnh SQL trên D1.

---

### 2.5. Quy trình & Lệnh Triển khai qua `wrangler d1 execute --remote`

Việc áp dụng DDL lên Cloudflare D1 được thực hiện thông qua công cụ dòng lệnh `wrangler`:

1. **Kiểm tra cấu hình môi trường Cloudflare:**
   Tài khoản: `hnhoa494@gmail.com` (Account ID: `5e76c2bf4e09587a010d31ea416ec8d3`).
   CSDL D1 đích: `assethub-test-d1` (`3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`).

2. **Lệnh thực thi DDL từ xa:**
   ```bash
   npx wrangler d1 execute assethub-test-d1 --remote --file=./cloudflare-worker/bridge/schema.sql
   ```

3. **Lệnh xác thực cấu trúc bảng trên D1 từ xa:**
   ```bash
   # Kiểm tra danh sách 18 bảng
   npx wrangler d1 execute assethub-test-d1 --remote --command="SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
   
   # Kiểm tra triggers đã cài đặt
   npx wrangler d1 execute assethub-test-d1 --remote --command="SELECT name, tbl_name FROM sqlite_master WHERE type='trigger';"
   
   # Kiểm tra ràng buộc khóa ngoại đã bật
   npx wrangler d1 execute assethub-test-d1 --remote --command="PRAGMA foreign_key_check;"
   ```

---

### 2.6. Quy trình TDD, File Ranh giới, Chữ ký Hàm & Kiểm chứng Test

#### File ranh giới:
- **Tệp DDL D1:** `cloudflare-worker/bridge/schema.sql` (bản sao tích hợp: `backend/assethub_api/backends/d1/sql/schema.sql`).
- **Tệp cấu hình Worker:** `cloudflare-worker/bridge/wrangler.toml` (và `wrangler.jsonc`).
- **Tệp kiểm thử:** `backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py`.

#### Quy trình TDD (Test-Driven Development):
- **Bước 1 (Red):** Viết bộ kiểm thử `test_d1_schema_constraints.py` kiểm tra:
  - Tồn tại đủ 18 bảng.
  - Vi phạm CHECK constraint trạng thái tài sản lạ (ví dụ `'UNKNOWN_STATUS'`) phải ném ra `IntegrityError`.
  - Vi phạm ràng buộc người giữ tài sản (`status = 'AVAILABLE'` nhưng có `assigned_to_user_id`) phải ném ra `IntegrityError`.
  - Vi phạm trigger hạ quyền hoặc xóa Admin cuối cùng phải ném ra `IntegrityError` với thông điệp `'Cannot demote or deactivate the last active Admin'`.
- **Bước 2 (Verify Red):**
  Chạy lệnh:
  `docker compose exec backend pytest assethub_api/backends/d1/tests/test_d1_schema_constraints.py -v`
  Kết quả mong đợi: FAIL (chưa có schema hoặc schema chưa đồng bộ).
- **Bước 3 (Green):** Áp dụng `schema.sql` lên D1 qua wrangler và chạy lại test.
- **Bước 4 (Verify Green):** Toàn bộ kiểm thử PASS 100%.

#### Mẫu Test Assertions cụ thể:
```python
def test_protect_last_admin_trigger_enforcement(d1_connection):
    cursor = d1_connection.cursor()
    # Khởi tạo 1 Admin duy nhất
    cursor.execute("DELETE FROM authentication_user WHERE role = 'ADMIN'")
    cursor.execute(
        "INSERT INTO authentication_user (id, username, email, password, role, is_active, auth_version) "
        "VALUES (101, 'only_admin', 'admin@test.local', 'pbkdf2_hash', 'ADMIN', 1, 1)"
    )
    
    # 1. Cố gắng de-activate Admin duy nhất -> Phải văng lỗi IntegrityError từ Trigger
    with pytest.raises(IntegrityError) as exc_update:
        cursor.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 101")
    assert "Cannot demote or deactivate the last active Admin" in str(exc_update.value)

    # 2. Cố gắng giáng quyền Admin duy nhất -> Phải văng lỗi IntegrityError từ Trigger
    with pytest.raises(IntegrityError) as exc_demote:
        cursor.execute("UPDATE authentication_user SET role = 'USER' WHERE id = 101")
    assert "Cannot demote or deactivate the last active Admin" in str(exc_demote.value)

    # 3. Cố gắng xóa Admin duy nhất -> Phải văng lỗi IntegrityError từ Trigger
    with pytest.raises(IntegrityError) as exc_delete:
        cursor.execute("DELETE FROM authentication_user WHERE id = 101")
    assert "Cannot delete the last active Admin" in str(exc_delete.value)

    # 4. Tạo Admin thứ hai -> Lúc này cho phép de-activate Admin thứ nhất
    cursor.execute(
        "INSERT INTO authentication_user (id, username, email, password, role, is_active, auth_version) "
        "VALUES (102, 'second_admin', 'admin2@test.local', 'pbkdf2_hash', 'ADMIN', 1, 1)"
    )
    cursor.execute("UPDATE authentication_user SET is_active = 0 WHERE id = 101")
    assert cursor.rowcount == 1
```

---

## 3. Task 3: Công Cụ Di Chuyển Dữ Liệu & Đối Soát 1:1 (`migrate_pg_to_d1`)

### 3.1. Kiến trúc Lệnh Quản trị `migrate_pg_to_d1`

Vị trí tệp: `backend/apps/common/management/commands/migrate_pg_to_d1.py`.
*(Lưu ý kiến trúc: Đăng ký tương thích qua `apps.common` vốn đã có trong `INSTALLED_APPS`, đồng thời tạo alias import tại `apps/core` nếu cần tương thích ngược).*

#### Giao diện dòng lệnh (CLI Interface):
```bash
python manage.py migrate_pg_to_d1 [OPTIONS]

Options:
  --dry-run             Chạy trích xuất, ánh xạ và kiểm tra logic mà không ghi dữ liệu lên D1.
  --chunk-size INTEGER   Kích thước mỗi batch nạp D1 (mặc định: 50 bản ghi/chunk).
  --tables TEXT          Danh sách bảng cần chuyển đổi phân tách bằng dấu phẩy (mặc định: toàn bộ).
  --reconcile-only      Bỏ qua bước di chuyển, chỉ chạy đối soát dữ liệu 1:1 giữa PostgreSQL và D1.
  --target-db TEXT       Tên kết nối CSDL D1 trong settings (mặc định: 'd1').
```

---

### 3.2. Thứ tự Khai thác Dữ liệu theo Đồ thị Phụ thuộc (Topological Sort)

Nhằm tuân thủ nghiêm ngặt ràng buộc khóa ngoại (Foreign Keys) đã kích hoạt trên D1 mà không cần vô hiệu hóa kiểm tra PRAGMA, dữ liệu được trích xuất từ PostgreSQL và chèn vào D1 theo đúng thứ tự topo:

```
Tier 1 (Không phụ thuộc):
  ├── 1. departments_department
  ├── 2. departments_category
  ├── 3. django_content_type
  └── 4. auth_group

Tier 2 (Phụ thuộc Tier 1):
  ├── 5. auth_permission (phụ thuộc django_content_type)
  ├── 6. auth_group_permissions (phụ thuộc auth_group, auth_permission)
  └── 7. authentication_user (phụ thuộc departments_department)

Tier 3 (Phụ thuộc Tier 2):
  ├── 8. authentication_user_groups (phụ thuộc authentication_user, auth_group)
  ├── 9. authentication_user_user_permissions (phụ thuộc authentication_user, auth_permission)
  ├── 10. django_admin_log (phụ thuộc authentication_user, django_content_type)
  ├── 11. django_migrations (độc lập)
  ├── 12. django_session (độc lập)
  ├── 13. assets_identifiersequence (độc lập)
  └── 14. assets_asset (phụ thuộc departments_category, departments_department, authentication_user)

Tier 4 (Phụ thuộc Tier 3):
  ├── 15. requests_assetrequest (phụ thuộc authentication_user, assets_asset, departments_category, departments_department)
  └── 16. assets_assethistorylog (phụ thuộc assets_asset, authentication_user, departments_department)
```

---

### 3.3. Chuẩn hóa Định dạng Dữ liệu

1. **Chuỗi Băm Mật Khẩu (Password Hashes):**
   - **Bảo toàn nguyên vẹn 100% từng ký tự:** Chuỗi băm dạng `pbkdf2_sha256$1000000$<salt>$<hash>` được trích xuất từ PostgreSQL và ghi trực tiếp vào cột `password` trên D1.
   - Tuyệt đối không gọi `make_password` hoặc tái mã hóa mật khẩu khi di chuyển dữ liệu.

2. **Kiểu dữ liệu Boolean:**
   - PostgreSQL lưu trữ `boolean` (`true`/`false`).
   - Chuẩn hóa sang SQLite INTEGER: `True -> 1`, `False -> 0`, `None -> NULL`.

3. **Thời gian Ngày Giờ (Datetime):**
   - Chuẩn hóa toàn bộ về chuỗi chuẩn **ISO 8601 UTC**: `YYYY-MM-DDTHH:MM:SS.ffffffZ` hoặc `YYYY-MM-DD HH:MM:SS`.
   - Nếu datetime ở dạng naive hoặc timezone địa phương, chuyển đổi sang UTC trước khi format:
     `val.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')`.

4. **Trường JSON (JSONField):**
   - Cột `specification` trên `assets_asset` được chuyển đổi thành chuỗi JSON hợp lệ thông qua `json.dumps(val, ensure_ascii=False)` nếu đang ở dạng Python dict.

---

### 3.4. Kịch bản Đối soát Dữ liệu Tự động 1:1 (Reconciliation Script)

Sau khi hoàn tất quá trình nạp dữ liệu, lệnh tự động kích hoạt bộ kiểm tra đối soát 6 tiêu chí:

```python
class D1Reconciler:
    def __init__(self, pg_conn, d1_client):
        self.pg = pg_conn
        self.d1 = d1_client

    def reconcile_all(self) -> dict:
        report = {
            "row_counts": self.check_row_counts(),
            "primary_keys": self.check_primary_keys(),
            "foreign_keys": self.check_foreign_key_integrity(),
            "password_hashes": self.check_password_hashes_verbatim(),
            "identifier_sequences": self.check_identifier_sequences(),
            "datetimes": self.check_utc_datetimes(),
            "status": "PASS"
        }
        if any(v.get("mismatch") for v in report.values() if isinstance(v, dict)):
            report["status"] = "FAIL"
        return report
```

#### Chi tiết 6 Tiêu Chí Đối Soát:
1. **Đối soát Số lượng Dòng (`COUNT(*)`):**
   Đối chiếu `COUNT(*)` giữa PostgreSQL và D1 trên toàn bộ 16 bảng dữ liệu nghiệp vụ. Số lượng phải khớp chính xác 100%.
2. **Đối soát Khóa Chính (Primary Keys):**
   Tập hợp `id` (hoặc `session_key`, `(kind, year)`) trên D1 phải trùng khớp hoàn toàn với tập hợp `id` trên PostgreSQL:
   `set(d1_ids) == set(pg_ids)`.
3. **Đối soát Tính Toàn Vẹn Khóa Ngoại (Foreign Keys):**
   Kiểm tra không có bản ghi mồ côi: Mọi `department_id`, `category_id`, `assigned_to_user_id`, `requester_id`, `asset_id` trên D1 đều phải trỏ tới bản ghi hợp lệ đã tồn tại trên bảng đích.
4. **Đối soát Chuỗi Hash Mật Khẩu (Verbatim PBKDF2 Check):**
   So sánh chuỗi `password` của từng người dùng giữa PG và D1 theo từng ký tự. Đảm bảo toàn bộ 1.000.000 vòng lặp PBKDF2 không bị biến dạng.
5. **Đối soát Bộ Đếm Mã (`IdentifierSequence`):**
   Giá trị `value` trên D1 của mỗi cặp `(kind, year)` phải lớn hơn hoặc bằng giá trị trên PostgreSQL:
   `D1.value >= PG.value`, đảm bảo khi hệ thống nhận request mới sẽ không sinh mã trùng lặp với quá khứ.
6. **Đối soát Thời Gian UTC:**
   Toàn bộ các trường `created_at`, `updated_at`, `date_joined` trên D1 phải phân tích cú pháp hợp lệ theo chuẩn ISO 8601 UTC và khớp mốc thời gian của PostgreSQL trong phạm vi mili-giây.

---

### 3.5. Kế hoạch Khôi phục & Rollback Hai Giai Đoạn (RPO / RTO)

```
                 [ KẾ HOẠCH BẢO TRÌ & CHUYỂN ĐỔI ]
                                 │
             ┌───────────────────┴───────────────────┐
             ▼                                       ▼
  [ Giai đoạn 1: Pre-Cutover ]             [ Giai đoạn 2: Post-Cutover ]
  - Đang nạp data hoặc đối soát           - Đã mở ghi cho người dùng trên D1
  - Phát hiện sai lệch số liệu            - Có giao dịch mới sinh ra tại Edge
             │                                       │
             ▼                                       ▼
  [ HÀNH ĐỘNG ROLLBACK ]                  [ HÀNH ĐỘNG PHỤC HỒI ]
  1. Hủy bỏ phiên nạp D1.                 1. Phục hồi qua D1 Time Travel:
  2. Giữ nguyên PostgreSQL gốc.              npx wrangler d1 time-travel restore
  3. Mở lại ứng dụng trên PG.                assethub-test-d1 --timestamp="..."
                                          2. Hoặc nạp lại từ Daily D1 Dump (R2)
             │                                       │
             ▼                                       ▼
  * RPO: 0 giây                           * RPO Mục tiêu: < 15 phút
  * RTO: < 5 phút                         * RTO Mục tiêu: < 30 phút
```

- **Giai đoạn 1 (Pre-cutover Rollback):**
  - Áp dụng khi chưa đổi cấu hình `DATABASE_ENGINE` của backend container sang `assethub_api.backends.d1`. CSDL PostgreSQL 16 đóng băng ở chế độ đọc trong suốt cửa sổ bảo trì.
  - Thao tác: Bỏ qua D1, tắt `MAINTENANCE_MODE=False`, ứng dụng tiếp tục hoạt động trên PostgreSQL.
  - **RPO:** 0 giây. **RTO:** < 5 phút.

- **Giai đoạn 2 (Post-cutover Disaster Recovery):**
  - Áp dụng khi hệ thống đã vận hành chính thức trên D1 và có dữ liệu mới phát sinh.
  - Sử dụng tính năng **Cloudflare D1 Time Travel** (hạn mức gói Free tối đa 7 ngày):
    ```bash
    npx wrangler d1 time-travel restore assethub-test-d1 --timestamp="2026-11-03T10:00:00Z"
    ```
  - Kết hợp lịch sao lưu tự động hàng ngày:
    ```bash
    npx wrangler d1 export assethub-test-d1 --remote --output=backup_$(date +%Y%m%d_%H%M%S).sql
    ```
  - **RPO Thiết kế:** < 15 phút. **RTO Thiết kế:** < 30 phút.

---

### 3.6. Quy trình TDD, Chữ ký Mã & Kiểm chứng Thực tế

#### File ranh giới:
- **Lệnh di chuyển:** `backend/apps/common/management/commands/migrate_pg_to_d1.py`.
- **Tệp kiểm thử:** `backend/apps/common/tests/test_d1_migration.py`.

#### Quy trình TDD:
- **Bước 1 (Red):** Viết test `test_migrate_pg_to_d1_dry_run_and_reconciliation` kiểm tra việc đọc từ PostgreSQL mock/test CSDL, định dạng dữ liệu và gọi D1 batch.
- **Bước 2 (Verify Red):**
  `docker compose exec backend pytest apps/common/tests/test_d1_migration.py -v`
  Kết quả: FAIL (Command does not exist).
- **Bước 3 (Green):** Cài đặt `migrate_pg_to_d1.py`.
- **Bước 4 (Verify Green):**
  `docker compose exec backend python manage.py migrate_pg_to_d1 --dry-run`
  `docker compose exec backend pytest apps/common/tests/test_d1_migration.py -v`
  Kết quả: PASS.

---

## 4. Task 4: Chuyển Đổi Dịch Vụ Nghiệp Vụ (`AssetService`, `RequestService`) Sang D1 Atomic Batch

### 4.1. Chuyển Dịch Cơ Chế Khóa: Từ `select_for_update()` Sang D1 Atomic Guarded Batch OCC

Trong PostgreSQL, `AssetService` sử dụng:
```python
with transaction.atomic():
    asset = Asset.objects.select_for_update().get(pk=asset_id)
    # verify conditions ...
    asset.save()
    AssetHistoryLog.objects.create(...)
```

Khi chạy trên Cloudflare D1:
1. SQLite tại Edge là single-writer thread cho mỗi CSDL, không hỗ trợ interactive transactions hay `select_for_update()`.
2. Toàn bộ logic cập nhật tài sản + chèn log lịch sử được chuyển thành một chuỗi câu lệnh **Atomic Batch** gửi qua `d1_batch`.
3. Áp dụng **Optimistic Concurrency Control (OCC):** Câu lệnh `UPDATE` mang theo điều kiện trạng thái hiện tại (`WHERE id = ? AND status = ?`).

---

### 4.2. Chống Ghi Lịch Sử Mồ Côi (Orphan Audit Logs) Bằng `_assert_guard`

Nếu 2 yêu cầu đồng thời cùng muốn cấp phát một tài sản ở trạng thái `AVAILABLE`:
- Request A đến trước, cập nhật `status = 'IN_USE'` (thay đổi 1 dòng).
- Request B đến sau, chạy `UPDATE ... WHERE id = 1 AND status = 'AVAILABLE'`. Do tài sản đã chuyển sang `IN_USE`, số dòng bị ảnh hưởng là **0**.
- Nếu không có cơ chế chặn, câu lệnh thứ hai trong batch (`INSERT INTO assets_assethistorylog`) vẫn sẽ được thực thi, tạo ra bản ghi lịch sử mồ côi (Orphan Log)!

**Giải pháp:** Chèn câu lệnh kiểm soát `_assert_guard` ngay sau lệnh `UPDATE`:
```sql
UPDATE assets_asset 
SET status = 'IN_USE', assigned_to_user_id = ?, updated_at = CURRENT_TIMESTAMP 
WHERE id = ? AND status = 'AVAILABLE';

INSERT INTO _assert_guard (rows_affected) VALUES (changes());

INSERT INTO assets_assethistorylog (
    asset_id, actor_id, action_type, from_status, to_status, 
    from_user_id, to_user_id, from_department_id, to_department_id, 
    reason, asset_code_snapshot, asset_name_snapshot, created_at
) VALUES (
    ?, ?, 'ALLOCATE', 'AVAILABLE', 'IN_USE', 
    NULL, ?, ?, ?, 
    ?, ?, ?, CURRENT_TIMESTAMP
);
```

- Tại Request B, `changes()` trả về 0. Lệnh `INSERT INTO _assert_guard (rows_affected) VALUES (0)` vi phạm ràng buộc `CHECK(rows_affected > 0)`.
- D1 ném lỗi `IntegrityError: CHECK constraint failed: rows_affected > 0`, toàn bộ batch bị hủy bỏ, **không có bản ghi lịch sử nào được tạo ra**.
- Service bắt lỗi `IntegrityError` và ném ra `ConflictError("Tài sản đã thay đổi trạng thái bởi một giao dịch khác.")` tương ứng với mã HTTP `409 Conflict`.

---

### 4.3. Sinh Mã Tuần Tự Nguyên Tử (`AST-YYYY-NNN`, `REQ-YYYY-NNN`) Trong Cùng Batch

Để loại bỏ hoàn toàn race condition khi hai request tạo mới tài sản hoặc tạo phiếu cùng lúc:
Bộ đếm `assets_identifiersequence` được cập nhật tăng giá trị bằng cú pháp SQLite `UPSERT` trong cùng batch nguyên tử với thao tác tạo tài sản:

```sql
-- Bước 1: Tăng bộ đếm tuần tự
INSERT INTO assets_identifiersequence (kind, year, value)
VALUES ('AST', ?, 1)
ON CONFLICT(kind, year) DO UPDATE SET value = assets_identifiersequence.value + 1;

-- Bước 2: Tạo tài sản sử dụng giá trị bộ đếm vừa tăng
INSERT INTO assets_asset (
    asset_code, name, category_id, department_id, serial_number, 
    specification, status, assigned_to_user_id, image, document, purchase_date, created_at, updated_at
) VALUES (
    printf('AST-%04d-%03d', ?, (SELECT value FROM assets_identifiersequence WHERE kind = 'AST' AND year = ?)),
    ?, ?, ?, ?, 
    ?, 'AVAILABLE', NULL, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
);

-- Bước 3: Xác nhận câu lệnh tạo thành công
INSERT INTO _assert_guard (rows_affected) VALUES (changes());

-- Bước 4: Tạo bản ghi CREATE trong AssetHistoryLog
INSERT INTO assets_assethistorylog (
    asset_id, actor_id, action_type, from_status, to_status, 
    from_user_id, to_user_id, from_department_id, to_department_id, 
    reason, asset_code_snapshot, asset_name_snapshot, created_at
) VALUES (
    last_insert_rowid(), ?, 'CREATE', '', 'AVAILABLE', 
    NULL, NULL, NULL, ?, 
    ?, printf('AST-%04d-%03d', ?, (SELECT value FROM assets_identifiersequence WHERE kind = 'AST' AND year = ?)), 
    ?, CURRENT_TIMESTAMP
);
```

Hàm `generate_identifier(kind, year)` khi gọi độc lập (ví dụ trong mock/helper) cũng sử dụng SQLite UPSERT tương đương:
```python
def generate_identifier(kind: str, year: int = None, using: str = 'default') -> str:
    if year is None:
        year = timezone.now().year
    
    conn = connections[using]
    if getattr(conn, 'vendor', '') == 'cloudflare_d1':
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO assets_identifiersequence (kind, year, value) "
            "VALUES (?, ?, 1) "
            "ON CONFLICT(kind, year) DO UPDATE SET value = assets_identifiersequence.value + 1 "
            "RETURNING value;",
            [kind, year]
        )
        row = cursor.fetchone()
        next_val = row[0]
        return f"{kind}-{year}-{next_val:03d}"
    else:
        # Fallback cho PostgreSQL
        ...
```

---

### 4.4. Phân Định Chuẩn: `DAMAGE_REPORT` (Phiếu Yêu Cầu) vs `DAMAGE` (Hành Động Kiểm Toán)

Đặc tả nghiêm ngặt quy định:
- **`DAMAGE_REPORT`** là loại phiếu yêu cầu của người dùng (`AssetRequest.RequestType.DAMAGE_REPORT`).
- **`DAMAGE`** là loại hành động kiểm toán trong sổ nhật ký vòng đời tài sản (`AssetHistoryLog.ActionType.DAMAGE`).
- Tuyệt đối không nhầm lẫn giữa hai danh mục: Khi Admin hoặc Manager duyệt một phiếu báo hỏng `DAMAGE_REPORT` (thông qua `RequestService.approve_request`), dịch vụ gọi `AssetService.confirm_damage_report` và ghi nhận một bản ghi `AssetHistoryLog` với `action_type = 'DAMAGE'` (chuyển trạng thái từ `IN_USE` sang `DAMAGED_PENDING_REPAIR`, giữ nguyên `assigned_to_user` để quy trách nhiệm).

---

### 4.5. Xử Lý Từ Chối Phiếu (`reject_request`)

Khi Quản trị viên hoặc Trưởng phòng từ chối một phiếu yêu cầu (`RequestService.reject_request`):
1. Cập nhật `status = 'REJECTED'` và `rejection_reason = rejection_reason` trên bản ghi `requests_assetrequest`.
2. Tài sản liên quan giữ nguyên hiện trạng.
3. **Tuyệt đối không tạo bản ghi lịch sử `AssetHistoryLog`** (bảo toàn 100% logic hiện hữu trong mã nguồn).

Batch D1 tương ứng:
```sql
UPDATE requests_assetrequest 
SET status = 'REJECTED', rejection_reason = ?, reviewed_by_id = ?, reviewed_at = CURRENT_TIMESTAMP 
WHERE id = ? AND status = 'PENDING';

INSERT INTO _assert_guard (rows_affected) VALUES (changes());
```

---

### 4.6. Phòng Chống Suy Thoái (Regression Guards)

Theo yêu cầu trọng tâm đánh giá (Review Focus):
1. **Chống Ghi Đè Bản Ghi Cũ Khi PATCH Tài Sản (Stale Instance Overwrite):**
   - Trong `AssetUpdateSerializer`: Chỉ cho phép chỉnh sửa danh sách trắng (`name`, `category_id`, `serial_number`, `specification`, `purchase_date`).
   - Cấm hoàn toàn cập nhật `status`, `assigned_to_user_id`, `department_id`, `asset_code`.
   - Trước khi cập nhật, gọi `instance.refresh_from_db()` hoặc chỉ cập nhật đúng các trường thuộc danh sách trắng (`update_fields=[...]`), đảm bảo tài sản vừa được allocate ở luồng khác không bị revert về trạng thái cũ.
2. **Chống Tự Kích Hoạt Lại Tài Khoản Khi Cập Nhật Hồ Sơ (Profile Update Re-activation):**
   - Trong `ProfileUpdateSerializer`: Chỉ cho phép cập nhật `full_name`, `email`, `phone`.
   - Cấm hoàn toàn mọi trường `is_active`, `role`, `department`, `auth_version`. Không một thao tác cập nhật hồ sơ cá nhân nào có thể ghi đè lệnh khóa tài khoản của Admin.
3. **Chống Vô Hiệu Hóa Đồng Thời Admin Cuối Cùng:**
   - Bảo vệ ở tầng CSDL bằng Database Trigger `protect_last_admin_before_update`. Vì SQLite tại Edge thực thi đơn luồng tuần tự, request thứ hai luôn bị chặn và trả về lỗi 409 Conflict.
4. **Chống Ghi Log Kiểm Toán Mồ Côi Khi Cập Nhật 0 Dòng:**
   - Bảo vệ bằng bảng `_assert_guard` kiểm tra `CHECK(rows_affected > 0)`.

---

### 4.7. File Ranh Giới, Giao Tiếp Consumes/Produces & Quy Trình TDD Chi Tiết

#### File ranh giới:
- `backend/apps/assets/services.py` (Cập nhật sang D1 batch)
- `backend/apps/requests/services.py` (Cập nhật sang D1 batch)
- `backend/apps/assets/serializers.py` (Thắt chặt bảo vệ trường workflow)
- `backend/apps/authentication/serializers.py` (Thắt chặt bảo vệ hồ sơ người dùng)
- `backend/apps/assets/tests/test_d1_business_integrity.py` (Bộ kiểm thử tích hợp nghiệp vụ D1 mới)

#### Giao tiếp Consumes / Produces:
- **Consumes:** `d1_batch` từ adapter `assethub_api.backends.d1`.
- **Produces:** Nghiệp vụ Asset và Request phi tập trung, an toàn tuyệt đối trước tranh chấp đồng thời (race condition), không rò rỉ dữ liệu, đảm bảo nguyên tử 100%.

#### Quy trình TDD (Test-Driven Development):
- **Bước 1 (Red):** Tạo `backend/apps/assets/tests/test_d1_business_integrity.py` bao gồm 5 trường hợp kiểm thử trọng tâm:
  1. `test_concurrent_asset_allocation_exactly_one_winner`: Hai request cùng cấp phát 1 tài sản AVAILABLE, đúng 1 request thành công, 1 request nhận ConflictError (409), đúng 1 bản ghi `AssetHistoryLog` được sinh ra.
  2. `test_update_zero_rows_prevents_orphan_history_log`: Cố gắng thu hồi tài sản không ở trạng thái `IN_USE` bị `_assert_guard` chặn, không có log nào được tạo.
  3. `test_asset_patch_does_not_overwrite_workflow_status`: PATCH tài sản không làm mất trạng thái `IN_USE`.
  4. `test_profile_patch_does_not_reactivate_disabled_user`: User bị vô hiệu hóa gửi PATCH hồ sơ không thể kích hoạt lại tài khoản.
  5. `test_reject_request_does_not_create_asset_history_log`: Từ chối phiếu đổi trạng thái sang `REJECTED` và xác nhận 0 bản ghi `AssetHistoryLog`.
- **Bước 2 (Verify Red):**
  Chạy lệnh:
  `docker compose exec backend pytest apps/assets/tests/test_d1_business_integrity.py -v`
  Kết quả: FAIL.
- **Bước 3 (Green):** Refactor `AssetService` và `RequestService` để sử dụng `d1_batch` với `_assert_guard` và OCC.
- **Bước 4 (Verify Green):** Chạy lại bộ kiểm thử để xác nhận PASS 100%.

---

## 5. Bảng Tổng Hợp Kiểm Thử & Tiêu Chí Nghiệm Thu Tuần 4

| Gói Công Việc | Tệp Kiểm Thử Chính | Lệnh Kiểm Thử Thực Tế | Tiêu Chí Nghiệm Thu Đạt Chuẩn |
| :--- | :--- | :--- | :--- |
| **Task 2: Schema & Triggers** | `test_d1_schema_constraints.py` | `docker compose exec backend pytest assethub_api/backends/d1/tests/test_d1_schema_constraints.py -v` | 18 bảng tồn tại; Trigger chặn vô hiệu hóa/xóa Admin cuối cùng; CHECK constraints chặn 100% dữ liệu sai. |
| **Task 3: Migration & Reconcile** | `test_d1_migration.py` | `docker compose exec backend pytest apps/common/tests/test_d1_migration.py -v` | `migrate_pg_to_d1` nạp dữ liệu không lỗi; Báo cáo đối soát 1:1 xác nhận COUNT, PK, FK, PBKDF2 hash khớp từng ký tự. |
| **Task 4: Business Integrity** | `test_d1_business_integrity.py` | `docker compose exec backend pytest apps/assets/tests/test_d1_business_integrity.py -v` | Phân bổ đồng thời an toàn; Không sinh orphan log khi update 0 dòng; Mã AST/REQ tuần tự không nhảy cóc; Từ chối phiếu không tạo log. |

---
*Bản đề xuất đã hoàn thiện, sẵn sàng để tích hợp vào Kế hoạch Thực thi chung của Tuần 4.*
