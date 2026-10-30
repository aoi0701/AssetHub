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
- [ ] **Chuyển trang được:** Tích hợp component dùng chung [`Pagination.jsx`](../frontend/src/components/Pagination.jsx) bên dưới bảng nhật ký cá nhân; hỗ trợ responsive trên cả mobile và desktop; hiển thị thông tin “Đang xem 1–X trên tổng Y bản ghi” cùng các nút Trước/Sau hoạt động chính xác.
- [ ] **Xem đủ lịch sử:** Người dùng có thể điều hướng qua tất cả các trang để đọc đầy đủ 100% lịch sử hoạt động thiết bị của mình mà không bị ngắt quãng hoặc sót sự kiện.
- [ ] **Bảo toàn phạm vi dữ liệu người đăng nhập (Strict Scoping):** Toàn bộ các lượt tải trang phân trang (`/api/me/asset-history/?page=N`) phải giữ nguyên quy tắc an ninh tuyệt đối: chỉ trả về các sự kiện mà tài khoản đăng nhập là người nhận (`to_user`) hoặc người bàn giao (`from_user`). Ngăn chặn mọi tham số can thiệp trái phép để xem lịch sử tài sản của người khác.
