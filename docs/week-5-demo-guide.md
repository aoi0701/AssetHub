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
| **Gate 3-L** | Offline Evaluator & Live AI Cloud Evaluation | **LIVE_BLOCKED** (sau lượt live 3) | Lượt live 1 trên Worker đánh giá riêng `assethub-assistant-eval`, model `@cf/meta/llama-3.1-8b-instruct-fp8`, prompt ở commit `f611abee`: **5 lần gọi AI thật, 7 ca `not_run`** (E1–E3 gọi sai tool, F1 sai selector, F2 HTTP 502 UNKNOWN). Lượt live 2 (prompt `e7a4d746`): **10 lần gọi AI thật (tổng 15)**; sau review tay có **6 ca đạt** (E2, E3, F1, F2, O1, O2); E1 thiếu `AVAILABLE`, A1 lặp câu hỏi, A2 tự đoán mã, P1 HTTP 502 `INVALID_PLAN`. Lượt live 3 (HEAD `0fce0011`, Worker version `c6855093`, prompt SHA `d5b5462b`, artifact SHA `63d8f473`): **7 lần gọi AI thật (tổng 22 / trần 27)**; **6 ca match** (E1, E2, E3, F1, F2, A1) được Claude review tay xác nhận đạt (E1 bắt đúng `AVAILABLE` ra 23/23; A1 làm rõ chuẩn không lặp câu hỏi). Ca A2 gặp lỗi `AI Worker request timed out: The read operation timed out` ở ngưỡng cấu hình 30s; evaluator tự dừng; 5 ca `not_run` (O1, O2, P1, P2, H1). Tuyệt đối **không ghép các ca giữa các lượt chạy** thành pass. Gate 3-L tiếp tục giữ **LIVE_BLOCKED**. |
| **Gate 4** | Storage & Support Ticket Atomic Engine | **APPROVED & VERIFIED** | Cơ chế đặt chỗ lượt hỏi (`reserve_turn`), xử lý tranh chấp OCC, hết hạn stale turn CAS 180s, và bàn giao tự động SupportTicket nguyên tử đã hoàn tất. |
| **Gate 5** | Authenticated Grounded Chat & Support APIs (51 focused tests đã duyệt) | **APPROVED & COMMITTED** | Đã commit tại `c5fb8fdae3bbc800fbd5f9ae029bd68aca0de265`. Đây là 51 test trọng tâm của Task 5, không phải toàn bộ test suite; đánh giá LLM thật vẫn đang chờ ở Gate 3-L. |
| **Gate 8** | D1 Local Target Proof & Rehearsal | **D1 LOCAL CHECKPOINT APPROVED (Task 8 chưa đóng)** | Lượt opt-in trên **SQLite thật qua Wrangler D1 local với fake AI**: **7/7 PASS (65,79s)** ở HEAD `2427612`, rehearsal `cdf264d6`, fixture `6cb8ea31`; Codex duyệt sau khi kiểm hash và cleanup. Chạy mặc định 3 file: **121 PASS, 15 SKIP (38,21s)**. Phần browser, cấu hình và deploy của Task 8 còn chờ. |

### 2.1. Đánh Giá Hạn Mức & Dự Toán Chi Phí Gate 3-L (Gate 3-L Quota & Budget Preflight)

- **Ảnh chụp Dashboard người dùng:**
  * Trước lượt live 1 (06/11/2026 07:50 VN): `"Neurons used today: 0 / 10k"`.
  * Trước lượt live 2 (06/11/2026 08:08 VN): `"Neurons used today: 128,28 / 10k"`.
  * Trước lượt live 3 (06/11/2026 08:45 VN): `"Neurons used today: 462,07 / 10.000"`; đồ thị 24 giờ ghi nhận 473,58 Neurons (gồm 11,52 Neurons của ngày 05/11).
  * Sau lượt live 3: Mức tiêu thụ thực tế là **UNKNOWN** (chưa có ảnh chụp dashboard mới; không gọi management API để tránh lãng phí request). Người dùng xác nhận gói **Workers Free**.
- **Đo lường ngoại tuyến (Offline Measurement) trên 12 ca kiểm thử (`planner_eval_cases.json`):**
  * Baseline ban đầu (Pretty JSON Schema `indent=2`): **132.675 bytes** tổng input (10.847 bytes system prompt/ca).
  * Bản compact cũ (commit `f611abee`): **76.731 bytes** tổng input (6.185 bytes system prompt/ca).
  * Prompt lượt 2 (`e7a4d746`, sha256 `a5c7714f`): **105.943 bytes** tổng input; dự toán **1.785,13 Neurons** cho 12 ca.
  * Prompt lượt 3 (JSON mode + semantic clarity, sha256 `08f383b3`): **118.951 bytes** tổng input (system prompt 9.802 bytes); dự toán **1.964,35 Neurons** cho 12 ca (giả định 1 token / byte UTF-8 + dự phòng 512 tokens chat-template/lượt gọi + kịch trần 768 tokens output/lượt gọi; giá FP8: 13.778 neurons/1 triệu input tokens và 26.128 neurons/1 triệu output tokens).
- **Sổ ngân sách bảo thủ ngày 06/11/2026:**
  * Mức tiêu thụ thực tế ghi nhận trước lượt 3 (ảnh 08:45 VN): **462,07 Neurons**.
  * Dự toán một lượt 12 ca cho prompt lượt 3: **1.964,35 Neurons**.
  * **Tổng dự toán ngày:** $462,07 + 1.964,35 = \mathbf{2.426,42\text{ Neurons}}$ (đây là dự toán bảo thủ, không phải số thực tế hay bảo đảm; mức trần kiểm thử là 4.000 Neurons/ngày, phần còn lại trên lý thuyết là 1.573,58 Neurons).
  * **Dự phòng demo còn lại:** $10.000 - 2.426,42 = \mathbf{7.573,58\text{ Neurons}}$ (đáp ứng trên lý thuyết mức tối thiểu $\ge 6.000$ Neurons dành cho demo; đây là dự toán bảo thủ, không phải số thực tế hay bảo đảm từ nhà cung cấp).
  * Các con số trên là dự toán theo công thức bảo thủ, **không phải số thực tế hay bảo đảm từ nhà cung cấp (provider guarantee)**.
- **Ranh giới an toàn & Giới hạn cam kết:**
  * Bắt buộc giữ trần kiểm thử $\le 4.000$ Neurons/ngày và bảo toàn tối thiểu $\ge 6.000$ Neurons cho phiên demo thực tế.
  * Mỗi lượt đánh giá tối đa $\le 12$ cuộc gọi mới; tuyệt đối không retry tự động, không lặp lại suite vô hạn khi gặp lỗi.
  * Tuyệt đối không ghép các ca thành công giữa các lượt chạy khác nhau thành pass (zero cherry-picking).
- **Trạng thái thực thi sau Lượt live 3:**
  * Gate 3-L tiếp tục ở trạng thái **`LIVE_BLOCKED`**; Task 8 chưa đóng và chưa đạt Gate PASS.
  * Cả 3 artifact đánh giá được lưu trữ đầy đủ cục bộ (bị gitignore, chỉ có trên máy local):
    - Lượt 1: `backend/reports/assistant/gate3l-2026-11-06T010313.930112Z.json` (5 calls: E1–E3 sai tool, F1 sai selector, F2 HTTP 502 UNKNOWN, 7 not_run).
    - Lượt 2: `backend/reports/assistant/gate3l-2026-11-06T012339.014183Z.json` (SHA `a41bf089`; 10 calls: 6 ca đạt sau review tay, E1 thiếu `AVAILABLE`, A1 lặp câu hỏi, A2 tự đoán mã, P1 HTTP 502 `INVALID_PLAN`, 2 not_run).
    - Lượt 3: `backend/reports/assistant/gate3l-2026-11-06T014804.986343Z.json` (SHA `63d8f4739fcc7314148ec1e11a9c555cec8061e1ae3f2d3e4501f8abc073f739`; 7 calls, tổng lũy kế cả 3 lượt là **22 calls / trần 27**; 6 ca match gồm E1, E2, E3, F1, F2, A1 được Claude review tay xác nhận đạt; ca A2 timeout; 5 ca not_run).
  * **Phân tích kỹ thuật sự cố Timeout tại ca A2 (Lượt 3):**
    - Cấu hình timeout thực tế tại Gateway: `ASSISTANT_AI_TIMEOUT_SECONDS=30` (30 giây) theo đúng đặc tả và cài đặt trong `backend/assethub_api/settings.py:188` và `backend/apps/assistant/gateway.py:150`.
    - Lỗi ghi nhận: gateway báo read timeout (`"AI Worker request timed out: The read operation timed out"`), không có transport trace.
    - **Thời gian thực thi từng ca (per-case latency):** **UNKNOWN / Chưa đo được**, do evaluator tại thời điểm chạy lượt 3 và artifact lượt 3 chưa ghi nhận thời gian phản hồi từng ca; tổng thời gian Pytest suite 49.11s bao gồm cả setup fixture, CSDL, tool execution và teardown; urllib socket timeout không phải deadline end-to-end.
    - **Nguyên nhân gốc của timeout:** **Chưa rõ (UNKNOWN)** do thiếu nhật ký kết nối tầng mạng và nhật ký xử lý máy chủ Cloudflare (server-side logs); không khẳng định lỗi mạng hay provider outage khi chưa có bằng chứng cụ thể.
    - Đo lường thời gian (per-case timing) đã được commit tại HEAD `06ed8e1` trên 2 file do Claude sở hữu (`planner_eval.py` và `test_planner_eval.py`); Codex đã kiểm chứng 151/151 tests PASS (7,16s) ở chế độ offline. Các artifact cũ (Lượt 1, 2, 3) không có thông tin latency và được giữ nguyên vẹn, không chỉnh sửa.
  * Worker bản cuối (`08f383b3`) đã bật `response_format: { type: 'json_object' }` và mã lỗi chẩn đoán tĩnh (`reason_code`); trang tài liệu Cloudflare ghi nhận `@cf/meta/llama-3.1-8b-instruct-fp8` có `Planned Deprecation: 11/15/2026` (ghi nhận làm phụ thuộc triển khai tương lai, chưa đổi model).
  * UI 6/7, Task 8 và Week 5 vẫn chưa đóng. Chưa deploy ứng dụng chính, chưa bật cờ môi trường.

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
