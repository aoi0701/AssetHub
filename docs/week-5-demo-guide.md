# Hướng Dẫn Vận Hành & Demo Trợ Lý Tài Sản Tuần 5 (Asset Assistant Demo Guide)

Tài liệu hướng dẫn vận hành, tham số cấu hình, kiểm thử và nghiệm thu tính năng **Trợ lý Tài sản (Asset Assistant)** thuộc Tuần 5 của dự án AssetHub. Tính năng áp dụng kiến trúc **Grounded AI Assistant** (Hướng 1: Tool-calling có căn cứ, không sinh SQL tùy ý, bảo toàn phân quyền RBAC và dữ liệu thực tế).

---

## 1. Tham Số Vận Hành & Cấu Hình Môi Trường (Operational Parameters & Configuration)

### 1.1. Cấu hình Backend (Django / DRF)

| Biến môi trường | Kiểu dữ liệu | Giá trị mặc định | Mô tả & Hướng dẫn vận hành |
| :--- | :--- | :--- | :--- |
| `ASSISTANT_ENABLED` | boolean | `false` | Cờ chuyển đổi tính năng (Feature Gate). Mặc định tắt để đảm bảo an toàn. Khi đặt `true`, toàn bộ API routes `/api/assistant/*` và các dịch vụ trợ lý được kích hoạt. Khi `false`, mọi yêu cầu API trả về HTTP 503 `ASSISTANT_DISABLED`. |
| `ASSISTANT_AI_TIMEOUT_SECONDS` | int | `30` | Thời gian timeout (giây) cho cuộc gọi HTTP từ Backend tới Cloudflare Worker AI Gateway (`gateway.py`). |
| `AI_WORKER_URL` | string (URL) | `http://127.0.0.1:8787` (fallback trong `settings.py`) | URL của planner mà `gateway.py` gọi. Fallback này **không phải cấu hình planner hợp lệ**: dù qua được kiểm tra URL của gateway (HTTP chỉ cho loopback), cổng `8787` đang là D1 bridge khi diễn tập, và diễn tập dùng fake AI. Trợ lý phải cấu hình URL planner riêng khi được phép theo Gate 3-L. |
| `AI_SERVICE_TOKEN` | string | Fallback phát triển có sẵn trong `settings.py` | Bearer Token giữa Backend và Worker. **Phải cấu hình token riêng** qua biến môi trường hoặc `.env` cục bộ (nên từ 32 ký tự ngẫu nhiên); gateway từ chối token trống và Worker chỉ nhận token trùng với secret của nó. Tuyệt đối không in hoặc commit khóa bí mật vào mã nguồn hay tài liệu. |
| `ASSISTANT_MODEL` | string | `@cf/meta/llama-3.1-8b-instruct-fp8` | **Không phải setting Django:** đây là biến `vars` của Cloudflare Worker (`env.ASSISTANT_MODEL`), đặt ở cấu hình Worker. Đặt biến này trong env backend không đổi được model. Worker dùng model này cho `plan_question`. |

> [!NOTE]
> **Phân biệt hai cơ chế Timeout độc lập:**
> 1. **`ASSISTANT_AI_TIMEOUT_SECONDS` (30 giây):** Quản lý thời gian chờ phản hồi của HTTP client từ Backend tới Cloudflare Worker AI Gateway.
> 2. **Cơ chế thu hồi lượt hỏi bị treo (Stale PROCESSING Turn Auto-Expiry) (180 giây):** Được thực thi độc lập thông qua so sánh `created_at` kết hợp kỹ thuật CAS (Compare-And-Swap) trong `storage.py` và `services.py`. Khi một lượt hỏi ở trạng thái `PROCESSING` quá 180s, hệ thống tự động chuyển trạng thái sang `ERROR` khi đọc hoặc dọn dẹp, hoàn toàn không phụ thuộc vào `ASSISTANT_AI_TIMEOUT_SECONDS`.

### 1.2. Cấu hình Frontend (React / Vite)

| Biến môi trường | Kiểu dữ liệu | Giá trị mặc định | Mô tả & Hướng dẫn |
| :--- | :--- | :--- | :--- |
| `VITE_ASSISTANT_ENABLED` | boolean | `false` | Bật/tắt giao diện Trợ lý tài sản trên Frontend (nút mở widget chat tại Sidebar và bảng điều khiển hỗ trợ Support Tickets). |

> [!IMPORTANT]
> **Điều Kiện Triển Khai Dự Kiến (Planned Deployment Conditions):**
> Các cấu hình như tham số Gunicorn `--timeout 120`, các cờ môi trường `ASSISTANT_ENABLED=true` / `VITE_ASSISTANT_ENABLED=true`, các tuyến route Frontend, các giao diện người dùng (UI Tasks 6/7) và kiểm thử trình duyệt (Browser Proof / Playwright E2E) là **điều kiện triển khai theo kế hoạch**, hiện tại **chưa được deploy hay kết nối (not yet deployed or wired)** trên môi trường live. UI Tasks 6/7 mới là bản chuẩn bị offline với MSW, **chưa nghiệm thu**. Task 8 **chưa đóng**; ngoài Worker đánh giá riêng của Gate 3-L (đã deploy thật, giữ lại để review), chưa deploy thành phần nào khác của trợ lý.

---

## 2. Trạng Thái Các Cổng Chất Lượng (Status of Quality Gates)

Dự án áp dụng mô hình kiểm soát chất lượng qua từng Gate tuần tự nghiêm ngặt:

| Quality Gate | Nội dung kiểm thử & Tiêu chuẩn nghiệm thu | Trạng thái | Ghi chú & Ranh giới an toàn |
| :--- | :--- | :---: | :--- |
| **Gate 1** | Contract locks (Models, SQL, DTO, Signatures, Error Codes) | **APPROVED & VERIFIED** | Đã khóa schema CSDL, DTO contracts, JSON schema và bộ mã lỗi chuẩn (`CONVERSATION_SCOPE_CHANGED`, `CLIENT_REQUEST_ID_CONFLICT`, `CONVERSATION_BUSY`, `STALE_VERSION_CONFLICT`). |
| **Gate 2** | Local AI Gateway & Mock Worker | **APPROVED & VERIFIED** | Đã kiểm chứng tính tương thích giữa backend `gateway.py` và Cloudflare Worker mock endpoint nội bộ. |
| **Gate 3-L** | Offline Evaluator & Live AI Cloud Evaluation | **LIVE_BLOCKED** (lượt live 1) | Lượt live đầu trên Worker đánh giá riêng `assethub-assistant-eval` (đã deploy thật, giữ lại để review), model `@cf/meta/llama-3.1-8b-instruct-fp8`, prompt ở commit `f611abee`: **5 lần gọi AI thật, 7 ca `not_run`**. E1–E3 chọn sai tool (`assets.search` với `query` thay vì `assets.summary`); F1 sai bộ chọn (`query` thay vì `asset_code`, tool vẫn tìm đúng tài sản); F2 nhận HTTP 502 chung, **nguyên nhân UNKNOWN**. Các mismatch đến từ quyết định của planner, không phải do CSDL hay bảng giá. 12 ca offline vẫn **không chứng minh** LLM hiểu đúng tiếng Việt và Gate 3-L **chưa đạt**. |
| **Gate 4** | Storage & Support Ticket Atomic Engine | **APPROVED & VERIFIED** | Cơ chế đặt chỗ lượt hỏi (`reserve_turn`), xử lý tranh chấp OCC, hết hạn stale turn CAS 180s, và bàn giao tự động SupportTicket nguyên tử đã hoàn tất. |
| **Gate 5** | Authenticated Grounded Chat & Support APIs (51 focused tests đã duyệt) | **APPROVED & COMMITTED** | Đã commit tại `c5fb8fdae3bbc800fbd5f9ae029bd68aca0de265`. Đây là 51 test trọng tâm của Task 5, không phải toàn bộ test suite; đánh giá LLM thật vẫn đang chờ ở Gate 3-L. |
| **Gate 8** | D1 Local Target Proof & Rehearsal | **D1 LOCAL CHECKPOINT APPROVED (Task 8 chưa đóng)** | Lượt opt-in trên **SQLite thật qua Wrangler D1 local với fake AI**: **7/7 PASS (65,79s)** ở HEAD `2427612`, rehearsal `cdf264d6`, fixture `6cb8ea31`; Codex duyệt sau khi kiểm hash và cleanup. Chạy mặc định 3 file: **121 PASS, 15 SKIP (38,21s)**. Phần browser, cấu hình và deploy của Task 8 còn chờ. |

### 2.1. Đánh Giá Hạn Mức & Dự Toán Chi Phí Gate 3-L (Gate 3-L Quota & Budget Preflight)

- **Ảnh chụp Dashboard người dùng:** trước lượt live (06/11/2026 07:50 VN) ghi `"Neurons used today: 0 / 10k"`. Sau lượt live (06/11/2026 08:08 VN): hôm nay **128,28 / 10.000 Neurons**; 24 giờ qua **139,8 Neurons** (gồm 11,52 Neurons của 05/11). Đây là số đọc từ dashboard. Người dùng xác nhận gói **Workers Free**.
- **Đo lường ngoại tuyến (Offline Measurement) trên 12 ca kiểm thử (`planner_eval_cases.json`), prompt cũ trước khi sửa ngữ nghĩa:**
  * Baseline trước rút gọn (Pretty JSON Schema `indent=2`): **132.675 bytes** tổng input (10.847 bytes system prompt/ca).
  * Bản rút gọn (Compact JSON Schema): **76.731 bytes** tổng input (6.185 bytes system prompt/ca; tiết kiệm 55.944 bytes qua 12 ca). Hai bản schema tương đương ngữ nghĩa 100%, chỉ loại bỏ khoảng trắng dư thừa.
  * Bảng giá Cloudflare chính thức cho `@cf/meta/llama-3.1-8b-instruct-fp8`: 13.778 Neurons / 1M input tokens và 26.128 Neurons / 1M output tokens.
  * **Dự toán bảo thủ (Conservative Projection) cho bản compact cũ:** Giả định 1 token / byte UTF-8 + dự phòng 512 tokens chat-template/lượt gọi + kịch trần 768 tokens output/lượt gọi $\to$ dự toán **1.382,65 Neurons** cho toàn bộ 12 ca. Đây là dự báo cho prompt compact cũ trước khi sửa ngữ nghĩa, **không dùng lại làm dự báo cho lượt live mới**.
  * **Ngân sách hiện hành do người dùng cấp (ưu tiên hơn mức 2.000/8.000 trước đây):** tối đa **4.000 Neurons/ngày** cho kiểm thử, giữ **6.000 Neurons** cho demo. Theo ảnh 08:08, phần còn lại cho kiểm thử hôm nay là **3.871,72 Neurons**.
- **Prompt ngữ nghĩa mới (Codex đã review, 53/53 Worker unit tests PASS, `src/assistant.js` sha256 `a5c7714f`):** 12 ca có tổng input **105.943 bytes**; với trần 768 tokens output và dự phòng 512 tokens chat-template mỗi lượt gọi, dự toán **1.785,13 Neurons** cho một lượt 12 ca. Cộng mức nền 128,28 theo ảnh 08:08, tổng trong ngày là **1.913,41 Neurons**, còn **8.086,59 Neurons** trên 10.000. Các số này dựa trên giả định, **không phải số thực tế hay bảo đảm từ nhà cung cấp**, và nằm trong ngân sách 4.000/giữ 6.000.
- **Ranh giới an toàn & Giới hạn cam kết:**
  * Con số 1.382,65 Neurons là dự toán bảo thủ theo công thức giả định, **không phải bảo đảm thực tế từ nhà cung cấp (provider guarantee)**.
  * Trước khi kích hoạt đánh giá live, bắt buộc phải xác nhận gói cước/hạn mức thực tế và nhận được sự đồng thuận rõ ràng từ người dùng.
  * Chính sách chạy: chỉ sau khi bản sửa offline được Codex review mới chạy thêm **một lượt tối đa 12 lần gọi** trong ngân sách 4.000; không tự retry, không chạy lại vô hạn khi lỗi, không coi kết quả mock là pass.
- **Trạng thái thực thi:** Gate 3-L đang **LIVE_BLOCKED**. Lượt live 1 (`f611abee`) giữ nguyên kết quả; chưa khẳng định lỗi F2 đã hết. Prompt ngữ nghĩa mới đã được Codex review offline; người dùng cho phép thêm một lượt tối đa 12 lần gọi sau khi pin SHA, **chưa thực thi**. Gateway diagnostics `741b5401` đã qua unit test (`test_gateway.py` 70/70 PASS): khi Worker trả lỗi HTTP, chỉ đọc body có giới hạn và chỉ ghi mã lỗi thuộc allowlist; không dùng để kết luận nguyên nhân F2 cũ. Artifact lượt live 1 lưu cục bộ tại `backend/reports/assistant/gate3l-2026-11-06T010313.930112Z.json` (bị gitignore, chỉ có trên máy local). UI 6/7, Task 8 và Week 5 vẫn chưa đóng.

---

## 3. Quy Trình Xác Minh & Chạy Kiểm Thử (Verification & Demo Procedures)

Mọi thao tác kiểm thử phải được thực hiện thông qua runner cô lập (`run_isolated_checks.py`) bằng venv hiện có, không cài thêm, để bảo vệ CSDL sản xuất và tránh rò rỉ dữ liệu hoặc kết nối:

### 3.1. Chạy Kiểm Thử Tích Hợp ChatService (Yêu cầu PostgreSQL Runner)
Bộ kiểm thử `test_chat_service.py` yêu cầu CSDL PostgreSQL được khởi tạo và cô lập bởi test harness:
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database postgres -- -m pytest backend/tests/assistant/test_chat_service.py
```
*Kết quả đo đạc thực tế:* 14/14 tests PASS (1.31s).

### 3.2. Chạy Diễn Tập Dịch Vụ D1 Local (D1 Rehearsal Run)
Quy trình kích hoạt kiểm chứng diễn tập D1 trên **Real SQLite via Wrangler D1 local bridge with fake AI** thông qua cờ opt-in `--rehearsal` (chạy trên loopback port nội bộ 8787):
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- -m pytest backend/tests/assistant/test_d1_rehearsal.py --rehearsal -v
```
*Trạng thái ghi nhận:*
- **7/7 PASS (65,79s)** ở HEAD `2427612` với `test_d1_rehearsal.py` `cdf264d6` và `d1_fixtures.py` `6cb8ea31`. Codex duyệt checkpoint D1 local sau khi kiểm hash và cleanup: thư mục D1 của lượt chạy trong `/tmp` đã trống, cổng 8787 trống, không còn tiến trình pytest, Wrangler hay workerd. Đây là checkpoint D1 local, chưa phải nghiệm thu toàn Task 8.
- **Điều kiện chạy:** cổng 8787 phải trống và do chính lượt chạy sở hữu; thiếu điều kiện (cổng bận, thiếu cache Wrangler) thì lượt chạy **FAIL**, không dùng lại tiến trình có sẵn và không tự cài.
- **Giới hạn của các kiểm soát:** `--no-install`, `npm_config_offline=true`, `WRANGLER_SEND_METRICS=false`, `CLOUDFLARE_CF_FETCH_ENABLED=false` chỉ tắt các đường mạng đã biết của tiến trình con Node. Đây không phải sandbox cấp hệ điều hành và không khẳng định tuyệt đối không có mạng.

*Mục tiêu kiểm chứng & Đối chiếu Schema kỳ vọng (Target Schema Structure):*
- **Cấu trúc bảng (Schema Parity):** **Đã khớp 22 bảng app/schema** trên SQLite, gồm cả các bảng kỹ thuật như `_assert_guard`:
  * 19 bảng canonical cơ sở từ `schema.sql` (bao gồm cả `_assert_guard` và `_idempotency_log`).
  * 3 bảng trợ lý mở rộng (additive) từ `0002_week5_assistant.sql`: `assistant_conversation`, `assistant_chatturn`, `assistant_supportticket`.
  * *Ghi chú quan trọng:* **Không tính** các bảng metadata `_cf_*` và `sqlite_*` vào con số 22 này.
- **Nghiệp vụ D1 đã đạt trên D1 local:** rollback khi vi phạm CHECK; unknown commit khi readback cũng lỗi trả HTTP 503 và replay giữ đúng 1 ticket; hai manager trả lời đồng thời qua CAS, bên thua nhận đúng 409 `STALE_VERSION_CONFLICT`; handoff và phân quyền RBAC qua HTTP; từ chối listener lạ; phân phối báo cáo khớp oracle.

### 3.3. Kiểm Tra Cơ Chế Opt-in Mặc Định Của D1 Rehearsal
Đảm bảo khi không truyền cờ `--rehearsal`, bộ test D1 tự động SKIP an toàn (bảo đảm không tác động tới CSDL khi chưa opt-in):
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- -m pytest backend/tests/assistant/test_d1_rehearsal.py -q
```
*Kết quả hiện tại:* 7 tests SKIPPED.

### 3.4. Chạy Bộ Kiểm Thử Cách Ly Mạng & An Toàn D1 (D1 Isolation Regression Suite)
Bộ kiểm thử `test_d1_isolation.py` kiểm chứng ranh giới an toàn và cơ chế bảo vệ tiến trình trước khi thực thi bất kỳ thao tác D1 nào:
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- -m pytest backend/apps/common/tests/test_harness_d1_target.py backend/tests/assistant/test_d1_isolation.py backend/tests/assistant/test_d1_rehearsal.py -q
```
*Đặc tính vận hành & Tiêu chuẩn nghiệm thu:*
- Chạy mặc định dưới chế độ `--database none`, **không cần cờ `--rehearsal`**. Kết quả hiện tại với đúng 3 file trên: **121 PASS, 15 SKIP (38,21s)**; các test bị skip là test rehearsal cần opt-in. Các test này dùng fixture thật với harness giả, nên chứng minh các chốt an toàn, không chứng minh D1.
- **Chặn Socket Mạng Ngoài (Network Socket Block):** Chặn kết nối và tra cứu tên tới địa chỉ non-loopback trong tiến trình Python; chỉ cho phép loopback và Unix socket. Không áp dụng cho tiến trình con Node.
- **Bảo Vệ Bridge Token (Listener Ownership):** Trước mỗi request mang `X-Bridge-Token`, kiểm tra qua `/proc` trên Linux rằng listener thuộc process group của bridge do lượt chạy tạo. Bước kiểm tra và bước kết nối không nguyên tử: điều này chống gửi nhầm tiến trình, không chống kẻ tấn công đổi cổng giữa hai bước.
- **Thiết Lập Môi Trường Ngoại Tuyến (Offline Env Configuration):** Bắt buộc các kiểm soát đã nêu ở mục 3.2 cho tiến trình con Wrangler; đây là kiểm soát các đường đã biết.
- **Phát Hiện Cổng Bị Chiếm Dụng (Port-Occupied Detection):** Kiểm tra cổng 8787 trước khi khởi động; nếu phát hiện tiến trình lạ đang chiếm dụng, dừng an toàn và báo lỗi rõ ràng, tuyệt đối không tự ý kill process lạ.
- **Sẵn Sàng HTTP 200-Only (HTTP 200-Only Readiness Probe):** Cơ chế thăm dò readiness chỉ chấp nhận mã phản hồi HTTP 200 trước khi chấp nhận phục vụ request.

### 3.5. Kiểm Tra & Xác Thực OpenAPI Schema Qua Runner Cô Lập
Đảm bảo tài liệu OpenAPI (drf-spectacular) tuân thủ 100% không có cảnh báo hoặc xung đột enum:
```bash
backend/.venv/bin/python backend/tests/assistant/run_isolated_checks.py --database none -- backend/manage.py spectacular --file /tmp/schema.yml --validate --fail-on-warn
```
*Kết quả đo đạc thực tế:* Exit code 0, 0 warning, 0 error.

---

## 4. Thứ Tự Ưu Tiên Công Cụ & Cơ Chế Dự Phòng (Multi-Tool Precedence & Fallbacks)

Khi hệ thống nhận được kết quả từ việc thực thi các công cụ, thứ tự ưu tiên xử lý kết quả tuân thủ nghiêm ngặt theo quy tắc:

$$\text{restricted} > \text{clarify} > \text{unsupported} \rightarrow \text{handoff (ESCALATED)} > \text{ok}$$

1. **`restricted` (Ưu tiên cao nhất):** Người dùng yêu cầu thông tin vượt quyền RBAC hoặc dữ liệu ngoài phạm vi phòng ban. Hệ thống lập tức trả về thông báo từ chối truy cập theo quy định bảo mật, đặt `outcome = RESTRICTED`.
2. **`clarify` (Ưu tiên nhì):** Thiếu tham số hoặc thông tin truy vấn chưa rõ ràng. Hệ thống yêu cầu người dùng làm rõ thêm, đặt `outcome = CLARIFY`.
3. **`unsupported` (Ưu tiên ba - Tự động Handoff):** Khi công cụ ghi nhận câu hỏi hoặc yêu cầu cần hỗ trợ thủ công (ví dụ: cấp phát tài sản ngoài danh mục):
   - Hệ thống tự động chuyển trạng thái lượt hỏi sang `outcome = ESCALATED`.
   - Tự động tạo phiếu hỗ trợ `SupportTicket` liên kết với lượt hỏi bằng UUID5 xác định (idempotent determinism).
   - Nội dung câu trả lời hướng dẫn bằng tiếng Việt chuẩn: `"Yêu cầu của bạn đã được chuyển tiếp tới bộ phận hỗ trợ chuyên môn."`.
   - Danh sách căn cứ trích dẫn (`sources`) được đặt về danh sách rỗng `[]` để tránh hiển thị thông tin rác.
   - Cơ chế Replay lũy thừa: Các yêu cầu gửi lại cùng `client_request_id` trả về HTTP 200 và giữ nguyên duy nhất 1 ticket trong CSDL.
4. **`ok` (Ưu tiên thông thường):** Xử lý bình thường, kết xuất dữ liệu căn cứ (`Evidence`), số liệu tổng hợp và trả lời trực tiếp cho người dùng với `outcome = ANSWERED`.

---

## 5. Ranh Giới An Toàn & Bảo Vệ Cổng Mạng (Port & Safety Guardrails)

Hệ thống tuân thủ các quy tắc bảo vệ an toàn nghiêm ngặt:

1. **Bảo Vệ Cổng Loopback 8787 (Port 8787 Loopback Guard):**
   - Bộ chạy thử nghiệm chỉ sử dụng cổng 8787 trên loopback interface (`127.0.0.1`).
   - Nếu phát hiện tiến trình lạ đang chiếm dụng cổng 8787, runner phải dừng lại ngay lập tức và báo lỗi rõ ràng.
   - **Tuyệt đối không** tự ý dùng `kill -9` hoặc hủy tiến trình lạ không do runner tạo ra.
2. **Bảo Vệ Kho Mã Nguồn & Triển Khai (Source Control & Deployment Safeguards):**
   - **Không thực hiện git push** lên bất kỳ nhánh remote nào (`origin/main`, `origin/dev`) khi chưa có lệnh rõ ràng từ người dùng.
   - **Không thực hiện git merge** vào các nhánh chính.
   - Mọi hoạt động deploy lên Cloudflare (Workers, D1 Cloud, Pages) chỉ được phép tiến hành sau khi đã hoàn thành các bước kiểm duyệt cục bộ và được người dùng xác nhận trực tiếp.
3. **Hoàn Tác (Rollback):**
   - Tắt `ASSISTANT_ENABLED` và `VITE_ASSISTANT_ENABLED`; API trả HTTP 503 `ASSISTANT_DISABLED` và giao diện ẩn đi. `VITE_ASSISTANT_ENABLED` có hiệu lực khi build, nên frontend cần build và deploy lại.
   - **Giữ nguyên dữ liệu:** không xóa bảng, hội thoại hay phiếu hỗ trợ khi hoàn tác.
