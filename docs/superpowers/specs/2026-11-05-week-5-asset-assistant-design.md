# Tuần 5 — Trợ lý hỏi đáp tài sản AssetHub

**Ngày:** 2026-11-05

**Trạng thái:** Bản yêu cầu và thiết kế kèm kế hoạch, chờ người dùng xem xét; chưa cho phép triển khai.

**Phương án đã chọn:** Hướng 1 — LLM hiểu câu hỏi và chọn công cụ; Django tạo và thực thi truy vấn.

**Kế hoạch:** [2026-11-05-week-5-asset-assistant.md](../plans/2026-11-05-week-5-asset-assistant.md)

## 1. Ý định và nguồn yêu cầu

Thêm trợ lý hỏi đáp trong phạm vi **quản lý tài sản AssetHub**, dựa trên mẫu thầy cung cấp tại `https://olp.hutech.online:8443/#/hoi`. Mẫu là tài liệu tham khảo về hành vi hỏi đáp có căn cứ và chuyển người phụ trách; không phải dịch vụ AI hoặc nơi nộp bài được xác nhận.

Những điểm người dùng đã xác nhận:

- Hỏi bằng tiếng Việt tự nhiên; tra CSDL thật để trả lời về tài sản, số lượng, trạng thái, người giữ và lịch sử.
- Phạm vi là nghiệp vụ tài sản nói chung. Các ví dụ hội thoại không phải danh sách câu hỏi cố định.
- Trả lời có căn cứ; câu thiếu thông tin có thể hỏi lại, câu chưa đủ căn cứ được chuyển người phụ trách.
- Chuyển theo phòng ban của người đang đăng nhập đến MANAGER phòng ban; thiếu phòng ban hoặc người phụ trách thì chuyển ADMIN.
- Người phụ trách phản hồi và người hỏi theo dõi được câu trả lời.
- Chọn Hướng 1; tuần 4 đang được làm ở phiên khác. Phiên hiện tại chỉ lập tài liệu tuần 5.

Các quyết định kỹ thuật và giới hạn bên dưới là **đề xuất của người lập kế hoạch**, cần được xem cùng plan; không tự coi là yêu cầu riêng do thầy giao.

## 2. Mục tiêu, giới hạn và điều kiện thành công

### Có trong tuần 5

1. Trang **Hỏi đáp tài sản** cho ADMIN, MANAGER, USER đã đăng nhập; lưu và mở lại hội thoại của mình.
2. Tra cứu, thống kê tài sản; tìm người giữ hiện tại; đọc lịch sử theo quyền; tra phiếu tài sản và hướng dẫn quy trình.
3. Hiểu nhiều cách diễn đạt và câu nối tiếp như “máy đó ai giữ?”, trong giới hạn công cụ được công bố.
4. Căn cứ do backend tạo: bản ghi, kết quả thống kê hoặc hướng dẫn có phiên bản, kèm thời điểm và phạm vi tra cứu.
5. Phiếu hỗ trợ thực sự được lưu, phân luồng, phản hồi và hiển thị trạng thái.
6. Chạy được trên PostgreSQL dùng để phát triển/kiểm thử và D1 của nền triển khai tuần 4.

### Ngoài phạm vi lần đầu

- SQL do AI sinh; thao tác cấp phát/thu hồi/sửa/xóa tài sản bằng chat; huấn luyện mô hình.
- Vector database, embedding, tải tài liệu tùy ý, web search, giọng nói, streaming, thông báo email/Zalo.
- Thêm hạn trả tài sản chỉ vì câu ví dụ “khi nào họ trả”. Dữ liệu chưa tồn tại phải được nói rõ, không suy đoán.
- Thay kiến trúc, nâng cấp thư viện, thay dữ liệu, tên miền hoặc tài nguyên đang làm trong tuần 4.

Không đạt nếu câu trả lời bịa số liệu/nguồn, đọc vượt quyền, lấy độ dài trang làm tổng số, hoặc báo “đã chuyển” khi phiếu chưa được lưu chắc chắn. Kết quả rỗng/đếm bằng 0 là một kết quả hợp lệ, không mặc nhiên là thiếu căn cứ.

## 3. Approaches

| Phương án | Đánh đổi | Kết luận |
|---|---|---|
| LLM chọn công cụ có cấu trúc; backend thực thi truy vấn bằng code | Cần thiết kế bộ công cụ đủ rộng; dễ kiểm tra số liệu và áp dụng phân quyền hiện có | **Người dùng chọn** vì dễ triển khai và kiểm soát độ chính xác |
| LLM sinh SQL; backend kiểm tra trước khi chạy | Có thể diễn đạt thêm tổ hợp truy vấn; kiểm soát phạm vi dữ liệu và tính đúng khó hơn | Không chọn cho tuần 5 |

Hướng 1 vẫn hỗ trợ hỏi tự nhiên. Không viết chuỗi `if question == ...` cho từng câu mẫu. Các công cụ tìm kiếm, lọc, tổng hợp, lịch sử và hướng dẫn phải có tham số kết hợp được.

## 4. Nền hiện có và phụ thuộc tuần 4

- React JSX, React Router, Tailwind; JWT nằm trong bộ nhớ qua `frontend/src/api/axiosClient.js` và `AuthContext`.
- Django 5.2.18 + DRF, `CustomJWTAuthentication`, ba vai trò và phòng ban của user.
- `Asset` có `category`, `status`, `department`, `assigned_to_user`; `AssetHistoryLog` lưu các sự kiện. Chưa có trường hạn trả riêng.
- USER xem tài sản đang giữ; MANAGER xem phòng ban mình; ADMIN xem toàn hệ thống. Lịch sử cá nhân dùng trường an toàn trong `apps/assets/history_api.py`.
- Worker hiện tại chỉ có `/api/suggest-category`, backend AI hiện tại chỉ cho ADMIN. Hỏi đáp phải có endpoint và kiểm tra quyền riêng, không nới quyền endpoint cũ.
- D1 qua adapter/bridge, multiwrite cần `d1_batch` và kiểm tra OCC; không dựa vào interactive transaction của PostgreSQL khi chạy D1.

Trước khi thực thi plan: chốt một commit nền sau tuần 4, đọc lại diff và cấu hình thực tế. Hoàn tất triển khai tuần 4 và xác nhận đường API/D1/Workers AI trước nghiệm thu cloud tuần 5. Có thể kiểm thử tuần 5 bằng mock/local trước; mock không được trình bày là bằng chứng chất lượng LLM thật.

### 4.1. PostgreSQL kiểm thử có vòng đời riêng

Tests PostgreSQL, kiểm tra migrations/OpenAPI và đánh giá planner có dùng DB phải chạy trong instance do lượt kiểm thử sở hữu; không trỏ vào PostgreSQL đang phục vụ ứng dụng. Port 5433 hoặc 5434 không chứng minh một instance là DB test.

Tái dùng `PGSourceHarness` trong `backend/apps/common/tests/harness_pg_source.py`, qua runner mỏng của tuần 5 được mô tả trong plan. Mỗi lượt có run ID, tên DB, user/password ngẫu nhiên và port loopback trống riêng. Chỉ gọi `start(recreate=False)`; không gọi `setup()` hoặc context manager của harness vì chúng chạy migration/seed bộ diễn tập tuần 4. Pytest-django tạo/migrate test DB riêng trên instance mới, rồi các fixture tuần 5 seed dữ liệu của mình.

Runner xác minh nhãn `assethub.run_id`, `assethub.owner=rehearsal`, container và port mapping trước khi truyền đầy đủ DB_* và TEST_DB_NAME cho child process; không dựa vào `.env` đang dùng. Mặc định vô hiệu hóa D1/R2/AI cloud. Image PostgreSQL phải có sẵn; thiếu image hoặc port bị chiếm thì dừng, không tự pull image hoặc dùng lại listener. Trong `finally`, đóng/đợi child rồi `teardown()` đúng harness; nhãn không khớp thì giữ tài nguyên và báo lỗi, không bulk cleanup hay dừng container tuần 4. Runner và các bài kiểm chứng vòng đời chỉ là công việc tương lai, không được tạo/chạy trong phiên sửa tài liệu này.

Nếu Docker tạo anonymous volume cho container mới, runner ghi lại ID/provenance trong lượt đó và chỉ dọn đúng volume đã chứng minh do container sở hữu tạo, không còn nơi khác sử dụng. Harness hiện tại chỉ xóa container; không được suy ra volume đã được dọn. Không chứng minh được ownership thì giữ và báo cleanup chưa hoàn tất.

### 4.2. Phương án AGY song song có điều kiện

Giữ 8 task của plan. Chỉ mở các nhánh triển khai sau khi chốt nền tuần 4, model/migration và contract dùng chung (schema, DTO, chữ ký công cụ, quyền và fixtures). Khi đó Task 2 (tools), Task 3 (planner/gateway) và Task 4 (storage/support) có thể giao ba worker riêng. Task 5 chờ cả ba; Task 6–7 hoàn thiện sau API và gate đánh giá LLM sớm; Task 8 tích hợp chứng cứ cuối.

Một coordinator là người duy nhất tích hợp settings, routes backend/Worker/frontend, Sidebar, API client, schema contract gốc và fixtures dùng chung. Workers chỉ sửa file được phân quyền trong bản đồ ownership của plan; thay đổi contract phải quay về coordinator để đồng bộ trước khi tiếp tục. Tối đa ba worker cùng coordinator, không giao đồng thời cả 8 task. Đây là phương án cho AGY về sau, chưa chọn thay cho Native và không cho phép gọi subagent trong phiên này.

## 5. Ràng buộc chung

- Chỉ viết tài liệu trong phiên lập kế hoạch; không sửa code/cấu hình tuần 4, cài thư viện, khởi động lại dịch vụ hoặc deploy cloud.
- Giữ Django 5.2.18, DRF, React JSX và các phiên bản đã khóa; không thêm dependency cho bản đầu.
- CSDL nghiệp vụ khi triển khai là D1; PostgreSQL tiếp tục dùng cho phát triển/đối chiếu theo nền tuần 4.
- Xác thực dùng JWT hiện có; không đưa token hoặc hội thoại vào localStorage/sessionStorage.
- USER chỉ xem tài sản đang giữ và lịch sử cá nhân đã được lọc; MANAGER chỉ xem phòng ban mình; ADMIN xem toàn hệ thống.
- AI chỉ chọn công cụ và tham số; backend kiểm tra schema, phân quyền, thực thi và tạo căn cứ. Không thực thi SQL/code/URL từ đầu ra mô hình.
- Không thay đổi quyền hoặc hành vi `/api/ai/suggest-category/`.
- Không tự bật tính năng trả phí hoặc tạo/deploy tài nguyên Cloudflare; nghiệm thu cloud chỉ khi người dùng yêu cầu ở bước thực thi.
- Không tự bật skill khác, phân công agent hoặc thực thi plan; phương thức thực thi do người dùng chọn sau khi xem tài liệu.
- Phiên cập nhật tài liệu này chỉ sửa hai file spec/plan hiện có; không gọi subagent, sửa code/cấu hình, cài đặt, chạy test, gọi AI, deploy, commit hoặc push.

## 6. Kiến trúc và hợp đồng công cụ

Luồng: React gửi câu hỏi → Django xác thực, tạo lượt hỏi → Worker phân tích câu hỏi → Django kiểm tra quyết định và gọi công cụ → CSDL trả dữ liệu → Django dựng câu trả lời/căn cứ, lưu lượt hỏi → React hiển thị.

Đề xuất bản đầu dùng **một lời gọi LLM để lập yêu cầu tra cứu**. Số liệu, tên người, trạng thái và nguồn được backend diễn đạt từ kết quả có cấu trúc, không nhờ LLM tự tạo thêm sự kiện. Điều này vẫn dùng LLM để hiểu tiếng Việt/chọn công cụ; không cam kết mọi câu tự nhiên đều được hiểu đúng. Có thể mở rộng cách diễn đạt sau khi bản nền được nghiệm thu.

`PlannerDecision` có đúng các khóa `kind`, `calls`, `clarification`, `reason`:

- `kind`: `tools | clarify | handoff | out_of_scope`.
- `calls`: tối đa 3 phần tử `{name, arguments}`; không được có khi kind khác `tools`.
- `clarification`: chuỗi tối đa 300 ký tự, chỉ có nội dung khi kind là `clarify`.
- `reason`: `missing_knowledge | unsupported_asset_question | null`; chỉ có nội dung khi `handoff`.
- `additionalProperties: false` ở mọi object; không tin vào schema/output từ Worker, backend xác thực lại bằng `jsonschema` đã có trong requirements.

| Công cụ | Tham số được phép | Kết quả |
|---|---|---|
| `assets.search` | `query`, `asset_code`, `category`, `statuses`, `department`, `holder`, `limit` | Danh sách/chi tiết gồm mã, tên, danh mục, trạng thái, phòng ban, người giữ được phép xem; `total`, `has_more` |
| `assets.summary` | Các bộ lọc trên trừ `limit`; thêm `group_by: null/status/category/department` | Tổng trên **toàn bộ tập đã lọc**, nhóm nếu có, tối đa 20 nhóm có cờ giới hạn |
| `assets.history` | `asset_code` hoặc `mine: true`; `actions`, `since`, `until`, `limit` | Sự kiện hợp lệ theo quyền; ngày ISO 8601 và trạng thái, không suy ra hạn trả |
| `requests.search` | `query`, `request_type`, `status`, `limit` | Phiếu của mình/phòng ban/toàn hệ thống theo vai trò |
| `procedures.lookup` | `topic: allocate/return/damage_report/roles/asset_statuses` | Nội dung hướng dẫn đã đối chiếu nghiệp vụ, version, hành động điều hướng theo vai trò |

Chuỗi lọc tối đa 100 ký tự; `asset_code` tối đa 50; `limit` là integer 1..20, không nhận boolean. `statuses` chỉ nhận sáu giá trị hiện có: `AVAILABLE`, `IN_USE`, `DAMAGED_PENDING_REPAIR`, `UNDER_MAINTENANCE`, `PENDING_DISPOSAL`, `DISPOSED`. `actions` lấy từ `AssetHistoryLog.ActionType`. `since/until` phải hợp lệ, có timezone hoặc là ngày UTC; `since <= until`. Không truyền tên trường ORM, toán tử tùy ý hoặc SQL.

Backend áp phạm vi **trước** bộ lọc, giải tên/mã trong phạm vi đó. Không tự chọn bản ghi đầu khi tên trùng; yêu cầu làm rõ bằng các lựa chọn nhìn thấy được. USER yêu cầu thống kê phòng ban hoặc người khác phải được giải thích giới hạn quyền, không lấy số cá nhân rồi gọi là tổng phòng ban. Mọi số đếm phải đi kèm nhãn phạm vi do backend đặt.

`ToolResult = {tool, status, data, sources, scope_label, observed_at}`; `status` là `ok | clarify | restricted | unsupported`. Tên nhân viên chỉ lấy các trường hiển thị cần thiết; không đưa password, email, phone, token, thông số kết nối vào kết quả. Nội dung ghi chú tài sản là dữ liệu, không phải lệnh cho mô hình.

`Evidence = {id, kind, label, href, observed_at, scope_label, facts}`; `kind` là `asset | aggregate | history | request | procedure`. Backend tạo toàn bộ trường, URL là đường nội bộ trong allowlist. USER dẫn về `/my-assets`, `/my-history`, `/my-requests`; ADMIN/MANAGER có thể dẫn về `/assets/{id}` hoặc danh sách phù hợp. Nguồn thống kê mở phần chi tiết gồm bộ lọc, tổng số, thời điểm; hướng dẫn mở nội dung procedure có version. Không lấy link/nguồn từ mô hình.

## 7. Worker và giới hạn vận hành đề xuất

- Thêm `POST /api/assistant/plan` vào AI Worker hiện có; dùng `AI_SERVICE_TOKEN` server-to-server. Không truy vấn `env.DB` hoặc nhận JWT người dùng ở endpoint này.
- Tận dụng `@cf/meta/llama-3.1-8b-instruct-fp8`; có `ASSISTANT_MODEL` riêng để không thay mô hình gợi ý danh mục. Output JSON được parse chặt dù provider không bảo đảm JSON schema.
- Contract nguồn: `backend/apps/assistant/plan.schema.json`; Worker đóng gói bản sao `cloudflare-worker/src/assistant.schema.json`, có test so sánh JSON để chống lệch hai deployment.
- Backend gửi câu hỏi, tối đa 6 lượt ngữ cảnh đã lọc, metadata tham chiếu còn quyền xem; không gửi toàn bộ CSDL/schema CSDL hoặc các kết quả cũ làm số liệu hiện tại.
- `scope` do backend dựng gồm role, nhãn phòng ban, `now` theo UTC và `display_timezone="Asia/Ho_Chi_Minh"` để hiểu “hôm nay/tháng này”. CSDL lưu UTC; giao diện hiển thị ngày giờ Việt Nam. Đây là metadata, không phải điều kiện phân quyền do mô hình quyết định.
- Một câu tối đa 1.000 ký tự; ngữ cảnh tối đa 6.000 ký tự; body Worker tối đa 32 KiB tính theo UTF-8; output tối đa 64 KiB; `max_tokens=768`, `temperature=0.1`, không streaming.
- `ASSISTANT_ENABLED=False`, `ASSISTANT_AI_TIMEOUT_SECONDS=30`; rate limit endpoint hỏi 10 lượt mới/phút/user. Rate limit DRF là bảo vệ ứng dụng, không cam kết quota tuyệt đối đa tiến trình.
- Feature frontend `VITE_ASSISTANT_ENABLED=false` mặc định; bật ở môi trường triển khai sau migration. Khi backend tắt, trả 503 `ASSISTANT_DISABLED` trước khi chạm bảng mới.
- Không retry suy luận tự động. Mô hình lỗi/quota/JSON sai → trạng thái lỗi rõ, không thay bằng kết quả bịa hoặc số 0. Người dùng có thể chủ động gửi hỗ trợ.

## 8. Dữ liệu lưu và tính nhất quán

Ba model trong app mới `apps.assistant`; PK UUID, thời gian UTC:

| Model | Trường quan trọng |
|---|---|
| `Conversation` | `id`, `owner` FK User, `role_snapshot`, `department_id_snapshot` integer nullable, `created_at`, `updated_at` |
| `ChatTurn` | `id`, `conversation`, `client_request_id` UUID, `question` (1..1000), `outcome`, `answer` (tối đa 6000), `sources` JSON list, `created_at`, `updated_at` |
| `SupportTicket` | `id`, `turn` OneToOne, `target_kind` DEPARTMENT/ADMIN, `target_department` nullable, `question_snapshot`, `status` OPEN/ANSWERED, `version` mặc định 1, `reply` tối đa 4000, `replied_by`, `replied_at`, `reply_request_id` UUID nullable, timestamps |

`ChatTurn.outcome`: `PROCESSING | ANSWERED | CLARIFY | ESCALATED | RESTRICTED | OUT_OF_SCOPE | ERROR`. Unique `(conversation, client_request_id)`; partial unique một `PROCESSING`/conversation. Một hội thoại chỉ xử lý một lượt tại một thời điểm.

Gửi lại cùng request ID và cùng question trả lượt đã lưu (202 nếu còn PROCESSING, 200 nếu xong), không gọi AI hay tạo ticket lần nữa. Dùng lại ID với question khác trả 409. PROCESSING quá 180 giây được CAS sang ERROR; worker cũ không được ghi đè sau đó. Người dùng muốn hỏi lại sau lỗi tạo request ID mới.

PostgreSQL dùng transaction + lock cho finalize nhiều bảng. D1 dùng batch gồm update lượt có điều kiện, `_assert_guard`, insert ticket nếu cần; ticket ID ổn định theo turn, idempotency key ổn định. Không dựa vào interactive `transaction.atomic()` trên D1. Khi kết quả commit chưa rõ, readback theo turn/ticket; không đủ bằng chứng thì trả lỗi, không báo “đã chuyển”.

Migration PostgreSQL của app mới và SQL D1 **bổ sung riêng** `backend/assethub_api/backends/d1/sql/0002_week5_assistant.sql` phải cùng schema. Không viết lại ba bản schema nền tuần 4 hoặc chạy DROP/reset. SQL mới re-entrant, giữ dữ liệu hiện có, có FK/index/unique/check tương đương. Việc đồng bộ metadata migration trên D1 theo runbook tuần 4; không chạy Django migrate schema lên D1 một cách mù quáng.

## 9. Hội thoại, làm rõ và chuyển người phụ trách

- Câu mơ hồ: hỏi lại; các lựa chọn chỉ chứa bản ghi được phép xem.
- Không có kết quả: trả rõ “không tìm thấy trong phạm vi được phép xem”; tổng 0 vẫn ANSWERED.
- Câu liên quan tài sản nhưng dữ liệu/quy trình chưa đủ, hoặc khả năng công cụ chưa hỗ trợ: tạo ticket và trả ESCALATED sau khi lưu thành công.
- Ngoài chủ đề: hướng dẫn về phạm vi tài sản; không tự tạo ticket.
- Từ chối do quyền: RESTRICTED; không tự chuyển cấp để lấy dữ liệu vượt quyền.
- Lỗi hạ tầng: ERROR; có nút gửi hỗ trợ thủ công. Handoff thủ công idempotent, không nhận người nhận từ client/LLM.

Routing lấy **phòng ban hiện tại của người hỏi ở lúc tạo ticket**. Có ít nhất một MANAGER active trong phòng ban, khác người hỏi → DEPARTMENT; nếu không → ADMIN. Nhiều manager dùng hộp thư chung của phòng, không chọn người đầu tiên. ADMIN xem được toàn bộ ticket và xử lý dự phòng; MANAGER chỉ ticket target phòng mình; người hỏi chỉ ticket của mình. Định tuyến đã lưu không tự thay khi nhân sự đổi; ADMIN vẫn tiếp nhận được ticket cũ.

Ticket chỉ cung cấp câu hỏi và tối đa 2 câu hỏi trước đó của chính người hỏi để làm rõ tham chiếu, tối đa 3000 ký tự trong `question_snapshot`; không cấp quyền đọc toàn bộ hội thoại hoặc các câu trả lời AI cho người nhận. Câu trả lời của cán bộ được ghi nhãn là **phản hồi người phụ trách**, không biến thành tri thức dùng chung cho người khác.

Reply một lần cho ticket OPEN, update nguyên tử với `version`, đổi sang ANSWERED và tăng version. Hai manager trả lời đồng thời: một thành công, một 409; không mất câu trả lời trước. Replay cùng `reply_request_id`, cùng actor và nội dung trả 200; dùng lại ID khác nội dung hoặc ghi đè trả 409.

Mỗi request xác thực lại quyền hiện tại. Người khác đoán UUID hội thoại/turn/ticket không được đọc (404 cho đối tượng ngoài phạm vi). Owner đổi role/phòng ban thì hội thoại cũ không được dùng hoặc trả nội dung cũ qua API (409 `CONVERSATION_SCOPE_CHANGED`); tạo hội thoại mới. Ngữ cảnh câu nối tiếp phải xác minh lại quyền xem các asset refs, tra lại dữ liệu thay vì dùng snapshot cũ như dữ liệu hiện tại.

## 10. API và giao diện

Tất cả API dưới `/api/assistant/`, JWT hiện có; response list theo `StandardPagination` nhưng endpoint assistant **không hỗ trợ** `all=true`/`pagination=false`.

| Endpoint | Hợp đồng |
|---|---|
| `POST conversations/` | Body `{}` → 201 `{id, created_at}`; backend lấy owner và snapshot |
| `GET conversations/` | Các hội thoại owner, không trả nội dung khi snapshot không khớp quyền hiện tại |
| `GET conversations/{uuid}/turns/` | Lượt hỏi cũ, ticket lồng nhỏ gọn và phản hồi nếu có |
| `POST conversations/{uuid}/turns/` | `{client_request_id: uuid, question: string}` → 201 lượt mới hoàn tất, 200 replay, 202 đang xử lý; 409 nếu xung đột |
| `GET turns/{uuid}/` | Owner xem trạng thái/kết quả; dùng để phục hồi sau lỗi kết nối |
| `POST turns/{uuid}/escalate/` | `{}` → 201 ticket mới hoặc 200 ticket có sẵn; không nhận route từ client |
| `GET procedures/{topic}/` | Nội dung hướng dẫn có phiên bản và link theo quyền |
| `GET support-tickets/?view=mine|inbox` | Mặc định mine; inbox chỉ ADMIN/MANAGER, lọc đúng target; optional `status=OPEN|ANSWERED` |
| `GET support-tickets/{uuid}/` | Owner hợp lệ hoặc staff đúng phạm vi; không trả hội thoại đầy đủ |
| `POST support-tickets/{uuid}/reply/` | `{client_request_id: uuid, expected_version: int, reply: string}` → 200; xung đột 409 |

Turn JSON: `{id, conversation_id, client_request_id, question, outcome, answer, sources, created_at, updated_at, ticket}`. `ticket` null hoặc `{id, target_kind, target_department_name, status, version, reply, replied_by_name, replied_at}`. API không xuất cấu trúc SQL, prompt nội bộ hoặc token.

Ticket detail JSON: các field ticket trên, thêm `turn_id`, `question_snapshot`, `created_at`, `updated_at`; không có full conversation. List assistant mặc định 15 mục, tối đa 20. Lỗi provider sau khi tạo turn trả HTTP503 `{code: "ASSISTANT_UNAVAILABLE", turn: TurnDTO}`; polling GET turn vẫn trả200 với outcome ERROR.

Trang `/assistant`: tiêu đề “Hỏi đáp tài sản”, ví dụ theo vai trò, hội thoại, ô nhập và nguồn; trạng thái đang tra cứu/làm rõ/chờ hỗ trợ/đã phản hồi/lỗi. Gửi bằng nút hoặc Enter, Shift+Enter xuống dòng, không gửi khi IME đang composition. Khóa lượt gửi thứ hai khi PROCESSING; retry mạng dùng lại request ID. Nguồn mở panel thông tin căn cứ; hiển thị thời điểm để phân biệt snapshot với tra cứu mới.

Trang `/assistant/support`: hộp thư MANAGER/ADMIN, bộ lọc OPEN/ANSWERED, chi tiết và ô phản hồi. Người hỏi theo dõi ticket trong hội thoại. Refresh hoặc nút “Kiểm tra phản hồi” tải phản hồi mới, không bắt buộc realtime/poll nền. React render text, không `dangerouslySetInnerHTML`.

## 11. Nghiệm thu và triển khai sau tuần 4

- Backend: tests cho schema/idempotency/atomic handoff, tool scoping, đầy đủ tổng số, ngữ cảnh hiện tại, lỗi provider và reply OCC.
- Worker: mock `env.AI.run`, contract parity, auth/method/UTF-8 byte limit, model output hỏng, endpoint gợi ý cũ còn chạy.
- Frontend: MSW cho luồng hỏi/căn cứ/clarify/retry/ticket/reply, role menu, XSS/IME và màn hình hẹp.
- D1 local: harness sở hữu riêng, chỉ loopback, fake AI; kiểm chứng SQL bổ sung, rollback batch, UUID/time/JSON, không đụng cloud hay PostgreSQL đang phục vụ.
- PostgreSQL: kiểm chứng runner và harness sở hữu riêng theo §4.1, gồm port bị chiếm, lỗi child, teardown và cấu hình không rơi về instance tuần 4.
- Đánh giá LLM sớm: thêm gate sau Task 3 và sau khi tools Task 2 sẵn sàng, trước khi hoàn tất UI Tasks 6–7. Dùng bộ câu hỏi tiếng Việt nhỏ, cố định, so khớp decision/tool/tham số sau chuẩn hóa; bao gồm diễn đạt tương đương, câu nối tiếp, mơ hồ, ngoài phạm vi và yêu cầu vượt quyền. Không so khớp nguyên văn câu trả lời; quyền do backend quyết định, không giao cho LLM.
- Live gate chỉ chạy khi được phép, với endpoint/model và ngân sách lượt gọi được xác nhận. Mặc định skip và ghi **LLM_EVAL_PENDING**, không dùng mock để đổi thành pass. Khi chưa được phép, AGY có thể tiếp tục backend và UI dựa trên mock nhưng chưa đóng gate hoàn thiện UI hoặc nghiệm thu hiểu câu hỏi. Lỗi đánh giá quay lại Task 2/3 và contract nếu cần, không mở rộng nghiệp vụ hoặc đổi kiến trúc.
- Nghiệm thu LLM thật cuối: tái dùng bộ đánh giá sớm và bổ sung kết quả rỗng, dữ liệu mới, ba vai trò và vòng phản hồi người phụ trách. Mock không thay thế lượt này; không tuyên bố độ chính xác 100%.
- Bật tính năng sau migration bổ sung và kiểm tra nền tuần 4; rollback bằng tắt feature flag ở backend/frontend, giữ nguyên bảng/hội thoại, không reset CSDL.

## 12. Tài liệu kỹ thuật đã đối chiếu khi lập kế hoạch

- [Cloudflare function calling](https://developers.cloudflare.com/workers-ai/features/function-calling/): cách mô hình lựa chọn công cụ/tham số; ở bản đầu áp dụng bằng protocol JSON được backend xác thực.
- [Workers AI JSON Mode](https://developers.cloudflare.com/workers-ai/features/json-mode/): output có cấu trúc vẫn cần xử lý lỗi, không phải bảo đảm mọi lần sinh đều đúng schema.
- [Mô hình đang cấu hình](https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct-fp8/): giữ model ID hiện có, không tự nâng/chuyển model trong phiên lập kế hoạch.
- [D1 database/batch](https://developers.cloudflare.com/d1/worker-api/d1-database/): kết hợp với helper và OCC trong repository; yêu cầu chứng minh bằng D1 local, không suy từ SQLite mock.
