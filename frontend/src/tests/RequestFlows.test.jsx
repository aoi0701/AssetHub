import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider } from '../context/AuthContext';
import UserPortalPage from '../pages/UserPortalPage';
import RequestInboxPage from '../pages/RequestInboxPage';

let mockRequests = [
  {
    id: 1,
    request_code: 'REQ-202610-0001',
    request_type: 'ALLOCATE',
    requester_id: 3,
    requester_name: 'Nhân Viên B',
    requester_username: 'user_b',
    asset_id: null,
    asset_code: null,
    asset_name: null,
    requested_category_id: 1,
    requested_category_name: 'Laptop',
    department_id: 1,
    department_name: 'Phòng IT',
    department_code: 'IT',
    reason: 'Cần máy tính để lập trình frontend',
    status: 'PENDING',
    reviewed_by_id: null,
    reviewed_by_name: null,
    rejection_reason: null,
    created_at: '2026-10-29T10:00:00Z',
  },
  {
    id: 2,
    request_code: 'REQ-202610-0002',
    request_type: 'RETURN',
    requester_id: 3,
    requester_name: 'Nhân Viên B',
    requester_username: 'user_b',
    asset_id: 10,
    asset_code: 'AST-202610-0010',
    asset_name: 'Màn hình Dell 24 inch',
    requested_category_id: null,
    requested_category_name: null,
    department_id: 1,
    department_name: 'Phòng IT',
    department_code: 'IT',
    reason: 'Thừa màn hình không dùng tới',
    status: 'PENDING',
    reviewed_by_id: null,
    reviewed_by_name: null,
    rejection_reason: null,
    created_at: '2026-10-29T10:30:00Z',
  },
];

let mockCategories = [
  { id: 1, name: 'Laptop' },
  { id: 2, name: 'Màn hình' },
];

let mockUserAssets = [
  {
    id: 10,
    asset_code: 'AST-202610-0010',
    name: 'Màn hình Dell 24 inch',
    category_id: 2,
    category_name: 'Màn hình',
    status: 'IN_USE',
    serial_number: 'DELL-24-001',
  },
];

let mockAvailableAssets = [
  {
    id: 5,
    asset_code: 'AST-202610-0005',
    name: 'ThinkPad X1 Carbon Gen 10',
    category_id: 1,
    department_id: 1,
    status: 'AVAILABLE',
    serial_number: 'TP-X1-9988',
  },
];

const server = setupServer(
  http.get('/api/categories/', () => HttpResponse.json(mockCategories)),
  http.get('/api/assets/', ({ request }) => {
    const url = new URL(request.url);
    if (url.searchParams.get('status') === 'AVAILABLE') {
      return HttpResponse.json(mockAvailableAssets);
    }
    return HttpResponse.json(mockUserAssets);
  }),

  http.get('/api/requests/', ({ request }) => {
    const url = new URL(request.url);
    const pageParam = url.searchParams.get('page');
    let filtered = mockRequests;
    const status = url.searchParams.get('status');
    if (status) {
      filtered = filtered.filter((r) => r.status === status);
    }
    const type = url.searchParams.get('request_type');
    if (type) {
      filtered = filtered.filter((r) => r.request_type === type);
    }
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

  http.post('/api/requests/', async ({ request }) => {
    const body = await request.json();
    const newReq = {
      id: 3,
      request_code: 'REQ-202610-0003',
      request_type: body.request_type,
      requester_id: 3,
      requester_name: 'Nhân Viên B',
      requester_username: 'user_b',
      asset_id: body.asset_id || null,
      asset_code: body.asset_id ? 'AST-202610-0010' : null,
      asset_name: body.asset_id ? 'Màn hình Dell 24 inch' : null,
      requested_category_id: body.requested_category_id || null,
      requested_category_name: mockCategories.find((c) => c.id === body.requested_category_id)?.name || null,
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      reason: body.reason,
      status: 'PENDING',
      reviewed_by_id: null,
      reviewed_by_name: null,
      rejection_reason: null,
      created_at: '2026-10-29T11:00:00Z',
    };
    mockRequests.push(newReq);
    return HttpResponse.json(newReq, { status: 201 });
  }),

  http.post('/api/requests/:id/approve/', async ({ request, params }) => {
    const id = Number(params.id);
    const req = mockRequests.find((r) => r.id === id);
    if (req) {
      req.status = 'APPROVED';
      req.reviewed_by_name = 'Manager IT';
      return HttpResponse.json(req);
    }
    return new HttpResponse(null, { status: 404 });
  }),

  http.post('/api/requests/:id/reject/', async ({ request, params }) => {
    const id = Number(params.id);
    const body = await request.json();
    const req = mockRequests.find((r) => r.id === id);
    if (req) {
      req.status = 'REJECTED';
      req.rejection_reason = body.rejection_reason;
      req.reviewed_by_name = 'Manager IT';
      return HttpResponse.json(req);
    }
    return new HttpResponse(null, { status: 404 });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  mockRequests = [
    {
      id: 1,
      request_code: 'REQ-202610-0001',
      request_type: 'ALLOCATE',
      requester_id: 3,
      requester_name: 'Nhân Viên B',
      requester_username: 'user_b',
      asset_id: null,
      asset_code: null,
      asset_name: null,
      requested_category_id: 1,
      requested_category_name: 'Laptop',
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      reason: 'Cần máy tính để lập trình frontend',
      status: 'PENDING',
      reviewed_by_id: null,
      reviewed_by_name: null,
      rejection_reason: null,
      created_at: '2026-10-29T10:00:00Z',
    },
    {
      id: 2,
      request_code: 'REQ-202610-0002',
      request_type: 'RETURN',
      requester_id: 3,
      requester_name: 'Nhân Viên B',
      requester_username: 'user_b',
      asset_id: 10,
      asset_code: 'AST-202610-0010',
      asset_name: 'Màn hình Dell 24 inch',
      requested_category_id: null,
      requested_category_name: null,
      department_id: 1,
      department_name: 'Phòng IT',
      department_code: 'IT',
      reason: 'Thừa màn hình không dùng tới',
      status: 'PENDING',
      reviewed_by_id: null,
      reviewed_by_name: null,
      rejection_reason: null,
      created_at: '2026-10-29T10:30:00Z',
    },
  ];
});
afterAll(() => server.close());

describe('RequestFlows Integration Tests', () => {
  it('User submits allocate request and return request via UserPortalPage', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 3, username: 'user_b', role: 'USER', department_id: 1 }}
          initialToken="token-user"
        >
          <UserPortalPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Màn hình Dell 24 inch')).toBeInTheDocument();

    // 1. Submit Allocate Request
    const allocateBtn = screen.getByRole('button', { name: /Gửi yêu cầu cấp mới/i });
    await userEvent.click(allocateBtn);

    const modal = screen.getByRole('dialog');
    expect(modal).toBeInTheDocument();
    await userEvent.selectOptions(within(modal).getByLabelText(/Danh mục cần cấp/i), '1');
    await userEvent.type(within(modal).getByLabelText(/Lý do chi tiết/i), 'Cần laptop mới');
    await userEvent.click(within(modal).getByRole('button', { name: /Gửi yêu cầu/i }));

    expect(await screen.findByRole('status')).toHaveTextContent(/Gửi yêu cầu thành công/i);

    // 2. Submit Return Request on held asset
    const returnBtn = screen.getByRole('button', { name: /Trả tài sản/i });
    await userEvent.click(returnBtn);

    const returnModal = screen.getByRole('dialog');
    await userEvent.type(within(returnModal).getByLabelText(/Lý do chi tiết/i), 'Không còn nhu cầu sử dụng');
    await userEvent.click(within(returnModal).getByRole('button', { name: /Gửi yêu cầu/i }));

    expect(await screen.findByRole('status')).toHaveTextContent(/Gửi yêu cầu thành công/i);
  });

  it('CreateRequestModal handles 409 Conflict when duplicate pending request', async () => {
    server.use(
      http.post('/api/requests/', () => {
        return HttpResponse.json(
          { detail: 'Bạn đã có một yêu cầu trả tài sản này đang chờ duyệt.' },
          { status: 409 }
        );
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 3, username: 'user_b', role: 'USER', department_id: 1 }}
          initialToken="token-user"
        >
          <UserPortalPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Màn hình Dell 24 inch')).toBeInTheDocument();

    const returnBtn = screen.getByRole('button', { name: /Trả tài sản/i });
    await userEvent.click(returnBtn);

    const returnModal = screen.getByRole('dialog');
    await userEvent.type(within(returnModal).getByLabelText(/Lý do chi tiết/i), 'Trả lại');
    await userEvent.click(within(returnModal).getByRole('button', { name: /Gửi yêu cầu/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Bạn đã có một yêu cầu trả tài sản này đang chờ duyệt/i);
  });

  it('Manager reviews and approves ALLOCATE request in RequestInboxPage', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 2, username: 'deptmanager', role: 'MANAGER', department_id: 1 }}
          initialToken="token-manager"
        >
          <RequestInboxPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('REQ-202610-0001')).toBeInTheDocument();

    const reviewButtons = screen.getAllByRole('button', { name: /Xét duyệt/i });
    await userEvent.click(reviewButtons[0]);

    const reviewModal = screen.getByRole('dialog');
    expect(reviewModal).toBeInTheDocument();

    // Select available asset in stock
    const assetSelect = within(reviewModal).getByLabelText(/Chọn tài sản có sẵn trong kho/i);
    await userEvent.selectOptions(assetSelect, '5');

    await userEvent.click(within(reviewModal).getByRole('button', { name: /Xác nhận duyệt/i }));

    expect(await screen.findByRole('status')).toHaveTextContent(/Xử lý phiếu yêu cầu thành công/i);
  });

  it('Manager rejects request with mandatory reason in RequestInboxPage', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 2, username: 'deptmanager', role: 'MANAGER', department_id: 1 }}
          initialToken="token-manager"
        >
          <RequestInboxPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('REQ-202610-0002')).toBeInTheDocument();

    const reviewButtons = screen.getAllByRole('button', { name: /Xét duyệt/i });
    await userEvent.click(reviewButtons[1]); // second request (RETURN)

    const reviewModal = screen.getByRole('dialog');

    // Switch to "Từ chối"
    const rejectTab = within(reviewModal).getByRole('button', { name: /Từ chối/i });
    await userEvent.click(rejectTab);

    // Enter rejection reason
    const reasonInput = within(reviewModal).getByLabelText(/Lý do từ chối yêu cầu/i);
    await userEvent.type(reasonInput, 'Phòng ban chưa chấp thuận bàn giao');

    await userEvent.click(within(reviewModal).getByRole('button', { name: /Xác nhận từ chối/i }));

    expect(await screen.findByRole('status')).toHaveTextContent(/Xử lý phiếu yêu cầu thành công/i);
  });

  it('RequestInboxPage renders pagination controls and navigates across pages', async () => {
    const manyRequests = Array.from({ length: 22 }, (_, i) => ({
      id: i + 1,
      request_code: `REQ-202610-${String(i + 1).padStart(4, '0')}`,
      request_type: 'ALLOCATE',
      requester_name: `Employee ${i + 1}`,
      requester_username: `emp_${i + 1}`,
      department_name: 'Phòng IT',
      department_code: 'IT',
      requested_category_name: 'Laptop',
      reason: `Project work ${i + 1}`,
      status: 'PENDING',
      created_at: '2026-10-01T00:00:00Z',
    }));

    server.use(
      http.get('/api/requests/', ({ request }) => {
        const url = new URL(request.url);
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = 15;
        const start = (page - 1) * pageSize;
        return HttpResponse.json({
          count: manyRequests.length,
          total_pages: Math.ceil(manyRequests.length / pageSize),
          current_page: page,
          page_size: pageSize,
          results: manyRequests.slice(start, start + pageSize),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <RequestInboxPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('REQ-202610-0001')).toBeInTheDocument();
    });

    expect(screen.getByText(/Đang xem 1–15 trên tổng 22 bản ghi/i)).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    expect(nextBtn).toBeEnabled();
    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(screen.getByText('REQ-202610-0016')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 16–22 trên tổng 22 bản ghi/i)).toBeInTheDocument();
  });

  it('differentiates empty state between filtered no-match and completely empty requests', async () => {
    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <RequestInboxPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('REQ-202610-0001')).toBeInTheDocument();

    // 1. Filter by status REJECTED (no rejected requests initially)
    const statusSelect = screen.getByLabelText(/Lọc theo trạng thái yêu cầu/i);
    await userEvent.selectOptions(statusSelect, 'REJECTED');

    expect(await screen.findByText(/Không tìm thấy phiếu yêu cầu nào phù hợp\./i)).toBeInTheDocument();
    const clearBtn = screen.getByRole('button', { name: /Xóa bộ lọc/i });
    expect(clearBtn).toBeInTheDocument();

    // 2. Click "Xóa bộ lọc" resets filters and displays requests
    await userEvent.click(clearBtn);
    expect(await screen.findByText('REQ-202610-0001')).toBeInTheDocument();
    expect(statusSelect.value).toBe('');

    // 3. Completely empty requests list with no filters
    server.use(
      http.get('/api/requests/', () => {
        return HttpResponse.json({
          count: 0,
          total_pages: 1,
          current_page: 1,
          page_size: 15,
          results: [],
        });
      })
    );

    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    expect(await screen.findByText(/Không có phiếu yêu cầu nào\./i)).toBeInTheDocument();
  });

  it('clamps currentPage when totalPages decreases below currentPage', async () => {
    let mockPageData = Array.from({ length: 20 }, (_, i) => ({
      id: i + 1,
      request_code: `REQ-202610-${String(i + 1).padStart(4, '0')}`,
      request_type: 'ALLOCATE',
      requester_name: `Employee ${i + 1}`,
      requester_username: `emp_${i + 1}`,
      department_name: 'Phòng IT',
      department_code: 'IT',
      requested_category_name: 'Laptop',
      reason: `Project work ${i + 1}`,
      status: 'PENDING',
      created_at: '2026-10-01T00:00:00Z',
    }));

    server.use(
      http.get('/api/requests/', ({ request }) => {
        const url = new URL(request.url);
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = 15;
        const start = (page - 1) * pageSize;
        return HttpResponse.json({
          count: mockPageData.length,
          total_pages: Math.ceil(mockPageData.length / pageSize),
          current_page: page,
          page_size: pageSize,
          results: mockPageData.slice(start, start + pageSize),
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialRole="ADMIN">
          <RequestInboxPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('REQ-202610-0001')).toBeInTheDocument();

    // Go to page 2
    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);
    expect(await screen.findByText('REQ-202610-0016')).toBeInTheDocument();

    // Dataset shrinks to 5 items (totalPages = 1)
    mockPageData = mockPageData.slice(0, 5);

    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    // Clamps back to page 1
    await waitFor(() => {
      expect(screen.getByText('REQ-202610-0001')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–5 trên tổng 5 bản ghi/i)).toBeInTheDocument();
  });

  it('recovers automatically from 404 Invalid page when records decrease and currentPage no longer exists', async () => {
    let records = Array.from({ length: 16 }, (_, i) => ({
      id: i + 1,
      request_code: `REQ-202610-${String(i + 1).padStart(4, '0')}`,
      request_type: 'ALLOCATE',
      requester_name: `Employee ${i + 1}`,
      requester_username: `emp_${i + 1}`,
      department_name: 'Phòng IT',
      department_code: 'IT',
      requested_category_name: 'Laptop',
      reason: `Project work ${i + 1}`,
      status: 'PENDING',
      created_at: '2026-10-01T00:00:00Z',
    }));

    server.use(
      http.get('/api/requests/', ({ request }) => {
        const url = new URL(request.url);
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
          <RequestInboxPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('REQ-202610-0001')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 1–15 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);
    expect(await screen.findByText('REQ-202610-0016')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 16–16 trên tổng 16 bản ghi/i)).toBeInTheDocument();

    // Data drops from 16 to 15 items (totalPages becomes 1)
    records = records.slice(0, 15);

    // Refresh triggers fetch with page=2, which returns 404
    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    // RequestInboxPage catches 404 and recovers back to page 1 without showing error alert
    await waitFor(() => {
      expect(screen.getByText('REQ-202610-0001')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–15 trên tổng 15 bản ghi/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
