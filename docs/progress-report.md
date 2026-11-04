# Báo Cáo Triển Khai Tuần 4: Chuyển Đổi Cloudflare D1 (AssetHub)
## Nghiệm Thu Hoàn Tất Task 0, Task 1, Task 2, Task 3 & Task 3c (Rehearsal Lifecycle & Harness Hardening)

- **Thời điểm:** 2026-11-04 14:55 (GMT+7)
- **Nhánh Git:** `feat/cloudflare-d1`
- **Tài khoản Cloudflare:** `hnhoa494@gmail.com` (Account ID: `5e76c2bf4e09587a010d31ea416ec8d3`)
- **Tài nguyên thử nghiệm:**
  - CSDL D1: `assethub-test-d1` (`3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`, APAC)
  - Worker Bridge Staging: `https://assethub-test-worker.hnhoa494-assethub.workers.dev` (Version ID: `9afb7c31-d0aa-4fc3-80f0-16a78040bd06`)
- **Trạng thái:** **Task 0, Task 1, Task 2, Task 3 & Task 3c PASS 100% — Sẵn sàng để Codex nghiệm thu.**
- **Ranh giới an toàn nghiêm ngặt:** 1 tab AGY, 0 worktree, 0 push/merge, 0 đụng CSDL PostgreSQL gốc (`USE_D1_DATABASE=False`), 0 chi phí Cloudflare, 0 đổi DNS, chưa chạy lệnh ghi lên D1 Cloud.

---

## 1. Xử Lý Khẩn Cấp Secret Leak & Làm Sạch Git History

Theo yêu cầu ưu tiên của Codex:
1. **Thay thế và xoay vòng Secret Token:**
   - Đã tạo ngẫu nhiên khóa bí mật 256-bit hex (`openssl rand -hex 32`) bằng cơ chế in-memory hoàn toàn không in ra màn hình hoặc log.
   - Cập nhật secret mới lên Cloudflare Worker test (`BRIDGE_SECRET_TOKEN`) qua `npx wrangler secret put`.
   - Đồng bộ cấu hình test local (`spikes/cf-d1-spike/.env.test` và `backend/.env.test`).
   - Kiểm chứng kết nối thành công với secret mới qua test suite (test isolation guard pass 100% với endpoint Cloudflare).
2. **Loại bỏ Secret khỏi tài liệu:**
   - Đã quét toàn bộ tệp git tracked (`git grep -n "ab88999"`).
   - Xóa chuỗi secret rò rỉ tại `docs/superpowers/plans/notes-agent4-runtime-deploy.md:127`, thay thế bằng placeholder `<D1_BRIDGE_SECRET_TOKEN_REDACTED>`.
3. **Làm sạch commit history bằng Controlled Amend:**
   - Thực hiện `git commit --amend` có kiểm soát, chỉ stage tệp tài liệu và progress report, không dùng `git add .` hay `commit -a`.
   - Commit Task 0 sạch hoàn toàn: SHA `b59893e`.

---

## 2. Kết Quả Triển Khai Task 1: Adapter, JOIN & Bảo Toàn Thứ Tự Cột

### 2.1. Bảo Toàn Cột Trùng Khi JOIN (`select_related`)
- **Vấn đề Codex chỉ ra:** Worker bridge dùng `stmt.all()` trả về JavaScript Object `{ col: val }`. Khi ORM thực hiện `select_related` (JOIN nhiều bảng có cột trùng tên như `id`, `name`), các key trong Object ghi đè nhau, làm hỏng dữ liệu hydrate của Django ORM models.
- **Giải pháp:**
  - Bridge Worker (`spikes/cf-d1-spike/bridge-worker/src/index.js`): Với read query, chuyển sang sử dụng `stmt.raw({ columnNames: true })`, trả về 2D array trong đó hàng 0 là mảng tên cột `columns`, theo sau là các mảng giá trị `rows`.
  - Python Adapter (`backend/assethub_api/backends/d1/base.py` & `client.py`): Gán trực tiếp `self._columns` và `self._rows` dạng tuple từ `columns` và `rows`, loại bỏ hoàn toàn việc chuyển đổi qua dictionary key.
  - Kiểm thử regression ORM `select_related` thực tế: `test_d1_orm_select_related_duplicate_columns_preservation` assert chính xác giá trị các model liên kết (`Asset`, `Category`, `Department`, `User`).

### 2.2. Bảo Toàn Siêu Dữ Liệu Qua Lớp Bọc Django (`D1DatabaseErrorWrapper`)
- Bọc toàn bộ ngoại lệ DB-API thành `django.db.utils.DatabaseError` nhưng bảo toàn 5 thuộc tính: `committed`, `code`, `idempotency_key`, `payload`, `raw_response`.
- Ghi đè `wrap_database_errors` trong `DatabaseWrapper` với `@cached_property`.

### 2.3. Chuẩn DB-API 2.0 & Helper Functions
- Cursor description trả về 7-tuple chuẩn `(col, None, None, None, None, None, None)`.
- Boolean normalization: `True -> 1`, `False -> 0`.
- Date truncation: `replace_date_trunc_in_sql(sql)` thay thế UDF thành SQLite `STRFTIME`/`DATE` cho 10 đơn vị thời gian.
- `d1_batch(statements, using='default', idempotency_key=None)` xuất tại `assethub_api.backends.d1`.

---

## 3. Kết Quả Triển Khai Task 2: Schema DDL An Toàn (Non-Destructive) & Tooling

### 3.1. Authoritative DDL An Toàn Tuyệt Đối (`cloudflare-worker/bridge/schema.sql`)
- **Loại bỏ hoàn toàn 21 lệnh `DROP`:** 19 `DROP TABLE` và 2 `DROP TRIGGER` bị xóa bỏ 100% khỏi DDL chính thức.
- **Sử dụng cú pháp an toàn:** Toàn bộ 19 bảng sử dụng `CREATE TABLE IF NOT EXISTS`, 2 triggers sử dụng `CREATE TRIGGER IF NOT EXISTS`.
- **Bảo toàn dữ liệu khi chạy lại (Re-entrant execution):** DDL có thể chạy lại nhiều lần mà không làm mất hoặc xóa đè bất kỳ bản ghi nào đã có.
- **19 Bảng đầy đủ:**
  - Core metadata: `django_content_type`, `auth_permission`, `auth_group`, `departments_department`, `departments_category`
  - Users & auth: `auth_group_permissions`, `authentication_user`, `authentication_user_groups`, `authentication_user_user_permissions`
  - System & sessions: `django_admin_log`, `django_migrations`, `django_session`, `assets_identifiersequence`
  - Assets & requests: `assets_asset`, `requests_assetrequest`, `assets_assethistorylog`
  - Infra, Idempotency & AI: `_assert_guard`, `_idempotency_log`, `ai_suggestion_logs`
- **2 Admin Protection Database Triggers:**
  - `protect_last_admin_before_update`: Chặn hạ quyền (`role != 'ADMIN'`) hoặc vô hiệu hóa (`is_active != 1`) Admin hoạt động cuối cùng.
  - `protect_last_admin_before_delete`: Chặn xóa Admin hoạt động cuối cùng.
- **CHECK Constraints trên `assets_asset`:**
  - Ràng buộc đúng 6 trạng thái: `'AVAILABLE'`, `'IN_USE'`, `'DAMAGED_PENDING_REPAIR'`, `'UNDER_MAINTENANCE'`, `'PENDING_DISPOSAL'`, `'DISPOSED'`.
  - Ràng buộc holder: `(status = 'AVAILABLE' AND assigned_to_user_id IS NULL) OR (status != 'AVAILABLE')`.

### 3.2. Tách Biệt Công Cụ Reset Database Test Có Guard Nghiêm Ngặt
- Tạo `scripts/reset_d1_test_db.sh` và `scripts/reset_d1_test_db.sql`:
  - Chỉ cho phép chạy khi có cờ tường minh `--confirm-destructive`.
  - Kiểm tra bắt buộc tên CSDL target phải là `assethub-test-d1` (chặn nhầm sang production).
- Script đồng bộ DDL `scripts/sync_d1_schema.sh` (`chmod +x`): Kiểm tra byte parity qua `cmp -s`, tự động đồng bộ sang `backend/assethub_api/backends/d1/sql/schema.sql`.
- Byte parity: Giống nhau 100% từng byte (10.970 bytes).

---

## 4. Kết Quả Triển Khai Task 3: Sửa Toàn Diện 5 Nhóm Lỗi Migration & Đối Soát

Đã hoàn thiện lệnh `migrate_pg_to_d1` (`backend/apps/common/management/commands/migrate_pg_to_d1.py`) và bộ test suite (`backend/apps/common/tests/test_d1_migration.py`) theo báo cáo review của Codex:

### 4.1. Nhóm 1 (P1): Mọi Sai Lệch Đối Soát Phải Kết Thúc Bằng Lỗi (Exit Non-Zero)
- Nếu `reconcile().success` là `False`, lệnh lập tức ghi danh sách vi phạm ra `stderr` và raise `CommandError(f"D1 Reconciliation failed with {len(rec_result.errors)} violations.")`.
- Tuyệt đối không bao giờ in `"Reconciliation PASSED"` khi còn bất kỳ sai lệch nào.
- Cờ `--allow-non-empty` chỉ bỏ qua kiểm tra đích ban đầu, tuyệt đối không được che giấu lỗi đối soát 1:1.

### 4.2. Nhóm 2 (P1): Đối Soát Nội Dung Từng Dòng/Cột (Row Content Parity)
- Bổ sung Criterion 7: `check_row_content_parity()` đối chiếu từng dòng theo khóa chính và từng cột.
- Bắt trọn vẹn mọi sai lệch: `name`, `status`, `role`, `is_active` (1 -> 0), `auth_version`, khóa ngoại hợp lệ nhưng trỏ bản ghi khác, dữ liệu JSON, và nội dung audit log.
- Chuẩn hóa kiểu dữ liệu an toàn (Canonicalization):
  - JSON: parse và so sánh canonical (`json.dumps(obj, sort_keys=True)`).
  - Bool: chuẩn hóa thành `1` hoặc `0`.
  - Datetime/Date: chuẩn hóa thành chuỗi UTC ISO-8601 string.
  - Chuỗi băm mật khẩu PBKDF2: đối chiếu nguyên văn từng ký tự, không bao giờ băm lại.
- **Bảo mật dữ liệu:** Thông báo lỗi chỉ ghi tên bảng, PK, và tên cột bị lệch (hoặc số lượng), tuyệt đối không in nội dung nhạy cảm, mật khẩu, hash hay dữ liệu cá nhân.

### 4.3. Nhóm 3 (P2): Deduplicate `sqlite_sequence` & Kiểm Tra Counter Theo `(kind, year)`
- `sqlite_sequence`: Tính `target_seq = max(existing_seq, max_id)`. Xóa dòng cũ và chèn duy nhất 1 dòng bằng batch. Đảm bảo `COUNT(*) == 1` cho mỗi bảng autoincrement, không bao giờ sinh dòng trùng và không hạ high-water mark. Khóa tự tăng tiếp theo tăng hợp lệ (`id > max_id`).
- `check_identifier_sequence_counters`: Phân tách kiểm tra chính xác theo từng cặp `(kind, year)` riêng biệt cho `AST-YYYY-NNN` và `REQ-YYYY-NNN`. Không gom chung năm làm che lấp sai lệch.

### 4.4. Nhóm 4 (P1): Lan Truyền Lỗi Guard Nguyên Trạng & Rollback SQLite Testbed
- Nhận diện backend D1 tường minh qua `conn.vendor == 'd1'`, settings `ENGINE` hoặc mocked `d1_batch`.
- Với D1: gọi `d1_batch` và ném thẳng mọi exception (đặc biệt là Isolation Guard hoặc Network error) ra ngoài; tuyệt đối không bắt lỗi rồi fallback sang cursor thực thi từng dòng.
- Với SQLite testbed: bọc các câu lệnh batch trong transaction tường minh (`BEGIN TRANSACTION;` và `ROLLBACK;` khi gặp lỗi), bảo đảm tính nguyên tử của batch.

### 4.5. Nhóm 5 (P1): Kiểm Tra Nguồn/Đích Chặt Chẽ & Non-Empty Guard Mặc Định
- Non-empty guard chạy **mặc định**: tự động kiểm tra CSDL đích và chặn dừng lại bằng `CommandError` nếu đích đã có dữ liệu, trừ khi người vận hành chỉ định `--allow-non-empty`.
- Bỏ fallback ngầm `postgres -> default`: yêu cầu cấu hình alias nguồn hợp lệ trong `connections.databases`.
- Chặn xung đột nguồn và đích: kiểm tra trùng alias và trùng file CSDL vật lý (`is_same_database`) trước khi thực hiện bất kỳ thao tác nào.
- `--dry-run`: chỉ trích xuất metadata và in kế hoạch 4-Tier, bảo đảm 0 ghi vào nguồn hoặc đích.

---

## 5. Tổng Hợp Kiểm Thử Toàn Tuyến & Ponytail Review

### 5.1. Kết Quả Kiểm Thử
- **Pytest Suite Toàn Diện (Container `assethub-backend-1`):**
  Lệnh: `docker exec assethub-backend-1 pytest assethub_api/backends/d1/tests/ apps/common/tests/test_d1_migration.py -v`
  Kết quả: **77 passed in 183.23s (100% PASS)**:
  - 14 tests atomicity & fault injection (Task 0)
  - 11 tests isolation guard & RFC 3986 (Task 0 & 1)
  - 11 tests ORM integration, error wrapper, joins raw protocol (Task 1)
  - 8 tests safe DDL, re-entrant, constraints, triggers & reset guards (Task 2)
  - **35 tests migration** (Task 3: 4-tier topo, sequence deduplication, content parity 7 criteria, guard propagation, atomic rollback, source/target validation).
- **Node Test Runner (Mock Tests):**
  Lệnh: `node spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs`
  Kết quả: **8 passed in 97.67ms (100% PASS)**.
- **Schema Byte Parity:** Xác nhận bằng `cmp -s` -> **PARITY_OK**.

### 5.2. Kết Luận Thẩm Định Độc Lập (Ponytail Review & Task 3 Followup)
- **Đánh giá:** **VERDICT: READY FOR CODEX REVIEW (ĐẠT PHẠM VI TESTBED / CHƯA CHẠY CLOUD)**.
- Đã giải quyết triệt để 4 vấn đề được Codex tái hiện trong lần review thứ hai:
  1. **Preflight schema & Chính sách bảng AI:** Phân biệt 16 bảng bắt buộc nguồn (`SOURCE_MANDATORY_TABLES`) và bảng mới ở đích (`TARGET_NEW_TABLES` gồm `ai_suggestion_logs`). 17 bảng dữ liệu đích, 19 bảng tổng thể gồm 2 bảng hạ tầng. Preflight kiểm tra tồn tại và khớp cấu trúc cột trước mọi thao tác ghi; bảng bắt buộc thiếu ở nguồn, đích hoặc cả hai đều bị chặn.
  2. **Canonicalization dựa trên kiểu schema:** Không đoán kiểu dữ liệu từ nội dung chuỗi. Tra cứu theo `(table, column)` metadata (`JSON_COLUMNS`, `DATETIME_COLUMNS`, `DATE_COLUMNS`, `BOOL_COLUMNS`, `PASSWORD_COLUMNS`). Toàn bộ các cột text/varchar được so sánh nguyên văn; password giữ nguyên văn; lỗi không in thông tin nhạy cảm.
  3. **Rollback lồng transaction & Bỏ mock detection:** Sử dụng `django.db.transaction.atomic(using=target_db)` cho SQLite batch execution để hỗ trợ savepoints lồng nhau và rollback sạch 100% khi lỗi; xóa bỏ hoàn toàn `from unittest.mock import Mock, MagicMock` và `is_mock` khỏi mã nguồn production; chặn backend không hỗ trợ trước ghi.
  4. **Clean Import & Không Upsert âm thầm:** Sử dụng `INSERT INTO` tiêu chuẩn; đích có xung đột PK bị từ chối sạch sẽ, không ghi đè âm thầm mật khẩu, vai trò, auth_version hay audit log.
- **Ranh giới an toàn:** Bằng chứng kiểm thử dựa trên testbed SQLite tạm / mock runner trong container độc lập. Chưa chạy migration lên Cloudflare D1 Cloud và chưa tác động vào PostgreSQL production (`USE_D1_DATABASE=False`).

---

## 6. Danh Sách Commit Trên Nhánh `feat/cloudflare-d1`

1. `b59893e` - `fix(bridge): complete end-to-end task 0 protocol for quoted returning and incomplete batch state`
2. `bacf717` - `feat(db): complete django d1 database backend adapter and client with db-api 2.0 compliance`
3. `6b4d921` - `feat(schema): define official d1 schema with 19 tables, 6 asset statuses and admin protection triggers`
4. `1e314d4` - `docs: update progress report for task 1 and task 2 verification`
5. `3d8d121` - `fix(adapter): preserve duplicate column ordering in joins using d1 raw protocol`
6. `1c72935` - `refactor(schema): make ddl non-destructive and isolate test reset with strict guards`
7. `1168ad3` - `feat(migration): implement pg-to-d1 topological migration command and reconciliation tests`
8. `1305783` - `docs: record task 1-3 implementation, safe ddl, and migration verification`
9. `8addff6` - `fix(migration): harden pg-to-d1 migration command with comprehensive content reconciliation and safety guards`
10. `a16aa8d` - `fix(migration): enforce schema-driven canonicalization, preflight validation and clean atomic import`
11. `83f95c1` - `test(migration): complete local end-to-end rehearsal from real postgresql to local d1 bridge`
12. `4ab02ae` - `fix(harness): harden process ownership, directory markers, and opt-in lifecycle gating`
13. *(Commit tiếp theo)* - `fix(harness): enforce foreign preflight, remove bulk cleanup, and self-clean failed setup`

---

## 7. Trạng Thái Baseline Task 3b (Commit 83f95c1)

Tại commit `83f95c1`, quy trình diễn tập PG thật -> D1 local đã được chứng minh lần đầu:
- **Nguồn PostgreSQL Test Độc Lập:**
  - Container chạy trên port `5434`, schema tạo bằng Django migrations (16 bảng bắt buộc, không có `ai_suggestion_logs`).
  - Nạp dữ liệu giả phong phú: PK gốc, PBKDF2 1,000,000 hashes, UTC datetimes, date, booleans, JSON objects/lists, M2M, sequence counters.
  - Cấp tài khoản `assethub_ro_user` chỉ có quyền `SELECT`.
- **Đích Cloudflare D1 Local & Worker Bridge:**
  - `wrangler dev` trên port `8787`, nạp DDL chuẩn `schema.sql` (17 bảng dữ liệu gồm `ai_suggestion_logs`, 19 bảng tổng thể gồm 2 bảng hạ tầng).
  - Khởi tạo 0 bản ghi trên 17 bảng dữ liệu.
  - Kết nối qua adapter Django `assethub_api.backends.d1` và Worker Bridge.
- **Kết quả baseline cũ:** 11 kịch bản migration và đối soát 1:1 đạt PASS. Tuy nhiên, Codex phát hiện hai lỗi P1 nghiêm trọng về lifecycle/process ownership và test gating.

---

## 8. Kết Quả Triển Khai Task 3c: Sửa Triệt Để P1/P2 Lifecycle & Harness Hardening

Nhận phản hồi review từ Codex đối với commit `83f95c1`, đã thực hiện tái cấu trúc toàn diện harness và lifecycle:

### 8.1. P1: Khắc Phục Process Ownership & Quản Lý Dọn Dẹp An Toàn
- **Xóa bỏ hoàn toàn lệnh nguy hiểm:** Loại bỏ 100% `_kill_processes_on_port`, `pkill`, `fuser`, và hành vi duyệt cây tiến trình cha trong `/proc` xuống con.
- **Chỉ quản lý process do run khởi tạo:** Worker bridge được khởi động qua `subprocess.Popen(..., start_new_session=True)`. Teardown chỉ gửi signal `SIGTERM` (sau đó `SIGKILL` nếu timeout) tới đúng process group của process do chính harness tạo thông qua `os.killpg()`.
- **Port Conflict Guard Tuyệt Đối:** Trước khi khởi động, kiểm tra socket port 8787 / 5434. Nếu port đang bận bởi tiến trình lạ, ném ngay `RuntimeError` và không chạm vào bất kỳ tiến trình nào trên host.
- **Sentinel Regression Test:** Đã kiểm chứng qua `test_sentinel_port_in_use_refuses_to_kill` và `test_sentinel_port_conflict_guard`: server socket sentinel cố ý chiếm port vẫn sống nguyên vẹn và tiếp tục nhận kết nối sau khi harness từ chối khởi động.

### 8.2. P1: Run Isolation & Directory Markers
- **Thư mục persist riêng biệt theo Run ID:** Mỗi lượt chạy tạo thư mục `/tmp/assethub_rehearsal_d1_{run_id}`.
- **Xác thực quyền sở hữu qua Marker:** Ghi file `run_marker.json` chứa `run_id`, `pid`, và `created_at`.
- **Cleanup Guard:** Hàm `cleanup_persist_directory()` kiểm tra bắt buộc: đường dẫn phải nằm trong `/tmp`, có tiền tố `assethub_rehearsal_d1_`, và nội dung file `run_marker.json` khớp chính xác với `run_id` của lượt chạy. Từ chối xóa và raise lỗi nếu thiếu marker hoặc lệch run ID.

### 8.3. P1: Gating Test Suite Mặc Định & Self-Contained Lifecycle Fixtures
- **Đăng ký Marker `@pytest.mark.rehearsal`:** Khai báo marker trong `backend/pytest.ini`.
- **Cơ chế Opt-in rõ ràng:** Bổ sung `backend/conftest.py` với cờ `--rehearsal` và biến môi trường `ASSETHUB_RUN_REHEARSAL=1`. Mặc định khi chạy `pytest` thông thường, toàn bộ 27 test rehearsal được skip ngay lập tức (0.04s), không tạo container Docker hay tiến trình Worker nào.
- **Fixture tự quản lý toàn vẹn (Self-Contained Lifecycle):**
  - Fixture `pg_rehearsal_harness` và `rehearsal_environment` tự động khởi động và dọn dẹp sạch sẽ cả CSDL PostgreSQL nguồn lẫn D1 đích trong khối `try ... finally`.
  - Không yêu cầu người vận hành phải chạy lệnh CLI thủ công trước hoặc sau test suite.
  - Bổ sung test hồi quy `test_00_teardown_reliability_regression`: chứng minh mọi tài nguyên nguồn/đích/port/thư mục tạm đều được dọn sạch hoàn toàn ngay cả khi có assertion cố tình thất bại giữa chừng.

### 8.4. P2: Cô Lập Network & Credentials An Toàn
- **Ràng buộc Loopback 127.0.0.1:** Container PostgreSQL test bind chặt chẽ vào `127.0.0.1:{port}:5432`, không mở ra `0.0.0.0`.
- **Docker Labels & Teardown Guard:** Gắn nhãn `assethub.owner=rehearsal` và `assethub.run_id={run_id}` cho container. Hàm teardown kiểm tra nhãn trước khi `docker rm -f`. Tuyệt đối bảo vệ các container hệ thống `assethub-db-1` (port 5433) và `pg-test` (port 5432).
- **Credentials ngẫu nhiên theo run:** Tự động sinh `admin_user` (`pgadm_{run_id}`), `ro_user` (`pgro_{run_id}`), và mật khẩu ngẫu nhiên qua `secrets.token_urlsafe(16)`. Không hardcode mật khẩu, không in credentials trong `repr()`, `str()` hay error logs.
- **Token Local Riêng Biệt:** Tự động sinh token ngẫu nhiên độc lập (`secrets.token_urlsafe(32)`), không tái sử dụng token Cloud. File env tạm được phân quyền chặt chẽ `chmod 0600`.

### 8.5. Vá 3 Đường Lỗi Cuối Cùng Theo Review 4ab02ae
- **Bỏ bulk cleanup khỏi test (P1):** `cleanup_rehearsal_containers` bắt buộc `run_id`, lọc đồng thời `label=assethub.owner=rehearsal` và `label=assethub.run_id={run_id}`. Xóa hoàn toàn lệnh dọn bulk trước test sentinel.
- **Preflight quyền sở hữu persist_dir & env_file (P1):** Thêm `assert_persist_dir_preflight` và `assert_env_file_preflight` trước mọi thao tác ghi/xóa trong `D1TargetHarness.setup`. Nếu đường dẫn đã tồn tại nhưng không thuộc `run_id`, lập tức ném `RuntimeError`, bảo toàn 100% marker và file sentinel của run khác, tuyệt đối không ghi đè marker.
- **Tự dọn dẹp khi setup thất bại (P2):** Cả `PGSourceHarness.setup` và `D1TargetHarness.setup` đều bọc toàn bộ các bước khởi tạo trong khối `try...except`. Nếu có lỗi (DDL ingest, process start, readiness, verify), harness tự động gọi `teardown()` dọn dẹp sạch tài nguyên mình vừa tạo và re-raise lỗi gốc.

---

## 9. Tổng Hợp Kiểm Thử Xác Minh Sau Khi Sửa (Task 3c)

### 9.1. Bộ Kiểm Thử Diễn Tập Opt-in (`--rehearsal`)
Lệnh: `backend/.venv/bin/pytest backend/apps/common/tests/test_harness_pg_source.py backend/apps/common/tests/test_harness_d1_target.py backend/apps/common/tests/test_pg_to_d1_rehearsal.py --rehearsal -v`
Kết quả: **32 passed in 38.55s (100% PASS)**:
- `test_harness_pg_source.py`: **12 passed** (sentinel conflict guard, foreign container on same port not removed, setup failure self-cleanup, foreign container teardown rejection, random credentials, labels & loopback binding, schema isolation 16 mandatory tables, seed completeness, PBKDF2 Django auth, richness verification, read-only privilege enforcement).
- `test_harness_d1_target.py`: **8 passed** (safe environment guard, env file 0600 mode, sentinel port kill refusal, marker validation refusal, lifecycle & process group clean termination, foreign marker & sentinel never overwritten/deleted, d1 setup failure self-cleanup, foreign env file preflight).
- `test_pg_to_d1_rehearsal.py`: **12 passed** (teardown reliability regression on failure, preflight checks, dry run 0 writes, real migration & 7-criteria parity, PBKDF2 auth verification, identifier sequences, non-empty target guard, 5 negative defect mutation detections).

### 9.2. Kiểm Thử Gating Suite Mặc Định (Zero Side-Effects)
Lệnh: `backend/.venv/bin/pytest backend/apps/common/tests/test_harness_pg_source.py backend/apps/common/tests/test_harness_d1_target.py backend/apps/common/tests/test_pg_to_d1_rehearsal.py`
Kết quả: **32 skipped in 0.04s (100% CLEAN)**.
- 0 container Docker được khởi tạo.
- 0 tiến trình Worker được spawn.
- 0 tiến trình ngoài bị ảnh hưởng.

### 9.3. Toàn Bộ Unit Test Suite Khác
- `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/apps/common/tests/test_d1_migration.py backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py backend/assethub_api/backends/d1/tests/test_isolation_guard.py -v`:
  **62 passed, 1 skipped in 25.99s (100% PASS)**.
- `node spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs`:
  **8 passed in 95.98ms (100% PASS)**.
- **Kiểm Tra Trạng Thái Hệ Thống:**
  - Cổng 5434 và 8787 hoàn toàn giải phóng (`Ports 5434 and 8787 are completely clean`).
  - Thư mục `/tmp/assethub_rehearsal_d1_*` dọn sạch 100%.
  - `assethub-db-1` (port 5433) và `pg-test` (port 5432) hoạt động bình thường, không bị gián đoạn.
  - Quét 0 chuỗi hex token/secret 64 ký tự trong git diff.

---

## 10. Ranh Giới An Toàn & Đề Xuất Nghiệm Thu Task 3c

- **Bằng chứng kỹ thuật:** Đã hoàn thiện toàn diện quy trình kiểm thử và diễn tập an toàn, giải quyết trọn vẹn mọi yêu cầu P1 và P2 của Codex về process ownership, marker isolation, opt-in gating, và dynamic credentials.
- **Ranh giới:** Chưa push/merge git, không ghi CSDL production PostgreSQL, không ghi Cloudflare D1 Cloud.

---

## 11. Báo Cáo Triển Khai Task 4: D1 Atomic Guarded Batch OCC & Toàn Vẹn Nghiệp Vụ

### 11.1. Khắc Phục P2 Dọn Dẹp Env File Ngoại Lai (`harness_d1_target.py`)
- **Vấn đề:** Khi `D1TargetHarness.teardown()` được gọi, nếu người dùng chỉ định đường dẫn `env_file` ngoại lai đã tồn tại từ trước, harness có thể vô tình xóa nhầm file đó khi gọi teardown lặp lại.
- **Giải pháp:**
  - `cleanup_env_file`: Bắt buộc kiểm tra `run_id in name`. Nếu đường dẫn không chứa `run_id`, bỏ qua không xóa.
  - Gắn cờ `self._created_env_file`: `teardown()` chỉ gỡ bỏ `env_file` nếu chính harness đã tạo file trong lượt chạy hiện tại.
  - Test hồi quy: `test_foreign_env_file_preflight_refuses_overwrite` kiểm tra việc gọi `harness.teardown()` hai lần liên tiếp nhưng file env ngoại lai vẫn nguyên vẹn 100%.

### 11.2. Chuyển Đổi Dịch Vụ Nghiệp Vụ Quản Lý Tài Sản (`apps/assets/`)
- **Sinh mã định danh nguyên tử (`identifiers.py`):**
  - Loại bỏ hoàn toàn vòng lặp `select_for_update()`.
  - Triển khai SQLite UPSERT RETURNING nguyên tử trên Cloudflare D1:
    `INSERT INTO assets_identifiersequence (kind, year, value) VALUES (%s, %s, 1) ON CONFLICT(kind, year) DO UPDATE SET value = assets_identifiersequence.value + 1 RETURNING value`.
  - Không sleep, không retry lock, chống hoàn toàn trùng lặp mã (`AST-YYYY-NNN`, `REQ-YYYY-NNN`).
  - Kiểm thử hành vi đa luồng (30 threads, 10 workers) và cách ly sequence giữa các loại mã (AST vs REQ).
- **Bảo vệ Cập nhật Thông tin Tài sản (`AssetUpdateSerializer`):**
  - Cập nhật ghi có điều kiện (conditional write) ở cấp CSDL: `Asset.objects.filter(pk=...).exclude(status=DISPOSED).update(...)`.
  - Khóa chặt danh sách trường thực tế cho phép: `name`, `category`, `serial_number`, `specification`, `purchase_date` (không bao gồm các trường không tồn tại).
  - Kiểm tra phạm vi phòng ban của Manager tại lúc ghi (`filter(department_id=actor.department_id)`).
  - Chặn đứng hoàn toàn việc stale instance ghi đè tài sản đã thanh lý (`DISPOSED`) hoặc chuyển phòng ban, ném `ConflictError` (HTTP 409) hoặc `PermissionDenied` (HTTP 403).
- **9 Phương thức Vòng đời Tài sản (`AssetService`):**
  - Toàn bộ 9 phương thức (`allocate`, `revoke`, `mark_damaged`, `confirm_damage_report`, `send_maintenance`, `complete_maintenance`, `transfer`, `propose_disposal`, `confirm_disposal`) được chuyển đổi sang cơ chế Guarded Atomic Batch OCC trên D1.
  - Guard toàn bộ điều kiện nghiệp vụ cốt lõi tại thời điểm ghi SQL (`WHERE id = ? AND status = ? AND department_id = ? AND assigned_to_user_id = ?`).
  - Cung cấp các helper dựng câu lệnh (`build_allocate_statements`, `build_revoke_statements`, `build_confirm_damage_statements`) trả về đầy đủ guard department/holder.

### 11.3. Bảo Vệ Tài Khoản, Phiên Làm Việc & Quyền Quản Trị (`apps/authentication/`)
- **Cập nhật hồ sơ cá nhân (`ProfileUpdateSerializer`):**
  - Giới hạn allowlist chỉ cho phép cập nhật `full_name`, `email`, `phone`.
  - Áp dụng bộ lọc `is_active=True` tại câu lệnh ghi: nếu tài khoản đã bị vô hiệu hóa, mọi yêu cầu PATCH lập tức bị từ chối bằng `PermissionDenied` (HTTP 403), không thể tự kích hoạt lại tài khoản.
- **Đổi mật khẩu an toàn (`ChangePasswordView`):**
  - Áp dụng CAS (Compare-And-Swap) trên `auth_version` kết hợp điều kiện `is_active=True`.
  - Tự động tăng `auth_version` nguyên tử (`F('auth_version') + 1`) để vô hiệu hóa toàn bộ Refresh Token cũ.
  - Ngăn chặn xung đột khi có nhiều phiên đổi mật khẩu hoặc cập nhật tài khoản song song.
- **Bảo vệ Quản trị viên & Chống Bypass CAS (`UserAdminUpdateSerializer`):**
  - Khắc phục triệt để lỗi bypass CAS khi payload gửi lên trùng giá trị với instance cũ (`is_active`, `role`, `department`): bắt buộc đối chiếu `auth_version=current_version` tại câu lệnh ghi `update()`.
  - Tăng `auth_version = current_version + 1` khi có bất kỳ trường bảo mật nào trong payload, ngăn chặn hoàn toàn stale Admin PATCH bật lại tài khoản đã bị khóa ở CSDL.
  - Tầng trigger SQLite: trigger `protect_last_admin_before_update` và `protect_last_admin_before_delete` chặn đứng mọi hành vi xóa/sửa trái phép Admin cuối cùng.
  - Xóa bỏ việc tự động dò port mạng (5433/5432) tại thời điểm import test.

### 11.4. Quy Trình Duyệt Phiếu Yêu Cầu Đơn Batch Nguyên Tử (`apps/requests/`)
- **Nguyên tắc Batch Duy Nhất (Single D1 Batch Contract):**
  - `RequestService.approve_request`: Ghép nối toàn bộ câu lệnh duyệt phiếu (`requests_assetrequest`), guard người yêu cầu đang hoạt động và cùng phòng ban (`UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s`), cập nhật tài sản (`assets_asset`), `_assert_guard`, và ghi nhật ký lịch sử (`assets_assethistorylog`) vào duy nhất 1 mảng lệnh `d1_batch`.
  - Cam kết không có commit riêng lẻ giữa các câu lệnh: nếu tài sản, người yêu cầu, hoặc phiếu bị thay đổi đồng thời, toàn bộ batch bị hủy, 0 bản ghi audit log mồ côi (zero orphan logs).
  - `RequestService.reject_request`: Cập nhật trạng thái `REJECTED`, ghi `rejection_reason` qua `_assert_guard`, không thay đổi tài sản và không tạo nhật ký lịch sử tài sản.

### 11.5. Bộ Adapter D1 Backend (`assethub_api/backends/d1/`)
- Chuẩn hóa tự động tham số `d1_batch`:
  - Chuyển đổi ký tự placeholder `%s` sang `?` tương thích SQLite/D1.
  - Chuẩn hóa boolean Python (`True` -> `1`, `False` -> `0`).

### 11.6. Khắc Phục Triệt Để 4 Lỗi Hồi Quy Nghiệp Vụ Theo Review 458c3a5
- **1. Bảo toàn người giữ khi xác nhận báo hỏng (`confirm_damage_report`):**
  - Trong `build_confirm_damage_statements` và `confirm_damage_report`, loại bỏ hoàn toàn việc gán `assigned_to_user_id = NULL`. Người giữ tài sản được giữ nguyên theo đúng hợp đồng nghiệp vụ PostgreSQL và docstring ("Retains holder for accountability").
  - Audit log ghi nhận chính xác `from_user_id = to_user_id = asset.assigned_to_user_id`.
  - Luồng bảo trì hoàn chỉnh chạy trơn tru không ngắt quãng: `allocate` -> `confirm_damage_report` (giữ holder) -> `send_maintenance` (giữ holder) -> `complete_maintenance(RETURN_TO_USER)` (bàn giao lại thành công cho người giữ cũ).
  - Bỏ hoàn toàn thao tác hack sửa trạng thái giữa luồng trong test (`test_d1_business_integrity.py`).
- **2. Xử lý predicate kho chung công ty (`department_id IS NULL`):**
  - Trong `propose_disposal` và `confirm_disposal`: sử dụng biểu thức điều kiện `department_id IS NULL` khi `asset.department_id is None`, tránh so sánh `= NULL` sai cú pháp SQL.
  - Xử lý tương tự cho `assigned_to_user_id IS NULL` khi tài sản không có người giữ.
  - Cho phép tài sản thuộc kho chung công ty được đề xuất và xác nhận thanh lý hợp lệ mà không bị vướng assertion guard.
- **3. Guard người yêu cầu tại thời điểm ghi cho cả RETURN và DAMAGE_REPORT:**
  - Bổ sung `requester_guard` (`UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s`) và `requester_guard_check` (`INSERT INTO _assert_guard (rows_affected) VALUES (changes());`) vào batch nguyên tử cho cả 3 loại phiếu (`ALLOCATE`, `RETURN`, `DAMAGE_REPORT`).
  - Nếu người yêu cầu bị khóa tài khoản hoặc chuyển phòng ban giữa lúc đọc và duyệt phiếu: toàn bộ batch bị hủy, ném `ConflictError`, 0 thay đổi tài sản, 0 audit log.
- **4. Guard danh mục tài sản tại thời điểm duyệt cấp phát (`ALLOCATE`):**
  - Bổ sung `category_guard` (`UPDATE assets_asset SET id = id WHERE id = %s AND category_id = %s`) và `category_guard_check` vào batch `ALLOCATE`.
  - Nếu danh mục tài sản bị thay đổi đồng thời giữa lúc đọc và duyệt: batch bị hủy với `ConflictError`, không thể cấp phát sai danh mục được yêu cầu.
- **5. Kiểm thử rollback thực tế qua trigger CSDL:**
  - `test_production_approve_request_rollback_via_trigger`: tạo trigger `test_fail_audit_insert` tạm thời trên D1 local gây lỗi khi chèn audit log sau các câu lệnh UPDATE. Gọi trực tiếp `RequestService.approve_request`.
  - Chứng minh thực tế CSDL D1 rollback toàn bộ thay đổi trước đó (phiếu giữ `PENDING`, tài sản giữ `AVAILABLE`, 0 audit log). Trigger được dọn dẹp an toàn trong khối `finally`.

---

## 12. Tổng Hợp Kết Quả Kiểm Thử Toàn Diện (Task 4 Regressions)

### 12.1. Concurrency, OCC & Rollback Test Suite Trên D1 Thật (`--rehearsal`)
Lệnh: `backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_business_integrity.py --rehearsal -v`
Kết quả: **21 passed in 55.36s (100% PASS)**:
1. `test_concurrent_asset_allocation_race`: Đua cấp phát đồng thời -> đúng 1 thành công, 1 nhận `ConflictError`, đúng 1 audit log.
2. `test_concurrent_request_approval_vs_rejection`: Đua duyệt vs từ chối -> đúng 1 thành công, 1 nhận `ConflictError`.
3. `test_interleaved_asset_patch_and_disposal`: PATCH sau thanh lý bị chặn bằng `ConflictError`.
4. `test_profile_patch_vs_admin_deactivation_race`: PATCH thông tin trên tài khoản bị khóa bị chặn bằng `PermissionDenied`.
5. `test_change_password_auth_version_cas`: CAS phát hiện phiên cũ, tăng version nguyên tử.
6. `test_last_admin_protection_trigger`: Chặn hạ quyền/khóa quản trị viên cuối cùng.
7. `test_concurrent_identifier_generation`: 8 luồng sinh mã AST/REQ -> 0 mã trùng, tăng đơn điệu liên tục.
8. `test_real_d1_batch_rollback_on_constraint_violation`: Batch thất bại rollback sạch CSDL D1.
9. `test_codex_regression_manager_allocates_after_transfer_to_other_department`: Manager IT cấp phát tài sản đã sang HR -> chặn bằng `ConflictError`.
10. `test_codex_regression_approve_request_after_requester_deactivated`: Duyệt phiếu sau khi requester bị khóa -> chặn bằng `ConflictError`.
11. `test_codex_regression_stale_admin_patch_reactivates_user`: Stale PATCH gửi `is_active=True` sau khi DB bị khóa -> chặn bằng `ConflictError`.
12. `test_codex_regression_stale_asset_patch_updates_name_after_disposal`: Stale PATCH sửa name sau khi thanh lý -> chặn bằng `ConflictError`.
13. `test_d1_all_asset_service_lifecycle_actions`: Happy-path 9 hành động tài sản không cần hack state.
14. `test_d1_all_asset_service_invalid_transitions`: Từ chối trạng thái không hợp lệ 9 hành động.
15. `test_d1_request_service_workflows_and_invalid_states`: Happy-path và invalid-states quy trình yêu cầu.
16. `test_d1_company_pool_disposal_and_maintenance`: Bảo trì và thanh lý kho chung (`department_id IS NULL`) thành công trên D1.
17. `test_interleaved_requester_state_allocate`: Interleave requester inactive / chuyển phòng khi duyệt ALLOCATE -> rollback.
18. `test_interleaved_requester_state_return`: Interleave requester inactive / chuyển phòng khi duyệt RETURN -> rollback.
19. `test_interleaved_requester_state_damage_report`: Interleave requester inactive / chuyển phòng khi duyệt DAMAGE_REPORT -> rollback.
20. `test_interleaved_category_change_on_allocate_request`: Interleave đổi category tài sản khi duyệt ALLOCATE -> rollback.
21. `test_production_approve_request_rollback_via_trigger`: Gọi trực tiếp `approve_request` với trigger gây lỗi audit -> rollback toàn bộ.

### 12.2. Unit Test Suites Cho Nghiệp Vụ D1
Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_asset_mutations.py backend/apps/requests/tests/test_d1_request_mutations.py backend/apps/authentication/tests/test_d1_auth_integrity.py backend/tests/test_identifiers.py -v`
Kết quả: **72 passed in 4.41s (100% PASS)**:
- `test_d1_asset_mutations.py`: **28 passed** (thêm test thanh lý kho chung, giữ holder).
- `test_d1_request_mutations.py`: **27 passed** (thêm test guard requester cho RETURN/DAMAGE_REPORT và guard category).
- `test_d1_auth_integrity.py`: **10 passed**.
- `test_identifiers.py`: **7 passed**.

### 12.3. Baseline Suite Hệ Thống (PostgreSQL Test Container)
Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/ -q`
Kết quả: **185 passed in 72.65s (100% PASS)**.

### 12.4. Harness & Migration Rehearsal Suites
Lệnh: `backend/.venv/bin/pytest backend/apps/common/tests/test_harness_pg_source.py backend/apps/common/tests/test_harness_d1_target.py backend/apps/common/tests/test_pg_to_d1_rehearsal.py --rehearsal -v`
Kết quả: **32 passed in 72.61s (100% PASS)**.

### 12.5. Gating Test Suite Mặc Định (Zero Side-Effects)
Lệnh: `backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_business_integrity.py backend/apps/common/tests/test_pg_to_d1_rehearsal.py -q`
Kết quả: **33 skipped in 0.35s (100% CLEAN)**.

### 12.6. Node Bridge Regression Suite
Lệnh: `node spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs`
Kết quả: **8 passed in 149.41ms (100% PASS)**.

### 12.7. Trạng Thái Môi Trường & Hệ Thống
- Cổng 5434 và 8787: sạch 100% (`ss -tulpn` xác nhận CLEAN).
- Containers hệ thống: `assethub-backend-1` (8000), `assethub-db-1` (5433), `pg-test` (5432) hoạt động bình thường, không bị gián đoạn.
- Quét git diff: 0 chuỗi hex secret 64 ký tự.

---

## 13. Ranh Giới An Toàn & Đề Xuất Nghiệm Thu Task 4

- **Bằng chứng kỹ thuật:**
  - 4 lỗi hồi quy (mất holder khi báo hỏng, thanh lý kho chung bị chặn, RETURN/DAMAGE thiếu guard requester, ALLOCATE thiếu guard category) đã được khắc phục hoàn toàn ở cả tầng production code và test suites.
  - Reviewer độc lập (Agent D) thẩm định đạt 6/6 tiêu chí kỹ thuật.
  - Rollback thực tế của `approve_request` được chứng minh trên D1 local bằng trigger kiểm thử.
- **Ranh giới an toàn:**
  - Ứng dụng chính giữ nguyên cấu hình PostgreSQL (`USE_D1_DATABASE=False`).
  - Chưa chuyển dữ liệu thật lên Cloudflare D1 Cloud.
  - Không push/merge git, không thay đổi DNS, không kích hoạt dịch vụ trả phí, chưa mở Task 5 (R2/AI).
- Codex đã nghiệm thu commit `bd1fcd2` (21/21 D1 integration tests 44.25s, 250/250 backend + domain unit tests 73.90s).

---

## 14. Báo Cáo Triển Khai Task 5: Cloudflare R2 Storage & Workers AI Proxy (Local/Mock First)

- **Thời điểm:** 2026-11-04 21:05 (GMT+7)
- **Trạng thái:** **Task 5 PASS 100% — Đã qua review độc lập Ponytail & sẵn sàng để Codex nghiệm thu.**
- **Ranh giới nghiêm ngặt:** Kiểm thử local/mock hoàn toàn; chưa đụng dữ liệu thật, chưa deploy cloud, không kích hoạt R2 billing, không tiêu quota AI cloud, chưa mở Task 6.

### 14.1. R2 Storage & Presigned URLs 15 Phút (`backend/apps/common/storage_backends.py`, `backend/apps/assets/views.py`)
- Cấu hình TTL mặc định 900s qua `settings.R2_PRESIGNED_URL_EXPIRE_SECONDS` (default 900), hỗ trợ override qua tham số `expires_in` / `expire_seconds`.
- Metadata `expires_in` trong response của action `download` trả về đúng 900s (khớp TTL thực tế, không còn hardcode 60s).
- Disposition: inline cho ảnh, attachment cho tài liệu (`attachment; filename="..."`).
- Database chỉ lưu relative object key (`assets/{id}/{kind}/{uuid}_{filename}`), tuyệt đối không lưu presigned URL.
- Conditional write an toàn trên D1/PostgreSQL:
  - Cập nhật ghi có điều kiện: `Asset.objects.filter(id=asset.id, **{kind: old_key}).exclude(status=Asset.Status.DISPOSED).update(...)`.
  - Nếu `rows == 0` (tài sản đã DISPOSED hoặc bị thay đổi đồng thời): dọn sạch file mới vừa upload (`storage.delete(saved_key)`), trả về HTTP 400 Bad Request.
  - Xử lý ngoại lệ commit: nếu `getattr(exc, 'committed', False) is True` (`D1IncompleteBatchError` báo đã committed), tuyệt đối không xóa mù object mới; nếu uncommitted DB failure thì dọn file mới.
  - Xóa file cũ an toàn: chỉ xóa `old_key` sau khi DB cập nhật thành công (`rows == 1`), thông qua `transaction.on_commit` nếu trong transaction.

### 14.2. Cloudflare Worker AI Proxy (`cloudflare-worker/src/index.js`, `wrangler.toml`, `tests/worker.test.js`)
- Model cấu hình: `@cf/meta/llama-3.1-8b-instruct-fp8` (qua `env.AI_MODEL || '@cf/meta/llama-3.1-8b-instruct-fp8'`). Đã loại bỏ hoàn toàn model `@cf/meta/llama-3-8b-instruct` đã deprecated.
- Chuẩn hóa mã lỗi theo tài liệu Cloudflare chính thức:
  - Quota Free (code 3036 / HTTP 429): trả về HTTP 429 `{ error: 'Quota exceeded', code: 3036 }`.
  - Capacity constrained (code 3040 / HTTP 429): trả về HTTP 429 `{ error: 'Capacity constrained', code: 3040 }`.
  - Lỗi 10016 không coi là quota, trả về HTTP 503 `UNAVAILABLE`.
  - Các lỗi khác: trả về HTTP 503 `{ error: 'AI service unavailable', code: 'UNAVAILABLE' }`.
- Xác thực danh mục trả về:
  - Loại bỏ hoàn toàn hành vi tự động chọn `categories[0]`.
  - Nếu category ID hoặc category name không khớp với danh sách `categories` được phép: trả về HTTP 502 Bad Gateway `{ error: 'Invalid AI suggestion: category not in list', code: 'INVALID_SUGGESTION' }`.
- Ghi log D1: lưu lịch sử vào bảng `ai_suggestion_logs` kèm `request_id`, trả về `log_saved: true/false` phản ánh đúng thực tế.

### 14.3. AI Backend & Heuristic Keyword Fallback (`backend/apps/ai/services.py`, `serializers.py`, `views.py`)
- Kích hoạt dự phòng (Heuristic Fallback) khi: upstream trả lỗi 429 (3036/3040), 5xx, timeout (`socket.timeout`), lỗi kết nối (`URLError`), hoặc circuit breaker đang MỞ.
- Heuristic Keyword Matching:
  - Phân tích từ khóa không phân biệt hoa thường và dấu tiếng Việt (`remove_accents`).
  - Chỉ chọn từ các danh mục ĐANG TỒN TẠI trong CSDL (`Category.objects.all()`).
  - Tuyệt đối không chọn bừa `categories[0]`. Nếu không tìm thấy từ khóa khớp hoặc danh mục CSDL trống, ném ngoại lệ có kiểm soát `ServiceUnavailable` (HTTP 503) để người dùng tự chọn thủ công trên UI.
  - Phản hồi fallback trả về `is_fallback: True` kèm giải thích rõ ràng; phản hồi thành công từ Worker trả về `is_fallback: False`.
- Bảo toàn xác thực & phân quyền:
  - Rate limit người dùng: 10 req/phút/user trả về HTTP 429 `Throttled`.
  - RBAC: chỉ Admin mới được gọi (HTTP 403 `PermissionDenied`).
  - Input validation: kiểm tra tên/mô tả (HTTP 400 `ValidationError`).
  - Các lỗi này không bao giờ bị nuốt thành fallback thành công.
- Circuit breaker: tự động mở sau 3 lỗi mạng liên tiếp trong 30 giây. Khi mạch mở, trực tiếp sử dụng heuristic fallback mà không gọi mạng.

### 14.4. Tổng Hợp Kết Quả Kiểm Thử Toàn Tuyến Sau Khi Tích Hợp
1. **Kiểm thử Storage & AI Backend:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_storage.py backend/tests/test_ai_proxy.py backend/apps/assets/tests/test_r2_storage.py -v`
   - Kết quả: **36 passed in 5.56s (100% PASS)**.
2. **Kiểm thử Cloudflare Worker Suite:**
   - Lệnh: `cd cloudflare-worker && npm test`
   - Kết quả: **13 passed in 311ms (100% PASS)**.
3. **Kiểm thử D1 Business Integrity & OCC Regression Suite:**
   - Lệnh: `backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_business_integrity.py --rehearsal -v`
   - Kết quả: **21 passed in 25.22s (100% PASS)**.
4. **Kiểm thử Baseline Backend Suite (PostgreSQL Test Container):**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/ -q`
   - Kết quả: **193 passed in 65.93s (100% PASS)**.
5. **Thẩm định Độc Lập Ponytail Review (Agent D):**
   - **Verdict: PASS (Approved)**. 16/16 tiêu chí kỹ thuật đạt yêu cầu, tuân thủ nguyên tắc anti-bloat/ponytail.

---

## 15. Ranh Giới An Toàn & Đề Xuất Nghiệm Thu Task 5
- **Ranh giới an toàn giữ vững 100%:**
  - Production app tiếp tục vận hành trên PostgreSQL (`USE_D1_DATABASE=False`).
  - Chưa chuyển dữ liệu thật lên Cloudflare D1 Cloud.
  - Không deploy lên Cloudflare Pages/Worker production, không mở Cloudflare Tunnel.
  - Không tạo/xóa bucket R2 cloud, không kích hoạt dịch vụ tính phí.
  - Không gọi AI cloud tiêu thụ định mức 10.000 Neurons/ngày.
  - Nhánh `feat/cloudflare-d1`: không worktree, không push/merge lên remote.
- Kính gửi Codex nghiệm thu Task 5.

---

## 16. Báo Cáo Triển Khai Task 5 Sửa Lỗi Hồi Quy (Theo Review Commit feff816)

- **Thời điểm:** 2026-11-04 22:00 (GMT+7)
- **Trạng thái:** **Task 5 Fixes PASS 100% — Đã qua review độc lập Agent D & sẵn sàng để Codex nghiệm thu.**
- **Ranh giới an toàn nghiêm ngặt:**
  - Không cài đặt/thêm/nâng cấp thư viện, công cụ, phần mềm; không kéo Docker image mới.
  - Kiểm thử mock/local trên D1 local và PostgreSQL test container; chưa đụng dữ liệu thật, chưa deploy cloud, không kích hoạt R2 billing, không tiêu quota AI cloud, chưa mở Task 6.

### 16.1. Chi Tiết Sửa 5 Lỗi Đã Tái Hiện Bằng Mã Nguồn Production
1. **Bug 1 (P1) - Ba trạng thái dọn dẹp commit (views.py:466-490):**
   - Đã sửa việc xóa mù tệp mới khi thiếu cờ `committed`.
   - Ba trạng thái rõ ràng:
     - `committed is True`: DB đã commit -> giữ `saved_key`.
     - `committed is False`: DB chắc chắn chưa ghi -> xóa `saved_key`.
     - `committed is None` (OperationalError / mất ack): thực hiện safe readback từ DB:
       - Nếu DB đã trỏ `saved_key`: giữ `saved_key` và xóa `old_key`.
       - Nếu DB chưa trỏ `saved_key`: an toàn xóa `saved_key`.
       - Nếu readback bản thân bị lỗi: giữ `saved_key` làm orphan tạm thời để tránh làm mất file khi DB có thể đang tham chiếu.
     - Khi `rows == 0`: xóa `saved_key` và trả HTTP 400.
     - Khi `rows == 1`: DB ghi thành công -> xóa `old_key`.
   - Kiểm thử thực tế trên D1 local thật (`test_d1_attachment_storage.py`): kiểm chứng 6 kịch bản trên Worker Bridge và LocalDevStorage thật.
2. **Bug 2 (P2) - Validate số nguyên và dải hợp lệ cho TTL (views.py:578-608):**
   - Kiểm tra chặt chẽ `int(raw_ttl)` và dải [1, 604800] giây (7 ngày giới hạn R2).
   - Input sai kiểu ("abc") hoặc ngoài dải (0, -1, 604801) trả HTTP 400 rõ ràng; không âm thầm fallback về default.
   - Khi không truyền param: sử dụng `settings.R2_PRESIGNED_URL_EXPIRE_SECONDS` (900s).
   - Test helpers nội bộ trong `storage_backends.py` vẫn giữ khả năng sinh URL âm/quá hạn có chủ đích để kiểm thử xác thực URL.
3. **Bug 3 (P2) - Bỏ `record_failure()` khi gặp lỗi auth 401/403 của Worker (services.py:309-322, 366-370):**
   - Lỗi xác thực Worker (401/403) là lỗi cấu hình/secret, không phải lỗi availability; trực tiếp raise `ServiceUnavailable`, không đếm lỗi circuit breaker.
   - Gọi liên tiếp 4+ lần với 401/403: luôn trả 503, breaker luôn ĐÓNG (`_failure_count == 0`), tuyệt đối không rơi vào heuristic 200 giả.
4. **Bug 4 (P2) - Validate kiểu `suggested_category_id` trước khi ORM filter (services.py:417-427):**
   - Loại trừ boolean, chuyển đổi và kiểm tra số nguyên dương (`val > 0`).
   - Các giá trị không hợp lệ (`"not-a-number"`, `[]`, `{}`, `True`, `-5`) kích hoạt heuristic fallback sạch sẽ, không gây `ValueError` (HTTP 500).
5. **Bug 5 (P2) - Bỏ tìm kiếm substring tên danh mục trong rawResponse/explanation (index.js:149-181):**
   - Loại bỏ hoàn toàn việc tìm tên substring trong `rawResponse` hoặc `explanation`.
   - Chỉ đối chiếu ID số hoặc thuộc tính JSON cấu trúc rõ ràng (`suggested_category_name` / `category_name`).
   - Khi output không hợp lệ hoặc explanation nhắc tên theo ý phủ định: trả HTTP 502 `INVALID_SUGGESTION`, không ghi log vào D1.

### 16.2. Tổng Hợp Kết Quả Kiểm Thử Sau Khi Sửa
1. **Kiểm thử Storage & AI Backend:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_storage.py backend/tests/test_ai_proxy.py backend/apps/assets/tests/test_r2_storage.py -v`
   - Kết quả: **57 passed in 8.20s (100% PASS)**.
2. **Kiểm thử Cloudflare Worker Suite:**
   - Lệnh: `cd cloudflare-worker && npm test`
   - Kết quả: **15 passed in 301ms (100% PASS)**.
3. **Kiểm thử D1 Local Attachment Storage Rehearsal Suite:**
   - Lệnh: `backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_attachment_storage.py --rehearsal -v`
   - Kết quả: **6 passed in 6.90s (100% PASS)**.
4. **Kiểm thử D1 Business Integrity & OCC Regression Suite:**
   - Lệnh: `backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_business_integrity.py --rehearsal -v`
   - Kết quả: **21 passed in 25.61s (100% PASS)**.
5. **Kiểm thử Baseline Backend Suite (PostgreSQL Test Container):**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/ -q`
   - Kết quả: **206 passed in 67.38s (100% PASS)**.
6. **Thẩm định Độc Lập Ponytail Review (Agent D):**
   - **Verdict: APPROVED**. Cả 5 lỗi hồi quy được khắc phục hoàn toàn ở tầng mã nguồn production, zero bloat.

### 16.3. Ranh Giới An Toàn & Đề Xuất Nghiệm Thu
- Cổng 5434 và 8787 sạch 100%. Containers `assethub-backend-1` (8000), `assethub-db-1` (5433), `pg-test` (5432) hoạt động bình thường.
- Không có thư viện/công cụ nào được cài mới; không có model/Docker image mới được tải về.
- Nhánh `feat/cloudflare-d1`: không worktree, không push/merge, chưa mở Task 6.
- Kính gửi Codex nghiệm thu Task 5.

---

## 17. Nghiệm Thu Hoàn Tất Task 5: Sửa Dứt Điểm Review ac29b29 (Unknown Commit Cleanup, Strict Integer ID & Fixture Isolation)

- **Thời điểm:** 2026-11-04 23:00 (GMT+7)
- **Trạng thái:** **Task 5 Hoàn Tất PASS 100% — Đã qua review độc lập Agent D & sẵn sàng để Codex nghiệm thu.**
- **Ranh giới an toàn nghiêm ngặt:**
  - Không cài đặt/thêm/nâng cấp bất kỳ thư viện, package hay công cụ nào.
  - Không kéo model weights hay Docker image mới.
  - Toàn bộ kiểm thử chạy trong môi trường local/mock độc lập; không mutation cloud, không R2 billing, không tiêu quota AI, CSDL chính vẫn là PostgreSQL (`USE_D1_DATABASE=False`), chưa deploy cloud.
  - Nhánh `feat/cloudflare-d1`: 1 tab AGY, 0 worktree, 0 push/merge.

### 17.1. Chi Tiết Sửa Ba Nhóm Lỗi Theo Review Codex
1. **Nhóm 1 (Agent A - P1) — Bảo Vệ Dữ Liệu Khi Commit State Chưa Xác Định (views.py:468-495, test_r2_storage.py):**
   - **Vấn đề:** Khi `update()` gặp lỗi không mang cờ `committed` (ví dụ `OperationalError` do timeout), mã cũ thực hiện readback. Nếu lúc đó mutation đang pending/xử lý trễ trên D1, readback thấy key cũ liền xóa `saved_key`. Khi mutation D1 hoàn tất sau đó, DB trỏ vào file đã bị xóa (dangling pointer).
   - **Khắc phục:** Khi trạng thái commit là unknown (`committed is None`), không xóa `saved_key` và không xóa `old_key`. Giữ cả hai tệp dưới dạng temporary orphan nhằm bảo đảm nguyên tắc zero data loss. Chỉ xóa `saved_key` khi chắc chắn chưa ghi (`rows == 0` do OCC / DISPOSED hoặc `committed is False`). Chỉ xóa `old_key` khi DB commit thành công xác định (`rows == 1`).
   - Cập nhật test suite `test_r2_storage.py` đồng bộ với hợp đồng bảo toàn dữ liệu.

2. **Nhóm 2 (Agent B - P2) — Validate Số Nguyên Dương Nghiêm Ngặt Trước Mọi Ép Kiểu (cloudflare-worker/src/index.js, apps/ai/services.py, tests):**
   - **Vấn đề:**
     - Worker: Ép kiểu `Number(rawId)` khiến `true` biến thành `1`, `[1]` biến thành `1`, trả về mã 200 sai danh mục.
     - Backend: Ép kiểu `int(suggested_id)` khiến số lẻ `1.9` bị cắt cụt thành `1` (trả về 200 `is_fallback=False`), còn `Infinity` ném `OverflowError` dẫn đến HTTP 500 unhandled.
   - **Khắc phục:**
     - Worker: Bổ sung hàm `parsePositiveInteger(val)` kiểm tra nghiêm ngặt `Number.isInteger`, `Number.isFinite`, `Number.isSafeInteger`, `val > 0` cho số hoặc chuỗi số nguyên thuần túy (`/^\d+$/`). Từ chối `boolean`, `array`, `object`, `float`, `Infinity`, `NaN`. Nếu không khớp danh mục, trả HTTP 502 với mã `INVALID_SUGGESTION` và không ghi log vào D1.
     - Backend: Bổ sung hàm `parse_positive_int_id(val)` từ chối `bool`, `float`, `list`, `inf`. Nếu không phải số nguyên dương hợp lệ hoặc không có trong CSDL, kích hoạt heuristic fallback (`is_fallback=True`) trả HTTP 200, tuyệt đối không ném `ValueError` hay `OverflowError` (500).
     - Bổ sung 6 regression tests trong `worker.test.js` và 4 regression tests trong `test_ai_proxy.py`.

3. **Nhóm 3 (Agent C - P2) — Cô Lập Router Lifecycle Giữa Các Test Suite & Regression Late Commit (test_d1_attachment_storage.py, test_d1_business_integrity.py):**
   - **Vấn đề:** Khi chạy cả hai module D1 trong cùng một tiến trình pytest, `test_concurrent_request_approval_vs_rejection` thất bại vì nhận `ConnectionDoesNotExist("The connection 'd1_attachment_test' doesn't exist.")` do Django cache `router._routers` và `router.routers` (`cached_property`), khiến worker thread truy vấn nhầm alias đã bị teardown.
   - **Khắc phục:**
     - Trong fixture `d1_env` của cả 2 file: Reset tường minh `router._routers = None` và `router.__dict__.pop("routers", None)` khi thiết lập và trong khối `finally:` khi khôi phục router gốc. Đóng và xóa sạch connection cache trong `(ALIAS, "default")`.
     - Bổ sung test hồi quy `test_d1_late_commit_after_timeout_preserves_new_file`: Dùng timing hook giữ mutation D1, mô phỏng timeout/`OperationalError`, view bảo toàn cả hai file; sau đó mutation D1 hoàn tất thành công, xác nhận DB trỏ tệp mới và tệp mới tồn tại nguyên vẹn trong storage.
     - Đã chứng minh 2 test suite pass 100% trong cùng tiến trình ở cả 2 thứ tự chạy đảo ngược.

### 17.2. Kết Quả Kiểm Thử Toàn Tuyến & Nghiệm Thu Độc Lập
1. **Kiểm thử Cloudflare Worker Vitest:**
   - Lệnh: `cd cloudflare-worker && npm test`
   - Kết quả: **21 passed in 336ms (100% PASS, exit code 0)**.
2. **Kiểm thử Storage, AI Backend & R2 Unit Tests:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_storage.py backend/tests/test_ai_proxy.py backend/apps/assets/tests/test_r2_storage.py -v`
   - Kết quả: **61 passed in 8.67s (100% PASS, exit code 0)**.
3. **Kiểm thử D1 Local Rehearsal (Thứ tự 1: Attachment -> Business):**
   - Lệnh: `backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_attachment_storage.py backend/apps/assets/tests/test_d1_business_integrity.py --rehearsal -q`
   - Kết quả: **28 passed in 32.06s (100% PASS, exit code 0)**.
4. **Kiểm thử D1 Local Rehearsal (Thứ tự 2 Đảo Lại: Business -> Attachment):**
   - Lệnh: `backend/.venv/bin/pytest backend/apps/assets/tests/test_d1_business_integrity.py backend/apps/assets/tests/test_d1_attachment_storage.py --rehearsal -q`
   - Kết quả: **28 passed in 33.54s (100% PASS, exit code 0)**.
5. **Kiểm thử Baseline Backend Suite trên PostgreSQL Test Container Mới Cô Lập:**
   - Container: `assethub-pg-reh-{run_id}` chạy port 5434, credentials ngẫu nhiên, không dùng `assethub-db-1` hay `pg-test`.
   - Lệnh: `pytest backend/tests/ backend/apps/assets/tests/test_r2_storage.py -q`
   - Kết quả: **227 passed in 71.17s (100% PASS, exit code 0)**. Container tự động teardown hoàn tất sau test.
6. **Thẩm Định Độc Lập Ponytail Review (Agent D):**
   - **Verdict: SHIP**.
   - Must fix: 0. Should fix: 0. Zero bloat, zero regression.

### 17.3. Trạng Thái Hệ Thống Sẵn Sàng
- Cổng 5434 và 8787 sạch. Các container `assethub-backend-1` (8000), `assethub-db-1` (5433), `pg-test` (5432) nguyên vẹn.
- Working tree sạch sau commit; sẵn sàng chờ Codex kiểm tra trước khi chuyển sang Task 6.

---

## 18. Nghiệm Thu Hoàn Tất Điểm Sửa Nhỏ Cuối Task 5: Không Che Giấu DatabaseError Trong Category Lookup

- **Thời điểm:** 2026-11-04 23:30 (GMT+7)
- **Trạng thái:** **Task 5 Sửa Dứt Điểm PASS 100% — Đã qua review độc lập Agent B & sẵn sàng để Codex kiểm tra.**
- **Ranh giới an toàn nghiêm ngặt:**
  - Không cài đặt/thêm/nâng cấp bất kỳ thư viện, package hay công cụ nào.
  - Không kéo Docker image mới hay tải model weights.
  - Không lặp lại toàn bộ kiểm thử lớn không liên quan; chỉ chạy regression mới và test suite AI theo đúng chỉ đạo.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.

### 18.1. Chi Tiết Khắc Phục Lỗi DatabaseError Masking
1. **Phân tích vấn đề:**
   - Trong `backend/apps/ai/services.py:438`, khối `try: category = Category.objects.filter(id=parsed_id).first() except (ValueError, OverflowError, Exception): category = None` đã vô tình bắt cả `django.db.DatabaseError` và lỗi lập trình.
   - Khi DB thật gặp sự cố trong lookup chính, lỗi bị nuốt thành `category = None`, kích hoạt `_heuristic_fallback` trả HTTP 200 `is_fallback=True` giả thay vì ném lỗi DB ra ngoài theo handler chuẩn.
2. **Khắc phục theo nguyên tắc Ponytail:**
   - Bỏ hoàn toàn `try ... except (ValueError, OverflowError, Exception)`.
   - Vì `parse_positive_int_id` đã validate chặt chẽ `parsed_id` là số nguyên dương hợp lệ hoặc `None`, `Category.objects.filter(id=parsed_id).first()` không bao giờ gặp lỗi kiểu dữ liệu.
   - Nếu xảy ra lỗi CSDL thật (`DatabaseError`), lỗi được truyền trực tiếp ra ngoài cho handler chuẩn xử lý (HTTP 500), không che giấu thành fallback.
   - Xóa bỏ alias không sử dụng `AiProxyService.suggest_category_for_asset = AiProxyService.suggest_category`.
   - Bổ sung regression test `test_db_error_during_category_lookup_raises_database_error_not_fallback` trong `backend/tests/test_ai_proxy.py`.

### 18.2. Kết Quả Kiểm Thử AI Liên Quan & Review Độc Lập
1. **Kiểm thử AI Suite Tập Trung:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_ai_proxy.py -v`
   - Kết quả: **32 passed in 4.67s (100% PASS, exit code 0)**.
   - Test hồi quy xác nhận: `DatabaseError` ném thẳng ra caller, không trả 200 / `is_fallback=True`.
2. **Thẩm Định Độc Lập Ponytail Review (Agent B):**
   - **Verdict: SHIP**.
   - Must fix: 0. Should fix: 0.
   - Không bloat, không thêm middleware/retry không cần thiết.

---

## 19. Nghiệm Thu Gói Chuẩn Bị Triển Khai Demo Cloudflare (Task 6 Pre-Deployment Package)

- **Thời điểm:** 2026-11-05 00:15 (GMT+7)
- **Trạng thái:** **Task 6 Pre-Deployment Package PASS 100% — Đã qua review độc lập Agent D & sẵn sàng trình Codex kiểm tra.**
- **Ranh giới an toàn nghiêm ngặt:**
  - Chưa tự deploy/tạo/xóa/ghi tài nguyên cloud, mở public Tunnel, bật R2 billing, gọi AI cloud, đổi DNS, chuyển dữ liệu thật hoặc đổi runtime đang phục vụ.
  - Không sửa `.env` đang dùng, không restart các container người dùng đang chạy (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Không cài đặt/thêm/nâng cấp thư viện/công cụ (`npm`, `pip`, `uv`, `npx`), không kéo Docker image mới, không cài Llama local.
  - 1 tab AGY, nhánh `feat/cloudflare-d1`, 0 worktree, 0 push/merge.

### 19.1. Chi Tiết Các Hạng Mục Chuẩn Bị Triển Khai
1. **Backend Runtime Opt-in (`USE_D1_DATABASE`):**
   - File: `backend/assethub_api/settings.py`, `backend/tests/test_settings_runtime.py`.
   - Mặc định tiếp tục giữ PostgreSQL cho môi trường dev local (`USE_D1_DATABASE=False`).
   - Khi kích hoạt `USE_D1_DATABASE=True`: chuyển `DATABASES['default']` sang engine `assethub_api.backends.d1`, yêu cầu bắt buộc `D1_BRIDGE_URL` và `D1_BRIDGE_SECRET_TOKEN` (ném `ImproperlyConfigured` nếu thiếu).
   - Gắn `DATABASES['postgres']` làm alias đối soát dữ liệu chỉ đọc, không fallback âm thầm sang PostgreSQL khi D1 gặp sự cố.
2. **Siết Chặt Bảo Mật Network & CORS:**
   - `ALLOWED_HOSTS`: Bỏ wildcard `*` ở default, mặc định chỉ cho phép `localhost,127.0.0.1,backend` hoặc danh sách từ env `DJANGO_ALLOWED_HOSTS`.
   - CORS Regex: Chuẩn hóa `r"^https:\/\/(?:[a-z0-9-]+\.)?assethub(?:-[a-z0-9-]+)?\.pages\.dev$"`, chấp nhận `assethub.pages.dev`, `assethub-demo.pages.dev` và preview branch, chặn tuyệt đối origin lạ.
3. **Frontend SPA Build & Ingress Config:**
   - File: `frontend/vite.config.js`, `frontend/public/_headers`, `frontend/public/_redirects`.
   - Đảm bảo `VITE_API_BASE_URL` trỏ về API Tunnel/Domain mà không bị lặp `/api/api`.
   - `_headers` thiết lập Content Security Policy chặt chẽ (cho phép connect API/Tunnel, ảnh blob/data và R2 presigned URL).
   - `_redirects` hỗ trợ SPA deep-link refresh (`/* /index.html 200`).
   - Build production `npm --prefix frontend run build` kiểm chứng không rò rỉ secret trong bundle.
4. **Cloudflare Tunnel Ingress & Smoke Harness:**
   - File: `deploy/cloudflared/config.yml`, `backend/tests/e2e/test_d1_cloud_smoke.py`.
   - Chuẩn hóa cấu hình Tunnel không dùng shell expansion `${...}` trong YAML.
   - Cung cấp phương án Quick Tunnel `cloudflared tunnel --url http://127.0.0.1:8000` cho demo mà không cần mua domain cố định.
   - Bộ test E2E smoke gồm 7 bước phủ toàn diện luồng nghiệp vụ trên cloud, được bảo vệ bằng cờ opt-in `RUN_CLOUD_SMOKE=1`, mặc định skip hoàn toàn khi chạy local.
5. **Khắc Phục Toàn Diện Phát Hiện Từ Reviewer Độc Lập (Agent D):**
   - Loại bỏ secret token fallback hardcoded trong `test_d1_cloud_smoke.py`.
   - Cập nhật regex CORS tương thích với subdomain preview `assethub-demo.pages.dev`.
   - Đưa giá trị mặc định `R2_BUCKET_NAME` trong compose về rỗng tránh ghi đè fixture test.
   - Thêm biến `CORS_EXTRA_REGEX` vào `deploy/docker-compose.prod.yml`.

### 19.2. Kết Quả Kiểm Thử Toàn Tuyến & Nghiệm Thu Độc Lập
1. **Frontend Production Build:**
   - Lệnh: `npm --prefix frontend run build`
   - Kết quả: **Exit code 0, 2255 modules transformed, sinh đủ `dist/_headers` và `dist/_redirects`**.
2. **Backend Runtime, CORS, Storage & Smoke Gating Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_settings_runtime.py backend/tests/test_cors.py backend/apps/assets/tests/test_r2_storage.py backend/tests/test_storage.py backend/tests/e2e/test_d1_cloud_smoke.py -v`
   - Kết quả: **49 passed, 7 skipped in 4.81s (100% PASS, exit code 0)**.
3. **Cloudflare Worker Vitest Suite:**
   - Lệnh: `npm --prefix cloudflare-worker test`
   - Kết quả: **21 passed in 298ms (100% PASS, exit code 0)**.
4. **AI Proxy Service Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_ai_proxy.py -v`
   - Kết quả: **32 passed in 4.42s (100% PASS, exit code 0)**.
5. **D1 Backend Unit & Isolation Guard Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/assethub_api/backends/d1/tests/ -v`
   - Kết quả: **21 passed, 21 skipped in 0.22s (100% PASS, exit code 0)**.
6. **Thẩm Định Độc Lập Ponytail Review (Agent D):**
   - **Verdict: SHIP**.
   - Must fix: 0. Should fix: 0.
   - Sạch sẽ, an toàn, không bloat mã nguồn.

### 19.3. Trạng Thái Hệ Thống Sẵn Sàng Trình Codex
- Cổng 5434 và 8787 sạch. Các container `assethub-backend-1` (8000), `assethub-db-1` (5433), `pg-test` (5432) giữ nguyên trạng.
- Sẵn sàng commit local và trình bày kế hoạch deploy chi tiết cho Codex duyệt trước khi publish.



