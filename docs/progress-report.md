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
  **62 passed, 1 skipped in 26.17s (100% PASS)**.
- `node spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs`:
  **8 passed in 98.97ms (100% PASS)**.
- **Kiểm Tra Trạng Thái Hệ Thống:**
  - Cổng 5434 và 8787 hoàn toàn giải phóng (`Ports 5434 and 8787 are completely clean`).
  - Thư mục `/tmp/assethub_rehearsal_d1_*` dọn sạch 100%.
  - `assethub-db-1` (port 5433) và `pg-test` (port 5432) hoạt động bình thường, không bị gián đoạn.
  - Quét 0 chuỗi hex token/secret 64 ký tự trong git diff.
- 0 tiến trình ngoài bị ảnh hưởng.

### 9.3. Toàn Bộ Unit Test Suite Khác
- `DB_HOST=127.0.0.1 DB_PORT=5433 backend/.venv/bin/pytest backend/apps/common/tests/test_d1_migration.py backend/assethub_api/backends/d1/tests/test_d1_schema_constraints.py backend/assethub_api/backends/d1/tests/test_isolation_guard.py -v`:
  **62 passed, 1 skipped in 25.93s (100% PASS)**.
- `node spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs`:
  **8 passed in 99.47ms (100% PASS)**.
- **Kiểm Tra Trạng Thái Hệ Thống:**
  - Cổng 5434 và 8787 hoàn toàn giải phóng (`Ports 5434 and 8787 are completely clean`).
  - Thư mục `/tmp/assethub_rehearsal_d1_*` dọn sạch 100%.
  - `assethub-db-1` (port 5433) và `pg-test` (port 5432) hoạt động bình thường, không bị gián đoạn.
  - Quét 0 chuỗi hex token/secret 64 ký tự trong git diff.

---

## 10. Ranh Giới An Toàn & Đề Xuất Nghiệm Thu

- **Bằng chứng kỹ thuật:** Đã hoàn thiện toàn diện quy trình kiểm thử và diễn tập an toàn, giải quyết trọn vẹn mọi yêu cầu P1 và P2 của Codex về process ownership, marker isolation, opt-in gating, và dynamic credentials.
- **Ranh giới:** Chưa push/merge git, không ghi CSDL production PostgreSQL, không ghi Cloudflare D1 Cloud.
- Kính gửi Codex nghiệm thu Task 3c để sẵn sàng chuyển sang Task 4 (Business Logic & Cloud Integration).
