# Thiết kế UI: Left Sidebar & Full-Width Layout (AssetHub)

## 1. Intent & Why
- Người dùng yêu cầu chuyển thanh điều hướng từ trên cùng (top navbar) sang bên trái (left sidebar).
- Loại bỏ khoảng trắng thừa hai bên (`max-w-7xl`), mở rộng nội dung tràn toàn màn hình (full-width) để tận dụng tối đa không gian hiển thị trên màn hình lớn.

## 2. Goals, Non-Goals, Anti-Goals
- **Goals:**
  - Thay thế top `Navbar` bằng `Sidebar` đặt ở cạnh trái cho người dùng đã đăng nhập.
  - Hỗ trợ thu gọn/mở rộng sidebar (`w-64` khi mở, `w-20` khi thu gọn icon). Lưu trạng thái thu gọn vào `localStorage`.
  - Mở rộng vùng nội dung chính `main` toàn màn hình (`w-full`), loại bỏ giới hạn `max-w-7xl` tại tất cả các trang.
  - Tối ưu Dashboard: Grid KPI dàn 6 cột trên màn hình rộng (`xl:grid-cols-6`), grid đồ thị 2 cột (`xl:grid-cols-2`).
  - Giữ tương thích 100% với 35 frontend unit tests và Playwright E2E suite (8 kịch bản).
- **Non-Goals:**
  - Không thay đổi nghiệp vụ backend, API endpoints hay quyền hạn (RBAC).
  - Không làm thay đổi giao diện trang Login/Register (giữ form ở giữa, top minimal header hoặc clean canvas).
- **Anti-Goals:**
  - Không làm vỡ layout trên màn hình nhỏ/laptop standard (tối thiểu 1024px mượt mà).
  - Không làm mất text accessibility khiến Playwright E2E không tìm thấy `getByRole('link', { name: ... })` hay button `Đăng xuất`.

## 3. Constraints
- Framework: React 18 + Tailwind CSS + Lucide React + React Router v6.
- Giữ nguyên trạng thái phân quyền: ADMIN (6 tabs), MANAGER (4 tabs), USER (3 tabs).
- Không thêm dependency bên ngoài (dùng CSS Tailwind và Lucide icons có sẵn).

## 4. Approaches & Selection
- **Phương án 1 (Đã chọn): Collapsible Left Sidebar + Full-width fluid content.**
  - Ưu điểm: Tối ưu không gian nhất; khi làm việc với bảng dữ liệu lớn (Asset list, Request inbox), người dùng có thể bấm thu gọn sidebar (`w-20`) để nội dung có không gian tối đa.
  - Đánh đổi: Cần quản lý state đóng/mở sidebar và animation transition nhẹ nhàng.
- **Phương án 2: Fixed Left Sidebar cố định (không co giãn, w-60).**
  - Đánh đổi: Không tối ưu được không gian khi cần bảng dữ liệu siêu rộng.
- **Phương án 3: Dual Sidebar (Sidebar nhóm + Sub-nav).**
  - Đánh đổi: Quá cồng kềnh cho số lượng tab hiện tại (3-6 mục).

## 5. Kiến trúc & Chi tiết triển khai

### 5.1 Component `Sidebar.jsx` (thay thế hoặc bổ sung cho Navbar)
- Vị trí: Cố định bên trái (`fixed left-0 top-0 bottom-0` hoặc flex child `h-screen sticky top-0`).
- Trạng thái: `isCollapsed` (mặc định `false`, toggle qua nút bấm `ChevronLeft` / `ChevronRight`, persist vào `localStorage.getItem('sidebar_collapsed')`).
- Phần Header Sidebar:
  - Logo Package icon + tên "AssetHub" (ẩn chữ khi thu gọn).
  - Nút toggle thu gọn/mở rộng.
- Phần Navigation:
  - Hiển thị danh sách menu theo vai trò (ADMIN, MANAGER, USER) với Icon + Label.
  - Tooltip khi sidebar ở trạng thái thu gọn (`title={item.name}`).
- Phần Footer Sidebar:
  - Role badge (ADMIN / MANAGER / USER).
  - User profile link (`/profile`) hiển thị avatar/user icon + tên tài khoản.
  - Nút đăng xuất (`Đăng xuất`) với icon `LogOut`.

### 5.2 Bố cục tổng thể `App.jsx`
- Khi chưa đăng nhập (Guest: `/login`, `/register`):
  - Giữ header đơn giản trên cùng (Logo AssetHub, Đăng nhập, Đăng ký) hoặc layout clean canvas.
- Khi đã đăng nhập:
  - Wrapper container: `flex h-screen w-screen overflow-hidden bg-gray-50`.
  - Cột trái: `<Sidebar />` (`w-64` hoặc `w-20`, `transition-all duration-200`).
  - Cột phải: `<main className="flex-1 min-w-0 h-screen overflow-y-auto">` chứa `<Routes>`.

### 5.3 Thay đổi tại các trang (Full-Width)
- Thay thế toàn bộ thẻ container `className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8"` thành `className="w-full py-6 px-4 sm:px-6 lg:px-8"` tại:
  1. `DashboardPage.jsx`: Cập nhật KPI grid `grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`.
  2. `AssetListPage.jsx`: Bảng tài sản hiển thị rộng rãi, lọc và bảng dữ liệu tận dụng hết chiều ngang.
  3. `AssetDetailPage.jsx`: Full-width 2 cột thông tin chi tiết và tabs.
  4. `RequestInboxPage.jsx`: Bảng yêu cầu full-width.
  5. `UserAdminPage.jsx`: Bảng quản lý người dùng full-width.
  6. `DepartmentPage.jsx`, `CategoryPage.jsx`: Bảng danh mục full-width.
  7. `UserPortalPage.jsx`, `PersonalHistoryPage.jsx`: Danh sách tài sản & timeline full-width.
  8. `ProfilePage.jsx`: Form hồ sơ cá nhân.

### 5.4 Test Impact & Kế hoạch kiểm thử
- `Sidebar.test.jsx`: Viết bộ unit test kiểm tra hiển thị menu theo vai trò, toggle thu gọn/mở rộng, nút đăng xuất.
- Chạy `npm --prefix frontend test -- --run` (35 tests hiện tại phải tiếp tục pass).
- Chạy `npm --prefix frontend run test:e2e` (Playwright E2E: 8 bước luồng đời thực nghiệm thu trơn tru).
