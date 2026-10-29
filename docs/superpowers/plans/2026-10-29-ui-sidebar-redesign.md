# UI Sidebar & Full-Width Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển đổi thanh điều hướng AssetHub sang Left Sidebar co giãn linh hoạt (`w-64` / `w-20`) và mở rộng nội dung toàn màn hình (`w-full`) trên tất cả các trang.

**Architecture:** Bố cục flex 2 cột (`h-screen overflow-hidden`): cột trái là `<Sidebar />` cố định tỷ lệ (`shrink-0`) với menu cuộn riêng (`overflow-y-auto`); cột phải là vùng nội dung chính `main` (`flex-1 min-w-0 overflow-y-auto`). Lưu duy nhất tùy chọn thu gọn vào `localStorage` có bọc try/catch an toàn; loại bỏ giới hạn `max-w-7xl` tại các trang nhưng giữ `max-w` hợp lý cho form hồ sơ, auth và modal.

**Tech Stack:** React 18, React Router v6, Tailwind CSS, Lucide React, Vitest, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-29-ui-sidebar-redesign.md`

## Global Constraints

- Chỉ lưu key `assethub_sidebar_collapsed` (`'true'` hoặc `'false'`) trong `localStorage`; tuyệt đối không lưu JWT token hoặc user data vào `localStorage`.
- Bọc tất cả thao tác đọc/ghi `localStorage` trong `try...catch` để giao diện luôn chạy mượt mà ngay cả khi trình duyệt chặn cookie/storage hoặc Private Browsing.
- Tất cả link điều hướng và nút bấm trong Sidebar phải giữ nguyên accessible name (`aria-label` hoặc text `sr-only`) và `title` tooltip khi thu gọn để tương thích hoàn toàn với screen reader và Playwright `getByRole('link', { name: ... })`.
- Giữ nguyên toàn bộ 100% routes, phân quyền RBAC (Admin 6 tabs, Manager 4 tabs, User 3 tabs) và luồng API.
- Dashboard KPI grid co giãn tự nhiên `grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`, không ép cứng 6 cột trên màn hình nhỏ. Bảng dữ liệu có `overflow-x-auto` nội bộ không làm vỡ chiều rộng trang.

## Review Focus

1. **Storage Disabled / Private Browsing:** Khi `localStorage` ném exception (SecurityError), Sidebar vẫn toggle bình thường bằng in-memory fallback mà không crash React tree.
2. **Accessible Names when Collapsed:** Khi `isCollapsed === true`, các query `getByRole('link', { name: 'Người dùng' })`, `getByRole('button', { name: 'Đăng xuất' })`, `getByRole('button', { name: /thu gọn|mở rộng/i })` vẫn tìm thấy chính xác phần tử.
3. **Viewport Responsiveness (1024px, 1366px, 1920px):** Tại 1024px x 768px, Dashboard KPI hiển thị 3 cột x 2 hàng mà không bị tràn hay đè chữ; tại 1366px và 1920px hiển thị 6 cột rộng rãi.
4. **Independent Scrolling:** Vùng nội dung `main` cuộn dọc độc lập; menu sidebar cuộn dọc nội bộ khi màn hình quá ngắn (`h < 500px`), không làm trượt header/footer của sidebar.
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
  - Storage: Đọc/ghi key `assethub_sidebar_collapsed` an toàn.

- [ ] **Step 1: Write the failing unit tests for `Sidebar` in `frontend/src/tests/Sidebar.test.jsx`**

```jsx
import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Sidebar from '../components/Sidebar';
import * as AuthContext from '../context/AuthContext';

describe('Sidebar Component', () => {
  const mockLogout = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  const renderWithRole = (role = 'ADMIN', fullName = 'Nguyễn Văn A') => {
    vi.spyOn(AuthContext, 'useAuth').mockReturnValue({
      user: { id: 1, username: 'admin', full_name: fullName, role },
      isAuthenticated: true,
      logout: mockLogout,
    });

    return render(
      <BrowserRouter>
        <Sidebar />
      </BrowserRouter>
    );
  };

  it('renders correct navigation links for ADMIN role', () => {
    renderWithRole('ADMIN');
    expect(screen.getByRole('link', { name: /Dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Tài sản/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Yêu cầu/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Người dùng/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Phòng ban/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Danh mục/i })).toBeInTheDocument();
  });

  it('renders correct navigation links for MANAGER and USER roles', () => {
    const { unmount } = renderWithRole('MANAGER');
    expect(screen.getByRole('link', { name: /Nhân viên/i })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Phòng ban/i })).not.toBeInTheDocument();
    unmount();

    renderWithRole('USER');
    expect(screen.getByRole('link', { name: /Tài sản của tôi/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Lịch sử/i })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Dashboard/i })).not.toBeInTheDocument();
  });

  it('toggles collapse state, updates aria-expanded, and persists to localStorage', () => {
    renderWithRole('ADMIN');
    const toggleBtn = screen.getByRole('button', { name: /Thu gọn thanh điều hướng/i });
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(toggleBtn);
    expect(screen.getByRole('button', { name: /Mở rộng thanh điều hướng/i })).toHaveAttribute(
      'aria-expanded',
      'false'
    );
    expect(localStorage.getItem('assethub_sidebar_collapsed')).toBe('true');

    // All links still have accessible name via aria-label or sr-only
    expect(screen.getByRole('link', { name: /Dashboard/i })).toBeInTheDocument();
  });

  it('handles localStorage errors gracefully without throwing', () => {
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError: Access Denied');
    });
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError: QuotaExceeded');
    });

    renderWithRole('ADMIN');
    const toggleBtn = screen.getByRole('button', { name: /Thu gọn thanh điều hướng/i });
    fireEvent.click(toggleBtn);
    expect(screen.getByRole('button', { name: /Mở rộng thanh điều hướng/i })).toBeInTheDocument();

    getItemSpy.mockRestore();
    setItemSpy.mockRestore();
  });

  it('triggers logout on clicking logout button', () => {
    renderWithRole('ADMIN');
    const logoutBtn = screen.getByRole('button', { name: /Đăng xuất/i });
    fireEvent.click(logoutBtn);
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- src/tests/Sidebar.test.jsx`
Expected: FAIL with "Cannot find module '../components/Sidebar'"

- [ ] **Step 3: Implement `Sidebar.jsx` in `frontend/src/components/Sidebar.jsx`**

Implement collapsible sidebar component with:
- Safe `getInitialCollapsed` and `saveCollapsed` with `try...catch`
- Flex container: `aside` with `h-screen shrink-0 bg-white border-r border-gray-200 flex flex-col transition-all duration-200 ease-in-out ${isCollapsed ? 'w-20' : 'w-64'}`
- Header: logo and toggle button with `aria-expanded` and `aria-label`
- Navigation area: `flex-1 overflow-y-auto px-3 py-4 space-y-1`
- Accessible `NavLink` items preserving `aria-label`, tooltip `title`, focus rings, and `sr-only` text when collapsed
- Footer: role badge, profile link, logout button

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- src/tests/Sidebar.test.jsx`
Expected: PASS (all 5 tests pass)

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
- Produces: Flex two-column application shell when authenticated; clean header when guest.

- [ ] **Step 1: Check existing `App` and layout test coverage**

Run: `npm --prefix frontend test -- src/tests/Auth.test.jsx`
Verify how login/navigation is currently exercised.

- [ ] **Step 2: Update `App.jsx` layout structure**

- When `!isAuthenticated || !user`:
  - Show minimal guest `<Navbar />` (or top header) + `<main className="flex-1">`
- When `isAuthenticated && user`:
  - Render wrapper `<div className="flex h-screen w-screen overflow-hidden bg-gray-50 text-gray-900">`
  - Cột trái: `<Sidebar />` (`shrink-0`)
  - Cột phải: `<main className="flex-1 min-w-0 h-screen overflow-y-auto">` containing `<Routes>`
- Tối ưu `Navbar.jsx`: Khi đã đăng nhập thì trả về `null` (được thay thế trọn vẹn bởi `Sidebar`), khi chưa đăng nhập hiển thị header tối giản Logo + Đăng nhập/Đăng ký.

- [ ] **Step 3: Run full Vitest suite to ensure no regressions in auth/navigation**

Run: `npm --prefix frontend test -- --run`
Expected: All tests pass (including Auth, AdminPages, AssetFlows).

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
- In `ProfilePage.jsx`: Thẻ bọc ngoài `w-full`, nhưng các thẻ card form bên trong giữ `max-w-3xl mx-auto` hoặc `max-w-4xl`.
- Verify data table containers in `AssetListPage.jsx`, `RequestInboxPage.jsx`, `UserAdminPage.jsx`: Verify each table is wrapped in `<div className="overflow-x-auto">` to ensure internal horizontal scrolling without page bleed.

- [ ] **Step 2: Optimize `DashboardPage.jsx` KPI Grid and Charts Grid**

- KPI cards grid: `grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`.
  - At 1024px: 3 cards x 2 rows (clean, no text truncation).
  - At 1366px & 1920px: 6 cards x 1 row (spacious, full utilization of wide screen).
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

### Task 4: Verification Across Screen Sizes, Production Build, and Playwright E2E

**Files:**
- Modify: `docs/verification-report.md` (Update report with new UI tests and verification results)

**Interfaces:**
- Consumes: Vite production build, Vitest suite, Playwright browser test runner.
- Produces: Complete verification proof with exact test counts and screen size checks.

- [ ] **Step 1: Run frontend production build**

Run: `npm --prefix frontend run build`
Expected: Build successfully completes without any syntax or CSS errors.

- [ ] **Step 2: Run all frontend unit tests**

Run: `npm --prefix frontend test -- --run`
Expected: All 40 unit tests pass (35 baseline + 5 Sidebar tests).

- [ ] **Step 3: Run Playwright E2E test suite**

Run: `npm --prefix frontend run test:e2e`
Expected: 1 test suite passing (containing all 8 lifecycle steps: register, admin activation, user allocate request, manager allocate approval, user return request, manager return approval, personal history check, RBAC guard check).

- [ ] **Step 4: Verify responsive layout via browser CLI/evaluation (1024px, 1366px, 1920px)**

Run Playwright screenshot/viewport check script to ensure zero horizontal scrollbar on body at 1024x768, 1366x768, and 1920x1080 in both collapsed and expanded sidebar states.

- [ ] **Step 5: Record results in `docs/verification-report.md` and commit**

```bash
git add docs/verification-report.md
git commit -m "docs: record verification report for UI sidebar and full-width layout"
```
