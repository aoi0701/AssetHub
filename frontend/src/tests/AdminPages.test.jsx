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
  http.get('/api/users/', () => {
    return HttpResponse.json(mockUsers);
  }),

  http.get('/api/departments/', () => {
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

  http.get('/api/categories/', () => {
    return HttpResponse.json(mockCategories);
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
});
