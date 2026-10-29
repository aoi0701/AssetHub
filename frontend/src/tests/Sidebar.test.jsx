import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import Sidebar from '../components/Sidebar';
import { AuthProvider } from '../context/AuthContext';

describe('Sidebar Component Unit Tests', () => {
  const adminUser = {
    id: 1,
    username: 'adminuser',
    full_name: 'Quản Trị Viên',
    role: 'ADMIN',
  };

  const managerUser = {
    id: 2,
    username: 'manageruser',
    full_name: 'Quản Lý Kho',
    role: 'MANAGER',
  };

  const standardUser = {
    id: 3,
    username: 'staffuser',
    full_name: 'Nhân Viên Ban Kỹ Thuật',
    role: 'USER',
  };

  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  const renderSidebar = (user = adminUser, initialRoute = '/dashboard') => {
    return render(
      <MemoryRouter initialEntries={[initialRoute]}>
        <AuthProvider initialUser={user}>
          <Routes>
            <Route
              path="*"
              element={
                <div className="flex">
                  <Sidebar />
                  <div data-testid="main-content">Content</div>
                </div>
              }
            />
            <Route path="/login" element={<div data-testid="login-page">Login Page</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );
  };

  describe('RBAC Menu Items Rendering', () => {
    it('renders exactly 6 navigation items for ADMIN role', () => {
      renderSidebar(adminUser);

      const nav = screen.getByRole('navigation', { name: /thanh điều hướng|menu|main/i });
      expect(nav).toBeInTheDocument();

      const expectedAdminTabs = [
        { name: 'Dashboard', href: '/dashboard' },
        { name: 'Tài sản', href: '/assets' },
        { name: 'Yêu cầu', href: '/requests' },
        { name: 'Người dùng', href: '/users' },
        { name: 'Phòng ban', href: '/departments' },
        { name: 'Danh mục', href: '/categories' },
      ];

      expectedAdminTabs.forEach((tab) => {
        const link = screen.getByRole('link', { name: tab.name });
        expect(link).toBeInTheDocument();
        expect(link).toHaveAttribute('href', tab.href);
      });

      // Role badge
      expect(screen.getByText('ADMIN')).toBeInTheDocument();

      // Manager-only or User-only tabs should NOT exist in navigation
      expect(screen.queryByRole('link', { name: 'Nhân viên' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Tài sản của tôi' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Lịch sử' })).toBeNull();
    });

    it('renders exactly 4 navigation items for MANAGER role', () => {
      renderSidebar(managerUser);

      const expectedManagerTabs = [
        { name: 'Dashboard', href: '/dashboard' },
        { name: 'Tài sản', href: '/assets' },
        { name: 'Yêu cầu', href: '/requests' },
        { name: 'Nhân viên', href: '/users' },
      ];

      expectedManagerTabs.forEach((tab) => {
        const link = screen.getByRole('link', { name: tab.name });
        expect(link).toBeInTheDocument();
        expect(link).toHaveAttribute('href', tab.href);
      });

      expect(screen.getByText('MANAGER')).toBeInTheDocument();

      // Admin-only tabs should NOT exist
      expect(screen.queryByRole('link', { name: 'Người dùng' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Phòng ban' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Danh mục' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Tài sản của tôi' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Lịch sử' })).toBeNull();
    });

    it('renders exactly 3 navigation items for USER role', () => {
      renderSidebar(standardUser, '/my-assets');

      const expectedUserTabs = [
        { name: 'Tài sản của tôi', href: '/my-assets' },
        { name: 'Lịch sử', href: '/my-history' },
        { name: 'Yêu cầu', href: '/my-requests' },
      ];

      expectedUserTabs.forEach((tab) => {
        const link = screen.getByRole('link', { name: tab.name });
        expect(link).toBeInTheDocument();
        expect(link).toHaveAttribute('href', tab.href);
      });

      expect(screen.getByText('USER')).toBeInTheDocument();

      // Admin & Manager tabs should NOT exist
      expect(screen.queryByRole('link', { name: 'Dashboard' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Người dùng' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Nhân viên' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Phòng ban' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Danh mục' })).toBeNull();
    });
  });

  describe('Collapse & Expand Functionality', () => {
    it('toggles collapse state, updates aria-expanded, aria-label, tooltip title and localStorage', async () => {
      renderSidebar(adminUser);

      const aside = screen.getByRole('complementary');
      expect(aside).toHaveClass('w-64');
      expect(aside).not.toHaveClass('w-20');

      // Toggle button initial state: expanded
      const toggleBtn = screen.getByRole('button', { name: /thu gọn thanh điều hướng/i });
      expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
      expect(toggleBtn).toHaveAttribute('title', 'Thu gọn thanh điều hướng');

      // Click to collapse
      await userEvent.click(toggleBtn);

      // Now collapsed
      expect(aside).toHaveClass('w-20');
      expect(aside).not.toHaveClass('w-64');
      expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
      expect(toggleBtn).toHaveAttribute('aria-label', 'Mở rộng thanh điều hướng');
      expect(toggleBtn).toHaveAttribute('title', 'Mở rộng thanh điều hướng');
      expect(localStorage.getItem('assethub_sidebar_collapsed')).toBe('true');

      // Accessible names and tooltip titles are preserved even when collapsed
      const dashboardLink = screen.getByRole('link', { name: 'Dashboard' });
      expect(dashboardLink).toBeInTheDocument();
      expect(dashboardLink).toHaveAttribute('title', 'Dashboard');

      // Click to expand again
      await userEvent.click(toggleBtn);

      expect(aside).toHaveClass('w-64');
      expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
      expect(toggleBtn).toHaveAttribute('aria-label', 'Thu gọn thanh điều hướng');
      expect(toggleBtn).toHaveAttribute('title', 'Thu gọn thanh điều hướng');
      expect(localStorage.getItem('assethub_sidebar_collapsed')).toBe('false');
    });

    it('restores collapsed state from localStorage on initial render and across remount', async () => {
      localStorage.setItem('assethub_sidebar_collapsed', 'true');

      const { unmount } = renderSidebar(adminUser);

      let aside = screen.getByRole('complementary');
      expect(aside).toHaveClass('w-20');

      let toggleBtn = screen.getByRole('button', { name: /mở rộng thanh điều hướng/i });
      expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');

      // Toggle to expand
      await userEvent.click(toggleBtn);
      expect(localStorage.getItem('assethub_sidebar_collapsed')).toBe('false');

      // Unmount and remount
      unmount();
      renderSidebar(adminUser);

      aside = screen.getByRole('complementary');
      expect(aside).toHaveClass('w-64');
      toggleBtn = screen.getByRole('button', { name: /thu gọn thanh điều hướng/i });
      expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
    });
  });

  describe('LocalStorage Error Resilience', () => {
    it('handles localStorage SecurityError or exception safely and defaults to expanded', async () => {
      const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new DOMException('Access denied', 'SecurityError');
      });
      const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new DOMException('Access denied', 'SecurityError');
      });

      // Component should render without throwing
      renderSidebar(adminUser);

      const aside = screen.getByRole('complementary');
      expect(aside).toHaveClass('w-64');

      const toggleBtn = screen.getByRole('button', { name: /thu gọn thanh điều hướng/i });
      expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');

      // Clicking toggle should update state in memory without crashing
      await userEvent.click(toggleBtn);
      expect(aside).toHaveClass('w-20');
      expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');

      getItemSpy.mockRestore();
      setItemSpy.mockRestore();
    });
  });

  describe('Profile Link & Logout Action', () => {
    it('renders profile link with user name and links to /profile', () => {
      renderSidebar(adminUser);

      const profileLink = screen.getByRole('link', { name: /Quản Trị Viên/i });
      expect(profileLink).toBeInTheDocument();
      expect(profileLink).toHaveAttribute('href', '/profile');
    });

    it('falls back to username if full_name is empty', () => {
      const userWithoutFullName = {
        id: 4,
        username: 'nonameuser',
        full_name: '',
        role: 'USER',
      };
      renderSidebar(userWithoutFullName, '/my-assets');

      const profileLink = screen.getByRole('link', { name: /nonameuser/i });
      expect(profileLink).toBeInTheDocument();
      expect(profileLink).toHaveAttribute('href', '/profile');
    });

    it('clicking logout triggers logout and redirects to /login', async () => {
      renderSidebar(adminUser);

      const logoutBtn = screen.getByRole('button', { name: /đăng xuất/i });
      expect(logoutBtn).toBeInTheDocument();
      expect(logoutBtn).toHaveAttribute('title', 'Đăng xuất');

      await userEvent.click(logoutBtn);

      expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    });

    it('renders nothing if not authenticated or user is null', () => {
      const { container } = render(
        <MemoryRouter>
          <AuthProvider initialUser={null}>
            <Sidebar />
          </AuthProvider>
        </MemoryRouter>
      );

      expect(container.firstChild).toBeNull();
    });
  });
});
