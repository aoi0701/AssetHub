# AssetHub Backlog & Known Issues

Tài liệu theo dõi các lỗi tồn đọng, nợ kỹ thuật và các hạng mục cải tiến cần xử lý trong các phiên làm việc tiếp theo.

---

## [BUG-PERSONAL-HISTORY-PAGINATION] Thiếu điều khiển phân trang tại trang Nhật ký tài sản cá nhân

- **Mã định danh:** `BUG-PERSONAL-HISTORY-PAGINATION`
- **Mức độ ưu tiên:** Medium
- **Phạm vi ảnh hưởng:**
  - Frontend: [`frontend/src/pages/PersonalHistoryPage.jsx`](../frontend/src/pages/PersonalHistoryPage.jsx)
  - Backend: [`backend/apps/assets/history_api.py`](../backend/apps/assets/history_api.py)

### 1. Hiện tượng (Symptoms)
- Endpoint backend `/api/me/asset-history/` áp dụng phân trang `PersonalAssetHistoryPagination` với kích thước mặc định `page_size = 20`.
- Giao diện người dùng tại trang `/my-history` ([`PersonalHistoryPage.jsx`](../frontend/src/pages/PersonalHistoryPage.jsx)) chỉ gọi `GET /api/me/asset-history/` một lần khi tải trang và lưu trực tiếp mảng kết quả vào state `historyItems`.
- Trang chưa nhúng component `Pagination`. Khi tài khoản người dùng tích lũy nhiều hơn 20 sự kiện vòng đời tài sản (cấp phát, hoàn trả, báo hỏng, bảo dưỡng...), bảng chỉ hiển thị 20 dòng đầu tiên. Các bản ghi lịch sử cũ hơn ở trang 2 trở đi bị ẩn hoàn toàn và người dùng không có cách nào chuyển trang để xem.

### 2. Cách tái hiện khi có hơn 20 sự kiện (Steps to Reproduce)
1. Đăng nhập bằng tài khoản nhân viên thông thường (`role == 'USER'`).
2. Khởi tạo trên 20 sự kiện tài sản liên quan đến tài khoản này (ví dụ: tạo 25 bản ghi `AssetHistoryLog` với `to_user` hoặc `from_user` là tài khoản hiện tại thông qua quy trình cấp phát/hoàn trả liên tục hoặc qua test fixture).
3. Điều hướng tới thanh menu bên trái, chọn mục **Lịch sử** (`/my-history`).
4. Quan sát danh sách sự kiện:
   - Bảng chỉ kết xuất đúng 20 bản ghi đầu tiên.
   - Không xuất hiện thanh điều hướng phân trang (nút Trước / Sau, danh sách số trang, tổng số bản ghi).
   - 5 bản ghi cũ hơn không thể xem được trên giao diện.

### 3. Tiêu chí hoàn thành (Acceptance Criteria)
- [x] **Chuyển trang được:** Tích hợp component dùng chung [`Pagination.jsx`](../frontend/src/components/Pagination.jsx) bên dưới bảng nhật ký cá nhân; hỗ trợ responsive trên cả mobile và desktop; hiển thị thông tin “Đang xem 1–X trên tổng Y bản ghi” cùng các nút Trước/Sau hoạt động chính xác.
- [x] **Xem đủ lịch sử:** Người dùng có thể điều hướng qua tất cả các trang để đọc đầy đủ 100% lịch sử hoạt động thiết bị của mình mà không bị ngắt quãng hoặc sót sự kiện.
- [x] **Bảo toàn phạm vi dữ liệu người đăng nhập (Strict Scoping):** Toàn bộ các lượt tải trang phân trang (`/api/me/asset-history/?page=N`) phải giữ nguyên quy tắc an ninh tuyệt đối: chỉ trả về các sự kiện mà tài khoản đăng nhập là người nhận (`to_user`) hoặc người bàn giao (`from_user`). Ngăn chặn mọi tham số can thiệp trái phép để xem lịch sử tài sản của người khác.

### 4. Trạng thái giải quyết (Resolution Status)
- **Trạng thái:** Đã giải quyết & hoàn tất kiểm thử mở rộng (RESOLVED)
- **Giải pháp tối thiểu (Ponytail approach):**
  - Tích hợp component [`Pagination.jsx`](../frontend/src/components/Pagination.jsx) vào [`PersonalHistoryPage.jsx`](../frontend/src/pages/PersonalHistoryPage.jsx).
  - Thêm state `currentPage` (mặc định 1), `pageSize = 20`, `totalCount`, `totalPages`, và `failedTargetPage`.
  - Gửi `page` và `page_size` lên endpoint `/api/me/asset-history/`. Đồng bộ giao diện và metadata với các trang quản trị.
  - Bổ sung cơ chế chống race condition khi chuyển trang nhanh (`reqIdRef` & `isAborted`), ngăn chặn phản hồi cũ ghi đè dữ liệu mới.
  - Xử lý lỗi 500 / Network: rollback `currentPage` về trang thành công gần nhất (`lastLoadedPageRef`), không hiển thị dữ liệu trang cũ dưới nhãn trang mới, giữ số trang và dữ liệu nhất quán 100%, hiển thị thông báo lỗi kèm nút "Thử lại".
  - Tự động phục hồi lỗi 404 (`currentPage > 1` tự lùi trang và re-fetch trang trước) khi số lượng bản ghi giảm hoặc truy cập trang không hợp lệ.
  - Giữ nguyên backend API hiện có và logic strict scoping (`from_user=user | to_user=user`). Không thêm dependency hay cấu trúc thừa.

### 5. Bằng chứng kiểm chứng (Verification Evidence)
- **Unit Test Frontend (`frontend/src/tests/PersonalHistory.test.jsx` - 8/8 tests passed):**
  - Test fixture 25 sự kiện của người đăng nhập và sự kiện của người khác (`AST-OTHER-999`):
    - Trang 1 hiển thị 20 bản ghi (#1 -> #20), text tóm tắt "Đang xem 1–20 trên tổng 25 bản ghi".
    - Trang 2 hiển thị 5 bản ghi (#21 -> #25), text tóm tắt "Đang xem 21–25 trên tổng 25 bản ghi".
    - Tuyệt đối không hiển thị sự kiện của người dùng khác.
  - Test race condition: Phản hồi chậm của trang cũ không ghi đè dữ liệu trang mới khi thao tác nhanh giữa các trang.
  - Test tự phục hồi lỗi 404:
    - Ban đầu API trả `count: 25` (trang 1) để nút Sau hoạt động.
    - Request trang 2 trả về 404.
    - Frontend tự động gọi lại trang 1 và phục hồi thành công.
    - Khẳng định chuỗi request thực tế qua MSW: `['1', '2', '1']`.
  - Test regression lỗi 500 khi chuyển trang:
    - Khi chuyển sang trang 2 gặp lỗi 500, dữ liệu trang 1 và nhãn "Đang xem 1–20 trên tổng 25 bản ghi" được bảo toàn nhất quán.
    - Tuyệt đối không hiển thị dữ liệu trang 1 dưới nhãn trang 2 ("Đang xem 21–25").
    - Hiển thị nút "Thử lại", khi click retry thành công sẽ chuyển sang trang 2 mượt mà.
  - Test trạng thái lỗi ban đầu (HTTP 500) và trạng thái trống (0 bản ghi).
- **Toàn bộ Frontend Unit Tests:** 87/87 passed trên 11 test files (`npm --prefix frontend test -- --run`).
- **Production Build:** `npm --prefix frontend run build` hoàn thành không cảnh báo lỗi biên dịch (0 error).
- **Backend Pytest Scoping & Pagination (`backend/tests/test_personal_history.py` - 6/6 passed):**
  - Kiểm thử `test_personal_history_pagination_and_strict_scoping` tạo 25 sự kiện cho `regular_user` (User A) và 10 sự kiện cho `user_hr` (User B):
    - User A query trang 1: nhận đúng 20 sự kiện, `count == 25` (không phải 35), không lộ sự kiện nào của User B.
    - User A query trang 2: nhận đúng 5 sự kiện, `count == 25`, không lộ sự kiện nào của User B.
    - Hợp của trang 1 và trang 2 gồm đúng 25 sự kiện phân biệt của User A.
    - User B query: nhận đúng 10 sự kiện của User B, không lộ bất kỳ sự kiện nào của User A.
  - Toàn bộ suite backend: 185/185 tests passed.
- **Playwright E2E (`frontend/e2e/pagination.spec.js` & `assethub.spec.js` - 16/16 passed):**
  - Test E2E chuyển trang thực tế 1 → 2 → 1 với fixture cô lập 25 sự kiện qua route interception:
    - Trang 1: 20 bản ghi, kiểm tra nút Trước disabled, nút Sau enabled.
    - Chuyển sang Trang 2: 5 bản ghi, kiểm tra nút Trước enabled, nút Sau disabled.
    - Chuyển về lại Trang 1: 20 bản ghi phục hồi chính xác.
    - Hoàn toàn cô lập bằng network route mocking của Playwright, không ghi dữ liệu thử nghiệm vào database demo.
