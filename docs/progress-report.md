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

---

## 20. Nghiệm Thu Khắc Phục Toàn Diện 6 Điểm Review Từ Codex Tại 39ec16c (Task 6 Pre-Deployment Package Polish)

- **Thời điểm:** 2026-11-05 00:52 (GMT+7)
- **Trạng thái:** **Task 6 Pre-Deployment Package Polish PASS 100% — Đã qua review độc lập Ponytail Agent D (Verdict: SHIP) & sẵn sàng để Codex kiểm tra.**
- **Ranh giới an toàn nghiêm ngặt:**
  - Chưa deploy/ghi cloud, mở public Tunnel, đổi DNS/billing, gọi AI cloud, đổi runtime đang phục vụ hay restart container (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Không cài thêm/nâng cấp package, không kéo image mới, không cài Llama local.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.
  - Không xóa hay thay đổi dữ liệu thật; toàn bộ kiểm thử dùng dữ liệu giả riêng biệt.

### 20.1. Chi Tiết Khắc Phục 6 Điểm Review
1. **P1: Alias `postgres` Trong D1 Mode Được Ép Quyền Read-Only Triệt Để:**
   - Trong `backend/assethub_api/settings.py`: Loại bỏ hoàn toàn fallback sang `DB_USER`/`DB_PASSWORD`.
   - Nếu `POSTGRES_RO_USER` hoặc `POSTGRES_RO_PASSWORD` thiếu/rỗng: Bỏ hoàn toàn alias `postgres` khỏi `DATABASES`, loại trừ tuyệt đối nguy cơ leo thang quyền ghi.
   - Khi được cấu hình: Gán `'OPTIONS': {'options': '-c default_transaction_read_only=on'}` tại cấp độ session kết nối PostgreSQL.
   - Kiểm thử thực tế trên PostgreSQL: Unit test chứng minh lệnh `SELECT` thành công, trong khi `INSERT`, `UPDATE`, `DELETE` bị chặn và ném lỗi `InternalError: cannot execute ... in a read-only transaction`. Chế độ PG dev mặc định (`USE_D1_DATABASE=False`) vẫn đọc-ghi bình thường.
2. **P2: Siết Chặt CORS Origin Regex Chặn Attacker Project:**
   - Trong `backend/assethub_api/settings.py`: Đổi regex thành `r"^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$"`.
   - Loại bỏ hậu tố `(?:-[a-z0-9-]+)?` đã từng cho phép các tên miền giả mạo như `https://assethub-attacker.pages.dev`.
   - Kiểm thử qua test client/middleware thật: Chấp nhận `https://assethub.pages.dev` và preview branch `https://preview-123.assethub.pages.dev`, từ chối dứt điểm `https://assethub-attacker.pages.dev` và các domain lạ.
3. **P3: Khởi Tạo Docker Compose Demo Độc Lập & Chuẩn Hóa Đường Dẫn:**
   - Tạo file cấu hình độc lập `deploy/docker-compose.demo.yml` với project name `assethub-demo`.
   - Chạy trên port riêng biệt `127.0.0.1:${DEMO_BACKEND_PORT:-8001}:8000`, không va chạm hay đụng độ với container `assethub-backend-1` (port 8000).
   - Loại bỏ phụ thuộc `depends_on: db` trong chế độ D1.
   - Chuẩn hóa build context và volume mounts; kiểm tra lệnh `docker compose -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo.example config` chạy từ repo root thành công 100% với exit code 0.
4. **P4: Đồng Bộ API Origin, AI Worker URL & Siết Chặt CSP Headers:**
   - Tách biệt rõ ràng URL của Worker AI (`cloudflare-worker/src/index.js`) khỏi D1 Worker Bridge trong `deploy/.env.demo.example`.
   - Trong `frontend/public/_headers`: Loại bỏ wildcard nguy hiểm `https:` trong `connect-src`, chỉ cho phép `'self' https://*.trycloudflare.com https://*.pages.dev https://*.r2.cloudflarestorage.com http://127.0.0.1:8001 http://127.0.0.1:8000;`.
   - Cập nhật `frontend/vite.config.js` linh hoạt theo port demo.
5. **P5: Bảo Đảm An Toàn Tuyệt Đối Cho Smoke Test (Không Sửa Admin Sẵn Có / Không Phá Guard):**
   - Xóa bỏ triệt để dòng code tự nới lỏng allowlist `ALLOWED_TEST_BRIDGE_ENDPOINTS.add(...)`.
   - Trong `test_06`: Tuyệt đối không deactive hay thay đổi bất kỳ tài khoản Admin nào có sẵn trong DB.
   - Cơ chế dọn dẹp (Teardown): Lưu trữ ID cụ thể và tiền tố duy nhất `f"SMK_{run_id}"`. Chỉ xóa đúng các thực thể do run tạo; kiểm tra xác nhận sau khi xóa và không dùng `except: pass` che giấu lỗi.
6. **P6: Kiểm Thử Khói Đường Truyền HTTP Thực Tế & Gating An Toàn:**
   - Sử dụng yêu cầu HTTP thực tế (`make_real_http_request`) tới `CLOUD_API_BASE_URL` cho toàn bộ luồng Auth, Asset, Request, R2 và AI.
   - R2 smoke tải byte thực tế để đối chiếu hash/nội dung; Workers AI kiểm tra phân loại phản hồi chặt chẽ, không đánh đồng lỗi mạng/auth 503 thành pass.
   - Bảo vệ bằng cờ opt-in `RUN_CLOUD_SMOKE=1`: Mặc định bỏ qua 7 bước kiểm thử khói trong 0.03s khi chạy local, không gửi bất kỳ request mạng hay ghi dữ liệu nào. 2 unit test kiểm tra an toàn guard và admin protection luôn chạy và đạt 100%.

### 20.2. Kết Quả Kiểm Thử Toàn Tuyến & Nghiệm Thu Độc Lập
1. **Frontend Production Build:**
   - Lệnh: `npm --prefix frontend run build`
   - Kết quả: **Exit code 0, 2255 modules transformed, dist/ sinh đầy đủ `_headers` (CSP đã siết chặt) và `_redirects`**.
2. **Docker Compose Demo Config Validation:**
   - Lệnh: `docker compose -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo.example config`
   - Kết quả: **Exit code 0, không có lỗi resolve đường dẫn context hay volumes**.
3. **Backend Runtime, CORS, Storage & Smoke Safety Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_settings_runtime.py backend/tests/test_cors.py backend/apps/assets/tests/test_r2_storage.py backend/tests/test_storage.py backend/tests/e2e/test_d1_cloud_smoke.py -v`
   - Kết quả: **63 passed, 7 skipped in 4.79s (100% PASS, exit code 0)**.
4. **Cloudflare Worker Vitest Suite:**
   - Lệnh: `npm --prefix cloudflare-worker test`
   - Kết quả: **21 passed in 298ms (100% PASS, exit code 0)**.
5. **AI Proxy Service Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_ai_proxy.py -v`
   - Kết quả: **32 passed in 4.42s (100% PASS, exit code 0)**.
6. **D1 Backend Unit & Isolation Guard Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/assethub_api/backends/d1/tests/ -v`
   - Kết quả: **21 passed, 21 skipped in 0.22s (100% PASS, exit code 0)**.
7. **Thẩm Định Độc Lập Ponytail Review (Agent D):**
   - **Verdict: SHIP**.
   - Must fix: 0. Should fix: 0 (đã chuẩn hóa `POSTGRES_RO_USER` default rỗng).
   - Zero bloat, mã nguồn tối giản, tuân thủ đúng yêu cầu.

### 20.3. Trạng Thái Hệ Thống Sẵn Sàng
- Cổng 5434 và 8787 sạch. Các container `assethub-backend-1` (8000), `assethub-db-1` (5433), `pg-test` (5432) nguyên vẹn.
- Sẵn sàng commit local và chờ Codex kiểm duyệt gói chuẩn bị deploy.

---

## 21. Nghiệm Thu Hoàn Tất Đóng Gói Demo Freshness & Positive HTTP Smoke Rehearsal (Review cda507f)

- **Thời điểm:** 2026-11-05 12:40 (GMT+7)
- **Trạng thái:** **Task 6 Demo Freshness & Smoke Rehearsal PASS 100% — Đã qua review độc lập Ponytail Agent D (Verdict: SHIP) & sẵn sàng để Codex kiểm tra.**
- **Ranh giới an toàn nghiêm ngặt:**
  - Chưa deploy/ghi cloud, mở public Tunnel, đổi DNS/billing, gọi AI cloud, đổi runtime đang phục vụ hay restart container (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Không cài thêm/nâng cấp package, không kéo image mới (`pull_policy: never`), không cài Llama local.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.
  - Không xóa hay thay đổi dữ liệu thật; toàn bộ kiểm thử dùng dữ liệu giả riêng biệt.

### 21.1. Chi Tiết Khắc Phục 3 Hạng Mục
1. **Demo Runtime Freshness & Source Mount (`deploy/docker-compose.demo.yml`):**
   - Image `assethub-backend:latest` được tái sử dụng mà không cần rebuild Docker image.
   - Gắn source code backend đã review read-only vào container: `volumes: - ../backend:/app:ro`.
   - Thêm `pull_policy: never` ngăn chặn việc tự động pull remote image.
   - Thêm `DJANGO_SETTINGS_MODULE: ${DJANGO_SETTINGS_MODULE:-assethub_api.settings}`.
   - Bằng chứng thực nghiệm: Chạy container không mount source gặp lỗi `ModuleNotFoundError: No module named 'assethub_api.backends'` (exit 1); chạy container có mount source in ra `D1 engine: assethub_api.backends.d1` (exit 0).
2. **Smoke Fixture DB Unblock, Cleanup Ownership & AI Classification (`backend/tests/e2e/test_d1_cloud_smoke.py`):**
   - Sửa lỗi `DatabaseAccessNotAllowed`: Bọc `django_db_blocker.unblock()` trong fixture `smoke_context`, và thêm autouse fixture `unblock_db_each_test(django_db_blocker)`.
   - Setup bọc trong `try ... finally` từ trước mutation đầu tiên, bảo đảm dọn dẹp sạch kể cả khi setup bị gián đoạn giữa chừng.
   - Lưu trữ danh sách explicit tracking IDs cho `requests`, `assets`, `categories`, `departments`, `users`, `r2_keys` trực tiếp từ API responses (thay vì phụ thuộc prefix `AST-SMK-*` / `REQ-SMK-*`).
   - Dọn dẹp teardown theo đúng thứ tự FK nghiêm ngặt (R2 -> Audit Log -> Request -> Asset -> Category -> Department -> User) và xác nhận `COUNT(*) == 0`.
   - Tuyệt đối không chạm đến Admin có sẵn trong D1.
   - Gọi đúng endpoint `/api/ai/suggest-category/`, loại bỏ assert bao trùm status `(200, 429, 503)`. Lỗi HTTP 503 auth/network được định nghĩa dứt điểm là FAIL qua `pytest.fail`. Phản hồi 200 kiểm tra category ID hợp lệ và cờ `is_fallback`. Bổ sung 4 unit regression tests cho bộ phân loại AI.
3. **Positive Local HTTP Smoke Subprocess Rehearsal (`backend/tests/e2e/test_smoke_local_http.py`):**
   - Tạo kịch bản diễn tập thực tế không phụ thuộc cloud: Khởi chạy local D1 harness trên port 8787 với `schema.sql` chuẩn.
   - Khởi chạy WSGI HTTP server thật bằng `wsgiref.simple_server` trên port loopback ngẫu nhiên với `USE_D1_DATABASE=True`.
   - Gọi `pytest` subprocess thực thi trực tiếp `backend/tests/e2e/test_d1_cloud_smoke.py` với `RUN_CLOUD_SMOKE=1`.
   - Kết quả thực nghiệm: **11 passed, 2 skipped in 1.52s, exit code 0** (Bridge health, Auth JWT login, Asset CRUD, Request CRUD, Request Approve, D1 readback, Last admin protection, Cleanup verification, cùng toàn bộ safety unit tests đều PASS 100%).

### 21.2. Kết Quả Kiểm Thử Toàn Tuyến & Nghiệm Thu Độc Lập
1. **Demo Compose Config Validation:**
   - Lệnh: `docker compose -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo.example config`
   - Kết quả: **Exit code 0, volume bind mount resolve chuẩn xác `/home/truong/Downloads/AssetHub/backend` -> `/app:ro`**.
2. **Backend Unit/Gating Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_settings_runtime.py backend/tests/test_cors.py backend/apps/assets/tests/test_r2_storage.py backend/tests/test_storage.py backend/tests/e2e/test_d1_cloud_smoke.py -v`
   - Kết quả: **67 passed, 7 skipped in 4.91s (100% PASS, exit code 0)**.
3. **Smoke Local HTTP Subprocess Rehearsal:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/e2e/test_smoke_local_http.py -v -s`
   - Kết quả: **1 passed (11 subprocess passed, 2 skipped) in 6.19s (100% PASS, exit code 0)**.
4. **Thẩm Định Độc Lập Ponytail Review (Agent D):**
   - **Verdict: SHIP**.
   - Must fix: 0. Should fix: 0.
   - Zero bloat, mã nguồn tối giản, tuân thủ đúng yêu cầu.

### 21.3. Trạng Thái Hệ Thống Sẵn Sàng
- Cổng 5434, 8787, 8001 sạch. Các container `assethub-backend-1` (8000), `assethub-db-1` (5433), `pg-test` (5432) nguyên vẹn.
- Sẵn sàng commit local và chờ Codex kiểm duyệt gói chuẩn bị deploy.

---

## 22. Nghiệm Thu Khắc Phục Cleanup Ownership, Preflight Bootstrap & Zero-Cloud Rehearsal (Review 19ca834)

- **Thời điểm:** 2026-11-05 13:30 (GMT+7)
- **Trạng thái:** **Step 8 Sửa an toàn cleanup smoke & Zero-Cloud Rehearsal PASS 100% — Đã qua review độc lập Ponytail Agent C (Verdict: SHIP) & sẵn sàng để Codex kiểm tra.**
- **Ranh giới an toàn tuyệt đối:**
  - Zero cloud mutation, zero live Workers AI call, zero R2 billing, zero public Tunnel.
  - Zero install / upgrade (`npm`, `pip`, `uv`), zero Docker image pull, zero model download.
  - Không reset / thay đổi database thật hay container stack đang chạy (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Toàn bộ kiểm thử dùng dữ liệu giả độc lập trên test database / local harness.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.

### 22.1. Chi Tiết Khắc Phục Kỹ Thuật
1. **Preflight Guard Trước Mutation Đầu Tiên (`smoke_context`):**
   - Đọc và kiểm tra có ít nhất 1 Admin active nền trong D1 trước khi tạo bất kỳ department/category/user test nào.
   - Nếu `baseline_admin_count == 0`: dừng bằng `pytest.skip` rõ ràng, không seed trước để tránh trigger lock `Cannot delete the last active Admin`.
   - Bổ sung regression test: `test_safety_preflight_blocks_mutation_when_no_baseline_admin` và `test_rehearsal_negative_preflight_unbootstrapped_d1` xác nhận D1 hoàn toàn trống (count == 0 ở mọi bảng liên quan).
2. **Xác Minh Quyền Sở Hữu Teardown & Bảo Tồn Sentinel (`execute_smoke_teardown`):**
   - Yêu cầu bắt buộc `run_id` hợp lệ, chặn rủi ro wildcard matching.
   - Không xóa theo ID trần từ API; kiểm tra thực sự thuộc `run_id` qua:
     - `r2_keys`: `run_id in r2_key`.
     - `assets`: `department_id == dept.id` và `category_id == cat.id`.
     - `requests`: `requester_id in tracked_users`.
     - `categories`: `name__contains=run_id`.
     - `departments`: `code == dept_code`.
     - `users`: `username__contains=run_id`.
   - Nếu phát hiện ID trần không thuộc quyền sở hữu (sentinel data), giữ nguyên bản ghi, append thông báo mismatch và ném `RuntimeError`.
   - Xóa đúng theo thứ tự FK và chỉ xóa `verified_*_ids`, xác nhận `count == 0` sau khi xóa.
   - Bổ sung unit regressions: `test_safety_execute_smoke_teardown_detects_ownership_mismatch_and_preserves_sentinels`, `test_safety_execute_smoke_teardown_cleans_owned_records_successfully`, `test_safety_execute_smoke_teardown_requires_run_id`.
3. **Đánh Dấu Rehearsal Marker & Cô Lập Môi Trường Zero-Cloud (`test_smoke_local_http.py`):**
   - Đánh dấu `pytestmark = pytest.mark.rehearsal`. Lượt chạy default pytest skip toàn bộ trong 0.01s.
   - Chạy diễn tập qua opt-in flag `--rehearsal` hoặc `ASSETHUB_RUN_REHEARSAL=1`.
   - Vô hiệu hóa triệt để biến môi trường và settings R2 / AI cloud ở cả server process và subprocess environment (`R2_*=""`, `AI_*=""`).
   - Khôi phục nguyên vẹn settings / env sau khi kết thúc diễn tập.

### 22.2. Kết Quả Kiểm Thử Thực Nghiệm
1. **Targeted Backend Unit / Gating Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/test_settings_runtime.py backend/tests/test_cors.py backend/apps/assets/tests/test_r2_storage.py backend/tests/test_storage.py backend/tests/e2e/test_d1_cloud_smoke.py -v`
   - Kết quả: **71 passed, 7 skipped in 4.85s (100% PASS)**.
2. **Rehearsal Default Gating Check (Không cờ opt-in):**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/e2e/test_smoke_local_http.py -v`
   - Kết quả: **3 skipped in 0.01s (100% GATED)**.
3. **Rehearsal Opt-In Execution (`--rehearsal`):**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/e2e/test_smoke_local_http.py --rehearsal -v -s`
   - Kết quả: **3 passed in 16.33s (Wrapper: 3/3 passed; Subprocess smoke: 15 passed, 2 skipped [R2/AI cloud skipped])**.

### 22.3. Báo Cáo Kế Hoạch Triển Khai (Deployment Package) Cho Codex Review
- **Tài nguyên Cloudflare dự kiến sử dụng (khi deploy thật sau review):**
  - Cloudflare D1 Database: `assethub-production-d1` (ID: binding qua wrangler).
  - Cloudflare R2 Bucket: `assethub-attachments`.
  - Cloudflare Worker Bridge: `assethub-d1-bridge.workers.dev` (kèm D1 Client binding nội bộ).
  - Cloudflare Tunnel: `tunnel.assethub.local` trỏ container demo backend cổng 8001.
  - Cloudflare Pages: `assethub.pages.dev` phục vụ static frontend SPA.
- **Tập dữ liệu demo tối thiểu đề xuất (Chưa seed, chưa reset):**
  - 1 Department: `Công nghệ thông tin` (mã `IT`).
  - 1 Category: `Laptop & Máy trạm` (`LAPTOP`).
  - 2 Users: 1 Admin (`demo_admin`), 1 User (`demo_user`).
  - 2 Assets: 1 Laptop đang sẵn sàng (`AST-DEMO-001`), 1 Máy trạm đang sử dụng (`AST-DEMO-002`).
  - 1 Request: Yêu cầu cấp phát laptop đang chờ duyệt (`REQ-DEMO-001`).
- **Thứ tự triển khai (Deploy sequence) chuẩn bị:**
  1. Review toàn bộ git diff và commit local trên `feat/cloudflare-d1`.
  2. Áp dụng schema D1 qua D1 migrations.
  3. Deploy Cloudflare Worker D1 Bridge.
  4. Khởi chạy demo container backend (port 8001, source mount `ro`, read-only `postgres`).
  5. Thiết lập Cloudflare Tunnel nội bộ trỏ về 8001.
  6. Deploy Cloudflare Pages frontend với URL backend Tunnel.
  7. Thực hiện end-to-end smoke test xác nhận.
- **Thứ tự hoàn tác (Rollback sequence):**
  1. Tắt Tunnel và dừng container demo backend (port 8001).
  2. Revert DNS/Route Cloudflare Pages về trạng thái bảo trì hoặc bản build trước.
  3. Dữ liệu PostgreSQL production trên port 5433 và 8000 hoàn toàn nguyên vẹn, sẵn sàng tiếp tục phục vụ mà không cần khôi phục dữ liệu.

---

## 23. Nghiệm Thu Sửa 3 Đường Cô Lập Test Rehearsal (Review 7cbd2ad)

- **Thời điểm:** 2026-11-05 13:55 (GMT+7)
- **Trạng thái:** **Step 9 Hoàn tất sửa 3 đường cô lập rehearsal PASS 100% — Đã qua review độc lập Ponytail Agent C (Verdict: SHIP) & sẵn sàng để Codex kiểm duyệt.**
- **Ranh giới an toàn nghiêm ngặt:**
  - Zero cloud mutation, zero live Workers AI call, zero R2 billing, zero public Tunnel.
  - Zero install / upgrade (`npm`, `pip`, `uv`), zero Docker image pull, zero model download.
  - Không reset / thay đổi database thật hay container stack đang chạy (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Toàn bộ kiểm thử dùng dữ liệu giả độc lập trên test database / local harness.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.

### 23.1. Chi Tiết Khắc Phục Kỹ Thuật 3 Điểm
1. **Bảo Vệ Listener Lạ & Phân Tách Port 8787 (`test_smoke_local_http.py`):**
   - Xóa bỏ hoàn toàn nhánh `else` gọi `client.query("DELETE FROM authentication_user;")` và nhánh `finally ... elif in_use: client.query("INSERT INTO ...")` trong `test_rehearsal_negative_preflight_unbootstrapped_d1`.
   - Nếu `in_use == True` (port 8787 có listener lạ): Dừng ngay bằng `pytest.skip("Port 8787 is in use by another listener. Refusing to connect or mutate foreign listener.")`, bảo đảm **0 câu SQL/mutation** gửi tới listener ngoài (đã kiểm chứng mock `D1Client.query call_count == 0`).
   - Đổi `live_rehearsal_server` sang `scope="function"` (thay vì `module`): Giải phóng port 8787 ngay sau từng test case, loại bỏ triệt để phụ thuộc vòng đời khi đảo thứ tự chạy.
2. **Cô Lập Server VÀ Child Từ Trước Startup:**
   - Khai báo danh sách 6 biến cloud: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`, `AI_WORKER_URL`, `AI_SERVICE_TOKEN`.
   - Snapshot `orig_env` và `orig_settings` trước khối `try:`. Gán đồng thời `os.environ[k] = ""` VÀ `setattr(settings, k, "")` trước khi khởi tạo WSGI server hoặc D1 bridge.
   - Gán `settings.SECRET_KEY = getattr(settings, "SECRET_KEY", None) or "local-rehearsal-secret-key-32chars"`.
   - Trong `finally:`: Khôi phục 100% `os.environ`, `settings`, `DATABASES`, `connections`, `ALLOWED_HOSTS`, đóng server và thread, teardown harness.
   - Bằng chứng thực nghiệm: Kể cả khi có fake credentials trong môi trường, `is_r2_configured()` bên trong server context trả về `False` dứt khoát; sau teardown khôi phục hoàn hảo.
3. **Loại Bỏ Hoàn Toàn PostgreSQL Khỏi Subprocess Cloud Rehearsal:**
   - Trong `test_local_http_smoke_subprocess_rehearsal`:
     - Truyền `"USE_D1_DATABASE": "True"` ngay trong subprocess env, khiến `settings.DATABASES` chỉ chứa duy nhất alias `'default'` với engine `assethub_api.backends.d1`.
     - Xóa bỏ `"DB_HOST": "127.0.0.1"` và `"DB_PORT": "5433"`. Pop sạch mọi biến PostgreSQL: `for pg_key in ["DB_HOST", "DB_PORT", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_RO_USER", "POSTGRES_RO_PASSWORD"]: env.pop(pg_key, None)`.
     - Chạy subprocess với flag `"-m", "cloud_smoke"`: Deselect 10 bài unit/safety (bao gồm 2 bài có `@pytest.mark.django_db`), chỉ chạy 7 bài test HTTP cloud smoke.
     - Subprocess chạy độc lập 100% qua loopback D1 Target harness (`127.0.0.1:8787`), không kết nối và không cần bất kỳ container/port PostgreSQL nào.
     - Bổ sung assertion: `assert "5 passed, 2 skipped" in result.stdout`.

### 23.2. Kết Quả Kiểm Thử Thực Nghiệm Sau Cùng
1. **Rehearsal Default Gating Check (Không có cờ opt-in):**
   - Lệnh: `backend/.venv/bin/pytest backend/tests/e2e/test_smoke_local_http.py -v`
   - Kết quả: **3 skipped in 0.01s (Exit code 0, 100% Gated)**.
2. **Rehearsal Opt-In Execution (`--rehearsal` hoặc `ASSETHUB_RUN_REHEARSAL=1`):**
   - Lệnh: `backend/.venv/bin/pytest backend/tests/e2e/test_smoke_local_http.py --rehearsal -v -s`
   - Kết quả: **3 passed in 14.39s (Subprocess smoke: 5 passed, 2 skipped, 10 deselected in 1.49s; R2/AI cloud skip có lý do rõ ràng)**.
3. **Đảo Thứ Tự Test (Kiểm chứng Decoupling):**
   - Lệnh: `backend/.venv/bin/pytest backend/tests/e2e/test_smoke_local_http.py::test_local_http_smoke_subprocess_rehearsal backend/tests/e2e/test_smoke_local_http.py::test_rehearsal_negative_preflight_unbootstrapped_d1 --rehearsal -v -s`
   - Kết quả: **2 passed in 9.69s (Exit code 0)**.
4. **Targeted Smoke Unit & Safety Suite:**
   - Lệnh: `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/tests/e2e/test_d1_cloud_smoke.py -v`
   - Kết quả: **10 passed, 7 skipped in 0.28s (Exit code 0)**.

---

## 24. Nghiệm Thu Khảo Sát Tài Nguyên Cloudflare Thực Tế & Gói Triển Khai (Review a4f8360)

- **Thời điểm:** 2026-11-05 14:15 (GMT+7)
- **Trạng thái:** **Step 10 Khảo sát Cloudflare & Chuẩn bị Gói Triển khai Hoàn tất 100% — Sẵn sàng cho Codex kiểm duyệt trước bước triển khai Cloud.**
- **Ranh giới an toàn tuyệt đối:**
  - Chưa tạo/cập nhật/xóa tài nguyên Cloudflare, chưa deploy Worker/Pages, chưa áp schema/seed D1 cloud, chưa gọi Workers AI, chưa mở Tunnel public.
  - Không cài thêm/nâng cấp package/công cụ, không pull/build Docker image.
  - Không sửa file `.env` đang dùng, không restart hay chạm vào stack đang phục vụ (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Không in token/secret/password/hash/JWT hay dump dữ liệu người dùng.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.

### 24.1. Kết Quả Khảo Sát Thực Tế Cloudflare (Agent A - Read-Only Inventory)
- **Account & Worker Subdomain:**
  - Account ID: `5e76c2bf4e09587a010d31ea416ec8d3` (Tên: `Hnhoa494@gmail.com's Account`).
  - workers.dev Subdomain: `hnhoa494-assethub.workers.dev`.
- **Cơ sở dữ liệu D1 (`assethub-test-d1`):**
  - UUID: `3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`.
  - Row COUNT tổng hợp: `authentication_user`: 3, `departments_department`: 5, `departments_category`: 1, `assets_asset`: 2, `_idempotency_log`: 1514.
  - **Đánh giá an toàn:** CSDL D1 hiện tại chứa dữ liệu người dùng thật; **BẮT BUỘC PHẢI GIỮ**, tuyệt đối không DROP TABLE hay áp schema PG đè lên; phải sao lưu export trước khi thao tác cloud.
- **Workers:**
  - `assethub-test-worker` (Bridge): Đang hoạt động tại `https://assethub-test-worker.hnhoa494-assethub.workers.dev` (Active version `382d0d9b-6e0b-4554-b899-84b73d8d31b1`), binding `env.DB` trỏ chuẩn xác D1 trên.
  - `assethub-ai-worker`: **Chưa tồn tại** (CF error 10007), cần deploy khi thực hiện bước Cloud.
- **Pages Projects:** Hiện có 0 project (`wrangler pages project list` rỗng); sẽ tạo khi deploy frontend SPA.
- **R2 Storage:** Bị chặn bởi lỗi `[code: 10042]` do Cloudflare Dashboard yêu cầu kích hoạt billing/thẻ. Đã dừng kiểm tra theo đúng quy tắc, không tự kích hoạt.
- **Workers AI:** Token có đủ quyền `ai (write)`, `ai-search (write)`, `ai-search (run)`; catalog model `@cf/meta/llama-3.1-8b-instruct-fp8` hợp lệ; không gửi request suy luận (0 model call).

### 24.2. Cấu Hình & Artifacts Triển Khai Local (Agent B)
1. **Đồng bộ D1 Binding trong Wrangler Configs:**
   - `cloudflare-worker/wrangler.toml`: Đồng bộ `database_name = "assethub-test-d1"`, `database_id = "3850e3a1-7c57-42f4-afd6-1fa1655bbcf1"` (loại bỏ placeholder `local-d1-id`).
   - `cloudflare-worker/bridge/wrangler.toml`: Đã khớp `database_name = "assethub-test-d1"`, `database_id = "3850e3a1-7c57-42f4-afd6-1fa1655bbcf1"`.
   - `cloudflare-worker/bridge/src/index.js`: Đã có source JS chuẩn cho Worker Bridge.
   - Kiểm tra cú pháp offline: `wrangler types` parse hợp lệ 100%; unit test AI worker pass 21/21, test bridge regression pass 8/8.
2. **Cập nhật `deploy/.env.demo.example`:**
   - Điền Account ID thực `5e76c2bf4e09587a010d31ea416ec8d3` vào R2 endpoint URL.
   - Điền URLs thật của Worker Bridge (`https://assethub-test-worker.hnhoa494-assethub.workers.dev`) và AI Worker (`https://assethub-ai-worker.hnhoa494-assethub.workers.dev`).
   - Phân định rõ 5 giai đoạn điền biến bảo mật; token/secret giữ nguyên placeholder để người dùng nhập cục bộ khi deploy.
3. **Frontend CSP Headers (`frontend/public/_headers`):**
   - Bổ sung `https://api.assethub.example.com` và `https://*.workers.dev` vào `connect-src`.

---

## 25. Nghiệm Thu Tinh Chỉnh CORS, Defaults & Runbook Chuẩn Hóa (Review c68ddb6)

- **Thời điểm:** 2026-11-05 15:10 (GMT+7)
- **Trạng thái:** **Hoàn tất tinh chỉnh toàn diện CORS, Runbook & Rollback chuẩn xác — Sẵn sàng cho Codex kiểm duyệt.**
- **Ranh giới an toàn:**
  - Zero cloud mutation, zero live Workers AI calls, zero public Tunnel, zero billing activation.
  - Zero package install (`npm`, `pip`, `uv`), zero Docker image pull/build.
  - Không sửa file `.env` đang dùng, không chạm stack phục vụ (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Bảo lưu nguyên trạng 2 tài liệu Tuần 5 untracked thuộc phiên khác.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.

### 25.1. Tinh Chỉnh CORS, CSP & Environment Defaults (Agent B)
1. **Khắc phục CORS Regex & Origins (`deploy/.env.demo.example`):**
   - Loại bỏ wildcard regex `^https:\/\/(?:[a-z0-9-]+\.)*pages\.dev$`. Để `CORS_EXTRA_REGEX` rỗng nhằm sử dụng regex mặc định bảo mật trong `settings.py` (`r"^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$"`).
   - Chỉ cho phép project `assethub.pages.dev` và các preview branch của chính nó; chặn đứng hoàn toàn `unrelated-project.pages.dev` và `assethub-attacker.pages.dev`.
   - Xóa placeholder origins không khả dụng (`https://<project-subdomain>.pages.dev`).
2. **Loại Bỏ Hostnames Không Cần Thiết Khỏi CSP (`frontend/public/_headers`):**
   - Xóa `https://api.assethub.example.com` và `https://*.workers.dev` khỏi CSP `connect-src`.
   - Frontend chỉ gọi Django API thông qua Tunnel hoặc localhost, tuyệt đối không gọi trực tiếp D1/AI Worker bằng service secret.
3. **Mặc Định R2 & AI Safe Unconfigured:**
   - Đặt rỗng `R2_ACCESS_KEY_ID=` và `R2_SECRET_ACCESS_KEY=` kích hoạt chế độ unconfigured/skip an toàn khi R2 chưa bật trên Dashboard (Error 10042).
   - Đặt rỗng `AI_SERVICE_TOKEN=` khi AI Worker chưa deploy trên Cloud, tránh gọi nhầm token giả.
4. **Kiểm Thử Hồi Quy Thực Nghiệm (`backend/tests/test_cors.py`):**
   - Bổ sung test case nạp cấu hình thực tế từ `deploy/.env.demo.example` và đi qua `CorsMiddleware` thật của Django.
   - Xác nhận: `assethub.pages.dev` và preview PASS; `unrelated-project.pages.dev` và `assethub-attacker.pages.dev` REJECT 100%.

### 25.2. Chuẩn Hóa Runbook Triển Khai & Quy Trình Rollback Chuẩn (Agent A)
1. **Cố Định Root CWD & Lệnh Khởi Chạy:**
   - Mọi giai đoạn triển khai đều giữ thư mục gốc repo `/home/truong/Downloads/AssetHub`. Dùng subshell `(cd cloudflare-worker/bridge && ...)` cho Worker deployment, không làm lệch working directory của các bước tiếp theo.
2. **Recreate Container Đúng Service Name:**
   - Service name chuẩn là `backend` (thay vì `backend-demo`).
   - Loại bỏ hoàn toàn `docker compose restart`. Áp dụng biến môi trường mới bằng lệnh recreate:
     `docker compose -p assethub-demo -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo up -d --no-build --pull never backend`.
3. **Quy Trình Tạo Project Pages & Triển Khai:**
   - Hướng dẫn tạo project bằng CLI có sẵn: `$WRANGLER pages project create assethub --production-branch main`.
   - Deploy chỉ định branch nhất quán: `$WRANGLER pages deploy frontend/dist --project-name assethub --branch main`.
4. **Quy Trình Hoàn Tác Chuẩn Xác Theo Tài Liệu Chính Thức:**
   - Loại bỏ lệnh không tồn tại `wrangler pages rollback`. Hướng dẫn rollback qua Cloudflare Dashboard cho production deployment (lưu ý lần deploy đầu chưa có target rollback).
   - Hoàn tác CSDL D1 bằng Cloudflare D1 Time Travel: `$WRANGLER d1 time-travel restore assethub-test-d1 --bookmark=<bookmark>` (hoặc `--timestamp=<RFC3339>`), kết hợp file SQL export làm offline safety backup.
   - Stack PostgreSQL production 5433/8000 hoàn toàn nguyên vẹn với RPO = 0s.

---

## 26. Nghiệm Thu Sửa 2 Đoạn Xử Lý Secret Trong Runbook (Review 5dc68ce)

- **Thời điểm:** 2026-11-05 15:20 (GMT+7)
- **Trạng thái:** **Sửa hoàn tất 2 đoạn xử lý secret trong Runbook — Sẵn sàng cho Codex kiểm duyệt.**
- **Ranh giới an toàn:**
  - Zero cloud mutation, zero live Workers AI calls, zero public Tunnel, zero billing activation.
  - Zero package install (`npm`, `pip`, `uv`), zero Docker image pull/build.
  - Không sửa file `.env` đang dùng, không chạm stack phục vụ (`assethub-backend-1`, `assethub-db-1`, `pg-test`).
  - Bảo lưu nguyên trạng 2 tài liệu Tuần 5 untracked thuộc phiên khác.
  - 1 tab AGY, 0 worktree, 0 push/merge trên nhánh `feat/cloudflare-d1`.

### 26.1. Chi Tiết Khắc Phục 2 Đoạn Executable Runbook
1. **Sinh `DJANGO_SECRET_KEY` An Toàn Khi Chạy Lại (Step 2):**
   - Loại bỏ điều kiện lỗi `"insecure-default-secret-key" in content or "DJANGO_SECRET_KEY=" in content`.
   - Bóc tách chính xác giá trị thực tế của `DJANGO_SECRET_KEY` trong file env; phân biệt rõ giá trị rỗng/thiếu/placeholder với key tùy chỉnh.
   - Chỉ tự động sinh khóa ngẫu nhiên khi giá trị là rỗng hoặc khớp chính xác danh sách placeholder đã biết (`change-this-to-a-secure-random-64-character-string-for-demo!`, `insecure-default-secret-key-for-dev-only`, hoặc tiền tố `placeholder`).
   - Bảo toàn nguyên vẹn từng byte khóa bí mật tùy chỉnh đã cấu hình; chạy lặp lại nhiều lần không bao giờ xoay vòng khóa ngoài ý muốn.
   - Ghi trực tiếp vào file cục bộ, tuyệt đối không in bí mật ra màn hình terminal hay shell history.
2. **Khởi Chạy Cloud Smoke Kín Đáo & Đầy Đủ Biến Môi Trường (Step 11):**
   - Thay thế lệnh thủ công bằng Python launcher sử dụng `backend/.venv/bin/python` nạp cấu hình kín từ `deploy/.env.demo` qua `dotenv_values`, không echo token ra terminal.
   - Nạp toàn bộ các biến cấu hình không-None từ `deploy/.env.demo` (bao gồm `DJANGO_SECRET_KEY`, `R2_*`, `AI_*`) sang môi trường tiến trình con; các biến rỗng trong file sẽ vô hiệu hóa triệt để giá trị cũ ở process cha, đảm bảo test không skip giả khi có cấu hình thật.
   - Truyền đồng thời cả hai biến môi trường bắt buộc: `D1_BRIDGE_SECRET_TOKEN` (yêu cầu bởi Django runtime `settings.py`) và `D1_BRIDGE_TOKEN` (yêu cầu bởi `D1Client`).
   - Kiểm tra xác thực trước khi chạy: `D1_BRIDGE_SECRET_TOKEN` phải >= 32 ký tự và không phải placeholder; `D1_BRIDGE_URL` và `CLOUD_API_BASE_URL` phải là URL https hợp lệ.
   - Chuyển tiếp mã thoát `res.returncode` trực tiếp ra shell, không nuốt lỗi test.












---

## 27. Nghiệm Thu Triển Khai Demo Cloudflare Đợt 1 (Phase 1 Live Cloud Deployment)

- **Thời điểm:** 2026-11-05 16:05 (GMT+7)
- **Trạng thái:** **Triển khai Đợt 1 thành công 100% trên tài nguyên Cloudflare thực tế — Sẵn sàng cho Codex kiểm duyệt.**
- **Ranh giới an toàn tuyệt đối:**
  - Không kích hoạt thẻ / billing / R2 trả phí (R2 giữ nguyên chế độ skip / unconfigured an toàn do Cloudflare Error 10042).
  - Không cài thêm package hay tool; giữ nguyên PostgreSQL production (`assethub-backend-1` port 8000, `assethub-db-1` port 5433).
  - Bảo toàn dữ liệu đang có trên CSDL `assethub-test-d1` (đã lưu Time Travel bookmark và offline export SQL trước khi thao tác).
  - Dữ liệu smoke test được cô lập theo `run_id` và dọn dẹp sạch sẽ 100% (deterministic teardown pass).
  - Không push/merge git, bảo lưu nguyên trạng các file Tuần 5 untracked.

### 27.1. Danh Sách Tài Nguyên Cloudflare Thực Tế
1. **Cơ sở dữ liệu D1 (`assethub-test-d1`):**
   - Account ID: `5e76c2bf4e09587a010d31ea416ec8d3`
   - Database ID: `3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`
   - Pre-deploy Time Travel Bookmark: `0000003f-00000000-0000511a-c9d03ad612a3f3279dcc3983be2d5289`
   - Offline SQL Backup: `backups/d1_backup_predeploy_20261105.sql` (529KB, chmod 0600, gitignored)
   - Admin nền xác thực: `gate_admin` (id: 1, role: ADMIN, is_active: 1, is_superuser: 1) nguyên vẹn.
2. **Worker D1 Bridge (`assethub-test-worker`):**
   - URL: `https://assethub-test-worker.hnhoa494-assethub.workers.dev`
   - Active Version ID: `382d0d9b-6e0b-4554-b899-84b73d8d31b1`
   - Xác thực: Khớp secret token có sẵn, không rotate, kiểm tra HTTP 200 `/health` và query `SELECT 1` thành công.
3. **AI Worker (`assethub-ai-worker`):**
   - URL: `https://assethub-ai-worker.hnhoa494-assethub.workers.dev`
   - Deployed Version ID: `60aaaf0f-e769-4edc-bbc7-cd52f6309ae9`
   - Model: `@cf/meta/llama-3.1-8b-instruct-fp8`
   - D1 Binding: `assethub-test-d1` (`3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`)
   - Secret: Đồng bộ `AI_SERVICE_TOKEN` từ file bảo mật `/tmp/ai_token.secret` qua `wrangler secret put`.
   - Auth Barrier: POST không token -> 401; POST sai token -> 401; GET root -> 404 (Pass 100%).
   - Kiểm chứng suy luận: Chạy đúng 1 request duy nhất trong hạn mức Free (Agent B); Backend fallback xử lý an toàn khi LLM trả INVALID_SUGGESTION; 0 ô nhiễm D1; không benchmark lặp.
4. **Demo Backend Container (Port 8001):**
   - Service: `backend` trong Compose project `assethub-demo` (`deploy/docker-compose.demo.yml`)
   - Cấu hình: nạp từ `deploy/.env.demo` (chmod 0600, gitignored)
   - Health check: `curl http://127.0.0.1:8001/api/health/` -> HTTP 200 `{"status":"ok","db":"ok"}` (truy vấn D1 trực tiếp qua Bridge thành công).
5. **Cloudflare Quick Tunnel:**
   - Process: `/usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8001`
   - Public Tunnel URL: `https://away-server-pot-catalogue.trycloudflare.com`
   - Health check qua Tunnel: `curl https://away-server-pot-catalogue.trycloudflare.com/api/health/` -> HTTP/2 200 `{"status":"ok","db":"ok"}`.
6. **Cloudflare Pages (`assethub`):**
   - Project Name: `assethub` (Branch `main`)
   - Production URL: `https://assethub-97m.pages.dev`
   - Deployment URL: `https://a535d342.assethub-97m.pages.dev`
   - Build artifact: Nhúng `VITE_API_BASE_URL="https://away-server-pot-catalogue.trycloudflare.com"`, sạch 0 secret, CSP connect-src cho phép `*.trycloudflare.com` và `*.pages.dev`.
   - Kiểm tra route: `/` và SPA rewrite `/login` đều trả về HTTP/2 200 OK.
7. **CORS liên kết Pages -> Tunnel Backend:**
   - OPTIONS preflight từ Origin `https://assethub-97m.pages.dev` tới `https://away-server-pot-catalogue.trycloudflare.com/api/auth/login/` trả về HTTP/2 200 với `access-control-allow-origin: https://assethub-97m.pages.dev`.

### 27.2. Kết Quả Cloud Smoke Test Suite (Live Cloud Execution)
- Lệnh chạy qua launcher bảo mật:
  `CLOUD_API_BASE_URL="https://away-server-pot-catalogue.trycloudflare.com" backend/.venv/bin/pytest backend/tests/e2e/test_d1_cloud_smoke.py -m cloud_smoke -v`
- Kết quả: **5 passed, 2 skipped, 10 deselected in 40.27s (100% PASS)**:
  - `test_01_bridge_health`: **PASSED** (Bridge Worker phản hồi 200 `d1-bridge`, `SELECT 1` D1 thành công).
  - `test_02_jwt_auth_login`: **PASSED** (Đăng nhập HTTP thật qua Tunnel cấp Access & Refresh JWT).
  - `test_03_d1_asset_request_workflow`: **PASSED** (Tạo tài sản HTTP, tạo phiếu yêu cầu HTTP, duyệt phiếu HTTP, đối soát đọc ngược trực tiếp từ D1 qua D1Client: status `IN_USE`, audit log ghi nhận đầy đủ).
  - `test_04_r2_storage_smoke`: **SKIPPED** (R2 credentials unconfigured do chưa kích hoạt billing Cloudflare Error 10042).
  - `test_05_workers_ai_smoke`: **SKIPPED** (Bảo toàn hạn mức Free tối đa 1 request suy luận, đã kiểm chứng độc lập bởi Agent B).
  - `test_06_d1_last_admin_protection_trigger`: **PASSED** (Trigger SQLite bảo vệ Admin cuối cùng trên CSDL D1 cloud hoạt động chính xác).
  - `test_07_cleanup_verification`: **PASSED** (Xác minh dọn dẹp sạch sẽ 100% các thực thể test thuộc `run_id`, 0 bản ghi mồ côi).

### 27.3. Hạng Mục Chưa Đủ & Rủi Ro Tồn Đọng
1. **Chưa hoàn tất toàn bộ Tuần 4:** Do Cloudflare R2 yêu cầu thẻ thanh toán/billing (Error 10042), đợt 1 chưa kích hoạt R2 hay chạy live upload attachment lên cloud.
2. **Giới hạn Quick Tunnel:** Quick Tunnel `away-server-pot-catalogue.trycloudflare.com` là đường truyền tạm thời. Nếu tiến trình `cloudflared` bị dừng hoặc máy khởi động lại, URL tunnel sẽ thay đổi và Pages frontend cần được build lại với URL mới.
3. **Vận hành Tunnel:** Tiến trình tunnel đang chạy nền dưới quyền người dùng. Để dừng: tắt tiến trình `cloudflared` hoặc dừng task nền.
4. **Trạng thái Git:** Giữ nguyên commit cục bộ, không push/merge nhánh `feat/cloudflare-d1`, bảo lưu tài liệu Tuần 5 untracked.


---

## 28. Nghiệm Thu Sửa Lớp AI Worker, Khắc Phục Triệt Để INVALID_SUGGESTION & Live Cloud Verification

- **Thời điểm:** 2026-11-05 16:25 (GMT+7)
- **Trạng thái:** **Sửa lỗi AI Worker thành công 100%, Live Cloud Inference trả về HTTP 200 & ghi nhận D1 — Sẵn sàng cho Codex kiểm duyệt.**
- **Ranh giới an toàn tuyệt đối:**
  - Giữ nguyên Pages frontend (`assethub-97m.pages.dev`), demo backend container (port 8001), Quick Tunnel (`away-server-pot-catalogue.trycloudflare.com`) và core stack PostgreSQL (8000/5433).
  - Tối đa đúng 1 lượt inference bổ sung trong hạn mức Free đã xác minh, 0 vi phạm quota.
  - Chưa kích hoạt R2 / thẻ thanh toán / billing (Error 10042).
  - Không push, không merge, bảo lưu 2 file Tuần 5 untracked.

### 28.1. Phân Tích Nguyên Nhân Gốc Rễ (Root Cause Analysis)
1. **Prompt template thiếu ràng buộc chặt chẽ:** Template cũ `{"suggested_category_id": <id>, "explanation": "..."}` dùng placeholder `<id>` trừu tượng, không yêu cầu `suggested_category_name` và không cấm markdown fence. LLaMA 3.1 8B sinh chuỗi dạng `"ID 1"`, đưa tên danh mục vào ID hoặc kèm markdown fence ` ```json ... ``` `.
2. **Parser JSON mong manh:** Regex cũ `\{[\s\S]*\}` tham lam (greedy) bị lỗi `SyntaxError` khi model sinh preamble/postscript hoặc trailing commas `{"key": "val",}`.
3. **`parsePositiveInteger` thiếu hỗ trợ định dạng tiền tố:** Chỉ nhận dạng regex `^\d+$`, từ chối các định dạng phổ biến như `"ID 1"`, `"ID: 1"`.
4. **Thiếu tham số điều khiển sinh:** Gọi `env.AI.run` mặc định không có `temperature: 0.1` hay `max_tokens`, làm tăng tính ngẫu nhiên (chattiness) của model.

### 28.2. Giải Pháp Khắc Phục Đúng Lớp (`cloudflare-worker/src/index.js`)
1. **Chuẩn hóa Prompt & Ràng Buộc Kiểu Dữ Liệu:**
   - Cung cấp ví dụ cụ thể số nguyên `${sampleCat.id}` (ví dụ: `1`) và tên `${sampleCat.name}` (`Laptop`).
   - Yêu cầu bắt buộc trả về cả `suggested_category_id` lẫn `suggested_category_name`.
   - Cấm markdown code block và văn bản thừa.
2. **Tham Số Nhiệt Độ & Giới Hạn Token:**
   - Bổ sung `temperature: 0.1` (deterministic output) và `max_tokens: 256` trong lệnh gọi `env.AI.run`.
3. **Parser Bóc Tách Đa Tầng:**
   - Tự động bóc tách markdown fences (` ```json ` và ` ``` `).
   - Bóc tách theo cặp ngoặc nhọn `{` đầu tiên và `}` cuối cùng hợp lệ.
   - Làm sạch trailing commas trước `JSON.parse`.
4. **Mở Rộng `parsePositiveInteger` & Name Fallback:**
   - Hỗ trợ regex tiền tố `^ID\s*[:#-]?\s*(\d+)$` an toàn (vẫn từ chối float, infinity, bool, array).
   - Hỗ trợ đa key fallback (`suggested_category_name`, `category_name`, `suggested_category`, `category`, `name`).
   - So khớp danh mục không phân biệt hoa thường và không dấu tiếng Việt (`normalize('NFD')`).

### 28.3. Kết Quả Kiểm Thử Unit Test Cục Bộ
- Test suite: `cloudflare-worker/tests/worker.test.js`
- Bổ sung 5 test case mới (markdown fence, conversational preamble/postscript, ID prefix `"ID 1"`, trailing comma, accent-insensitive fallback).
- Kết quả: **26 passed in 361ms (100% PASS)**.
- Backend unit test: `backend/tests/test_ai_proxy.py` đạt **32 passed in 4.51s (100% PASS)**.

### 28.4. Triển Khai Cloud & Kết Quả Suy Luận Live Thực Tế (Agent B)
1. **Deploy Worker mới:**
   - Version ID: `9c636cf0-3147-4839-8dbb-1dac84185730`
   - Trigger URL: `https://assethub-ai-worker.hnhoa494-assethub.workers.dev`
   - Model bind: `@cf/meta/llama-3.1-8b-instruct-fp8`
2. **Lượt suy luận duy nhất bổ sung (`req-live-ai-002`):**
   - Payload: `Dell Latitude 7420 Laptop` | Danh mục: `[{"id": 1, "name": "Laptop"}, {"id": 2, "name": "Monitor"}]`
   - Phản hồi HTTP: **HTTP/2 200 OK**
   - Response body:
     ```json
     {
       "request_id": "req-live-ai-002",
       "suggested_category_id": 1,
       "suggested_category_name": "Laptop",
       "explanation": "Là một chiếc laptop dành cho công việc, được sử dụng bởi kỹ sư với 16GB RAM",
       "log_saved": true
     }
     ```
3. **Đối soát CSDL D1 thực tế (`ai_suggestion_logs`):**
   - Query remote D1 xác nhận bản ghi tồn tại:
     - `id`: `req-live-ai-002`
     - `asset_name`: `Dell Latitude 7420 Laptop`
     - `suggested_category_id`: `1`
     - `suggested_category_name`: `Laptop`
     - `model`: `@cf/meta/llama-3.1-8b-instruct-fp8`
4. **Bảo toàn ranh giới:** Dừng ngay sau 1 request duy nhất, 0 gọi thêm request suy luận nào khác.

---

## 29. Nghiệm Thu Sửa Lớp Django HTTP Client (User-Agent), Vượt Rào Cản Cloudflare Edge 1010 & Live Backend Integration

- **Thời điểm:** 2026-11-05 16:40 (GMT+7)
- **Trạng thái:** **Sửa lỗi HTTP Client hoàn tất, Live Backend → Worker AI thành công 100% — Sẵn sàng cho Codex kiểm duyệt.**
- **Ranh giới an toàn tuyệt đối:**
  - Giữ nguyên live URLs: Pages Frontend `https://assethub-97m.pages.dev`, Quick Tunnel `https://away-server-pot-catalogue.trycloudflare.com`.
  - Không kích hoạt billing R2 / thẻ thanh toán (trạng thái R2 pending Cloudflare Error 10042, cần người dùng thao tác kích hoạt trên Dashboard/Billing khi muốn nâng cấp; không đánh đồng fallback/skip là hoàn tất).
  - Không push, không merge nhánh `feat/cloudflare-d1`.
  - Bảo lưu nguyên trạng 2 tài liệu Tuần 5 untracked (`docs/superpowers/specs/2026-11-05-week-5-asset-assistant-design.md`, `docs/superpowers/plans/2026-11-05-week-5-asset-assistant.md`).
  - Tiêu tốn đúng 1 lượt inference live duy nhất trong hạn mức Free, 0 rò rỉ secret token.

### 29.1. Phân Tách Điểm Nghẽn & Paired Control Probe (Đối Chứng 0-Quota)
1. **Xác nhận trạng thái Worker AI trước đó:**
   - Suy luận trực tiếp tới Cloudflare Worker AI đã được nghiệm thu và kiểm chứng thành công tại commit `86ad326` (HTTP 200, `log_saved: true`).
   - Điểm nghẽn không nằm ở Worker AI hay model LLaMA 3.1 8B, mà phát sinh tại tầng Django HTTP integration khi backend gọi Worker qua mạng.
2. **Paired Control Probe (Đối chứng không tiêu tốn inference):**
   - Thực nghiệm với payload rỗng `{}` gửi tới Cloudflare Worker AI endpoint:
     - **Thực nghiệm A (Default urllib User-Agent):** Python `urllib` mặc định (`Python-urllib/3.12`) bị Cloudflare Edge WAF chặn đứng ngay tại rào cản mạng với HTTP 403 (Mã lỗi Cloudflare Error 1010: "Access denied by Cloudflare Ray ID"). Request chưa từng chạm tới mã nguồn Worker.
     - **Thực nghiệm B (Custom User-Agent `AssetHub-Backend/1.0`):** Request vượt qua hoàn toàn rào cản Cloudflare Edge WAF, được tầng validator của Worker thụ lý và trả về HTTP 400 Bad Request `{"error": "Invalid request body"}` trước khi chạm tới `env.AI.run`.
   - **Kết luận:** Bằng chứng đối chứng thực tế chứng minh thay đổi User-Agent giải quyết dứt điểm lỗi 403 Error 1010 mà không tiêu hao hạn mức suy luận (0 model inference).

### 29.2. Sửa Đổi Tinh Gọn Tại Backend (`backend/apps/ai/services.py`)
- Khai báo hằng số `DEFAULT_USER_AGENT = "AssetHub-Backend/1.0"` trên `AiProxyService`.
- Tự động gắn header `User-Agent: AssetHub-Backend/1.0` trong `_post_request` và `suggest_category`, cho phép override nếu caller truyền header tùy chỉnh.
- Sử dụng thư viện chuẩn `urllib.request`, 0 thêm dependency bên ngoài, 0 can thiệp logic phân quyền hay circuit breaker.

### 29.3. Bổ Sung Regression Test Suite Cục Bộ (`backend/tests/test_ai_proxy.py`)
- Bổ sung các unit tests kiểm chứng hành vi:
  - `test_post_request_includes_default_user_agent`: Xác minh header mặc định `AssetHub-Backend/1.0`.
  - `test_post_request_allows_custom_user_agent_override`: Xác minh cho phép ghi đè custom User-Agent.
  - `test_suggest_category_headers_and_body_intact`: Xác minh `User-Agent`, `Authorization: Bearer ...`, `Content-Type: application/json` và request body gửi đi đầy đủ, chính xác.
  - `test_suggest_category_403_raises_service_unavailable_not_fallback`: Xác minh mã lỗi 403 thật từ upstream vẫn raise `ServiceUnavailable` (HTTP 503) và không bị nuốt thành fallback.
- **Kết quả chạy test cục bộ:**
  - AGY chạy trên container `pg-test` có sẵn (port 5432): **36/36 tests PASSED in 4.70s (100% PASS)**.
  - Codex chạy độc lập trên PG harness riêng (`/tmp/assethub-6c2d3e5-ai-review.txt`, đã teardown): **36/36 tests PASSED in 5.25s (100% PASS)**.

### 29.4. Kiểm Chứng Live Toàn Tuyến: Backend API → Worker → Model LLaMA 3.1 8B
- Khởi động lại container `assethub-demo-backend-1` (port 8001), healthcheck pass `{"status":"ok","db":"ok"}`.
- Gọi API đề xuất danh mục thật qua backend:
  - Endpoint: `POST http://127.0.0.1:8001/api/ai/suggest-category/` (qua tài khoản Admin hợp lệ).
  - Payload thực tế:
    ```json
    {
      "name": "Thiết bị kiểm thử tải cao D1",
      "description": "Hệ thống máy đo kiểm thử hiệu năng D1"
    }
    ```
  - Phản hồi API thực tế: **HTTP/2 200 OK**
  - Response Data thực tế:
    ```json
    {
      "request_id": "1df70b2d-016f-4c6b-94de-4129305f460e",
      "suggested_category_id": 65,
      "suggested_category_name": "Thiết bị kiểm thử D1",
      "explanation": "Là hệ thống máy đo kiểm thử hiệu năng D1",
      "log_saved": true,
      "is_fallback": false
    }
    ```
- **Tiêu chí nghiệm thu hoàn tất:**
  - `is_fallback: false`: Model AI LLaMA 3.1 8B (`@cf/meta/llama-3.1-8b-instruct-fp8`) phân loại thành công, không dùng heuristic.
  - `log_saved: true`: Ghi log vào Cloudflare D1 `ai_suggestion_logs` thành công (Codex đã đối soát trực tiếp trên D1 metadata).
  - Phân loại chính xác danh mục ID 65 ("Thiết bị kiểm thử D1").
  - Tiêu tốn đúng 1 lượt inference duy nhất trong hạn mức Free.

### 29.5. Lưu Ý Kỹ Thuật Về Tính Tiền Định (Determinism Nuance)
- Thiết lập `temperature: 0.1` cùng các ràng buộc cấu trúc prompt nghiêm ngặt làm giảm thiểu tối đa tính ngẫu nhiên (nondeterminism) của mô hình ngôn ngữ lớn (LLM).
- Tuy nhiên, `temperature: 0.1` không phải là một bảo đảm toán học tuyệt đối cho tính tiền định 100% trong mọi điều kiện (vẫn có xác suất biến thiên ở tầng sampling token). Hệ thống đã được gia cố bằng parser đa tầng (hỗ trợ prefix `^ID\s*[:#-]?\s*(\d+)$`, key aliases, accent-insensitive matching) để bảo đảm tính ổn định thực tế của hệ thống production.

### 29.6. Trạng Thái Dịch Vụ & URLs Hiện Tại
- **Cloudflare Pages:** `https://assethub-97m.pages.dev`
- **Cloudflare Quick Tunnel:** `https://away-server-pot-catalogue.trycloudflare.com`
- **Worker AI:** `https://assethub-ai-worker.hnhoa494-assethub.workers.dev`
- **D1 Database:** `assethub-test-d1` (`3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`)
- **Cloudflare R2:** Pending Cloudflare Error 10042 (chưa kích hoạt thanh toán trên Dashboard).

