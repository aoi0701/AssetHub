import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider } from '../context/AuthContext';
import UserPortalPage from '../pages/UserPortalPage';

const createInitialMockAssets = () =>
  Array.from({ length: 22 }, (_, i) => ({
    id: i + 1,
    asset_code: `AST-202610-${String(i + 1).padStart(4, '0')}`,
    name: `Tài sản người dùng ${i + 1}`,
    category_id: (i % 2) + 1,
    category_name: i % 2 === 0 ? 'Laptop' : 'Màn hình',
    department_id: 1,
    department_name: 'Phòng IT',
    department_code: 'IT',
    assigned_to_user_id: 2,
    assigned_to_user_name: 'Nhân Viên A',
    assigned_to_user_username: 'staffuser',
    status: 'IN_USE',
    serial_number: `SN-${String(i + 1).padStart(4, '0')}`,
    specification: {},
    created_at: '2026-10-01T00:00:00Z',
  }));

let mockAssets = createInitialMockAssets();

let mockCategories = [
  { id: 1, name: 'Laptop' },
  { id: 2, name: 'Màn hình' },
];

let createdRequests = [];

const server = setupServer(
  http.get('/api/categories/', () => HttpResponse.json(mockCategories)),

  http.get('/api/assets/', ({ request }) => {
    const url = new URL(request.url);
    if (url.searchParams.get('all') === 'true') {
      return HttpResponse.json(mockAssets);
    }
    const pageParam = url.searchParams.get('page');
    if (pageParam) {
      const page = parseInt(pageParam, 10);
      const pageSize = parseInt(url.searchParams.get('page_size') || '15', 10);
      const totalPages = Math.ceil(mockAssets.length / pageSize) || 1;

      if (page > totalPages) {
        return HttpResponse.json({ detail: 'Invalid page.' }, { status: 404 });
      }

      const start = (page - 1) * pageSize;
      return HttpResponse.json({
        count: mockAssets.length,
        total_pages: totalPages,
        current_page: page,
        page_size: pageSize,
        results: mockAssets.slice(start, start + pageSize),
      });
    }
    return HttpResponse.json(mockAssets);
  }),

  http.post('/api/requests/', async ({ request }) => {
    const body = await request.json();
    const newReq = {
      id: createdRequests.length + 1,
      request_code: `REQ-202610-${String(createdRequests.length + 1).padStart(4, '0')}`,
      ...body,
      status: 'PENDING',
      created_at: '2026-10-30T10:00:00Z',
    };
    createdRequests.push(newReq);
    return HttpResponse.json(newReq, { status: 201 });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  mockAssets = createInitialMockAssets();
  createdRequests = [];
});
afterAll(() => server.close());

const renderUserPortal = () => {
  return render(
    <MemoryRouter>
      <AuthProvider
        initialUser={{ id: 2, username: 'staffuser', full_name: 'Nhân Viên A', role: 'USER', department_id: 1 }}
        initialToken="token-staff"
      >
        <UserPortalPage />
      </AuthProvider>
    </MemoryRouter>
  );
};

describe('UserPortalPage - Phân trang backend 15 bản ghi và Regression Tests', () => {
  it('hiển thị trang 1 với 15 tài sản đầu tiên và tóm tắt phân trang 1–15 trên tổng 22 bản ghi', async () => {
    renderUserPortal();

    // Chờ tải dữ liệu trang 1
    expect(await screen.findByText('Tài sản người dùng 1')).toBeInTheDocument();
    expect(screen.getByText('Tài sản người dùng 15')).toBeInTheDocument();
    expect(screen.queryByText('Tài sản người dùng 16')).toBeNull();

    // Kiểm tra tóm tắt phân trang
    expect(screen.getByText(/Đang xem 1–15 trên tổng 22 bản ghi/i)).toBeInTheDocument();

    // Nút "Trước" bị disable ở trang 1, nút "Sau" bật
    const prevBtn = screen.getByRole('button', { name: /Trang trước/i });
    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    expect(prevBtn).toBeDisabled();
    expect(nextBtn).toBeEnabled();
  });

  it('bấm nút "Sau" hoặc trang "2" hiển thị tiếp 7 tài sản còn lại với tóm tắt 16–22 trên tổng 22 bản ghi', async () => {
    renderUserPortal();

    expect(await screen.findByText('Tài sản người dùng 1')).toBeInTheDocument();

    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);

    // Trang 2 hiển thị các tài sản từ 16 đến 22
    expect(await screen.findByText('Tài sản người dùng 16')).toBeInTheDocument();
    expect(screen.getByText('Tài sản người dùng 22')).toBeInTheDocument();
    expect(screen.queryByText('Tài sản người dùng 1')).toBeNull();
    expect(screen.queryByText('Tài sản người dùng 15')).toBeNull();

    // Kiểm tra tóm tắt trang 2
    expect(screen.getByText(/Đang xem 16–22 trên tổng 22 bản ghi/i)).toBeInTheDocument();

    // Ở trang 2 (trang cuối), nút "Sau" bị disable, nút "Trước" bật
    expect(screen.getByRole('button', { name: /Trang trước/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Trang sau/i })).toBeDisabled();

    // Bấm nút "Trước" quay lại trang 1
    const prevBtn = screen.getByRole('button', { name: /Trang trước/i });
    await userEvent.click(prevBtn);

    expect(await screen.findByText('Tài sản người dùng 1')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 1–15 trên tổng 22 bản ghi/i)).toBeInTheDocument();
  });

  it('nút "Báo hỏng" mở đúng modal với thông tin tài sản tương ứng và gửi yêu cầu thành công', async () => {
    renderUserPortal();

    expect(await screen.findByText('Tài sản người dùng 1')).toBeInTheDocument();

    const damageButtons = screen.getAllByRole('button', { name: /Báo hỏng/i });
    expect(damageButtons.length).toBeGreaterThan(0);

    // Bấm Báo hỏng cho tài sản đầu tiên (id: 1, AST-202610-0001)
    await userEvent.click(damageButtons[0]);

    const modal = screen.getByRole('dialog');
    expect(modal).toBeInTheDocument();

    // Kiểm tra select loại yêu cầu và tài sản đang giữ
    const typeSelect = within(modal).getByLabelText(/Loại yêu cầu/i);
    expect(typeSelect.value).toBe('DAMAGE_REPORT');

    const assetSelect = within(modal).getByLabelText(/Tài sản đang giữ/i);
    expect(assetSelect.value).toBe('1');

    // Điền lý do và gửi yêu cầu
    const reasonInput = within(modal).getByLabelText(/Lý do chi tiết/i);
    await userEvent.type(reasonInput, 'Màn hình bị sọc ngang không hiển thị rõ');

    await userEvent.click(within(modal).getByRole('button', { name: /Gửi yêu cầu/i }));

    // Thông báo thành công hiển thị
    expect(await screen.findByRole('status')).toHaveTextContent(/Gửi yêu cầu thành công/i);
    expect(createdRequests).toHaveLength(1);
    expect(createdRequests[0].request_type).toBe('DAMAGE_REPORT');
    expect(createdRequests[0].asset_id).toBe(1);
    expect(createdRequests[0].reason).toBe('Màn hình bị sọc ngang không hiển thị rõ');
  });

  it('nút "Trả tài sản" mở đúng modal với thông tin tài sản tương ứng và gửi yêu cầu thành công', async () => {
    renderUserPortal();

    expect(await screen.findByText('Tài sản người dùng 2')).toBeInTheDocument();

    const returnButtons = screen.getAllByRole('button', { name: /Trả tài sản/i });
    expect(returnButtons.length).toBeGreaterThan(1);

    // Bấm Trả tài sản cho dòng thứ 2 (id: 2, AST-202610-0002)
    await userEvent.click(returnButtons[1]);

    const modal = screen.getByRole('dialog');
    expect(modal).toBeInTheDocument();

    const typeSelect = within(modal).getByLabelText(/Loại yêu cầu/i);
    expect(typeSelect.value).toBe('RETURN');

    const assetSelect = within(modal).getByLabelText(/Tài sản đang giữ/i);
    expect(assetSelect.value).toBe('2');

    // Điền lý do và submit
    const reasonInput = within(modal).getByLabelText(/Lý do chi tiết/i);
    await userEvent.type(reasonInput, 'Đổi dự án không cần dùng nữa');

    await userEvent.click(within(modal).getByRole('button', { name: /Gửi yêu cầu/i }));

    expect(await screen.findByRole('status')).toHaveTextContent(/Gửi yêu cầu thành công/i);
    expect(createdRequests).toHaveLength(1);
    expect(createdRequests[0].request_type).toBe('RETURN');
    expect(createdRequests[0].asset_id).toBe(2);
    expect(createdRequests[0].reason).toBe('Đổi dự án không cần dùng nữa');
  });

  it('kiểm tra trường hợp 404 khi trang 2 không còn tồn tại: tự động quay về trang 1', async () => {
    renderUserPortal();

    expect(await screen.findByText('Tài sản người dùng 1')).toBeInTheDocument();

    // Chuyển sang trang 2
    const nextBtn = screen.getByRole('button', { name: /Trang sau/i });
    await userEvent.click(nextBtn);
    expect(await screen.findByText('Tài sản người dùng 16')).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 16–22 trên tổng 22 bản ghi/i)).toBeInTheDocument();

    // Dữ liệu trên backend giảm xuống còn 10 tài sản (trang 2 không còn tồn tại -> API trả về 404 Invalid page)
    mockAssets = mockAssets.slice(0, 10);

    // Bấm Làm mới khi đang ở trang 2 -> backend trả về 404
    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);

    // Component tự động phục hồi về trang 1 mà không hiện error alert
    await waitFor(() => {
      expect(screen.getByText('Tài sản người dùng 1')).toBeInTheDocument();
    });
    expect(screen.getByText(/Đang xem 1–10 trên tổng 10 bản ghi/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('nút "Gửi yêu cầu cấp mới" mở modal ALLOCATE và nút "Làm mới" hoạt động bình thường', async () => {
    renderUserPortal();

    expect(await screen.findByText('Tài sản người dùng 1')).toBeInTheDocument();

    // Bấm nút "Gửi yêu cầu cấp mới"
    const newRequestBtn = screen.getByRole('button', { name: /Gửi yêu cầu cấp mới/i });
    await userEvent.click(newRequestBtn);

    const modal = screen.getByRole('dialog');
    expect(modal).toBeInTheDocument();
    const typeSelect = within(modal).getByLabelText(/Loại yêu cầu/i);
    expect(typeSelect.value).toBe('ALLOCATE');

    // Chọn danh mục và nhập lý do
    await userEvent.selectOptions(within(modal).getByLabelText(/Danh mục cần cấp/i), '1');
    await userEvent.type(within(modal).getByLabelText(/Lý do chi tiết/i), 'Cần laptop làm việc onsite');

    await userEvent.click(within(modal).getByRole('button', { name: /Gửi yêu cầu/i }));

    expect(await screen.findByRole('status')).toHaveTextContent(/Gửi yêu cầu thành công/i);
    expect(createdRequests).toHaveLength(1);
    expect(createdRequests[0].request_type).toBe('ALLOCATE');
    expect(createdRequests[0].requested_category_id).toBe(1);

    // Kiểm tra nút Làm mới
    const refreshBtn = screen.getByRole('button', { name: /Làm mới/i });
    await userEvent.click(refreshBtn);
    expect(await screen.findByText('Tài sản người dùng 1')).toBeInTheDocument();
  });
});
