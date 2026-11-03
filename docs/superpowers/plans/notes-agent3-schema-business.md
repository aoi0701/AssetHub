# Technical Synthesis Notes: Task 3 & Task 4 (Schema, Migration & Business Domain)

**Author:** Agent 3 (Schema, Migration & Business Domain Plan Specialist)  
**Branch:** `feat/cloudflare-d1`  
**Target:** Input synthesis notes for Plan Coordinator (`docs/superpowers/plans/2026-11-03-cloudflare-d1-implementation.md`)  
**Scope:** Review points 2 and 4 from `/home/truong/Downloads/AssetHub-review-plan-2026-11-04.md`

---

## 1. Task 4: Concurrency & Race Condition Resolution

### 1.1. Why `refresh_from_db()` Fails Against Race Conditions
- Cloudflare D1 uses SQLite at Edge.
- D1 does not provide row-level pessimistic locking (`SELECT ... FOR UPDATE`).
- `refresh_from_db()` fetches latest committed row into Python memory.
- Network latency separates `refresh_from_db()` HTTP roundtrip from `save()` HTTP roundtrip.
- Time-of-check to time-of-use (TOCTOU) race window remains open.
- Concurrent request can write to D1 between `refresh_from_db()` response and `save()` request.
- Default DRF `ModelSerializer` or `save()` overwrites all columns or writes stale memory values.
- Result: Silent state resurrection, lost updates, and orphan audit logs.

### 1.2. The Concrete Solution: Strict Allowlist & Atomic CAS / Assertion Guard

#### Rule 1: PATCH Must Update Only Permitted Fields
- Never execute whole-instance `instance.save()`.
- Always specify explicit `update_fields=[...]` or execute targeted `.filter(...).update(...)`.
- Reject forbidden workflow fields (`status`, `department_id`, `assigned_to_user_id`, `asset_code`) in serializers.

#### Rule 2: Conditional Writes (CAS) & `_assert_guard`
- Execute lifecycle state transitions inside single atomic batch via `d1_batch`.
- Use Compare-And-Swap (CAS) in UPDATE SQL:
  ```sql
  UPDATE assets_asset
  SET status = ?, assigned_to_user_id = ?, updated_at = ?
  WHERE id = ? AND status = ?;
  ```
- Enforce atomic assertion guard immediately after UPDATE:
  ```sql
  INSERT INTO _assert_guard (rows_affected) VALUES (changes());
  ```
- D1 aborts entire transaction if `changes() == 0` due to `CHECK(rows_affected > 0)`.
- Concurrent conflicting request receives `IntegrityError` or `ConflictError`.
- Zero orphan audit logs created when asset state changes concurrently.

### 1.3. Concurrency Protection Across Profile, Admin & Password Changes

#### 1. Profile PATCH (`apps/authentication/views.py` & `ProfileUpdateSerializer`)
- Endpoint: `PATCH /api/auth/profile/`.
- Permitted fields: `full_name`, `email`, `phone` only.
- Execution rule:
  ```python
  updated = User.objects.filter(pk=request.user.pk, is_active=True).update(
      **serializer.validated_data
  )
  if updated == 0:
      raise ConflictError("Tài khoản đã bị vô hiệu hóa hoặc bị thay đổi.")
  ```
- Guarantees profile update cannot reactivate disabled user or overwrite role.

#### 2. Admin User PATCH (`apps/authentication/admin_api.py` & `UserAdminUpdateSerializer`)
- Endpoint: `PATCH /api/admin/users/{id}/`.
- Security-critical fields: `role`, `department_id`, `is_active`.
- Execution rule:
  - If security field changes, atomically increment `auth_version`.
  - Use CAS on `auth_version` in SQL:
    ```sql
    UPDATE authentication_user
    SET role = ?, department_id = ?, is_active = ?, auth_version = auth_version + 1, updated_at = ?
    WHERE id = ? AND auth_version = ?;
    ```
  - Verify `changes() == 1`. If 0, raise `ConflictError("Tài khoản đã được cập nhật bởi phiên làm việc khác.")`.
  - Enforce DB trigger `protect_last_admin_before_update` on Edge.

#### 3. Change Password (`apps/authentication/views.py` & `ChangePasswordSerializer`)
- Endpoint: `POST /api/auth/change-password/`.
- Execution rule:
  - Hash new password using `PBKDF2PasswordHasher` with 1,000,000 iterations verbatim.
  - Execute conditional update:
    ```python
    current_version = request.user.auth_version
    updated = User.objects.filter(pk=request.user.pk, auth_version=current_version).update(
        password=make_password(new_password),
        auth_version=current_version + 1,
    )
    if updated == 0:
        raise ConflictError("Phiên làm việc đã bị hủy hoặc thay đổi đồng thời.")
    ```
  - Invalidates all existing JWT tokens immediately.

### 1.4. Atomic Sequential Identifier Generation (`apps/assets/identifiers.py`)
- Remove `select_for_update()` retry loop.
- Use native SQLite UPSERT with RETURNING supported in D1:
  ```sql
  INSERT INTO assets_identifiersequence (kind, year, value)
  VALUES (?, ?, 1)
  ON CONFLICT(kind, year) DO UPDATE SET value = assets_identifiersequence.value + 1
  RETURNING value;
  ```
- Executes in single atomic statement.
- Thread-safe and race-condition free at database engine level.

### 1.5. Exact Lifecycle Method Names Mapping
Match existing service implementations in codebase exactly:

| Service Class | Method Name | Input Arguments | Lifecycle Transition | Audit ActionType |
| :--- | :--- | :--- | :--- | :--- |
| `AssetService` | `allocate` | `asset_id, actor, assigned_to_user_id, reason` | `AVAILABLE -> IN_USE` | `ALLOCATE` |
| `AssetService` | `revoke` | `asset_id, actor, received_confirmed, condition, reason` | `IN_USE -> AVAILABLE` (GOOD)<br>`IN_USE -> DAMAGED_PENDING_REPAIR` (DAMAGED) | `RETURN` |
| `AssetService` | `mark_damaged` | `asset_id, actor, reason` | `AVAILABLE -> DAMAGED_PENDING_REPAIR` | `DAMAGE` |
| `AssetService` | `confirm_damage_report`| `asset_id, actor, reason` | `IN_USE -> DAMAGED_PENDING_REPAIR` | `DAMAGE` |
| `AssetService` | `send_maintenance` | `asset_id, actor, reason` | `DAMAGED_PENDING_REPAIR -> UNDER_MAINTENANCE` | `MAINTENANCE_START` |
| `AssetService` | `complete_maintenance`| `asset_id, actor, result, reason` | `UNDER_MAINTENANCE -> IN_USE` (RETURN_TO_USER)<br>`UNDER_MAINTENANCE -> AVAILABLE` (RETURN_TO_STOCK) | `MAINTENANCE_END` |
| `AssetService` | `transfer` | `asset_id, actor, to_department_id, reason` | `AVAILABLE -> AVAILABLE` (updates dept) | `TRANSFER` |
| `AssetService` | `propose_disposal` | `asset_id, actor, reason, received_confirmed` | `(AVAILABLE \| DAMAGED_PENDING_REPAIR \| UNDER_MAINTENANCE) -> PENDING_DISPOSAL` | `DISPOSAL_PROPOSE` |
| `AssetService` | `confirm_disposal` | `asset_id, actor, reason` | `PENDING_DISPOSAL -> DISPOSED` | `DISPOSAL_CONFIRM` |
| `RequestService`| `approve_request` | `request_id, actor, data` | `PENDING -> APPROVED` (delegates asset transition) | Matches asset transition |
| `RequestService`| `reject_request` | `request_id, actor, rejection_reason` | `PENDING -> REJECTED` | None (no asset log created) |

### 1.6. Concurrency Regression Test Specifications
Implement in `backend/apps/assets/tests/test_d1_business_integrity.py`:

1. **Test `test_concurrent_asset_allocation_race`:**
   - Two concurrent threads attempt to allocate same `AVAILABLE` asset to different users.
   - Thread 1 and Thread 2 both read asset state.
   - Thread 1 commits allocation batch successfully.
   - Thread 2 executes allocation batch.
   - Verify: Thread 2 fails with `ConflictError` or `IntegrityError` (`_assert_guard`).
   - Verify: Exactly 1 `AssetHistoryLog` record exists. Zero orphan logs.

2. **Test `test_interleaved_asset_patch_and_disposal`:**
   - Thread 1 prepares `PATCH` for asset metadata (`name="Updated Name"`).
   - Thread 2 executes `confirm_disposal` (`status='DISPOSED'`).
   - Thread 1 executes metadata update.
   - Verify: Stale in-memory state in Thread 1 cannot revert status from `DISPOSED` to `AVAILABLE`.
   - Verify: Update uses `update_fields` or conditional check `WHERE id = ? AND status != 'DISPOSED'`.

3. **Test `test_profile_patch_vs_admin_deactivation_race`:**
   - User loads profile in memory (`is_active=True`, `auth_version=1`).
   - Admin concurrently executes `PATCH /api/admin/users/{id}/` setting `is_active=False` and `auth_version=2`.
   - User executes `PATCH /api/auth/profile/` with `full_name="New Name"`.
   - Verify: User remains `is_active=False`.
   - Verify: `auth_version` remains 2.
   - Verify: User profile update fails with 401/409 or updates `full_name` only without touching auth flags.

4. **Test `test_change_password_auth_version_cas`:**
   - User initiates password change with `auth_version=1`.
   - Admin concurrently revokes sessions (`auth_version=2`).
   - User password update executes CAS.
   - Verify: Request aborted due to version mismatch, preventing stale session reinstatement.

### 1.7. Real File Ownership for Task 4
Update File Ownership table in plan:
- `backend/apps/assets/serializers.py` (`AssetUpdateSerializer` allowlist, remove full `instance.save()`)
- `backend/apps/authentication/serializers.py` (`ProfileUpdateSerializer` allowlist, remove full `instance.save()`)
- `backend/apps/authentication/admin_api.py` (`UserAdminUpdateSerializer` CAS & atomic `auth_version` bump)
- `backend/apps/authentication/views.py` (`ProfileView.patch`, `ChangePasswordView.post` CAS updates)
- `backend/apps/assets/identifiers.py` (Atomic SQLite UPSERT RETURNING)
- `backend/apps/assets/services.py` (9 lifecycle methods converted to atomic CAS batches)
- `backend/apps/requests/services.py` (`approve_request`, `reject_request` atomic CAS batches)
- `backend/apps/assets/tests/test_d1_business_integrity.py` (Comprehensive concurrency regression suite)

---

## 2. Task 3: Migration & Schema Safeguards

### 2.1. Strict Migration Source Scope
- Real PostgreSQL cutover is an unauthorized, separate phase.
- Task 3 must ONLY use cloned test database or dummy dataset (`tests/fixtures/test_dataset.json` / local test container).
- Command `migrate_pg_to_d1` flags:
  - `--dry-run`: Read source, calculate checksums, print topological execution plan, 0 writes to D1.
  - `--target-check`: Abort if target database has existing rows unless `--allow-non-empty` provided.
  - `--batch-size 50`: Chunks inserts into batches of 50 rows (fits D1 limits).

### 2.2. Topological Import Preserving Exact Primary Keys

#### Why Exact Primary Key Preservation Is Mandatory
- `django_content_type` IDs link directly to `auth_permission.content_type_id` and `django_admin_log.content_type_id`.
- `auth_permission` IDs link directly to `auth_group_permissions.permission_id` and `authentication_user_user_permissions.permission_id`.
- If D1 auto-generates IDs or runs Django migrations independently, IDs drift.
- Foreign keys from source data point to incorrect models or permissions.
- Result: Broken permission checks and corrupt audit trails.

#### 4-Tier Topological Import Sequence
The migration script must import tables in strict topological order with exact IDs:

```
Tier 1: Independent Tables & Core Metadata
  ├── django_content_type (exact id, app_label, model)
  ├── auth_permission (exact id, name, content_type_id, codename)
  ├── auth_group (exact id, name)
  ├── departments_department (exact id, name, code, parent_id)
  └── departments_category (exact id, name, code, description)

Tier 2: Direct Dependencies & Users
  ├── auth_group_permissions (exact id, group_id, permission_id)
  └── authentication_user (exact id, username, email, password verbatim, auth_version, department_id)

Tier 3: User Relations, System Logs & Core Assets
  ├── authentication_user_groups (exact id, user_id, group_id)
  ├── authentication_user_user_permissions (exact id, user_id, permission_id)
  ├── django_admin_log (exact id, user_id, content_type_id, object_id, action_flag, ...)
  ├── django_migrations (exact id, app, name, applied)
  ├── django_session (session_key, session_data, expire_date)
  ├── assets_identifiersequence (exact id, kind, year, value)
  └── assets_asset (exact id, asset_code, name, category_id, department_id, assigned_to_user_id, status)

Tier 4: Dependent History Logs & Requests
  ├── requests_assetrequest (exact id, request_code, requester_id, department_id, asset_id, status)
  └── assets_assethistorylog (exact id, asset_id, actor_id, from_user_id, to_user_id, action_type, ...)
```

#### SQLite Sequence Synchronization Post-Import
For every table imported with explicit `id`, update `sqlite_sequence`:
```sql
INSERT INTO sqlite_sequence (name, seq)
VALUES ('table_name', (SELECT MAX(id) FROM table_name))
ON CONFLICT(name) DO UPDATE SET seq = excluded.seq;
```

### 2.3. Automated 1:1 Reconciliation Protocol (6 Verification Criteria)
`D1Reconciler` verifies destination matches source dataset across 6 metrics:
1. **Row Count Parity:** `COUNT(*)` matches on all tables.
2. **Key Set Integrity:** Primary key sets (`SELECT id ...`) match 100%.
3. **Foreign Key Integrity:** `PRAGMA foreign_key_check;` returns 0 violations on D1.
4. **Password Hash Verbatim Check:** Checksum of PBKDF2 strings (`password`) matches 100% (verbatim 1,000,000 iterations preserved).
5. **Timestamp & Boolean Normalization:** Datetimes conform to ISO-8601 UTC; booleans mapped to `1/0`.
6. **Identifier Sequence Counters:** Max counter values in `assets_identifiersequence` match or exceed highest existing `asset_code` and `request_code`.

### 2.4. Single Versioned DDL Source & Verification Mechanism
- Authoritative source: `cloudflare-worker/bridge/schema.sql`.
- Mirror copy: `backend/assethub_api/backends/d1/sql/schema.sql`.
- Rule: Developers never edit mirror copy manually.
- Verification mechanism:
  - Add test in `backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py`:
    ```python
    def test_schema_ddl_copies_are_identical():
        source_path = settings.BASE_DIR.parent / "cloudflare-worker" / "bridge" / "schema.sql"
        mirror_path = settings.BASE_DIR / "assethub_api" / "backends" / "d1" / "sql" / "schema.sql"
        assert source_path.exists(), "Primary schema.sql missing"
        assert mirror_path.exists(), "Mirror schema.sql missing"
        assert source_path.read_bytes() == mirror_path.read_bytes(), (
            "Schema copies are out of sync. Run sync script."
        )
    ```
  - Provide synchronization script: `scripts/sync_d1_schema.sh` (`cp cloudflare-worker/bridge/schema.sql backend/assethub_api/backends/d1/sql/schema.sql`).

### 2.5. Table Inventory Decision & AI Logs Architecture
- Do not fixate strictly on 18 tables.
- Analysis:
  - 14 business/auth/system tables (`departments_*`, `authentication_*`, `assets_*`, `requests_*`, `django_*`, `auth_*`).
  - 2 D1 operational tables (`_assert_guard`, `_idempotency_log`).
  - 1 AI audit log table (`ai_suggestion_logs`).
- Recommended Table Inventory: **19 tables in total**.
- `ai_suggestion_logs` schema included in authoritative DDL:
  ```sql
  CREATE TABLE IF NOT EXISTS ai_suggestion_logs (
      id TEXT PRIMARY KEY,
      asset_name TEXT NOT NULL,
      description TEXT NOT NULL,
      suggested_category_id INTEGER,
      suggested_category_name TEXT,
      model TEXT NOT NULL,
      created_at TEXT NOT NULL
  );
  ```
- Permits Workers AI and Django to share unified audit query capability on Edge.

### 2.6. Confirmed `.gitignore` Rule for `schema.sql`
- Verified current `.gitignore`:
  - Line 58: `*.sql` (ignores SQL dumps and migration dumps).
  - Line 59: `!**/schema.sql` (whitelists all `schema.sql` files).
- Verification via `git check-ignore -v`:
  - `cloudflare-worker/bridge/schema.sql` -> Whitelisted (NOT ignored).
  - `backend/assethub_api/backends/d1/sql/schema.sql` -> Whitelisted (NOT ignored).
  - `dumps/pg_dump.sql`, `backup.sql` -> Ignored.
- Safeguard is verified and functioning as intended.

---

## 3. Recommended Implementation Plan Text Patches

Coordinator can incorporate the following concrete text into `docs/superpowers/plans/2026-11-03-cloudflare-d1-implementation.md`:

### 3.1. Replacement for Review Focus Point 5 (Plan Line 61)
```markdown
5. **Ghi đè trạng thái cũ & Tranh chấp truy cập (Stale Instance Overwrite & Concurrency Race):**
   - Loại bỏ hoàn toàn giả định `refresh_from_db()` đủ chống race condition vì TOCTOU window vẫn tồn tại giữa refresh và save, và D1 không hỗ trợ `select_for_update()`.
   - Các API PATCH (`AssetUpdateSerializer`, `ProfileUpdateSerializer`, `UserAdminUpdateSerializer`) chỉ được phép cập nhật danh sách trường cho phép (`update_fields`), tuyệt đối không lưu toàn bộ instance.
   - Toàn bộ chuyển đổi trạng thái nghiệp vụ và bảo vệ dữ liệu phải sử dụng Optimistic Concurrency Control (OCC) / Conditional Writes (`WHERE id = ? AND status = ?`) kết hợp `_assert_guard` trong cùng atomic batch.
   - Thao tác bảo mật tài khoản (khóa tài khoản, đổi vai trò, đổi mật khẩu) phải cập nhật nguyên tử `auth_version` bằng CAS (`WHERE id = ? AND auth_version = ?`).
```

### 3.2. Updates for Task 3 Description & Steps (Plan Lines 331–379)
```markdown
### Task 3: Công Cụ Di Chuyển Dữ Liệu & Đối Soát 1:1 (`migrate_pg_to_d1`)

> **Mục tiêu:** Xây dựng lệnh quản trị Django `migrate_pg_to_d1` trích xuất dữ liệu từ nguồn test/cloned dataset độc lập (tuyệt đối không đưa CSDL production lên cloud trong giai đoạn này), nạp lên D1 theo 4 Tier topo bảo toàn 100% khóa chính gốc của ContentTypes và Permissions, đồng bộ `sqlite_sequence`, và tự động đối soát 1:1 theo 6 tiêu chí kỹ thuật.
> 
> **Điều kiện tiên quyết:** Task 2 đã hoàn tất (D1 đã có cấu trúc DDL hoàn chỉnh gồm 19 bảng và triggers).

**Files:**
- Create: `backend/apps/common/management/commands/migrate_pg_to_d1.py`
- Create: `backend/apps/common/tests/test_d1_migration.py`
- Create: `scripts/sync_d1_schema.sh`

- [ ] **Step 1: Viết failing test cho lệnh `migrate_pg_to_d1` và bảo vệ nguồn dữ liệu**
  Trong `backend/apps/common/tests/test_d1_migration.py`:
  - Test tham số `--dry-run` không làm biến đổi CSDL D1 và xuất báo cáo đối soát dự kiến.
  - Test kiểm tra chặn không cho ghi nếu đích D1 không rỗng khi thiếu cờ `--allow-non-empty`.
  - Test bảo toàn nguyên vẹn ID gốc của `django_content_type`, `auth_permission`, `auth_group` và quan hệ M2M.
  - Test cập nhật `sqlite_sequence` khớp với `MAX(id)` sau khi import.
  - Test bảo toàn chuỗi băm PBKDF2 1.000.000 vòng nguyên vẹn từng ký tự.

- [ ] **Step 3: Cài đặt lệnh `migrate_pg_to_d1.py` theo 4 Tier Topo**
  - Thực hiện nhập dữ liệu theo đúng thứ tự:
    - Tier 1: `django_content_type`, `auth_permission`, `auth_group`, `departments_department`, `departments_category`.
    - Tier 2: `auth_group_permissions`, `authentication_user`.
    - Tier 3: `authentication_user_groups`, `authentication_user_user_permissions`, `django_admin_log`, `django_migrations`, `django_session`, `assets_identifiersequence`, `assets_asset`.
    - Tier 4: `requests_assetrequest`, `assets_assethistorylog`, `ai_suggestion_logs`.
  - Bảo toàn `id` tường minh cho mọi bản ghi.
  - Cập nhật `sqlite_sequence` sau mỗi bảng có autoincrement.
  - Đóng gói batch nạp tối đa 50 bản ghi / batch qua `d1_batch`.
  - Tự động chạy đối soát 6 tiêu chí (`D1Reconciler`).
```

### 3.3. Updates for Task 4 Description & Steps (Plan Lines 382–434)
```markdown
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

- [ ] **Step 1: Viết failing regression tests cho Concurrency & Race Conditions**
  Tạo `backend/apps/assets/tests/test_d1_business_integrity.py`:
  - Test 1: Hai request đồng thời gọi `AssetService.allocate` trên cùng tài sản `AVAILABLE` -> 1 thành công, 1 thất bại qua `_assert_guard`, không sinh orphan audit log.
  - Test 2: Request `PATCH /api/assets/{id}/` chạy xen giữa lúc tài sản bị chuyển trạng thái (`confirm_disposal` hoặc `allocate`) -> không làm đảo ngược trạng thái tài sản, chỉ cập nhật trường allowlist.
  - Test 3: Request `PATCH /api/auth/profile/` chạy xen giữa lúc Admin vô hiệu hóa tài khoản (`is_active=False`) -> tài khoản vẫn giữ `is_active=False`, không bị kích hoạt lại.
  - Test 4: Request Admin cập nhật quyền/khóa tài khoản hoặc User đổi mật khẩu sử dụng CAS trên `auth_version` -> phát hiện xung đột và tăng `auth_version` nguyên tử.
  - Test 5: Sinh mã `generate_identifier('AST')` và `generate_identifier('REQ')` đồng thời không bị trùng lặp hoặc lỗi khóa.
  - Test 6: Kiểm tra đầy đủ 9 phương thức của `AssetService` (`allocate`, `revoke`, `mark_damaged`, `confirm_damage_report`, `send_maintenance`, `complete_maintenance`, `transfer`, `propose_disposal`, `confirm_disposal`) và 2 phương thức của `RequestService` (`approve_request`, `reject_request`).
  - Test 7: Từ chối phiếu (`reject_request`) đổi trạng thái `REJECTED`, cập nhật `rejection_reason` và không tạo bất kỳ bản ghi `AssetHistoryLog` nào.

- [ ] **Step 3: Tái cấu trúc Services, Serializers và Views sang D1 Concurrency Guard**
  - Cập nhật `AssetUpdateSerializer`: Sử dụng `update_fields` hoặc `.filter(id=..., status!=DISPOSED).update(...)`. Loại bỏ `instance.save()`.
  - Cập nhật `ProfileUpdateSerializer`: Chỉ cập nhật `full_name`, `email`, `phone` với điều kiện `is_active=True`.
  - Cập nhật `UserAdminUpdateSerializer` & `ChangePasswordView`: Thực hiện cập nhật có điều kiện trên `auth_version` (`auth_version = auth_version + 1`).
  - Cập nhật `apps/assets/identifiers.py`: Thay thế vòng lặp `select_for_update` bằng câu lệnh SQLite UPSERT RETURNING nguyên tử.
  - Tái cấu trúc 9 phương thức `AssetService` và 2 phương thức `RequestService` sử dụng `d1_batch` kèm `_assert_guard`.
```
