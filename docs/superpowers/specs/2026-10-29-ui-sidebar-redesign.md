# Thiết kế UI: Left Sidebar & Full-Width Layout (AssetHub)

## 1. Intent & Why
- Người dùng yêu cầu chuyển thanh điều hướng từ trên cùng (top navbar) sang bên trái (left sidebar).
- Loại bỏ khoảng trắng thừa hai bên (`max-w-7xl`), mở rộng nội dung tràn toàn màn hình (full-width) để tận dụng tối đa không gian hiển thị trên màn hình lớn (1024px, 1366px, 1920px+).

## 2. Goals, Non-Goals, Anti-Goals
- **Goals:**
  - Thay thế top `Navbar` bằng `Sidebar` đặt ở cạnh trái trong flex layout (`shrink-0`) cho các trang được bảo vệ sau khi xác thực.
  - Trang `/login` và `/register` hiển thị theo Guest Layout (header tối giản hoặc không sidebar, form căn giữa màn hình, giữ nguyên quy tắc không hiển thị sidebar cho khách).
  - Vùng nội dung chính `main` dùng `flex-1 min-w-0`, có thanh cuộn dọc độc lập, không bị sidebar che khuất (sidebar không bao giờ đè lên `main`).
  - Sidebar hỗ trợ thu gọn/mở rộng (`w-64` khi mở, `w-20` khi thu gọn icon). Lưu duy nhất tùy chọn này vào `localStorage` có bọc try/catch guard. Khôi phục chính xác trạng thái sau khi unmount/remount.
  - Vùng menu điều hướng bên trong sidebar có thanh cuộn riêng (`overflow-y-auto`) khi chiều cao màn hình hạn chế (ví dụ viewport chiều cao 480px), đảm bảo header và footer của sidebar vẫn luôn hiển thị và tương tác được.
  - Đảm bảo trọn vẹn Accessibility: Khi thu gọn, link và button vẫn giữ accessible name bằng `aria-label` hoặc `sr-only`, tooltip `title`, hỗ trợ focus bàn phím rõ nét (`focus:ring-2`). Nút toggle thu gọn có `aria-expanded` và nhãn rõ ràng.
  - Mở rộng vùng container các trang toàn màn hình (`w-full px-4 sm:px-6 lg:px-8 py-6`), bảng dữ liệu có `overflow-x-auto` nội bộ không tràn trang.
  - Giữ nguyên giới hạn bề rộng hợp lý cho Form (Profile `max-w-3xl`, Login/Register `max-w-md`, Modals `max-w-lg` / `max-w-2xl`).
  - Kiểm tra bố cục đáp ứng chuẩn ở các mốc phân giải 1024px, 1366px và 1920px (cả mở rộng và thu gọn), cộng với viewport chiều cao thấp (1280x480px). Bảng cuộn ngang trong vùng bảng, chụp và lưu screenshot tại các độ phân giải để review.
  - Tối ưu Dashboard: KPI card co giãn linh hoạt (`grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`), tự xuống dòng khi thiếu chỗ, không ép 6 cột trên màn hẹp gây cắt chữ/số.
  - Giữ nguyên 100% route, quyền hạn RBAC và các quy tắc bảo mật (tuyệt đối không lưu JWT vào localStorage; JWT lưu hoàn toàn trong bộ nhớ in-memory).
  - Bổ sung bộ test cho `Sidebar.jsx`, tạo file Playwright layout `frontend/e2e/layout.spec.js`, đảm bảo toàn bộ unit test (Vitest) và E2E test pass 100%. Báo cáo chính xác số lượng test thực tế đã chạy, không cố định số 40.

- **Non-Goals:**
  - Không thay đổi nghiệp vụ backend, API endpoints hay schema database.
  - Không thay đổi form đăng nhập / đăng ký (`/login`, `/register`).
- **Anti-Goals:**
  - Không dùng `position: fixed` tuyệt đối che đè nội dung `main`.
  - Không để crash giao diện nếu trình duyệt chặn hoặc lỗi `localStorage`.
  - Không để tràn ngang toàn màn hình do bảng dữ liệu lớn.
  - Không lưu JWT tokens hay thông tin nhạy cảm vào `localStorage`.

## 3. Constraints
- Framework: React 18, Tailwind CSS, Lucide React, React Router v6.
- RBAC Menu:
  - ADMIN: Dashboard, Tài sản, Yêu cầu, Người dùng, Phòng ban, Danh mục (6 tabs).
  - MANAGER: Dashboard, Tài sản, Yêu cầu, Nhân viên (4 tabs).
  - USER: Tài sản của tôi, Lịch sử, Yêu cầu (3 tabs).
- Storage Constraint: Chỉ lưu duy nhất key `assethub_sidebar_collapsed` (`'true'` / `'false'`). Tuyệt đối không lưu token hay user data vào `localStorage`.

## 4. Approaches & Selection
- **Phương án 1 (Đã chọn): Collapsible Left Sidebar + Full-width fluid content.**
  - Ưu điểm: Tối ưu không gian hiển thị cho bảng dữ liệu lớn; linh hoạt thu gọn `w-20` hoặc mở rộng `w-64`. Bố cục flex 2 cột liền mạch (`shrink-0` và `flex-1 min-w-0`), không lo chồng chéo vị trí.
  - Đánh đổi: Cần quản lý state đóng/mở và xử lý tooltip/accessible name khi thu gọn.
- **Phương án 2: Fixed Left Sidebar cố định (không co giãn).** Bị loại vì chiếm diện tích cố định 240px trên màn hình 1024px-1366px.
- **Phương án 3: Dual Sidebar (Nhóm menu đa cấp).** Bị loại vì quá cồng kềnh với hệ thống có 3-6 tabs.

## 5. Kiến trúc & Chi tiết triển khai

### 5.1 Component `Sidebar.jsx`
- **Cấu trúc Shell:**
  - Container: `<aside className={`h-screen shrink-0 bg-white border-r border-gray-200 flex flex-col transition-all duration-200 ease-in-out ${isCollapsed ? 'w-20' : 'w-64'}`} aria-label="Thanh điều hướng chính">`
- **Header:**
  - Logo Package icon + Text "AssetHub" (hiển thị khi mở, ẩn khi thu gọn nhưng có `sr-only`).
  - Nút toggle thu gọn/mở rộng:
    - Icon: `ChevronLeft` khi mở, `ChevronRight` khi thu gọn.
    - Thuộc tính: `aria-expanded={!isCollapsed}`, `aria-label={isCollapsed ? "Mở rộng thanh điều hướng" : "Thu gọn thanh điều hướng"}`, `title={isCollapsed ? "Mở rộng thanh điều hướng" : "Thu gọn thanh điều hướng"}`.
- **Menu Items (Scrollable Body):**
  - Vùng chứa: `flex-1 overflow-y-auto px-3 py-4 space-y-1`.
  - Item NavLink:
    - Khi mở rộng: Hiển thị icon + label text tiếng Việt.
    - Khi thu gọn: Căn giữa icon, thêm `title={item.label}`, `aria-label={item.label}` và span `sr-only` chứa nhãn để hỗ trợ screen readers và Playwright `getByRole('link', { name: item.label })`.
    - Focus state: `focus:outline-none focus:ring-2 focus:ring-blue-500 rounded-lg`.
- **Footer:**
  - Role badge: Badge vai trò (ADMIN / MANAGER / USER). Khi thu gọn chỉ hiển thị chữ viết tắt hoặc icon nhỏ kèm tooltip.
  - User profile link (`/profile`): Hiển thị avatar/User icon + tên tài khoản. Khi thu gọn giữ tooltip tên tài khoản và `aria-label`.
  - Nút đăng xuất (`Đăng xuất`): Icon `LogOut`, thuộc tính `aria-label="Đăng xuất"`, `title="Đăng xuất"`.
- **LocalStorage Helper:**
  - Hàm an toàn `getInitialCollapsed()` và `setCollapsedStorage(val)` bọc `try...catch`. Nếu `localStorage` bị vô hiệu hóa hoặc ném SecurityError thì fallback mượt mà vào state bộ nhớ (`false`).

### 5.2 Bố cục tổng thể `App.jsx`
- **Guest Layout** (chưa đăng nhập hoặc đường dẫn `/login`, `/register`):
  - Hiển thị Navbar tối giản trên cùng (`Navbar.jsx` giản lược hoặc header tối giản) và nội dung đăng nhập/đăng ký căn giữa trang. Không hiển thị Sidebar.
- **Authenticated Layout** (đã xác thực, các route được bảo vệ):
  - Wrapper: `<div className="flex h-screen w-screen overflow-hidden bg-gray-50 text-gray-900">`
  - Cột 1: `<Sidebar />` (`shrink-0`).
  - Cột 2: `<main className="flex-1 min-w-0 h-screen overflow-y-auto">` chứa `<Routes>`.

### 5.3 Cập nhật các trang (Full-Width & Containment)
1. Bỏ `max-w-7xl mx-auto` tại tất cả trang: Thay bằng container `w-full px-4 sm:px-6 lg:px-8 py-6`.
2. Bảo toàn độ rộng form/modal:
   - `ProfilePage.jsx`: Thẻ cha `w-full`, nhưng khối form bên trong giữ `max-w-3xl`.
   - Modals (`AssetActionModal`, `AssetCreateModal`, `CreateRequestModal`, `RequestReviewModal`): Giữ nguyên `max-w-md` đến `max-w-2xl` căn giữa modal.
3. Bảng dữ liệu: Đảm bảo bọc thẻ table bằng `div className="overflow-x-auto"` để bảng nhiều cột cuộn ngang nội bộ, không phá vỡ chiều ngang màn hình.
4. `DashboardPage.jsx`:
   - KPI Grid: `grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`. Đảm bảo ở độ phân giải 1024px hiển thị 3 cột x 2 hàng, 1366px và 1920px hiển thị 6 cột rộng rãi mà không bị cắt chữ.
   - Charts Grid: `grid grid-cols-1 xl:grid-cols-2 gap-6`.

### 5.4 Test & Kiểm chứng
1. Tạo file test `frontend/src/tests/Sidebar.test.jsx`:
   - Kiểm tra render đúng menu theo vai trò (Admin 6, Manager 4, User 3).
   - Kiểm tra toggle thu gọn/mở rộng, kiểm tra thuộc tính `aria-expanded`.
   - Kiểm tra khôi phục trạng thái từ `localStorage` sau khi unmount và remount component.
   - Kiểm tra xử lý an toàn khi `localStorage` bị lỗi/SecurityError.
   - Kiểm tra điều hướng `/profile` và sự kiện click nút `Đăng xuất`.
2. Tạo file Playwright layout test `frontend/e2e/layout.spec.js`:
   - Kiểm tra đăng nhập Admin, Manager, User.
   - Kiểm tra các viewport: `1024x768`, `1366x768`, `1920x1080` (ở cả 2 trạng thái: mở rộng `w-64` và thu gọn `w-20`).
   - Kiểm tra viewport chiều cao hạn chế `1280x480`: kiểm tra menu bên trong sidebar cuộn được (`scrollHeight > clientHeight`), header toggle và footer logout vẫn hiển thị trong viewport và click được bình thường.
   - Bounding box check: `sidebarRect.x + sidebarRect.width <= mainRect.x + 1` (đảm bảo sidebar không đè lên vùng main).
   - Bảng dữ liệu có scrollbar nội bộ (`scrollWidth >= clientWidth` trong wrapper, document body không có scroll ngang ngoài ý muốn).
   - Chụp và lưu ảnh screenshot vào `frontend/e2e-screenshots/` cho các mốc để phục vụ kiểm tra visual.
3. Chạy toàn bộ test suites và báo cáo số lượng thực chạy:
   - `npm --prefix frontend test -- --run` (báo cáo số file và số test thực chạy).
   - `npm --prefix frontend run build` (kiểm tra build production).
   - `npm --prefix frontend run test:e2e` (chạy cả suite nghiệp vụ `assethub.spec.js` và suite bố cục `layout.spec.js`).
