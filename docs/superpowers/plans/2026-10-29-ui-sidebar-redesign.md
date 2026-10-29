# UI Sidebar & Full-Width Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển đổi thanh điều hướng AssetHub sang Left Sidebar co giãn linh hoạt (`w-64` / `w-20`) và mở rộng nội dung toàn màn hình (`w-full`) trên tất cả các trang, đảm bảo accessibility, bố cục độc lập không đè nội dung và bảo toàn quy tắc bảo mật token.

**Architecture:** Bố cục flex 2 cột (`h-screen overflow-hidden`): cột trái là `<Sidebar />` cố định tỷ lệ (`shrink-0`) với menu cuộn riêng (`overflow-y-auto`); cột phải là vùng nội dung chính `main` (`flex-1 min-w-0 overflow-y-auto`). Lưu duy nhất tùy chọn thu gọn vào `localStorage` có bọc try/catch an toàn; loại bỏ giới hạn `max-w-7xl` tại các trang nhưng giữ `max-w` hợp lý cho form hồ sơ, auth và modal. Trang `/login` và `/register` hiển thị guest layout không có sidebar.

**Tech Stack:** React 18, React Router v6, Tailwind CSS, Lucide React, Vitest, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-29-ui-sidebar-redesign.md`

## Global Constraints

- Chỉ lưu duy nhất key `assethub_sidebar_collapsed` (`'true'` hoặc `'false'`) trong `localStorage`; tuyệt đối không lưu JWT token hoặc user data vào `localStorage` (JWT chỉ lưu in-memory).
- Bọc tất cả thao tác đọc/ghi `localStorage` trong `try...catch` để giao diện luôn chạy mượt mà ngay cả khi trình duyệt chặn cookie/storage hoặc Private Browsing.
- Tất cả link điều hướng và nút bấm trong Sidebar phải giữ nguyên accessible name (`aria-label` hoặc text `sr-only`) và `title` tooltip khi thu gọn để tương thích hoàn toàn với screen reader và Playwright `getByRole('link', { name: ... })`.
- Giữ nguyên toàn bộ 100% routes, phân quyền RBAC (Admin 6 tabs, Manager 4 tabs, User 3 tabs) và luồng API.
- Dashboard KPI grid co giãn tự nhiên `grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`, không ép cứng 6 cột trên màn hình nhỏ. Bảng dữ liệu có `overflow-x-auto` nội bộ không làm vỡ chiều rộng trang.
- Không để sidebar đè lên vùng `main`: đảm bảo `sidebarRect.x + sidebarRect.width <= mainRect.x + 1` tại mọi kích thước màn hình.
- Báo đúng số lượng test thực chạy từ test runner, không làm tròn hoặc giả định số cố định.

## Review Focus

1. **Storage Disabled / Private Browsing & Remount:** Khi `localStorage` ném exception (SecurityError), Sidebar vẫn toggle bình thường bằng in-memory fallback; khi unmount và remount, trạng thái đã lưu trong `localStorage` được khôi phục chính xác.
2. **Accessible Names when Collapsed:** Khi `isCollapsed === true`, các query `getByRole('link', { name: 'Người dùng' })`, `getByRole('button', { name: 'Đăng xuất' })`, `getByRole('button', { name: /thu gọn|mở rộng/i })` vẫn tìm thấy chính xác phần tử.
3. **Viewport Responsiveness (1024px, 1366px, 1920px & 480px height):** Tại 1024px x 768px, Dashboard KPI hiển thị 3 cột x 2 hàng mà không bị tràn hay đè chữ; tại 1366px và 1920px hiển thị 6 cột rộng rãi. Tại chiều cao 480px, menu sidebar cuộn riêng mà header/footer vẫn bấm được.
4. **Independent Scrolling & Overlap Protection:** Vùng nội dung `main` cuộn dọc độc lập; sidebar nằm cạnh `main` trong flex container, không bao giờ đè lên `main`.
5. **Horizontal Table Overflow Containment:** Bảng nhiều cột (Asset List, Request Inbox) khi màn hình hẹp xuất hiện thanh cuộn ngang trong card bảng (`overflow-x-auto`), không đẩy `main` tràn khỏi màn hình.

---

### Task 1: Component `Sidebar.jsx` & Storage Helper với Unit Tests (TDD)

**Files:**
- Create: `frontend/src/components/Sidebar.jsx`
- Create: `frontend/src/tests/Sidebar.test.jsx`

**Interfaces:**
- Consumes: `useAuth()` từ `frontend/src/context/AuthContext.jsx` (`user`, `isAuthenticated`, `logout`)
- Produces: `<Sidebar />` component:
  - Header: Logo `Package` + text "AssetHub" + toggle button (`aria-expanded`, `aria-label`, icon `ChevronLeft` / `ChevronRight`).
  - Nav: NavLinks theo vai trò ADMIN (6 tabs: Dashboard, Tài sản, Yêu cầu, Người dùng, Phòng ban, Danh mục), MANAGER (4 tabs: Dashboard, Tài sản, Yêu cầu, Nhân viên), USER (3 tabs: Tài sản của tôi, Lịch sử, Yêu cầu).
  - Footer: Role badge, link `/profile` (`user.full_name || user.username`), button `Đăng xuất` (`handleLogout`).
  - Storage: Đọc/ghi key `assethub_sidebar_collapsed` an toàn trong try/catch.

- [ ] **Step 1: Write failing unit tests for `Sidebar` in `frontend/src/tests/Sidebar.test.jsx`**

Bao gồm:
1. Render đúng menu cho ADMIN (6 tabs), MANAGER (4 tabs), USER (3 tabs).
2. Toggle collapse state, kiểm tra `aria-expanded`, nhãn accessibility (`aria-label`, tooltip `title`), và cập nhật `localStorage`.
3. Khôi phục trạng thái thu gọn từ `localStorage` sau khi unmount và remount component.
4. Xử lý an toàn khi `localStorage` bị lỗi / ném SecurityError.
5. Kiểm tra link `/profile` và sự kiện click nút `Đăng xuất`.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- src/tests/Sidebar.test.jsx`
Expected: FAIL with "Cannot find module '../components/Sidebar'"

- [ ] **Step 3: Implement `Sidebar.jsx` in `frontend/src/components/Sidebar.jsx`**

Implement collapsible sidebar component with:
- Safe `getInitialCollapsed` and `saveCollapsed` with `try...catch`
- Flex container: `aside` with `h-screen shrink-0 bg-white border-r border-gray-200 flex flex-col transition-all duration-200 ease-in-out ${isCollapsed ? 'w-20' : 'w-64'}`
- Header: logo and toggle button with `aria-expanded` and `aria-label`
- Navigation area: `flex-1 overflow-y-auto px-3 py-4 space-y-1` (vùng cuộn riêng cho menu khi thiếu chiều cao)
- Accessible `NavLink` items preserving `aria-label`, tooltip `title`, focus rings, and `sr-only` text when collapsed
- Footer: role badge, profile link, logout button

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- src/tests/Sidebar.test.jsx`
Expected: PASS (tất cả các test case đều pass)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.jsx frontend/src/tests/Sidebar.test.jsx
git commit -m "feat(ui): add accessible collapsible Sidebar component and unit tests"
```

---

### Task 2: Layout Integration in `App.jsx` & Guest Header Optimization

**Files:**
- Modify: `frontend/src/App.jsx:32-163`
- Modify: `frontend/src/components/Navbar.jsx:1-40`

**Interfaces:**
- Consumes: `<Sidebar />`, `useAuth()`, React Router `<Routes>`
- Produces: Flex two-column application shell khi authenticated (`<div className="flex h-screen w-screen overflow-hidden bg-gray-50 text-gray-900">`); guest header tối giản trên `/login` và `/register` không có sidebar.

- [ ] **Step 1: Check existing `App` and layout test coverage**

Run: `npm --prefix frontend test -- src/tests/Auth.test.jsx`
Verify how login/navigation is currently exercised.

- [ ] **Step 2: Update `App.jsx` layout structure**

- Khi `!isAuthenticated || !user` hoặc đang ở trang guest:
  - Hiển thị guest `<Navbar />` (chỉ có logo + link Đăng nhập / Đăng ký) + `<main className="flex-1">`
- Khi `isAuthenticated && user`:
  - Render wrapper `<div className="flex h-screen w-screen overflow-hidden bg-gray-50 text-gray-900">`
  - Cột 1: `<Sidebar />` (`shrink-0`)
  - Cột 2: `<main className="flex-1 min-w-0 h-screen overflow-y-auto">` containing `<Routes>`
- Tối ưu `Navbar.jsx`: Khi đã đăng nhập thì trả về `null` (được thay thế trọn vẹn bởi `Sidebar`), khi chưa đăng nhập hiển thị header tối giản Logo + Đăng nhập/Đăng ký.

- [ ] **Step 3: Run full Vitest suite to ensure no regressions in auth/navigation**

Run: `npm --prefix frontend test -- --run`
Expected: Tất cả các test suites pass không có lỗi hồi quy.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.jsx frontend/src/components/Navbar.jsx
git commit -m "feat(ui): integrate Left Sidebar into App layout shell"
```

---

### Task 3: Full-Width Layout on Pages, Data Table Scrolling & Dashboard KPI Grid

**Files:**
- Modify: `frontend/src/pages/DashboardPage.jsx:130-220`
- Modify: `frontend/src/pages/AssetListPage.jsx:80-140`
- Modify: `frontend/src/pages/AssetDetailPage.jsx:100-140`
- Modify: `frontend/src/pages/RequestInboxPage.jsx:75-120`
- Modify: `frontend/src/pages/UserAdminPage.jsx:135-180`
- Modify: `frontend/src/pages/DepartmentPage.jsx:95-130`
- Modify: `frontend/src/pages/CategoryPage.jsx:90-120`
- Modify: `frontend/src/pages/UserPortalPage.jsx:55-90`
- Modify: `frontend/src/pages/PersonalHistoryPage.jsx:40-70`
- Modify: `frontend/src/pages/ProfilePage.jsx:90-130`

**Interfaces:**
- Consumes: Tailwind utility classes (`w-full`, `overflow-x-auto`, responsive grids).
- Produces: Full-width fluid responsive view on all pages with bounded forms/tables.

- [ ] **Step 1: Update container class in all 10 pages**

- Replace `max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8` with `w-full py-6 px-4 sm:px-6 lg:px-8`.
- In `ProfilePage.jsx`: Thẻ bọc ngoài `w-full`, nhưng các thẻ card form bên trong giữ `max-w-3xl mx-auto`.
- Verify data table containers in `AssetListPage.jsx`, `RequestInboxPage.jsx`, `UserAdminPage.jsx`: Đảm bảo table nằm trong `div className="overflow-x-auto"` để bảng nhiều cột cuộn ngang nội bộ.

- [ ] **Step 2: Optimize `DashboardPage.jsx` KPI Grid and Charts Grid**

- KPI cards grid: `grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`.
  - At 1024px: 3 cards x 2 rows (gọn gàng, không bị cắt chữ).
  - At 1366px & 1920px: 6 cards x 1 row (rộng rãi, dàn đều full-width).
- Charts grid: `grid grid-cols-1 xl:grid-cols-2 gap-6`.

- [ ] **Step 3: Run Vitest dashboard and admin tests to confirm zero breakage**

Run: `npm --prefix frontend test -- src/tests/Dashboard.test.jsx src/tests/AdminPages.test.jsx`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/*.jsx
git commit -m "feat(ui): convert pages to full-width layout with responsive KPI grid and bounded tables"
```

---

### Task 4: Layout E2E Suite, Viewport Tests, Screenshots & Final Verification

**Files:**
- Create: `frontend/e2e/layout.spec.js`
- Modify: `docs/verification-report.md`

**Interfaces:**
- Consumes: Vite production build, Vitest suite, Playwright browser test runner.
- Produces: `frontend/e2e/layout.spec.js` kiểm tra các mốc viewport (1024x768, 1366x768, 1920x1080 ở cả expanded & collapsed, và 1280x480 short viewport), lưu screenshot vào `frontend/e2e-screenshots/`, ghi nhận báo cáo số test thực chạy vào `docs/verification-report.md`.

- [ ] **Step 1: Implement `frontend/e2e/layout.spec.js`**

Kiểm tra:
1. Đăng nhập Admin và kiểm tra layout tại các viewport: `1024x768`, `1366x768`, `1920x1080`.
2. Kiểm tra bounding box: `sidebarRect.x + sidebarRect.width <= mainRect.x + 1` (đảm bảo sidebar không bao giờ đè lên main).
3. Kiểm tra toggle co giãn: mở rộng `w-64` (~256px) và thu gọn `w-20` (~80px).
4. Kiểm tra short viewport `1280x480`: menu sidebar có thanh cuộn riêng, header toggle và footer logout vẫn hiển thị trong viewport.
5. Kiểm tra bảng `overflow-x-auto` trên trang danh sách tài sản.
6. Chụp và lưu ảnh screenshot vào `frontend/e2e-screenshots/layout-{viewport}-{state}.png`.

- [ ] **Step 2: Run frontend production build**

Run: `npm --prefix frontend run build`
Expected: Build successfully completes without any syntax or CSS errors.

- [ ] **Step 3: Run all frontend unit tests**

Run: `npm --prefix frontend test -- --run`
Expected: Tất cả các file test và test cases đều pass (ghi nhận chính xác số file và số test thực tế).

- [ ] **Step 4: Run Playwright E2E suites**

Run: `npm --prefix frontend run test:e2e`
Expected: Cả 2 test suites (`assethub.spec.js` và `layout.spec.js`) đều pass.

- [ ] **Step 5: Record results in `docs/verification-report.md` and commit**

```bash
git add frontend/e2e/layout.spec.js docs/verification-report.md
git commit -m "test(e2e): add layout viewport tests, screenshot captures, and update verification report"
```
