# Hướng Dẫn Vận Hành & Demo Trợ Lý Tài Sản Tuần 5 (Asset Assistant Demo Guide)

Tài liệu hướng dẫn vận hành, tham số cấu hình, kiểm thử và nghiệm thu tính năng **Trợ lý Tài sản (Asset Assistant)** thuộc Tuần 5 của dự án AssetHub. Tính năng áp dụng kiến trúc **Grounded AI Assistant** (Hướng 1: Tool-calling có căn cứ, không sinh SQL tùy ý, bảo toàn phân quyền RBAC và dữ liệu thực tế).

---

## 1. Tham Số Vận Hành & Cấu Hình Môi Trường (Operational Parameters & Configuration)

### 1.1. Cấu hình Backend (Django / DRF)

| Biến môi trường | Kiểu dữ liệu | Giá trị mặc định (code/env) | Giá trị vận hành Live | Mô tả & Hướng dẫn vận hành |
| :--- | :--- | :--- | :--- | :--- |
| `ASSISTANT_ENABLED` | boolean | `false` | `true` | Cờ chuyển đổi tính năng (Feature Gate). Mặc định trong cấu hình mẫu (`.env.example`) là `false` để đảm bảo an toàn. Trên container backend demo live, cờ đã chính thức kích hoạt `true` sau khi nghiệm thu Phase C. Khi `false`, request đã xác thực trả HTTP 503 `ASSISTANT_DISABLED`; request chưa xác thực trả 401. Khi `true`, toàn bộ API routes `/api/assistant/*` và các dịch vụ trợ lý hoạt động bình thường. |
| `ASSISTANT_AI_TIMEOUT_SECONDS` | int | `30` | `30` | Thời gian timeout (giây) cho cuộc gọi HTTP từ Backend tới Cloudflare Worker AI Gateway (`gateway.py`). |
| `AI_WORKER_URL` | string (URL) | `http://127.0.0.1:8787` (fallback nội bộ) | `https://assethub-ai-worker.hnhoa494-assethub.workers.dev` | URL của Worker AI Gateway. Giá trị fallback loopback `8787` chỉ dùng cho D1 bridge và fake AI khi diễn tập cục bộ. Trên môi trường live, Backend kết nối trực tiếp tới Worker chính `https://assethub-ai-worker.hnhoa494-assethub.workers.dev` (phiên bản triển khai `0a92106b-199b-4480-ac9e-12d616de4a1e`). Worker eval phục vụ đánh giá Gate 3-L là `https://assethub-assistant-eval.hnhoa494-assethub.workers.dev` (`2c746465-5d11-487e-be6e-4c8095f58084`). |
| `AI_SERVICE_TOKEN` | string | Fallback trong `settings.py` | Config bí mật qua `.env.demo` | Bearer Token xác thực giữa Backend và Worker (tối thiểu 32 ký tự). Gateway từ chối token trống và Worker chỉ nhận token trùng với secret của nó. Tuyệt đối không in hoặc commit khóa bí mật vào mã nguồn hay tài liệu. |
| `ASSISTANT_MODEL` | string | `@cf/google/gemma-4-26b-a4b-it` (Worker config) | `@cf/google/gemma-4-26b-a4b-it` | **Biến môi trường của Cloudflare Worker (`env.ASSISTANT_MODEL`):** Cấu hình chính thức sử dụng mô hình Gemma `@cf/google/gemma-4-26b-a4b-it` cho tính năng Trợ lý (commit `19ccbea`, deploy tại Worker `0a92106b-199b-4480-ac9e-12d616de4a1e`). Mã nguồn Worker chỉ fallback Llama khi biến này bị bỏ trống. Dịch vụ gợi ý danh mục tài sản `AI_MODEL` vẫn giữ nguyên mô hình `@cf/meta/llama-3.1-8b-instruct-fp8`. |

> [!NOTE]
> **Phân biệt ba cơ chế Timeout độc lập:**
> 1. **`ASSISTANT_AI_TIMEOUT_SECONDS` (30 giây):** Quản lý thời gian chờ phản hồi của HTTP client từ Backend tới Cloudflare Worker AI Gateway.
> 2. **Cơ chế thu hồi lượt hỏi bị treo (Stale PROCESSING Turn Auto-Expiry) (180 giây):** Được thực thi độc lập thông qua so sánh `created_at` kết hợp kỹ thuật CAS (Compare-And-Swap) trong `storage.py` và `services.py`. Khi một lượt hỏi ở trạng thái `PROCESSING` quá 180s, hệ thống tự động chuyển trạng thái sang `ERROR` khi đọc hoặc dọn dẹp, hoàn toàn không phụ thuộc vào `ASSISTANT_AI_TIMEOUT_SECONDS`.
> 3. **Gunicorn Server Timeout (120 giây):** Tiến trình Gunicorn backend trong container được cấu hình `--timeout 120` để bao bọc các lượt xử lý AI phức tạp, tránh ngắt kết nối gateway sớm.

### 1.2. Cấu hình Frontend (React / Vite)

| Biến môi trường | Kiểu dữ liệu | Giá trị mặc định | Giá trị Production | Mô tả & Hướng dẫn |
| :--- | :--- | :--- | :--- | :--- |
| `VITE_ASSISTANT_ENABLED` | boolean | `false` | `true` (trong ON build) | Bật/tắt giao diện Trợ lý tài sản trên Frontend (nút mở widget chat tại Sidebar và bảng điều khiển hỗ trợ Support Tickets). Bản build ON đã được xuất bản chính thức lên Cloudflare Pages Production. |

### 1.3. Công Cụ Quản Lý & Triển Khai Cloudflare (Cloudflare Tooling)

- **Wrangler CLI (Chính thức sử dụng):** Dự án triển khai Cloudflare Workers, Pages và D1 qua Wrangler CLI `4.158.0` (`npm_config_offline=true npx --no-install wrangler ...`).
- **Cloudflare CLI (`cf` v1.0.0-beta.19):** Công cụ `cf` đã được cài đặt cục bộ và lệnh `cf auth whoami` xác nhận tài khoản AssetHub hợp lệ. Quy trình phát triển giữ nguyên cấu hình `wrangler.toml`, tuyệt đối không tự ý chạy `cf migrate`, `cf init` trên kho mã nguồn.

### 1.4. Hiện Trạng Triển Khai Thực Tế Cloudflare & D1 (Phase A & Phase C Đã Hoàn Tất)

Toàn bộ các giai đoạn triển khai nền tảng (Phase A) và kích hoạt nghiệm thu đám mây (Phase C) đã hoàn thành và được kiểm chứng độc lập bởi Root:

- **Cơ sở dữ liệu D1 Cloud (`assethub-test-d1`):** UUID `3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`, Account ID `5e76c2bf4e09587a010d31ea416ec8d3`.
- **Bản sao lưu an toàn CSDL D1 (Pre-Migration Snapshot):**
  * Snapshot xuất trước khi thực hiện ghi: `backups/week5-before-assistant-20261106T115418Z.sql` (558.725 bytes, SHA256 `9b703a760499051b54444494675f715ffa7a9e0a6c0841e56d957b5dd59ff1fa`, quyền 0600, gitignored).
  * Khôi phục kiểm thử cục bộ trên SQLite xác nhận `PRAGMA integrity_check: ok`, bảo toàn nguyên vẹn 23 bảng cơ sở, bảng trợ lý bằng 0. Manifest lưu trữ an toàn.
- **Áp dụng DDL Trợ lý trên CSDL D1 Remote:**
  * File DDL: `backend/assethub_api/backends/d1/sql/0002_week5_assistant.sql` (SHA256 `eb97eea218fac496ac3e4b743582667d5fa66d7f3435d83b525fe9962679322f`).
  * Khởi tạo thành công 3 bảng mới (`assistant_conversation`, `assistant_chatturn`, `assistant_supportticket`) và 4 chỉ mục bằng `CREATE TABLE IF NOT EXISTS` và `CREATE INDEX IF NOT EXISTS`.
  * Toàn bộ 23 bảng cơ sở cũ bảo toàn 100% số lượng dòng. D1 Time Travel bookmark: `0000004a-00000006-0000511b-ddd01100892e4bfed0f113bfb653ba7e`.
- **Đồng bộ Metadata Baseline CSDL D1 (`deploy/sql/week5_d1_metadata_baseline.sql`):**
  * Root đã kiểm chứng độc lập và commit tại `46f1af5` (SHA256 `9dc3b020f9a9d9e09b2b7248e4cc3a154ab13649d6632da2b867612c11a83bb8`).
  * Đã nạp thành công vào remote D1 qua Wrangler. Django `MigrationRecorder` ghi nhận đủ **25/25 unique migration**, đồ thị nhất quán (`check_consistent_history: PASS`), `migration_plan` bằng 0. Cú pháp `INSERT ... SELECT ... WHERE NOT EXISTS` đã được kiểm tra tính idempotent trên bản sao offline (0 DDL).
  * Bảo tồn `django_content_type` và `auth_permission` ở mức 0 dòng (không ảnh hưởng runtime vì AssetHub dùng phân quyền RBAC qua `User.role`, không dùng model permission của Django).
- **Worker Chính (`assethub-ai-worker`):**
  * Phiên bản đã deploy: **`0a92106b-199b-4480-ac9e-12d616de4a1e`** (`https://assethub-ai-worker.hnhoa494-assethub.workers.dev`).
  * Mô hình: `ASSISTANT_MODEL=@cf/google/gemma-4-26b-a4b-it`, `AI_MODEL=@cf/meta/llama-3.1-8b-instruct-fp8`.
  * Rollback version: `9c636cf0-3147-4839-8dbb-1dac84185730` (deployment `5ce17ea7-a9e1-47f1-aef0-8c84c77d5ee2`).
- **Demo Backend Container (Cổng 8001):**
  * Container `assethub-demo-backend-1` chạy image `sha256:3adb911fa8bbdd8f80081413fb2bd9f3b362da9484641798247cf3284f37eed3`, mount read-only `/home/truong/Downloads/AssetHub-week5/backend -> /app`, Gunicorn `--timeout 120`.
  * **Trạng thái vận hành:** `ASSISTANT_ENABLED=true` đã kích hoạt thành công, container healthy. (Bằng chứng lịch sử Phase A trả HTTP 503 trước khi bật cờ đã xác nhận cơ chế an toàn đóng ngắt hoạt động đúng đặc tả).
- **Cloudflared Live Quick Tunnel:**
  * Tiến trình tunnel active (PID 124458, khởi chạy 12:04:14 UTC), URL public: **`https://construction-evening-consultancy-fantastic.trycloudflare.com`**.
  * Cấu hình CORS cho phép chính xác origin `https://assethub-97m.pages.dev`, chặn các origin lạ; JWT auth header được chấp nhận đầy đủ.
- **Nghiệm Thu Public API Thực Tế (Live Public API Acceptance Driver):**
  * Thực thi chính xác 1 lần script `driver.py` (SHA256 `7c02b9a395165e8670206ee6d4a86eda1ade32d0fc0c39c6151656b4e72430ae`) với cờ `WEEK5_LIVE_ACCEPT_OPT_IN=ROOT_AUTHORIZED_MAX4`.
  * **Kết quả: 18/18 tiêu chí PASS**, ghi nhận tại ledger `/home/truong/.config/assethub/week5-live-acceptance-ledger-20261106T122243Z.json` (SHA256 `ed465cd5fdb3b5ad74199252bbaf51fc2ab5efdec15e0585caadf4cb3c3b28e4`).
  * Tiêu thụ đúng **3 cuộc gọi suy luận AI thực tế** (đếm tài sản, hỏi lại trung lập, bàn giao hỗ trợ) và **1 lượt replay idempotency** (trả về 200, 0 suy luận mới).
  * Khởi tạo thành công 3 cuộc hội thoại, 3 lượt hỏi, và 1 phiếu hỗ trợ (chuyển trạng thái `ANSWERED`, version 2). Do CSDL live hiện có 2 ADMIN và 3 USER (chưa có MANAGER), phản hồi phiếu hỗ trợ mang nhãn kiểm thử nghiệm thu rõ ràng và được xử lý fallback chính xác qua tài khoản ADMIN. Kiểm tra phân quyền xác nhận ADMIN nhận đúng 404 khi truy cập hội thoại riêng của USER và USER nhận đúng 403 khi vào inbox quản trị.
  * Phiên kiểm thử API sử dụng JWT nội bộ do server phát hành trực tiếp trong bộ nhớ, không qua form đăng nhập mật khẩu vật lý nhằm bảo vệ trường `last_login`.
- **Cloudflare Pages Production (`assethub`):**
  * Đã xuất bản thành công bản build ON lên môi trường sản xuất: Deployment ID **`fcb0df68-fee3-41be-8614-4e7729c53fce`** (`assethub --branch main`, build source commit `19ccbea`), URL preview `https://fcb0df68.assethub-97m.pages.dev`.
  * **Production URL chính thức:** **`https://assethub-97m.pages.dev`**.
  * File JS bundle thực tế trên production: `assets/index-D2f8d9D0.js` (867.744 bytes, SHA256 **`1fdf88e1fcb9b6dacb36661373e4f1caac6861296019d958fd719fdc4a017f67`**), Root đã curl kiểm tra trực tiếp và xác nhận khớp 100%.
  * Toàn bộ các route SPA (`/`, `/assistant`, `/assistant/support`, `/login`) trả về HTTP 200 SPA. Kết nối API tunnel và CORS hoạt động trơn tru. Bản production cũ `a535...` (commit `aad64a1`) trỏ vào dead tunnel đã được thay thế hoàn toàn.
  * Bản build OFF rollback an toàn được lưu trữ sẵn tại `/tmp/assethub-week5-public-off-20261106T120448Z` (file `assets/index-g-qMUSJI.js` SHA256 `f92c2c7a5193c156c7784ec35b0da343d310ea6cb4924a76ee2e6d833ea6b974`).

---

## 2. Trạng Thái Các Cổng Chất Lượng (Status of Quality Gates)

Dự án áp dụng mô hình kiểm soát chất lượng qua từng Gate tuần tự nghiêm ngặt:

| Quality Gate | Nội dung kiểm thử & Tiêu chuẩn nghiệm thu | Trạng thái | Ghi chú & Ranh giới an toàn |
| :--- | :--- | :---: | :--- |
| **Gate 1** | Contract locks (Models, SQL, DTO, Signatures, Error Codes) | **APPROVED & VERIFIED** | Đã khóa schema CSDL, DTO contracts, JSON schema và bộ mã lỗi chuẩn (`CONVERSATION_SCOPE_CHANGED`, `CLIENT_REQUEST_ID_CONFLICT`, `CONVERSATION_BUSY`, `STALE_VERSION_CONFLICT`). |
| **Gate 2** | Local AI Gateway & Mock Worker | **APPROVED & VERIFIED** | Đã kiểm chứng tính tương thích giữa backend `gateway.py` và Cloudflare Worker mock endpoint nội bộ. |
| **Gate 3-L (Base)** | Live AI Cloud Evaluation (12 ca cơ sở) | **ROOT APPROVED 12/12** | **Nghiệm thu chính thức 12/12 ca** tại artifact `backend/reports/assistant/gate3l-2026-11-06T114743.460674Z.json` (SHA256 `11c0d06f8aa2597631a9793ab8a23fda825b50f5faa73bd25e1b3cca69c1ace1`). 12 calls trong 20,3s, 0 lỗi. Commit `4fbeff94d08fdf52cbc0c4f45a314dbf91863b56`. Toàn bộ 12 ca đạt tiêu chuẩn chất lượng nghiêm ngặt; không ghép ca giữa các lượt chạy. |
| **Gate 3-L (Extended)** | Live AI Evaluation mở rộng (12 ca độc lập) | **FAILED 9/12 (Known Safe Limits)** | Bộ test mở rộng tách riêng (artifact `gate3l-extended-2026-11-06T115110.675463Z.json`, SHA256 `55c5af6d8e924d63b03222285674590dc2fba4d13e8c220a5673bcae37f8366a`). Đạt 9 ca strict match, 3 ca mismatch (X1, X6, X3). Root quyết định phát hành với các giới hạn chất lượng an toàn đã ghi nhận rõ ràng, không sửa prompt tránh rủi ro hồi quy. |
| **Gate 4** | Storage & Support Ticket Atomic Engine | **APPROVED & VERIFIED** | Cơ chế đặt chỗ lượt hỏi (`reserve_turn`), xử lý tranh chấp OCC, hết hạn stale turn CAS 180s, và bàn giao tự động SupportTicket nguyên tử đã hoàn tất. |
| **Gate 5** | Authenticated Grounded Chat & Support APIs | **APPROVED & COMMITTED** | Đã commit tại `c5fb8fdae3bbc800fbd5f9ae029bd68aca0de265` với 51 focused tests đã duyệt. |
| **Gate 6 & 7** | Frontend UI & Browser Integration | **APPROVED & COMMITTED** | Đã duyệt tích hợp UI: 194/194 Vitest (14 files, commit `961db3b`), Playwright E2E mock API cổng 5176 đạt 7/7 PASS (7,3s, commit `d7822c8`). Đã xuất bản lên Pages Production `https://assethub-97m.pages.dev` (deployment `fcb0df68...`). Root đã duyệt ảnh thực tế desktop 1280px và mobile 320px (rail 80px, nút gửi rõ ràng, không tràn ngang). |
| **Gate 8** | D1 Target Proof & Cloud Live Acceptance | **APPROVED — Task 8 và Tuần 5 đã nghiệm thu bản demo** | Lượt opt-in trên SQLite thật qua Wrangler D1 local với fake AI: 7/7 PASS (65,79s, commit `2427612`). Full backend PG suite: 783 PASS, 9 SKIP (98,34s); isolation suite 86 PASS (32,37s). Phase A và Phase C hoàn tất trọn vẹn, nghiệm thu API thật 18/18 PASS. Root đã review artifact, API thật và giao diện production đọc dữ liệu thật; các giới hạn còn lại được ghi ở mục 2.2 và 2.5. |

---

### 2.1. Chi Tiết Nghiệm Thu Gate 3-L Base (12/12 Đạt)

Đợt kiểm thử đánh giá mô hình LLM trực tiếp trên Cloudflare Workers AI với mô hình Gemma `@cf/google/gemma-4-26b-a4b-it` đã được Root chính thức phê chuẩn đạt **12/12 ca kiểm thử**:
- **Artifact nghiệm thu:** `backend/reports/assistant/gate3l-2026-11-06T114743.460674Z.json` (SHA256 `11c0d06f8aa2597631a9793ab8a23fda825b50f5faa73bd25e1b3cca69c1ace1`).
- **Thời gian thực thi:** 12 cuộc gọi hoàn tất trong 20,3 giây, 0 lỗi hệ thống.
- **Mã nguồn Worker đã duyệt:** Commit `4fbeff94d08fdf52cbc0c4f45a314dbf91863b56` (source SHA256 `8fc35a55f280c1b00661bd504421994b5e451cd135cf6b915ca99d84d45640f5`, prompt SHA256 `cf7bb6774188a83216c478ddded5a155ff65035a852d1e734ecdff4567cafd64`).
- **Worker Deployed Version:** `2c746465-5d11-487e-be6e-4c8095f58084` trên Worker đánh giá `assethub-assistant-eval`.
- **Kiểm thử đơn vị Worker:** 152/152 tests PASS, bao gồm kiểm thử đột biến và chứng minh an toàn tiêu cực.
- **Canonical Schema:** Schema chuẩn JSON không thay đổi (SHA256 `f6f76ee18be3e08744d23a41c912c2cda5ae41bf31bcd7966fc12e1cd52c7699`).
- **Phân tích 12 ca kiểm thử đạt:**
  * **E1, E2, E3 (Tổng hợp trạng thái tài sản):** Lựa chọn đúng công cụ `assets.summary`, đếm chính xác số lượng tài sản khả dụng (`AVAILABLE`) và tổng số 23 tài sản. Tại ca E1, câu hỏi nêu rõ `"phòng tôi"`, việc mô hình sinh thêm bộ lọc phòng ban của chính người dùng được chấp nhận vì đây là bộ lọc dư phòng mình, không vi phạm phân quyền.
  * **F1, F2 (Tra cứu tài sản cụ thể):** Lựa chọn chính xác công cụ `assets.search` với mã tài sản hoặc bộ lọc đặc thù.
  * **P1, P2 (Bảo vệ phân quyền RBAC):** Ca P1 chặn người dùng thường tra cứu tài sản của nhân viên IT khác. Ca P2: Actor là **MANAGER IT** hỏi danh sách tài sản phòng Nhân sự (HR) -> hệ thống nhận diện đúng ranh giới phòng ban và chặn `RESTRICTED`.
  * **O1, O2 (Ngoài phạm vi hệ thống):** Nhận diện chính xác câu hỏi ngoài phạm vi và trả lời `OUT_OF_SCOPE`.
  * **H1 (Bàn giao hỗ trợ):** Câu hỏi cơ sở `"Quy định bồi thường laptop bị mất..."` được mô hình nhận diện chính xác lý do `missing_knowledge` (chính sách chưa được tích hợp trong công cụ tra cứu tài sản) và trả quyết định `kind=handoff`. Đánh giá Base Gate chứng minh quyết định routing của planner; việc tạo và lưu phiếu hỗ trợ vào CSDL được chứng minh độc lập ở Phase C.
  * **A1, A2 (Hỏi lại trung lập & Xác thực mã):** Ca A1 hỏi lại người dùng một cách trung lập; ca A2 chỉ chấp nhận các mã tài sản đã được xác thực trong ngữ cảnh.
- **Cơ chế kỹ thuật tương thích nhà cung cấp (Gemma Provider Schema Projection):**
  * **Provider Schema Projection:** Loại bỏ từ khóa `pattern` khi thuộc tính cha có định nghĩa `minLength` hoặc `maxLength`; giữ lại 2 pattern ngày tháng (`since`, `until`) khi không có ràng buộc độ dài; loại bỏ `uniqueItems` và mệnh đề `not: { required: [...] }` đơn lẻ; bổ sung `items` vào các nhánh `calls`. Validator chuẩn (`canonical schema`) phía backend và worker vẫn kiểm soát 100% các ràng buộc an toàn.
  * **Provider Ordered Properties:** Thứ tự thuộc tính trong công cụ `assets.summary` được sắp xếp theo đúng ngữ pháp `xgrammar` (`query, asset_code, category, department, holder, statuses, group_by`), khắc phục lỗi không thể sinh thuộc tính đứng trước của engine suy luận.
  * **Grounded Clarify Enum:** Nhánh làm rõ câu hỏi sinh động danh sách enum dựa trên các mã tài sản đã xác thực trong ngữ cảnh (tối đa 300 ký tự UTF-16), giúp ca A2 đạt độ chính xác cao mà không tự đoán mã lạ.

---

### 2.2. Đánh Giá Bộ Test Mở Rộng & Giới Hạn Chất Lượng An Toàn (Extended Suite & Known Limits)

Bộ kiểm thử mở rộng được chạy tách biệt nhằm thăm dò ranh giới chất lượng của mô hình (không làm phương hại kết quả 12/12 của Base Gate):
- **Artifact:** `backend/reports/assistant/gate3l-extended-2026-11-06T115110.675463Z.json` (SHA256 `55c5af6d8e924d63b03222285674590dc2fba4d13e8c220a5673bcae37f8366a`), 12 calls trong 24 giây.
- **Kết quả:** **9 ca strict match, 3 ca mismatch**.
- **Ba giới hạn chất lượng đã ghi nhận rõ (Known Quality Limitations):**
  1. **Ca X1 (Câu hỏi không dấu `"phong minh"`):** Người dùng gõ không dấu nhằm ý nghĩa "phòng mình", mô hình diễn giải thành tên phòng ban riêng biệt và áp dụng quy tắc RBAC dẫn tới phản hồi `RESTRICTED` thay vì đếm tài sản phòng mình.
  2. **Ca X6 (Hỏi tiếp nối trạng thái 1 tài sản):** Sau khi đã xác định một tài sản cụ thể, câu hỏi tiếp nối hỏi trạng thái nhưng mô hình chọn nhầm công cụ `assets.summary` thay vì `assets.search`, dẫn tới phản hồi thông tin không sát.
  3. **Ca X3 (Lỗi chính tả `"nhân sư"`):** Câu hỏi thật là `"Phòng nhân sư có bao nhiu laptop đang trống?"`. Planner giữ department `"nhân sư"`, khác chuỗi chuẩn `HR` của evaluator; backend vẫn resolve đúng phòng và trả tổng số 2 tài sản.
- **Quyết định phát hành:** Root quyết định **KHÔNG tiếp tục can thiệp prompt** để tránh overfit, chấp nhận phát hành phiên bản với các giới hạn chất lượng an toàn đã ghi nhận trung thực.

---

### 2.3. Sổ Ngân Sách & Đo Lường Hạn Mức (Workers AI Budget & Analytics)

- **Số liệu đo lường thực tế từ Cloudflare Analytics API (Cập nhật chính thức lúc 12:26:09 UTC):**
  * Tổng tiêu thụ trong ngày 06/11 UTC: **6.760,0555528998375 Neurons**.
  * Tổng số API requests: **193 requests** (bao gồm **188 lượt suy luận** và **5 lượt bị từ chối ngữ pháp** `grammar_reject` được phân loại riêng).
  * Phân bổ theo mô hình:
    - **Llama (`@cf/meta/llama-3.1-8b-instruct-fp8`):** 119 requests, tiêu thụ **4.789,691974 Neurons**.
    - **Gemma (`@cf/google/gemma-4-26b-a4b-it`):** 74 requests, tiêu thụ **1.970,363579 Neurons** (69 lượt suy luận và 5 grammar reject; trong 69 lượt suy luận có 3 lượt của Acceptance Driver Phase C).
  * Ngân sách kiểm thử còn lại tới trần 8.000 N: **1.239,944447 Neurons**.
  * Hạn mức bảo lưu người dùng: Toàn bộ $\ge 2.000$ Neurons dự phòng cho phiên demo thực tế của người dùng được bảo toàn nguyên vẹn.
  * **Trạng thái hiện tại:** Không phát sinh thêm bất kỳ chi phí AI nào.
- **Phương pháp dự toán chi phí áp dụng cho Gemma (Đã được Root phê duyệt):**
  * Dựa trên số đo thực tế của prompt Gemma: 3118 input tokens cho 17960 bytes request.
  * Công thức dự toán: $3 \times (3118 + \max(0, \text{bytes} - 17960) + 512) \text{ tokens input} + 768 \text{ tokens output kịch trần}$.
  * Đơn giá quy ước dự toán cho Gemma: 9.091 N / 1 triệu input tokens và 27.273 N / 1 triệu output tokens.
  * Dự toán quy ước: ~1.500 Neurons cho bộ base 12 ca; ~1.520 Neurons cho bộ extended 12 ca. Đây là dự toán có dự phòng, không phải giới hạn tiêu thụ được provider bảo đảm.
- **Nhật ký các đợt chạy live lịch sử (Preserved Historical Runs):**
  * Lượt 1 (01:03 UTC): 5 calls (E1–E3 sai tool, F1 sai selector, F2 HTTP 502).
  * Lượt 2 (01:23 UTC): 10 calls (SHA `a41bf089`, 6 ca đạt sau review tay).
  * Lượt 3 (01:48 UTC): 7 calls (SHA `63d8f473`, A2 timeout 30s).
  * Lượt 4 (02:08 UTC): 12 calls (`LIVE_FAILED`, 10 match, 1 mismatch, 1 protocol_error).
  * Lượt 5 (02:25 UTC): 12 calls (SHA `e6befdfc`, 10 match, 1 mismatch, 1 protocol_error).
  * Lượt 6 (05:51 UTC): 12 calls (SHA `fd1f0574`, 6 match, 3 mismatch, 3 protocol_error).
  * Lượt 7 (06:39 UTC): 12 calls (SHA `b8b78c4c`, 6 match, 3 protocol_error, 3 mismatch).
  * Lượt 8 (07:47 UTC): 12 calls (SHA `229d801d`, phát hiện mã lỗi `BLANK_OR_NULL`).
  * Lượt 9 (08:56 UTC): 12 calls (SHA `45d75bd3`, 8 match, 3 protocol_error, 1 mismatch P2).
  * Lượt 10 (10:38 UTC): 12 calls (SHA `69a332ca`, Worker version `597725a4...`, prompt `30f4ac74...`, 4 match, 5 mismatch, 3 protocol_error).
  * **Lượt Nghiệm Thu Chính Thức Gate 3-L (11:47:43 UTC):** 12 calls, **12/12 MATCH**, ROOT APPROVED (artifact `11c0d06f...`, Worker source `8fc35a55...`, prompt `cf7bb677...`).
  * **Lượt Nghiệm Thu Live Acceptance Phase C (12:22:43 UTC):** 3 calls suy luận mới + 1 replay idempotency, **18/18 TIÊU CHÍ PASS** (ledger SHA `ed465cd5...`).

---

### 2.4. Kết Quả Kiểm Thử Backend & Giao Diện Người Dùng (Backend & UI Verification)

- **Bộ regression backend Tuần 5 (Harness PostgreSQL độc lập):**
  * Lệnh chạy: `run_isolated_checks.py --database postgres -- -m pytest backend/tests/assistant/ backend/tests/test_ai_proxy.py -q`. Đây là bộ regression trợ lý và AI proxy, không phải toàn bộ repository.
  * Kết quả: **783 PASS, 9 SKIP trong 98,34 giây** (9 test skip gồm 2 test live eval cần cờ opt-in và 7 test rehearsal D1).
  * Riêng `test_d1_isolation.py` ở chế độ `--database none`: **86 PASS trong 32,37 giây**.
  * Kiểm tra di chuyển CSDL: `makemigrations --check --dry-run` trả về exit code 0 (`No changes detected`).
  * Kiểm tra OpenAPI Schema: `spectacular --validate --fail-on-warn` trả về exit code 0, tạo schema hợp lệ 100.112 bytes tại `/tmp/assethub-week5-final-openapi.yml`.
  * Kiểm thử chống rò rỉ (Negative Mutation Test): Đã cố ý vô hiệu hóa cơ chế bảo vệ và chứng minh bộ test phát hiện chính xác các vi phạm `ENGINE`, `NAME`, `USER`.
- **Frontend UI Integration (Tasks 6 & 7):**
  * Vitest Suite: **194/194 PASS trên 14 files** (commit `961db3b`).
  * Playwright E2E Suite (Mock API cổng 5176): **7/7 PASS trong 7,3 giây** (commit `d7822c8`), kiểm tra luồng chat và cơ chế IME tiếng Việt; test adapter timeout phục hồi lượt đã lưu thuộc Vitest, không đợi 120 giây thật (không gửi tin khi đang gõ tổ hợp ký tự).
  * Bằng chứng hiển thị trực quan (Visual Approval): Root đã kiểm tra trực tiếp ảnh render desktop 1280px và mobile 320px tại `frontend/e2e-screenshots/assistant/`: khi mở trực tiếp tại 320px, sidebar thu thành rail 80px, nút gửi hiển thị và không tràn ngang. Hạn chế khi resize từ desktop được nêu riêng ở mục 2.5.
  * **Ranh giới an toàn & Giới hạn kiểm thử:**
    - Kiểm thử IME qua giao thức Chrome DevTools Protocol (CDP) trên Chromium, không thay thế bàn phím vật lý của hệ điều hành.
    - CSDL D1 hiện tại chỉ chứa 2 tài khoản ADMIN và 3 tài khoản USER (chưa có tài khoản MANAGER); do đó khi người dùng thường gửi yêu cầu hỗ trợ trên môi trường cloud, hệ thống tự động fallback phân phối tới ADMIN để trả lời.

---

### 2.5. Giao Diện Production Đọc API Thật Và Giới Hạn Nghiệm Thu

Root đã duyệt báo cáo `week5-public-ui-readonly-proof-20261106T123209Z.json` (SHA256 `fbff8ffefc86e96752c63c0544218d73a0ea48f4f7e42b2d3b547b631e90454f`). Báo cáo gồm 15 mục: 14 kiểm tra đạt và 1 quan sát về sidebar khi resize. Trình duyệt Chromium mở Pages production, đọc 10 request GET trợ lý thật đều trả 200: ADMIN thấy câu trả lời đã lưu với tổng 2, mở nguồn có phạm vi/thời điểm/bộ lọc; ADMIN và USER đều thấy phản hồi hỗ trợ đã lưu và phiếu ANSWERED version 2. USER không có menu inbox hỗ trợ. Không phát sinh lượt AI hay ghi dữ liệu trong bài browser này.

Chỉ endpoint đăng nhập được giả lập để khởi tạo phiên bằng JWT thật cấp sẵn và profile DTO thật; dữ liệu trợ lý và các GET còn lại đi tới API/D1 thật. Bài này chưa xác minh đăng nhập bằng mật khẩu và không thay thế kiểm thử tay với tài khoản của người dùng. Phản hồi nghiệm thu có nhãn kiểm thử, không phải cam kết chính sách công ty.

Ảnh desktop và mobile được lưu trong thư mục git-ignored `frontend/e2e-screenshots/assistant/` (`public-admin-count-source-1280.png`, `public-admin-ticket-answered-1280.png`, `public-user-saved-reply-320.png`). Mở trực tiếp ở 320px hiển thị được phản hồi, không tràn ngang. Nếu mở ở desktop rồi resize xuống 320px, sidebar vẫn giữ chiều rộng mở; cần bấm thu sidebar. Đây là hạn chế UI đã ghi nhận, chưa sửa trong đợt phát hành này.

Giới hạn còn lại: IME mới được mô phỏng bằng CDP, chưa thử bàn phím IME thật; browser proof dùng Chromium, chưa kiểm Firefox/Safari hoặc trình đọc màn hình. Live hiện không có MANAGER nên luồng trả lời cloud kiểm chứng ADMIN fallback; quyền MANAGER đã có bằng chứng unit/local D1 và UI mock. Bộ mở rộng ở mục 2.2 vẫn có hai lỗi hiểu câu và một khác biệt chuẩn hóa; không tuyên bố mọi cách hỏi đều đạt.

---

## 3. Quy Trình Xác Minh & Chạy Kiểm Thử Ngoại Tuyến (Offline Verification Procedures)

Mọi thao tác kiểm thử phải được thực hiện thông qua runner cô lập (`run_isolated_checks.py`) bằng venv hiện có, không cài thêm, để bảo vệ CSDL sản xuất và tránh rò rỉ dữ liệu hoặc kết nối:

### 3.1. Chạy Kiểm Thử Tích Hợp ChatService (Yêu cầu PostgreSQL Runner)
Bộ kiểm thử `test_chat_service.py` yêu cầu CSDL PostgreSQL được khởi tạo và cô lập bởi test harness:
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database postgres -- -m pytest backend/tests/assistant/test_chat_service.py
```
*Kết quả thực tế:* 14/14 tests PASS (1.31s).

### 3.2. Chạy Diễn Tập Dịch Vụ D1 Local (D1 Rehearsal Run)
Quy trình kích hoạt kiểm chứng diễn tập D1 trên **Real SQLite via Wrangler D1 local bridge with fake AI** thông qua cờ opt-in `--rehearsal` (chạy trên loopback port nội bộ 8787):
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- -m pytest backend/tests/assistant/test_d1_rehearsal.py --rehearsal -v
```
*Trạng thái ghi nhận:*
- **7/7 PASS (65,79s)** ở HEAD `2427612` với `test_d1_rehearsal.py` `cdf264d6` và `d1_fixtures.py` `6cb8ea31`. Codex đã duyệt checkpoint D1 local sau khi kiểm tra hash và dọn dẹp sạch tiến trình/cổng.
- Điều kiện chạy: cổng 8787 phải trống và do chính lượt chạy sở hữu; thiếu điều kiện thì lượt chạy dừng an toàn.

### 3.3. Kiểm Tra Cơ Chế Opt-in Mặc Định Của D1 Rehearsal
Đảm bảo khi không truyền cờ `--rehearsal`, bộ test D1 tự động SKIP an toàn:
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- -m pytest backend/tests/assistant/test_d1_rehearsal.py -q
```
*Kết quả:* 7 tests SKIPPED.

### 3.4. Chạy Bộ Kiểm Thử Cách Ly Mạng & An Toàn D1 (D1 Isolation Regression Suite)
Bộ kiểm thử `test_d1_isolation.py` kiểm chứng ranh giới an toàn và cơ chế bảo vệ tiến trình trước khi thực thi bất kỳ thao tác D1 nào:
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- -m pytest backend/apps/common/tests/test_harness_d1_target.py backend/tests/assistant/test_d1_isolation.py backend/tests/assistant/test_d1_rehearsal.py -q
```
*Đặc tính vận hành:*
- Chạy mặc định dưới chế độ `--database none`, không cần cờ `--rehearsal`. Kết quả: **121 PASS, 15 SKIP (38,21s)**.
- Kiểm chứng chặt chẽ: Chặn Socket mạng ngoài (non-loopback), bảo vệ bridge token qua kiểm tra quyền sở hữu listener trong `/proc`, phát hiện và dừng lại nếu cổng 8787 bị chiếm dụng, và yêu cầu HTTP 200-only readiness probe.

### 3.5. Kiểm Tra & Xác Thực OpenAPI Schema Qua Runner Cô Lập
Đảm bảo tài liệu OpenAPI (drf-spectacular) tuân thủ 100% không có cảnh báo hoặc xung đột enum:
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- backend/manage.py spectacular --file /tmp/schema.yml --validate --fail-on-warn
```
*Kết quả thực tế:* Exit code 0, 0 warning, 0 error.

---

## 4. Thứ Tự Ưu Tiên Công Cụ & Cơ Chế Dự Phòng (Multi-Tool Precedence & Fallbacks)

Khi hệ thống nhận được kết quả từ việc thực thi các công cụ, thứ tự ưu tiên xử lý kết quả tuân thủ nghiêm ngặt theo quy tắc:

$$\text{restricted} > \text{clarify} > \text{unsupported} \rightarrow \text{handoff (ESCALATED)} > \text{ok}$$

1. **`restricted` (Ưu tiên cao nhất):** Người dùng yêu cầu thông tin vượt quyền RBAC hoặc dữ liệu ngoài phạm vi phòng ban. Hệ thống lập tức trả về thông báo từ chối truy cập theo quy định bảo mật, đặt `outcome = RESTRICTED`.
2. **`clarify` (Ưu tiên nhì):** Thiếu tham số hoặc thông tin truy vấn chưa rõ ràng. Hệ thống yêu cầu người dùng làm rõ thêm, đặt `outcome = CLARIFY`.
3. **`unsupported` (Ưu tiên ba - Tự động Handoff):** Khi công cụ ghi nhận câu hỏi hoặc yêu cầu cần hỗ trợ thủ công (ví dụ: câu hỏi về chính sách bồi thường tài sản mất mát chưa có trong tri thức công cụ):
   - Hệ thống tự động chuyển trạng thái lượt hỏi sang `outcome = ESCALATED`.
   - Tự động tạo phiếu hỗ trợ `SupportTicket` liên kết với lượt hỏi bằng UUID5 xác định (idempotent determinism).
   - Nội dung câu trả lời hướng dẫn chuẩn: `"Yêu cầu của bạn đã được chuyển tiếp tới bộ phận hỗ trợ chuyên môn."`.
   - Danh sách căn cứ trích dẫn (`sources`) được đặt về danh sách rỗng `[]` để tránh hiển thị thông tin rác.
   - Cơ chế Replay lũy thừa: Các yêu cầu gửi lại cùng `client_request_id` trả về HTTP 200 và giữ nguyên duy nhất 1 ticket trong CSDL.
4. **`ok` (Ưu tiên thông thường):** Xử lý bình thường, kết xuất dữ liệu căn cứ (`Evidence`), số liệu tổng hợp và trả lời trực tiếp cho người dùng với `outcome = ANSWERED`.

---

## 5. Ranh Giới An Toàn, Kết Quả Phase C & Hoàn Tác (Guardrails, Phase C Realized & Rollback)

### 5.1. Ranh Giới An Toàn Vận Hành (Safety Guardrails)
1. **Bảo Vệ Cổng Loopback 8787:** Chỉ sử dụng cổng 8787 trên loopback interface (`127.0.0.1`). Nếu phát hiện tiến trình lạ chiếm dụng, runner dừng ngay lập tức; tuyệt đối không tự ý kill process lạ.
2. **Bảo Vệ Kho Mã Nguồn & Triển Khai:** Không git push lên nhánh remote (`origin/main`, `origin/dev`), không git merge vào các nhánh chính. Mọi hoạt động triển khai tuân thủ phạm vi người dùng đã phê duyệt.
3. **Bảo Vệ Dữ Liệu Sản Xuất:** Tuyệt đối **KHÔNG DROP BẢNG** trên CSDL D1. Không xóa người dùng, tài sản, hội thoại hay dữ liệu lịch sử khi thực hiện cập nhật hoặc hoàn tác.

### 5.2. Kết Quả Triển Khai Thực Tế Phase C & Hiện Trạng Vận Hành (Phase C Realized & Operational Status)
Giai đoạn triển khai Phase C đã hoàn thành trọn vẹn và được kiểm chứng độc lập bởi Root:
1. **Đồng bộ Metadata Baseline:** Đã thực thi file SQL `deploy/sql/week5_d1_metadata_baseline.sql` (commit `46f1af5`) nạp thành công 25 dòng migration baseline vào D1 cloud bằng cú pháp idempotent `INSERT ... SELECT ... WHERE NOT EXISTS`. Live `MigrationRecorder` xác nhận đủ 25 migration, `migration_plan` = 0.
2. **Kích hoạt Backend:** Đã cập nhật biến môi trường `ASSISTANT_ENABLED=true` và tái khởi động container backend `assethub-demo-backend-1` (giữ nguyên image). Backend healthy và kết nối D1 / Worker ổn định.
3. **Nghiệm Thu Public API Thực Tế (Live API Acceptance Driver):** Thực thi chính xác 1 lần script `driver.py` (SHA256 `7c02b9...`, opt-in `ROOT_AUTHORIZED_MAX4`), đạt **18/18 tiêu chí PASS** (ledger SHA256 `ed465cd5fdb3b5ad74199252bbaf51fc2ab5efdec15e0585caadf4cb3c3b28e4`). Tiêu thụ đúng 3 cuộc gọi AI thực tế + 1 replay idempotency.
4. **Xuất Bản Giao Diện Pages:** Đã xuất bản thành công bản build ON lên Cloudflare Pages production (`assethub --branch main`): Deployment ID **`fcb0df68-fee3-41be-8614-4e7729c53fce`**, URL sản xuất **`https://assethub-97m.pages.dev`**.
5. **Kiểm tra Live Verification:** HTTP fetch trực tiếp xác nhận mã băm bundle JS `assets/index-D2f8d9D0.js` khớp chuẩn xác `1fdf88e1fcb9b6dacb36661373e4f1caac6861296019d958fd719fdc4a017f67`. Toàn bộ các route SPA trả HTTP 200, CORS tunnel bảo đảm an toàn.
6. **Kiểm chứng browser production chỉ đọc:** Đã hoàn tất trên Chromium với UI Pages thật và 10 GET trợ lý thật trả 200. Root đã review bằng chứng và ảnh desktop/320px; không gọi AI và không ghi dữ liệu. Chi tiết xác thực và giới hạn ở mục 2.5.
7. **Kết luận nghiệm thu:** Root chốt Task 8 và bản demo Tuần 5 sau khi kiểm tra Gate cơ sở 12/12, API thật 18/18, bằng chứng giao diện và runbook. Bộ mở rộng vẫn là 9/12; kết luận này không biến các ca mở rộng lỗi thành PASS.

### 5.3. Lệnh Triển Khai Và Tái Lập

Các lệnh dưới đây dành cho một đợt triển khai được giao, chạy từ worktree Tuần 5. Bản demo hiện đã deploy; không cần chạy lại để sử dụng. Trước khi đồng bộ metadata, phải đối chiếu schema và dependency migration thực tế với bản đã duyệt, không đánh dấu 25 migration chỉ vì bảng đã tồn tại.

```bash
cd /home/truong/Downloads/AssetHub-week5
mkdir -p backups
cd cloudflare-worker

# Xuất snapshot mới trước mọi ghi D1; không nạp đè bản dump vào DB đang dùng.
WEEK5_BACKUP_SQL="../backups/week5-predeploy-$(date -u +%Y%m%dT%H%M%SZ).sql"
npm_config_offline=true npx --no-install wrangler d1 export assethub-test-d1 --remote --config wrangler.toml --output "$WEEK5_BACKUP_SQL"
chmod 600 "$WEEK5_BACKUP_SQL"

# Chỉ sau khi snapshot và schema/dependency được xác minh.
npm_config_offline=true npx --no-install wrangler d1 execute assethub-test-d1 --remote --config wrangler.toml --file ../backend/assethub_api/backends/d1/sql/0002_week5_assistant.sql
npm_config_offline=true npx --no-install wrangler d1 execute assethub-test-d1 --remote --config wrangler.toml --file ../deploy/sql/week5_d1_metadata_baseline.sql
npm_config_offline=true npx --no-install wrangler deploy --config wrangler.toml

cd ..
# deploy/.env.demo chứa cấu hình riêng, không commit hoặc in các giá trị bí mật.
docker compose -p assethub-demo -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo up -d --no-build --pull never backend

# Xác nhận tunnel còn hoạt động trước build. Nếu URL đổi, thay URL ở dòng tiếp theo.
VITE_API_BASE_URL="https://construction-evening-consultancy-fantastic.trycloudflare.com" \
VITE_ASSISTANT_ENABLED=true npm --prefix frontend run build

cd cloudflare-worker
npm_config_offline=true npx --no-install wrangler pages deploy ../frontend/dist --project-name assethub --branch main
```

`ASSISTANT_ENABLED` mặc định false trong template. Khi cập nhật, kiểm tra backend ở trạng thái disabled trước; chỉ bật true sau khi schema, Worker và kiểm tra đã đạt. Thay đổi env cần recreate container; `docker compose restart` không nạp lại env. Có thể xác thực CLI bằng `cf auth whoami`; dự án dùng Wrangler cho lệnh deploy và giữ nguyên cấu trúc cấu hình, không chạy `cf init`/`cf migrate`.

Quick Tunnel phụ thuộc máy backend và tiến trình cloudflared đang chạy. Nếu khởi động lại tạo URL khác, cập nhật `DJANGO_ALLOWED_HOSTS`, giữ CORS đúng origin Pages, rồi build và publish frontend với API URL mới. Pages vẫn tải được khi backend mất kết nối nhưng các thao tác API sẽ lỗi.

### 5.4. Hoàn Tác

1. Đặt `ASSISTANT_ENABLED=false` trong `deploy/.env.demo`, rồi recreate đúng service backend để nạp env mới:

   ```bash
   cd /home/truong/Downloads/AssetHub-week5
   docker compose -p assethub-demo -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo up -d --no-build --pull never backend
   ```

   Request trợ lý đã xác thực trả 503 `ASSISTANT_DISABLED`; request chưa xác thực vẫn có thể trả 401. Giữ backend và tunnel hoạt động cho các tính năng khác.

2. Nếu cần hoàn tác mã Worker, dùng đúng version cũ; thao tác này cũng hoàn tác endpoint category về mã của version đó:

   ```bash
   cd /home/truong/Downloads/AssetHub-week5/cloudflare-worker
   npm_config_offline=true npx --no-install wrangler rollback 9c636cf0-3147-4839-8dbb-1dac84185730 --name assethub-ai-worker --config wrangler.toml
   ```

3. Ẩn giao diện trợ lý bằng bản OFF đã chuẩn bị, vẫn trỏ API tunnel mới:

   ```bash
   cd /home/truong/Downloads/AssetHub-week5/cloudflare-worker
   npm_config_offline=true npx --no-install wrangler pages deploy /tmp/assethub-week5-public-off-20261106T120448Z --project-name assethub --branch main
   ```

   Bản OFF này có JS SHA256 `f92c2c7a5193c156c7784ec35b0da343d310ea6cb4924a76ee2e6d833ea6b974`. Nếu thư mục tạm không còn hoặc tunnel đổi, build lại với `VITE_ASSISTANT_ENABLED=false` và API URL đang hoạt động. Deployment Pages cũ trước Tuần 5 trỏ tunnel đã chết, nên không dùng nó để giả định API còn hoạt động.

4. Giữ nguyên các bảng và dữ liệu D1; không DROP, reset hay nạp đè snapshot. Snapshot trước triển khai dành cho quy trình phục hồi có kiểm tra riêng, không phải bước rollback mặc định.
