import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider } from '../context/AuthContext';
import AssetListPage from '../pages/AssetListPage';
import AssetDetailPage from '../pages/AssetDetailPage';

let mockAssets = [
  {
    id: 1,
    asset_code: 'AST-202610-0001',
    name: 'MacBook Pro M2 14 inch',
    category_id: 1,
    category_name: 'Laptop',
    department_id: 1,
    department_name: 'Phòng IT',
    department_code: 'IT',
    assigned_to_user_id: null,
    assigned_to_user_name: null,
    status: 'AVAILABLE',
    serial_number: 'C02G1234MD6R',
    specification: { cpu: 'M2 Pro', ram: '16GB' },
    created_at: '2026-10-01T10:00:00Z',
  },
  {
    id: 2,
    asset_code: 'AST-202610-0002',
    name: 'Màn hình Dell UltraSharp 27',
    category_id: 2,
    category_name: 'Màn hình',
    department_id: 1,
    department_name: 'Phòng IT',
    department_code: 'IT',
    assigned_to_user_id: 2,
    assigned_to_user_name: 'Nhân Viên A',
    assigned_to_user_username: 'staffuser',
    status: 'IN_USE',
    serial_number: 'DELL-98765',
    specification: { resolution: '4K' },
    created_at: '2026-10-02T11:00:00Z',
  },
];

let mockCategories = [
  { id: 1, name: 'Laptop' },
  { id: 2, name: 'Màn hình' },
];

let mockDepartments = [
  { id: 1, code: 'IT', name: 'Phòng IT' },
  { id: 2, code: 'HR', name: 'Phòng HR' },
];

let mockUsers = [
  { id: 1, username: 'adminuser', full_name: 'Admin User', department_id: 1, role: 'ADMIN' },
  { id: 2, username: 'staffuser', full_name: 'Nhân Viên A', department_id: 1, role: 'USER' },
];

let mockHistoryLogs = [
  {
    id: 1,
    asset_id: 1,
    action_type: 'CREATE',
    from_status: '',
    to_status: 'AVAILABLE',
    actor_name: 'Admin User',
    reason: 'Khởi tạo tài sản mới',
    created_at: '2026-10-01T10:00:00Z',
  },
];

const server = setupServer(
  http.get('/api/categories/', () => HttpResponse.json(mockCategories)),
  http.get('/api/departments/', () => HttpResponse.json(mockDepartments)),
  http.get('/api/users/', () => HttpResponse.json(mockUsers)),

  http.get('/api/assets/', ({ request }) => {
    const url = new URL(request.url);
    const search = url.searchParams.get('search');
    const status = url.searchParams.get('status');
    const category_id = url.searchParams.get('category_id');

    let filtered = [...mockAssets];
    if (search) {
      filtered = filtered.filter((a) => a.name.toLowerCase().includes(search.toLowerCase()) || a.asset_code.includes(search));
    }
    if (status) {
      filtered = filtered.filter((a) => a.status === status);
    }
    if (category_id) {
      filtered = filtered.filter((a) => a.category_id === Number(category_id));
    }
    return HttpResponse.json(filtered);
  }),

  http.post('/api/assets/', async ({ request }) => {
    const body = await request.json();
    const newAsset = {
      id: 3,
      asset_code: 'AST-202610-0003',
      name: body.name,
      category_id: body.category_id,
      category_name: mockCategories.find((c) => c.id === body.category_id)?.name || 'Laptop',
      department_id: body.department_id || null,
      department_name: mockDepartments.find((d) => d.id === body.department_id)?.name || null,
      assigned_to_user_id: null,
      assigned_to_user_name: null,
      status: 'AVAILABLE',
      serial_number: body.serial_number || '',
      specification: body.specification || {},
      created_at: '2026-10-29T12:00:00Z',
    };
    mockAssets.push(newAsset);
    return HttpResponse.json(newAsset, { status: 201 });
  }),

  http.get('/api/assets/:id/', ({ params }) => {
    const id = Number(params.id);
    const asset = mockAssets.find((a) => a.id === id);
    if (asset) return HttpResponse.json(asset);
    return new HttpResponse(null, { status: 404 });
  }),

  http.patch('/api/assets/:id/', async ({ request, params }) => {
    const id = Number(params.id);
    const body = await request.json();
    const asset = mockAssets.find((a) => a.id === id);
    if (asset) {
      Object.assign(asset, body);
      return HttpResponse.json(asset);
    }
    return new HttpResponse(null, { status: 404 });
  }),

  http.get('/api/assets/:id/history/', () => HttpResponse.json(mockHistoryLogs)),

  http.post('/api/assets/:id/allocate/', async ({ request, params }) => {
    const id = Number(params.id);
    const body = await request.json();
    const asset = mockAssets.find((a) => a.id === id);
    if (asset) {
      asset.status = 'IN_USE';
      asset.assigned_to_user_id = body.assigned_to_user_id;
      const u = mockUsers.find((user) => user.id === body.assigned_to_user_id);
      asset.assigned_to_user_name = u?.full_name;
      asset.assigned_to_user_username = u?.username;
      return HttpResponse.json(asset);
    }
    return new HttpResponse(null, { status: 404 });
  }),

  http.post('/api/assets/:id/revoke/', async ({ request, params }) => {
    const id = Number(params.id);
    const body = await request.json();
    const asset = mockAssets.find((a) => a.id === id);
    if (asset) {
      asset.status = body.condition === 'GOOD' ? 'AVAILABLE' : 'DAMAGED_PENDING_REPAIR';
      asset.assigned_to_user_id = null;
      asset.assigned_to_user_name = null;
      return HttpResponse.json(asset);
    }
    return new HttpResponse(null, { status: 404 });
  }),

  http.post('/api/assets/:id/transfer/', async ({ request, params }) => {
    const id = Number(params.id);
    const body = await request.json();
    const asset = mockAssets.find((a) => a.id === id);
    if (asset) {
      asset.department_id = body.to_department_id || body.target_department_id || null;
      asset.department_name = mockDepartments.find((d) => d.id === asset.department_id)?.name || null;
      return HttpResponse.json(asset);
    }
    return new HttpResponse(null, { status: 404 });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  mockAssets = [
    {
      id: 1,
      asset_code: 'AST-202610-0001',
      name: 'MacBook Pro M2 14 inch',
      category_id: 1,
      category_name: 'Laptop',
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      assigned_to_user_id: null,
      assigned_to_user_name: null,
      status: 'AVAILABLE',
      serial_number: 'C02G1234MD6R',
      specification: { cpu: 'M2 Pro', ram: '16GB' },
      created_at: '2026-10-01T10:00:00Z',
    },
    {
      id: 2,
      asset_code: 'AST-202610-0002',
      name: 'Màn hình Dell UltraSharp 27',
      category_id: 2,
      category_name: 'Màn hình',
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      assigned_to_user_id: 2,
      assigned_to_user_name: 'Nhân Viên A',
      assigned_to_user_username: 'staffuser',
      status: 'IN_USE',
      serial_number: 'DELL-98765',
      specification: { resolution: '4K' },
      created_at: '2026-10-02T11:00:00Z',
    },
  ];
});
afterAll(() => server.close());

describe('AssetFlows Integration Tests', () => {
  it('AssetListPage renders assets, filters and search', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <AssetListPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('MacBook Pro M2 14 inch')).toBeInTheDocument();
    expect(screen.getByText('Màn hình Dell UltraSharp 27')).toBeInTheDocument();
    expect(screen.getByText('AST-202610-0001')).toBeInTheDocument();

    // Search filter
    const searchInput = screen.getByPlaceholderText(/Tìm theo mã, tên, serial.../i);
    await userEvent.type(searchInput, 'MacBook');

    await waitFor(() => {
      expect(screen.getByText('MacBook Pro M2 14 inch')).toBeInTheDocument();
      expect(screen.queryByText('Màn hình Dell UltraSharp 27')).toBeNull();
    });
  });

  it('Admin creates asset, edits asset', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <AssetListPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('MacBook Pro M2 14 inch')).toBeInTheDocument();

    // Click "Tạo tài sản mới"
    const createBtn = screen.getByRole('button', { name: /Tạo tài sản mới/i });
    await userEvent.click(createBtn);

    const modal = screen.getByRole('dialog');
    expect(modal).toBeInTheDocument();

    await userEvent.type(within(modal).getByLabelText(/Tên tài sản/i), 'Bàn phím cơ Keychron');
    await userEvent.selectOptions(within(modal).getByLabelText(/Danh mục/i), '1');
    await userEvent.type(within(modal).getByLabelText(/Số serial/i), 'KC-K2-V2');

    await userEvent.click(within(modal).getByRole('button', { name: /Tạo tài sản/i }));

    expect(await screen.findByText('Bàn phím cơ Keychron')).toBeInTheDocument();
  });

  it('Manager cannot see create asset button', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{
            id: 2,
            username: 'deptmanager',
            role: 'MANAGER',
            department_id: 1,
            department_name: 'Phòng IT',
          }}
          initialToken="token-manager"
        >
          <AssetListPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('MacBook Pro M2 14 inch')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Tạo tài sản mới/i })).toBeNull();
  });

  it('AssetDetailPage allows Admin to edit asset and allocate asset', async () => {
    render(
      <MemoryRouter initialEntries={['/assets/1']}>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <Routes>
            <Route path="/assets/:id" element={<AssetDetailPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('MacBook Pro M2 14 inch')).toBeInTheDocument();
    expect(screen.getByText('Lịch sử vòng đời tài sản')).toBeInTheDocument();

    // Edit asset
    const editBtn = screen.getByRole('button', { name: /Sửa thông tin/i });
    await userEvent.click(editBtn);

    const editModal = screen.getByRole('dialog');
    expect(editModal).toBeInTheDocument();

    const nameInput = within(editModal).getByLabelText(/Tên tài sản/i);
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'MacBook Pro M2 Max 14 inch');

    await userEvent.click(within(editModal).getByRole('button', { name: /Lưu thay đổi/i }));

    expect(await screen.findByText('MacBook Pro M2 Max 14 inch')).toBeInTheDocument();

    // Allocate asset
    const allocateBtn = screen.getByRole('button', { name: 'Cấp phát' });
    await userEvent.click(allocateBtn);

    const actionModal = screen.getByRole('dialog');
    expect(actionModal).toBeInTheDocument();

    await userEvent.selectOptions(within(actionModal).getByLabelText(/Nhân viên nhận bàn giao/i), '2');
    await userEvent.type(within(actionModal).getByLabelText(/Lý do/i), 'Cấp phát cho dự án mới');

    await userEvent.click(within(actionModal).getByRole('button', { name: 'Cấp phát' }));

    // Success reflected
    expect(await screen.findByText('Nhân Viên A (@staffuser)')).toBeInTheDocument();
  });

  it('AssetActionModal handles 409 Conflict gracefully with alert', async () => {
    server.use(
      http.post('/api/assets/:id/allocate/', () => {
        return HttpResponse.json(
          { detail: 'Xung đột: Tài sản đã được cấp phát cho nhân viên khác trong lúc bạn thao tác.' },
          { status: 409 }
        );
      })
    );

    render(
      <MemoryRouter initialEntries={['/assets/1']}>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <Routes>
            <Route path="/assets/:id" element={<AssetDetailPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('MacBook Pro M2 14 inch')).toBeInTheDocument();

    const allocateBtn = screen.getByRole('button', { name: 'Cấp phát' });
    await userEvent.click(allocateBtn);

    const actionModal = screen.getByRole('dialog');
    await userEvent.selectOptions(within(actionModal).getByLabelText(/Nhân viên nhận bàn giao/i), '2');
    await userEvent.type(within(actionModal).getByLabelText(/Lý do/i), 'Cấp máy');

    await userEvent.click(within(actionModal).getByRole('button', { name: 'Cấp phát' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Xung đột/i);
  });
});
