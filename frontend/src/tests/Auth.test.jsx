import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeEach, beforeAll, afterAll, afterEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import axiosClient, {
  setTokens,
  getAccessToken,
  getRefreshToken,
  clearTokens,
} from '../api/axiosClient';
import { AuthProvider, useAuth } from '../context/AuthContext';
import LoginPage from '../pages/LoginPage';
import RegisterPage from '../pages/RegisterPage';
import ProfilePage from '../pages/ProfilePage';
import ProtectedRoute from '../components/ProtectedRoute';

const server = setupServer(
  http.post('/api/auth/login/', async ({ request }) => {
    const body = await request.json();
    if (body.username === 'testuser' && body.password === 'correctpass') {
      return HttpResponse.json({
        access: 'mock-access-token-123',
        refresh: 'mock-refresh-token-456',
        user: {
          id: 1,
          username: 'testuser',
          email: 'test@example.com',
          full_name: 'Test User',
          phone: '0123456789',
          role: 'ADMIN',
          department_id: 1,
          department_name: 'IT',
        },
      });
    }
    return HttpResponse.json(
      { detail: 'Tên đăng nhập hoặc mật khẩu không chính xác hoặc tài khoản chưa kích hoạt.' },
      { status: 401 }
    );
  }),

  http.post('/api/auth/register/', async ({ request }) => {
    const body = await request.json();
    if (body.username && body.email && body.password) {
      return HttpResponse.json(
        { message: 'Đăng ký thành công. Tài khoản đang chờ Admin phê duyệt.' },
        { status: 201 }
      );
    }
    return HttpResponse.json({ username: ['Tên đăng nhập đã tồn tại.'] }, { status: 400 });
  }),

  http.patch('/api/auth/profile/', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json({
      id: 1,
      username: 'testuser',
      full_name: body.full_name || 'Updated Name',
      email: body.email || 'updated@example.com',
      phone: body.phone || '0987654321',
      role: 'ADMIN',
      department_id: 1,
    });
  }),

  http.post('/api/auth/change-password/', async ({ request }) => {
    const body = await request.json();
    if (body.old_password === 'wrong') {
      return HttpResponse.json(
        { old_password: ['Mật khẩu cũ không chính xác.'] },
        { status: 400 }
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.post('/api/auth/logout/', async () => {
    return new HttpResponse(null, { status: 204 });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  clearTokens();
  localStorage.clear();
  sessionStorage.clear();
});
afterAll(() => server.close());

describe('Auth Flow & Architecture Tests', () => {
  it('login success stores tokens in memory and strictly not in localStorage', async () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await userEvent.type(screen.getByLabelText(/Tên đăng nhập/i), 'testuser');
    await userEvent.type(screen.getByLabelText(/Mật khẩu/i), 'correctpass');
    await userEvent.click(screen.getByRole('button', { name: /Đăng nhập/i }));

    await waitFor(() => {
      expect(getAccessToken()).toBe('mock-access-token-123');
      expect(getRefreshToken()).toBe('mock-refresh-token-456');
    });

    // STRICT V1 check: No tokens in localStorage or sessionStorage
    expect(localStorage.getItem('access')).toBeNull();
    expect(localStorage.getItem('token')).toBeNull();
    expect(sessionStorage.getItem('access')).toBeNull();
  });

  it('login failure with invalid credentials shows Vietnamese error alert', async () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await userEvent.type(screen.getByLabelText(/Tên đăng nhập/i), 'testuser');
    await userEvent.type(screen.getByLabelText(/Mật khẩu/i), 'wrongpass');
    await userEvent.click(screen.getByRole('button', { name: /Đăng nhập/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'Tên đăng nhập hoặc mật khẩu không chính xác hoặc tài khoản chưa kích hoạt.'
    );
    expect(getAccessToken()).toBeNull();
  });

  it('register user shows pending approval message on 201 Created', async () => {
    render(
      <MemoryRouter initialEntries={['/register']}>
        <AuthProvider>
          <RegisterPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await userEvent.type(screen.getByLabelText(/Tên đăng nhập/i), 'newuser');
    await userEvent.type(screen.getByLabelText(/Email/i), 'newuser@example.com');
    await userEvent.type(screen.getByLabelText(/Họ và tên/i), 'Người Dùng Mới');
    await userEvent.type(screen.getByLabelText(/Số điện thoại/i), '0901234567');
    await userEvent.type(screen.getByLabelText(/^Mật khẩu/i), 'SecurePass123!');
    await userEvent.type(screen.getByLabelText(/Xác nhận mật khẩu/i), 'SecurePass123!');

    await userEvent.click(screen.getByRole('button', { name: /Đăng ký/i }));

    const statusBanner = await screen.findByRole('status');
    expect(statusBanner).toHaveTextContent(
      'Đăng ký thành công!'
    );
    expect(statusBanner).toHaveTextContent(
      'Tài khoản đang chờ Quản trị viên kích hoạt trước khi có thể đăng nhập.'
    );
  });

  it('profile update and change password work as expected', async () => {
    const initialUser = {
      id: 1,
      username: 'testuser',
      email: 'old@example.com',
      full_name: 'Old Name',
      phone: '0111111111',
      role: 'ADMIN',
    };

    render(
      <MemoryRouter initialEntries={['/profile']}>
        <AuthProvider initialUser={initialUser} initialToken="dummy-token">
          <ProfilePage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Profile form tab
    expect(screen.getByDisplayValue('Old Name')).toBeInTheDocument();
    const nameInput = screen.getByLabelText(/Họ và tên/i);
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'New Name');
    await userEvent.click(screen.getByRole('button', { name: /Lưu thay đổi/i }));

    const successMsg = await screen.findByRole('status');
    expect(successMsg).toHaveTextContent('Cập nhật thông tin cá nhân thành công!');

    // Switch to Change Password tab
    await userEvent.click(screen.getByRole('button', { name: /Đổi mật khẩu/i }));

    await userEvent.type(screen.getByLabelText(/Mật khẩu hiện tại/i), 'correctold');
    await userEvent.type(screen.getByLabelText(/^Mật khẩu mới/i), 'NewStrongPassword123!');
    await userEvent.type(screen.getByLabelText(/Xác nhận mật khẩu mới/i), 'NewStrongPassword123!');

    await userEvent.click(screen.getByRole('button', { name: /Xác nhận đổi mật khẩu/i }));

    const pwSuccess = await screen.findByRole('status');
    expect(pwSuccess).toHaveTextContent('Đổi mật khẩu thành công!');
  });

  it('logout clears memory state and tokens', async () => {
    const LogoutConsumer = () => {
      const { user, isAuthenticated, logout } = useAuth();
      return (
        <div>
          <div data-testid="auth-status">{isAuthenticated ? 'LOGGED_IN' : 'LOGGED_OUT'}</div>
          <div data-testid="user-info">{user?.username || 'NO_USER'}</div>
          <button onClick={logout}>Do Logout</button>
        </div>
      );
    };

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'tester', role: 'USER' }}
          initialToken="token-xyz"
        >
          <LogoutConsumer />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByTestId('auth-status')).toHaveTextContent('LOGGED_IN');
    expect(getAccessToken()).toBe('token-xyz');

    await userEvent.click(screen.getByRole('button', { name: /Do Logout/i }));

    await waitFor(() => {
      expect(screen.getByTestId('auth-status')).toHaveTextContent('LOGGED_OUT');
      expect(screen.getByTestId('user-info')).toHaveTextContent('NO_USER');
      expect(getAccessToken()).toBeNull();
    });
  });

  it('handles concurrent 401 requests with a single refresh call queue', async () => {
    let refreshCallCount = 0;
    let req1Attempts = 0;
    let req2Attempts = 0;

    server.use(
      http.post('/api/auth/refresh/', async ({ request }) => {
        refreshCallCount++;
        const body = await request.json();
        if (body.refresh === 'valid-refresh') {
          return HttpResponse.json({ access: 'refreshed-access-token' });
        }
        return new HttpResponse(null, { status: 401 });
      }),

      http.get('/api/test-res-1/', ({ request }) => {
        req1Attempts++;
        const auth = request.headers.get('Authorization');
        if (auth === 'Bearer refreshed-access-token') {
          return HttpResponse.json({ result: 'req1-ok' });
        }
        return new HttpResponse(null, { status: 401 });
      }),

      http.get('/api/test-res-2/', ({ request }) => {
        req2Attempts++;
        const auth = request.headers.get('Authorization');
        if (auth === 'Bearer refreshed-access-token') {
          return HttpResponse.json({ result: 'req2-ok' });
        }
        return new HttpResponse(null, { status: 401 });
      })
    );

    setTokens({ access: 'expired-token', refresh: 'valid-refresh' });

    // Fire two concurrent requests while token is expired
    const [res1, res2] = await Promise.all([
      axiosClient.get('/api/test-res-1/'),
      axiosClient.get('/api/test-res-2/'),
    ]);

    expect(res1.data.result).toBe('req1-ok');
    expect(res2.data.result).toBe('req2-ok');

    // ONLY ONE refresh call should have taken place!
    expect(refreshCallCount).toBe(1);
    expect(getAccessToken()).toBe('refreshed-access-token');
    expect(req1Attempts).toBe(2);
    expect(req2Attempts).toBe(2);
  });

  it('ProtectedRoute guards roles correctly (Admin allowed, User blocked with 403)', () => {
    // 1. Unauthenticated -> redirects to /login
    const { unmount } = render(
      <MemoryRouter initialEntries={['/admin-only']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<div>Login Screen</div>} />
            <Route
              path="/admin-only"
              element={
                <ProtectedRoute allowedRoles={['ADMIN']}>
                  <div>Admin Secret Content</div>
                </ProtectedRoute>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText('Login Screen')).toBeInTheDocument();
    unmount();

    // 2. USER role trying to access ADMIN route -> 403 screen
    const { unmount: unmount2 } = render(
      <MemoryRouter initialEntries={['/admin-only']}>
        <AuthProvider
          initialUser={{ id: 2, username: 'regularuser', role: 'USER' }}
          initialToken="token-u"
        >
          <Routes>
            <Route
              path="/admin-only"
              element={
                <ProtectedRoute allowedRoles={['ADMIN']}>
                  <div>Admin Secret Content</div>
                </ProtectedRoute>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText(/403 - Truy cập bị từ chối/i)).toBeInTheDocument();
    expect(screen.queryByText('Admin Secret Content')).not.toBeInTheDocument();
    unmount2();

    // 3. ADMIN role allowed
    render(
      <MemoryRouter initialEntries={['/admin-only']}>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-a"
        >
          <Routes>
            <Route
              path="/admin-only"
              element={
                <ProtectedRoute allowedRoles={['ADMIN']}>
                  <div>Admin Secret Content</div>
                </ProtectedRoute>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText('Admin Secret Content')).toBeInTheDocument();
  });
});
