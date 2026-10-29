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

  http.get('/api/requests/', () => HttpResponse.json(mockRequests)),

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
});
