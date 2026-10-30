import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider } from '../context/AuthContext';
import UserAdminPage from '../pages/UserAdminPage';
import DepartmentPage from '../pages/DepartmentPage';
import CategoryPage from '../pages/CategoryPage';

let mockUsers = [
  {
    id: 1,
    username: 'adminuser',
    email: 'admin@example.com',
    full_name: 'Admin User',
    phone: '0911111111',
    role: 'ADMIN',
    department_id: 1,
    department_name: 'Phòng IT',
    department_code: 'IT',
    is_active: true,
  },
  {
    id: 2,
    username: 'staffuser',
    email: 'staff@example.com',
    full_name: 'Nhân Viên A',
    phone: '0922222222',
    role: 'USER',
    department_id: 1,
    department_name: 'Phòng IT',
    department_code: 'IT',
    is_active: true,
  },
];

let mockDepartments = [
  { id: 1, code: 'IT', name: 'Phòng Công nghệ', description: 'Phụ trách kỹ thuật' },
  { id: 2, code: 'HR', name: 'Phòng Nhân sự', description: 'Phụ trách tuyển dụng' },
];

let mockCategories = [
  { id: 1, name: 'Laptop', description: 'Máy tính xách tay các loại' },
  { id: 2, name: 'Màn hình', description: 'Màn hình hiển thị Dell/LG' },
];

const server = setupServer(
  http.get('/api/users/', ({ request }) => {
    const url = new URL(request.url);
    const allParam = url.searchParams.get('all');
    let filtered = [...mockUsers];

    const role = url.searchParams.get('role');
    if (role) {
      filtered = filtered.filter((u) => u.role === role);
    }
    const deptId = url.searchParams.get('department_id');
    if (deptId) {
      filtered = filtered.filter((u) => String(u.department_id) === String(deptId));
    }
    const isActive = url.searchParams.get('is_active');
    if (isActive !== null && isActive !== '') {
      const activeBool = isActive === 'true';
      filtered = filtered.filter((u) => u.is_active === activeBool);
    }
    const search = (url.searchParams.get('search') || '').toLowerCase().trim();
    if (search) {
      filtered = filtered.filter(
        (u) =>
          u.username.toLowerCase().includes(search) ||
          (u.full_name && u.full_name.toLowerCase().includes(search)) ||
          (u.email && u.email.toLowerCase().includes(search))
      );
    }

    if (allParam === 'true') {
      return HttpResponse.json(filtered);
    }

    const pageParam = url.searchParams.get('page');
    if (pageParam) {
      const page = parseInt(pageParam, 10);
      const pageSize = parseInt(url.searchParams.get('page_size') || '15', 10);
      const start = (page - 1) * pageSize;
      return HttpResponse.json({
        count: filtered.length,
        total_pages: Math.ceil(filtered.length / pageSize) || 1,
        current_page: page,
        page_size: pageSize,
        results: filtered.slice(start, start + pageSize),
      });
    }
    return HttpResponse.json(filtered);
  }),

  http.get('/api/departments/', ({ request }) => {
    const url = new URL(request.url);
    const allParam = url.searchParams.get('all');
    let filtered = [...mockDepartments];

    const search = (url.searchParams.get('search') || '').toLowerCase().trim();
    if (search) {
      filtered = filtered.filter(
        (d) =>
          (d.name && d.name.toLowerCase().includes(search)) ||
          (d.code && d.code.toLowerCase().includes(search)) ||
          (d.description && d.description.toLowerCase().includes(search))
      );
    }

    if (allParam === 'true') {
      return HttpResponse.json(filtered);
    }

    const pageParam = url.searchParams.get('page');
    if (pageParam) {
      const page = parseInt(pageParam, 10);
      const pageSize = parseInt(url.searchParams.get('page_size') || '15', 10);
      const start = (page - 1) * pageSize;
      return HttpResponse.json({
        count: filtered.length,
        total_pages: Math.ceil(filtered.length / pageSize) || 1,
        current_page: page,
        page_size: pageSize,
        results: filtered.slice(start, start + pageSize),
      });
    }
    return HttpResponse.json(mockDepartments);
  }),

  http.post('/api/departments/', async ({ request }) => {
    const body = await request.json();
    const newDept = { id: 3, ...body };
    mockDepartments.push(newDept);
    return HttpResponse.json(newDept, { status: 201 });
  }),

  http.patch('/api/departments/:id/', async ({ request, params }) => {
    const body = await request.json();
    const id = Number(params.id);
    const index = mockDepartments.findIndex((d) => d.id === id);
    if (index !== -1) {
      mockDepartments[index] = { ...mockDepartments[index], ...body };
      return HttpResponse.json(mockDepartments[index]);
    }
    return new HttpResponse(null, { status: 404 });
  }),

  http.get('/api/categories/', ({ request }) => {
    const url = new URL(request.url);
    const allParam = url.searchParams.get('all');
    let filtered = [...mockCategories];

    const search = (url.searchParams.get('search') || '').toLowerCase().trim();
    if (search) {
      filtered = filtered.filter(
        (c) =>
          (c.name && c.name.toLowerCase().includes(search)) ||
          (c.description && c.description.toLowerCase().includes(search))
      );
    }

    if (allParam === 'true') {
      return HttpResponse.json(filtered);
    }

    const pageParam = url.searchParams.get('page');
    if (pageParam) {
      const page = parseInt(pageParam, 10);
      const pageSize = parseInt(url.searchParams.get('page_size') || '15', 10);
      const start = (page - 1) * pageSize;
      return HttpResponse.json({
        count: filtered.length,
        total_pages: Math.ceil(filtered.length / pageSize) || 1,
        current_page: page,
        page_size: pageSize,
        results: filtered.slice(start, start + pageSize),
      });
    }
    return HttpResponse.json(filtered);
  }),

  http.post('/api/categories/', async ({ request }) => {
    const body = await request.json();
    const newCat = { id: 3, ...body };
    mockCategories.push(newCat);
    return HttpResponse.json(newCat, { status: 201 });
  }),

  http.patch('/api/categories/:id/', async ({ request, params }) => {
    const body = await request.json();
    const id = Number(params.id);
    const index = mockCategories.findIndex((c) => c.id === id);
    if (index !== -1) {
      mockCategories[index] = { ...mockCategories[index], ...body };
      return HttpResponse.json(mockCategories[index]);
    }
    return new HttpResponse(null, { status: 404 });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  mockUsers = [
    {
      id: 1,
      username: 'adminuser',
      email: 'admin@example.com',
      full_name: 'Admin User',
      phone: '0911111111',
      role: 'ADMIN',
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      is_active: true,
    },
    {
      id: 2,
      username: 'staffuser',
      email: 'staff@example.com',
      full_name: 'Nhân Viên A',
      phone: '0922222222',
      role: 'USER',
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      is_active: true,
    },
  ];
  mockDepartments = [
    { id: 1, code: 'IT', name: 'Phòng Công nghệ', description: 'Phụ trách kỹ thuật' },
    { id: 2, code: 'HR', name: 'Phòng Nhân sự', description: 'Phụ trách tuyển dụng' },
  ];
  mockCategories = [
    { id: 1, name: 'Laptop', description: 'Máy tính xách tay các loại' },
    { id: 2, name: 'Màn hình', description: 'Màn hình hiển thị Dell/LG' },
  ];
});
afterAll(() => server.close());

describe('Admin Management Pages Tests', () => {
  it('UserAdminPage renders users and allows Admin to toggle active and change department', async () => {
    let patchedPayload = null;

    server.use(
      http.patch('/api/users/:id/', async ({ request, params }) => {
        patchedPayload = await request.json();
        const id = Number(params.id);
        const user = mockUsers.find((u) => u.id === id);
        if (user) {
          Object.assign(user, patchedPayload);
          return HttpResponse.json(user);
        }
        return new HttpResponse(null, { status: 404 });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <UserAdminPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Verify both users are displayed
    expect(await screen.findByText('Admin User')).toBeInTheDocument();
    expect(screen.getByText('Nhân Viên A')).toBeInTheDocument();

    // Admin toggles active status of staffuser (Click "Khóa")
    const lockButtons = screen.getAllByRole('button', { name: 'Khóa' });
    await userEvent.click(lockButtons[1]); // staffuser's lock button

    await waitFor(() => {
      expect(patchedPayload).toEqual({ is_active: false });
    });

    // Admin opens edit modal for staffuser
    const editButtons = screen.getAllByRole('button', { name: /Sửa/i });
    await userEvent.click(editButtons[1]);

    const modal = screen.getByRole('dialog');
    expect(modal).toBeInTheDocument();

    // Select Department 2 (HR) inside modal
    const deptSelect = within(modal).getByLabelText('Phòng ban');
    await userEvent.selectOptions(deptSelect, '2');

    await userEvent.click(screen.getByRole('button', { name: /Lưu thay đổi/i }));

    await waitFor(() => {
      expect(patchedPayload).toMatchObject({ department_id: 2 });
    });

    // STRICT CHECK: Ensure NO DELETE button is rendered in UserAdminPage
    expect(screen.queryByRole('button', { name: /Xóa|Delete/i })).toBeNull();
  });

  it('UserAdminPage shows friendly alert on 409 Conflict', async () => {
    server.use(
      http.patch('/api/users/:id/', () => {
        return HttpResponse.json(
          {
            detail:
              'Tài khoản đang giữ tài sản hoặc có phiếu chờ xử lý, không thể đổi phòng ban hoặc vô hiệu hóa.',
          },
          { status: 409 }
        );
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <UserAdminPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Nhân Viên A')).toBeInTheDocument();

    const lockButtons = screen.getAllByRole('button', { name: 'Khóa' });
    await userEvent.click(lockButtons[1]);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'Tài khoản đang giữ tài sản hoặc có phiếu chờ xử lý, không thể đổi phòng ban hoặc vô hiệu hóa.'
    );
  });

  it('UserAdminPage: Manager sees read-only view without edit/activate buttons', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{
            id: 3,
            username: 'deptmanager',
            role: 'MANAGER',
            department_id: 1,
            department_name: 'Phòng IT',
          }}
          initialToken="token-manager"
        >
          <UserAdminPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Nhân Viên A')).toBeInTheDocument();

    // In Manager view, no "Sửa" or "Khóa"/"Kích hoạt" buttons are rendered
    expect(screen.queryByRole('button', { name: /Sửa/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Khóa/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Kích hoạt/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Xóa|Delete/i })).toBeNull();
  });

  it('DepartmentPage: Admin can create and edit; verify absence of DELETE buttons', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <DepartmentPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Initial departments rendered
    expect(await screen.findByText('Phòng Công nghệ')).toBeInTheDocument();
    expect(screen.getByText('Phòng Nhân sự')).toBeInTheDocument();

    // Verify ABSENCE of any delete buttons
    expect(screen.queryByRole('button', { name: /Xóa|Delete/i })).toBeNull();

    // Open Create Modal
    await userEvent.click(screen.getByRole('button', { name: /Thêm phòng ban/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Mã phòng ban/i), 'ACC');
    await userEvent.type(screen.getByLabelText(/Tên phòng ban/i), 'Phòng Kế toán');
    await userEvent.type(screen.getByLabelText(/Mô tả/i), 'Quản lý tài chính');
    await userEvent.click(screen.getByRole('button', { name: /Lưu phòng ban/i }));

    expect(await screen.findByText('Phòng Kế toán')).toBeInTheDocument();

    // Edit an existing department
    const editButtons = screen.getAllByRole('button', { name: /Sửa/i });
    await userEvent.click(editButtons[0]);

    const nameInput = screen.getByLabelText(/Tên phòng ban/i);
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Phòng Công nghệ và Chuyển đổi số');
    await userEvent.click(screen.getByRole('button', { name: /Lưu phòng ban/i }));

    expect(await screen.findByText('Phòng Công nghệ và Chuyển đổi số')).toBeInTheDocument();

    // Re-verify NO delete buttons exist anywhere
    expect(screen.queryByRole('button', { name: /Xóa|Delete/i })).toBeNull();
  });

  it('CategoryPage: Admin can create and edit; verify absence of DELETE buttons', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <CategoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Initial categories rendered
    expect(await screen.findByText('Laptop')).toBeInTheDocument();
    expect(screen.getByText('Màn hình')).toBeInTheDocument();

    // Verify ABSENCE of any delete buttons
    expect(screen.queryByRole('button', { name: /Xóa|Delete/i })).toBeNull();

    // Open Create Modal
    await userEvent.click(screen.getByRole('button', { name: /Thêm danh mục/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Tên danh mục/i), 'Máy in & Photo');
    await userEvent.type(screen.getByLabelText(/Mô tả/i), 'Thiết bị in ấn văn phòng');
    await userEvent.click(screen.getByRole('button', { name: /Lưu danh mục/i }));

    expect(await screen.findByText('Máy in & Photo')).toBeInTheDocument();

    // Edit an existing category
    const editButtons = screen.getAllByRole('button', { name: /Sửa/i });
    await userEvent.click(editButtons[0]);

    const nameInput = screen.getByLabelText(/Tên danh mục/i);
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Laptop & Máy trạm');
    await userEvent.click(screen.getByRole('button', { name: /Lưu danh mục/i }));

    expect(await screen.findByText('Laptop & Máy trạm')).toBeInTheDocument();

    // Re-verify NO delete buttons exist anywhere
    expect(screen.queryByRole('button', { name: /Xóa|Delete/i })).toBeNull();
  });

  it('CategoryPage: search filter narrows down items and displays count correctly', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <CategoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Laptop')).toBeInTheDocument();
    expect(screen.getByText('Màn hình')).toBeInTheDocument();
    expect(screen.getByText(/Tổng số:/i)).toHaveTextContent('2');

    // Type in search box
    const searchInput = screen.getByPlaceholderText(/Tìm theo tên hoặc mô tả/i);
    await userEvent.type(searchInput, 'Dell');

    // 'Màn hình' has description 'Màn hình hiển thị Dell/LG' -> should be visible
    expect(screen.getByText('Màn hình')).toBeInTheDocument();
    // 'Laptop' has description 'Máy tính xách tay các loại' -> should be hidden
    expect(screen.queryByText('Laptop')).toBeNull();
    expect(screen.getByText(/Tìm thấy/i)).toHaveTextContent('1');

    // Clear search
    const clearBtn = screen.getByRole('button', { name: /Xóa tìm kiếm/i });
    await userEvent.click(clearBtn);

    expect(screen.getByText('Laptop')).toBeInTheDocument();
    expect(screen.getByText('Màn hình')).toBeInTheDocument();
  });

  it('CategoryPage modal: closes on Escape, closes on X button, and retains input on error', async () => {
    server.use(
      http.post('/api/categories/', () => {
        return HttpResponse.json(
          { name: ['Tên danh mục này đã tồn tại trong hệ thống.'] },
          { status: 400 }
        );
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <CategoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Laptop')).toBeInTheDocument();

    const addBtn = screen.getByRole('button', { name: /Thêm danh mục/i });
    await userEvent.click(addBtn);

    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();

    // Type input
    const nameInput = screen.getByLabelText(/Tên danh mục/i);
    const descInput = screen.getByLabelText(/Mô tả/i);
    await userEvent.type(nameInput, 'Laptop');
    await userEvent.type(descInput, 'Mô tả thử nghiệm');

    // Submit form -> triggers 400 error
    await userEvent.click(screen.getByRole('button', { name: /Lưu danh mục/i }));

    // Alert error visible
    expect(await screen.findByText(/Tên danh mục này đã tồn tại/i)).toBeInTheDocument();

    // Verify inputs retain entered data
    expect(nameInput).toHaveValue('Laptop');
    expect(descInput).toHaveValue('Mô tả thử nghiệm');

    // Press Escape to close modal
    await userEvent.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });

  it('CategoryPage: renders pagination controls and navigates across pages', async () => {
    const manyCategories = Array.from({ length: 25 }, (_, i) => ({
      id: i + 1,
      name: `Category ${i + 1}`,
      description: `Description ${i + 1}`,
    }));

    server.use(
      http.get('/api/categories/', ({ request }) => {
        const url = new URL(request.url);
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = 15;
        const start = (page - 1) * pageSize;
        const results = manyCategories.slice(start, start + pageSize);
        return HttpResponse.json({
          count: manyCategories.length,
          total_pages: Math.ceil(manyCategories.length / pageSize),
          current_page: page,
          page_size: pageSize,
          results,
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <CategoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Category 1')).toBeInTheDocument();
    });

    expect(screen.getByText(/Đang xem 1–15 trên tổng 25 bản ghi/i)).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    expect(nextBtn).toBeEnabled();
    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(screen.getByText('Category 16')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 16–25 trên tổng 25 bản ghi/i)).toBeInTheDocument();
  });

  it('DepartmentPage: renders pagination controls and navigates across pages', async () => {
    const manyDepts = Array.from({ length: 20 }, (_, i) => ({
      id: i + 1,
      code: `DP${i + 1}`,
      name: `Phòng Ban ${i + 1}`,
      description: `Mô tả ${i + 1}`,
    }));

    server.use(
      http.get('/api/departments/', ({ request }) => {
        const url = new URL(request.url);
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = 15;
        const start = (page - 1) * pageSize;
        return HttpResponse.json({
          count: manyDepts.length,
          total_pages: Math.ceil(manyDepts.length / pageSize),
          current_page: page,
          page_size: pageSize,
          results: manyDepts.slice(start, start + pageSize),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <DepartmentPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Phòng Ban 1')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–15 trên tổng 20 bản ghi/i)).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    expect(nextBtn).toBeEnabled();
    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(screen.getByText('Phòng Ban 16')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 16–20 trên tổng 20 bản ghi/i)).toBeInTheDocument();
  });

  it('UserAdminPage: renders pagination controls and resets page to 1 on filter change', async () => {
    const manyUsers = Array.from({ length: 25 }, (_, i) => ({
      id: i + 1,
      username: `user_${i + 1}`,
      email: `user_${i + 1}@example.com`,
      full_name: `User ${i + 1}`,
      role: i === 0 ? 'ADMIN' : 'USER',
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      is_active: true,
    }));

    let lastRequestedPage = null;

    server.use(
      http.get('/api/users/', ({ request }) => {
        const url = new URL(request.url);
        lastRequestedPage = url.searchParams.get('page');
        const page = parseInt(lastRequestedPage || '1', 10);
        const pageSize = 15;
        const start = (page - 1) * pageSize;
        return HttpResponse.json({
          count: manyUsers.length,
          total_pages: Math.ceil(manyUsers.length / pageSize),
          current_page: page,
          page_size: pageSize,
          results: manyUsers.slice(start, start + pageSize),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <UserAdminPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('User 1')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–15 trên tổng 25 bản ghi/i)).toBeInTheDocument();

    // Navigate to page 2
    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(screen.getByText('User 16')).toBeInTheDocument();
    });
    expect(lastRequestedPage).toBe('2');

    // Change role filter -> should reset to page 1
    const roleSelect = screen.getByLabelText(/Lọc theo vai trò/i);
    await userEvent.selectOptions(roleSelect, 'USER');

    await waitFor(() => {
      expect(lastRequestedPage).toBe('1');
    });
  });

  it('DepartmentPage: search filter narrows down items, displays count, and resets page', async () => {
    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <DepartmentPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Phòng Công nghệ')).toBeInTheDocument();
    expect(screen.getByText('Phòng Nhân sự')).toBeInTheDocument();
    expect(screen.getByText(/Tổng số:/i)).toHaveTextContent('2');

    // Type in search box
    const searchInput = screen.getByPlaceholderText(/Tìm theo tên hoặc mã/i);
    await userEvent.type(searchInput, 'Nhân sự');

    expect(await screen.findByText('Phòng Nhân sự')).toBeInTheDocument();
    expect(screen.queryByText('Phòng Công nghệ')).toBeNull();
    expect(screen.getByText(/Tìm thấy/i)).toHaveTextContent('1');

    // Clear search button
    const clearBtn = screen.getByRole('button', { name: /Xóa tìm kiếm/i });
    await userEvent.click(clearBtn);

    expect(await screen.findByText('Phòng Công nghệ')).toBeInTheDocument();
    expect(screen.getByText('Phòng Nhân sự')).toBeInTheDocument();
  });

  it('DepartmentPage: distinguishes empty search results from zero departments', async () => {
    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <DepartmentPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Phòng Công nghệ')).toBeInTheDocument();

    // Search with no matching items
    const searchInput = screen.getByPlaceholderText(/Tìm theo tên hoặc mã/i);
    await userEvent.type(searchInput, 'NonExistentXYZ');

    expect(await screen.findByText(/Không tìm thấy kết quả phù hợp/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Xóa bộ lọc tìm kiếm/i })).toBeInTheDocument();

    // Clear and mock 0 departments total
    server.use(
      http.get('/api/departments/', () => {
        return HttpResponse.json({
          count: 0,
          total_pages: 1,
          current_page: 1,
          page_size: 15,
          results: [],
        });
      })
    );

    const clearBtn = screen.getByRole('button', { name: /Xóa bộ lọc tìm kiếm/i });
    await userEvent.click(clearBtn);

    expect(await screen.findByText(/Chưa có phòng ban nào/i)).toBeInTheDocument();
  });

  it('DepartmentPage: clamps currentPage to totalPages if currentPage > totalPages', async () => {
    let currentPageParam = null;

    server.use(
      http.get('/api/departments/', ({ request }) => {
        const url = new URL(request.url);
        currentPageParam = parseInt(url.searchParams.get('page') || '1', 10);
        // Initially 25 items (2 pages), then shrink to 5 items (1 page)
        return HttpResponse.json({
          count: 20,
          total_pages: 2,
          current_page: currentPageParam,
          page_size: 15,
          results: Array.from({ length: 15 }, (_, i) => ({
            id: i + 1,
            code: `D${i + 1}`,
            name: `Phòng ${i + 1}`,
          })),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <DepartmentPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Phòng 1')).toBeInTheDocument();
    });

    // Go to page 2
    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(currentPageParam).toBe(2);
    });

    // Now server returns total_pages: 1
    server.use(
      http.get('/api/departments/', ({ request }) => {
        const url = new URL(request.url);
        currentPageParam = parseInt(url.searchParams.get('page') || '1', 10);
        return HttpResponse.json({
          count: 5,
          total_pages: 1,
          current_page: currentPageParam,
          page_size: 15,
          results: [{ id: 1, code: 'D1', name: 'Phòng 1 duy nhất' }],
        });
      })
    );

    // Click Làm mới to fetch
    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    // Clamping should reset currentPage to 1
    await waitFor(() => {
      expect(currentPageParam).toBe(1);
      expect(screen.getByText('Phòng 1 duy nhất')).toBeInTheDocument();
    });
  });

  it('CategoryPage: distinguishes empty search results from zero categories and clamps page', async () => {
    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <CategoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Laptop')).toBeInTheDocument();

    // Search miss
    const searchInput = screen.getByPlaceholderText(/Tìm theo tên hoặc mô tả/i);
    await userEvent.type(searchInput, 'UnknownDevice123');

    expect(await screen.findByText(/Không tìm thấy kết quả phù hợp/i)).toBeInTheDocument();

    // Mock zero categories
    server.use(
      http.get('/api/categories/', () => {
        return HttpResponse.json({
          count: 0,
          total_pages: 1,
          current_page: 1,
          page_size: 15,
          results: [],
        });
      })
    );

    const clearBtn = screen.getByRole('button', { name: /Xóa bộ lọc tìm kiếm/i });
    await userEvent.click(clearBtn);

    expect(await screen.findByText(/Chưa có danh mục nào/i)).toBeInTheDocument();
  });

  it('UserAdminPage: loads departments with all=true for filter and modal dropdowns', async () => {
    let capturedAllParam = null;

    server.use(
      http.get('/api/departments/', ({ request }) => {
        const url = new URL(request.url);
        capturedAllParam = url.searchParams.get('all');
        return HttpResponse.json(mockDepartments);
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <UserAdminPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(capturedAllParam).toBe('true');
    });

    // Verify departments dropdown in filters contains both departments
    const deptFilterSelect = screen.getByLabelText(/Lọc theo phòng ban/i);
    expect(within(deptFilterSelect).getByText('Phòng Công nghệ (IT)')).toBeInTheDocument();
    expect(within(deptFilterSelect).getByText('Phòng Nhân sự (HR)')).toBeInTheDocument();

    // Open edit modal and verify modal department dropdown
    const editButtons = screen.getAllByRole('button', { name: /Sửa/i });
    await userEvent.click(editButtons[0]);

    const modal = screen.getByRole('dialog');
    const modalDeptSelect = within(modal).getByLabelText('Phòng ban');
    expect(within(modalDeptSelect).getByText('Phòng Công nghệ (IT)')).toBeInTheDocument();
    expect(within(modalDeptSelect).getByText('Phòng Nhân sự (HR)')).toBeInTheDocument();
  });

  it('UserAdminPage: clamps currentPage to totalPages if currentPage > totalPages', async () => {
    let currentPageParam = null;

    server.use(
      http.get('/api/users/', ({ request }) => {
        const url = new URL(request.url);
        currentPageParam = parseInt(url.searchParams.get('page') || '1', 10);
        return HttpResponse.json({
          count: 20,
          total_pages: 2,
          current_page: currentPageParam,
          page_size: 15,
          results: Array.from({ length: 15 }, (_, i) => ({
            id: i + 1,
            username: `u${i + 1}`,
            role: 'USER',
            is_active: true,
          })),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <UserAdminPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('@u1')).toBeInTheDocument();
    });

    // Go to page 2
    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(currentPageParam).toBe(2);
    });

    // Shrunk to 1 page
    server.use(
      http.get('/api/users/', ({ request }) => {
        const url = new URL(request.url);
        currentPageParam = parseInt(url.searchParams.get('page') || '1', 10);
        return HttpResponse.json({
          count: 2,
          total_pages: 1,
          current_page: currentPageParam,
          page_size: 15,
          results: [{ id: 1, username: 'u_only', role: 'USER', is_active: true }],
        });
      })
    );

    // Refresh
    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    await waitFor(() => {
      expect(currentPageParam).toBe(1);
      expect(screen.getByText('@u_only')).toBeInTheDocument();
    });
  });

  it('CategoryPage: race condition guard ignores stale delayed response from previous search', async () => {
    // We simulate request 1 for "lap" being slow (150ms) and request 2 for "mon" being fast (20ms)
    server.use(
      http.get('/api/categories/', async ({ request }) => {
        const url = new URL(request.url);
        const search = url.searchParams.get('search') || '';

        if (search === 'lap') {
          // Slow response
          await new Promise((resolve) => setTimeout(resolve, 150));
          return HttpResponse.json({
            count: 1,
            total_pages: 1,
            current_page: 1,
            page_size: 15,
            results: [{ id: 1, name: 'Laptop Stale', description: 'Stale item' }],
          });
        }

        if (search === 'mon') {
          // Fast response
          await new Promise((resolve) => setTimeout(resolve, 20));
          return HttpResponse.json({
            count: 1,
            total_pages: 1,
            current_page: 1,
            page_size: 15,
            results: [{ id: 2, name: 'Monitor Latest', description: 'Fresh item' }],
          });
        }

        return HttpResponse.json({
          count: 2,
          total_pages: 1,
          current_page: 1,
          page_size: 15,
          results: mockCategories,
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <CategoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Laptop')).toBeInTheDocument();

    const searchInput = screen.getByPlaceholderText(/Tìm theo tên hoặc mô tả/i);
    // Type 'lap', then rapidly replace with 'mon'
    await userEvent.clear(searchInput);
    await userEvent.type(searchInput, 'lap');
    await userEvent.clear(searchInput);
    await userEvent.type(searchInput, 'mon');

    // Wait for the faster 'mon' result
    expect(await screen.findByText('Monitor Latest')).toBeInTheDocument();

    // Wait until past the 150ms delay of 'lap'
    await new Promise((resolve) => setTimeout(resolve, 200));

    // 'Monitor Latest' should STILL be in document, and 'Laptop Stale' must NOT overwrite it
    expect(screen.getByText('Monitor Latest')).toBeInTheDocument();
    expect(screen.queryByText('Laptop Stale')).toBeNull();
  });

  it('UserAdminPage recovers automatically from 404 Invalid page when records decrease', async () => {
    let records = Array.from({ length: 16 }, (_, i) => ({
      id: i + 1,
      username: `user_${i + 1}`,
      email: `user_${i + 1}@example.com`,
      full_name: `User ${i + 1}`,
      role: 'USER',
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      is_active: true,
    }));

    server.use(
      http.get('/api/users/', ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('all') === 'true') {
          return HttpResponse.json(records);
        }
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = 15;
        const totalPages = Math.ceil(records.length / pageSize) || 1;

        if (page > totalPages) {
          return HttpResponse.json({ detail: 'Invalid page.' }, { status: 404 });
        }

        const start = (page - 1) * pageSize;
        return HttpResponse.json({
          count: records.length,
          total_pages: totalPages,
          current_page: page,
          page_size: pageSize,
          results: records.slice(start, start + pageSize),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <UserAdminPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('User 1')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 1–15 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);
    expect(await screen.findByText('User 16')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 16–16 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    // Data drops to 15 items
    records = records.slice(0, 15);

    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    await waitFor(() => {
      expect(screen.getByText('User 1')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–15 trên tổng 15 bản ghi/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('DepartmentPage recovers automatically from 404 Invalid page when records decrease', async () => {
    let records = Array.from({ length: 16 }, (_, i) => ({
      id: i + 1,
      code: `DP${i + 1}`,
      name: `Phòng Ban ${i + 1}`,
      description: `Mô tả ${i + 1}`,
    }));

    server.use(
      http.get('/api/departments/', ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('all') === 'true') {
          return HttpResponse.json(records);
        }
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = 15;
        const totalPages = Math.ceil(records.length / pageSize) || 1;

        if (page > totalPages) {
          return HttpResponse.json({ detail: 'Invalid page.' }, { status: 404 });
        }

        const start = (page - 1) * pageSize;
        return HttpResponse.json({
          count: records.length,
          total_pages: totalPages,
          current_page: page,
          page_size: pageSize,
          results: records.slice(start, start + pageSize),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <DepartmentPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Phòng Ban 1')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 1–15 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);
    expect(await screen.findByText('Phòng Ban 16')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 16–16 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    // Data drops to 15 items
    records = records.slice(0, 15);

    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    await waitFor(() => {
      expect(screen.getByText('Phòng Ban 1')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–15 trên tổng 15 bản ghi/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('CategoryPage recovers automatically from 404 Invalid page when records decrease', async () => {
    let records = Array.from({ length: 16 }, (_, i) => ({
      id: i + 1,
      name: `Category ${i + 1}`,
      description: `Description ${i + 1}`,
    }));

    server.use(
      http.get('/api/categories/', ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('all') === 'true') {
          return HttpResponse.json(records);
        }
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = 15;
        const totalPages = Math.ceil(records.length / pageSize) || 1;

        if (page > totalPages) {
          return HttpResponse.json({ detail: 'Invalid page.' }, { status: 404 });
        }

        const start = (page - 1) * pageSize;
        return HttpResponse.json({
          count: records.length,
          total_pages: totalPages,
          current_page: page,
          page_size: pageSize,
          results: records.slice(start, start + pageSize),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <CategoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Category 1')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 1–15 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);
    expect(await screen.findByText('Category 16')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 16–16 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    // Data drops to 15 items
    records = records.slice(0, 15);

    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    await waitFor(() => {
      expect(screen.getByText('Category 1')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–15 trên tổng 15 bản ghi/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
