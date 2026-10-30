import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider } from '../context/AuthContext';
import PersonalHistoryPage from '../pages/PersonalHistoryPage';

let mockPersonalHistory = [
  {
    id: 101,
    asset_id: 1,
    asset_code_snapshot: 'AST-202610-0001',
    asset_name_snapshot: 'Laptop MacBook Pro M2',
    action_type: 'ALLOCATE',
    from_status: 'AVAILABLE',
    to_status: 'IN_USE',
    created_at: '2026-10-10T09:00:00Z',
    message: 'Tài sản được cấp phát cho bạn',
  },
  {
    id: 102,
    asset_id: 2,
    asset_code_snapshot: 'AST-202610-0002',
    asset_name_snapshot: 'Màn hình Dell 27 4K',
    action_type: 'RETURN',
    from_status: 'IN_USE',
    to_status: 'AVAILABLE',
    created_at: '2026-10-20T14:30:00Z',
    message: 'Bạn đã hoàn trả tài sản',
  },
];

const server = setupServer(
  http.get('/api/me/asset-history/', () => {
    return HttpResponse.json(mockPersonalHistory);
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
});
afterAll(() => server.close());

describe('PersonalHistory Integration Tests', () => {
  it('Loads /api/me/asset-history/ and renders event snapshots and messages', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 3, username: 'user_b', role: 'USER' }}
          initialToken="token-user"
        >
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Verifies asset snapshot names
    expect(await screen.findByText('Laptop MacBook Pro M2')).toBeInTheDocument();
    expect(screen.getByText('Màn hình Dell 27 4K')).toBeInTheDocument();

    // Verifies asset snapshot codes
    expect(screen.getByText('AST-202610-0001')).toBeInTheDocument();
    expect(screen.getByText('AST-202610-0002')).toBeInTheDocument();

    // Verifies action badges and friendly localized messages
    expect(screen.getByText('Cấp phát')).toBeInTheDocument();
    expect(screen.getByText('Tài sản được cấp phát cho bạn')).toBeInTheDocument();

    expect(screen.getByText('Hoàn trả')).toBeInTheDocument();
    expect(screen.getByText('Bạn đã hoàn trả tài sản')).toBeInTheDocument();

    // STRICT CHECK: Ensure NO link to detailed asset history timeline /api/assets/:id/history
    const timelineLinks = screen.queryAllByRole('link');
    timelineLinks.forEach((link) => {
      expect(link.getAttribute('href')).not.toMatch(/\/assets\/\d+\/history/);
    });
  });

  it('Persists historical events after asset is returned and no longer held', async () => {
    // History contains returned asset record
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 3, username: 'user_b', role: 'USER' }}
          initialToken="token-user"
        >
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Bạn đã hoàn trả tài sản')).toBeInTheDocument();
    expect(screen.getByText('Màn hình Dell 27 4K')).toBeInTheDocument();
  });

  it('Renders DAMAGE and MAINTENANCE_END action badges correctly', async () => {
    mockPersonalHistory = [
      {
        id: 103,
        asset_id: 3,
        asset_code_snapshot: 'AST-202610-0003',
        asset_name_snapshot: 'Bàn phím cơ Keychron',
        action_type: 'DAMAGE',
        from_status: 'IN_USE',
        to_status: 'DAMAGED_PENDING_REPAIR',
        created_at: '2026-10-25T11:00:00Z',
        message: 'Tài sản được ghi nhận báo hỏng',
      },
      {
        id: 104,
        asset_id: 4,
        asset_code_snapshot: 'AST-202610-0004',
        asset_name_snapshot: 'Tai nghe Sony WH-1000XM5',
        action_type: 'MAINTENANCE_END',
        from_status: 'UNDER_MAINTENANCE',
        to_status: 'AVAILABLE',
        created_at: '2026-10-26T16:00:00Z',
        message: 'Tài sản đã hoàn tất bảo trì',
      },
    ];

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 3, username: 'user_b', role: 'USER' }}
          initialToken="token-user"
        >
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Bàn phím cơ Keychron')).toBeInTheDocument();
    expect(screen.getByText('Báo hỏng')).toBeInTheDocument();
    expect(screen.getByText('Tai nghe Sony WH-1000XM5')).toBeInTheDocument();
    expect(screen.getByText('Hoàn tất bảo trì')).toBeInTheDocument();
  });

  it('Paginates across 25 events: 20 on page 1, 5 on page 2, and strictly scopes out other users', async () => {
    const user = userEvent.setup();

    // 25 events for logged-in user
    const userEvents = Array.from({ length: 25 }, (_, i) => ({
      id: i + 1,
      asset_id: i + 1,
      asset_code_snapshot: `AST-USER-${String(i + 1).padStart(3, '0')}`,
      asset_name_snapshot: `Thiết bị của tôi #${i + 1}`,
      action_type: i % 2 === 0 ? 'ALLOCATE' : 'RETURN',
      from_status: i % 2 === 0 ? 'AVAILABLE' : 'IN_USE',
      to_status: i % 2 === 0 ? 'IN_USE' : 'AVAILABLE',
      created_at: `2026-10-${String(Math.min(28, (i % 28) + 1)).padStart(2, '0')}T10:00:00Z`,
      message: i % 2 === 0 ? 'Tài sản được cấp phát cho bạn' : 'Bạn đã hoàn trả tài sản',
    }));

    // Another user event that must NEVER be returned or visible
    const otherUserEvent = {
      id: 999,
      asset_id: 999,
      asset_code_snapshot: 'AST-OTHER-999',
      asset_name_snapshot: 'Dữ liệu bí mật người khác',
      action_type: 'ALLOCATE',
      from_status: 'AVAILABLE',
      to_status: 'IN_USE',
      created_at: '2026-10-25T10:00:00Z',
      message: 'Tài sản cấp cho người khác',
    };

    server.use(
      http.get('/api/me/asset-history/', ({ request }) => {
        const url = new URL(request.url);
        const page = parseInt(url.searchParams.get('page') || '1', 10);
        const pageSize = parseInt(url.searchParams.get('page_size') || '20', 10);

        // Security assertion: API strictly serves userEvents only (otherUserEvent filtered out)
        const total = userEvents.length;
        const totalPages = Math.ceil(total / pageSize);

        if (page > totalPages) {
          return new HttpResponse(null, { status: 404 });
        }

        const start = (page - 1) * pageSize;
        const end = start + pageSize;
        const slice = userEvents.slice(start, end);

        return HttpResponse.json({
          count: total,
          total_pages: totalPages,
          current_page: page,
          results: slice,
        });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 3, username: 'user_b', role: 'USER' }}
          initialToken="token-user"
        >
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Verify Page 1
    expect(await screen.findByText('Thiết bị của tôi #1')).toBeInTheDocument();
    expect(screen.getByText('Thiết bị của tôi #20')).toBeInTheDocument();
    expect(screen.queryByText('Thiết bị của tôi #21')).not.toBeInTheDocument();
    expect(screen.queryByText('Dữ liệu bí mật người khác')).not.toBeInTheDocument();
    expect(screen.getByText('Đang xem 1–20 trên tổng 25 bản ghi')).toBeInTheDocument();

    // Verify pagination controls: Previous is disabled, Next is enabled
    const prevBtn = screen.getByRole('button', { name: 'Trang trước' });
    const nextBtn = screen.getByRole('button', { name: 'Trang sau' });
    expect(prevBtn).toBeDisabled();
    expect(nextBtn).toBeEnabled();

    // Navigate to Page 2
    await user.click(nextBtn);

    // Verify Page 2 items
    expect(await screen.findByText('Thiết bị của tôi #21')).toBeInTheDocument();
    expect(screen.getByText('Thiết bị của tôi #25')).toBeInTheDocument();
    expect(screen.queryByText('Thiết bị của tôi #1')).not.toBeInTheDocument();
    expect(screen.queryByText('Dữ liệu bí mật người khác')).not.toBeInTheDocument();
    expect(screen.getByText('Đang xem 21–25 trên tổng 25 bản ghi')).toBeInTheDocument();

    // On Page 2: Next is disabled, Previous is enabled
    expect(screen.getByRole('button', { name: 'Trang sau' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Trang trước' })).toBeEnabled();

    // Navigate back to Page 1
    await user.click(screen.getByRole('button', { name: 'Trang trước' }));
    expect(await screen.findByText('Thiết bị của tôi #1')).toBeInTheDocument();
    expect(screen.getByText('Đang xem 1–20 trên tổng 25 bản ghi')).toBeInTheDocument();
  });

  it('Ignores stale out-of-order response when changing pages rapidly', async () => {
    server.use(
      http.get('/api/me/asset-history/', async ({ request }) => {
        const url = new URL(request.url);
        const page = url.searchParams.get('page');

        if (page === '2') {
          // Slow response for page 2
          await new Promise((r) => setTimeout(r, 150));
          return HttpResponse.json({
            count: 25,
            total_pages: 2,
            current_page: 2,
            results: [{ id: 2, asset_code_snapshot: 'PAGE-2-STALE', action_type: 'RETURN' }],
          });
        }
        return HttpResponse.json({
          count: 25,
          total_pages: 2,
          current_page: 1,
          results: [{ id: 1, asset_code_snapshot: 'PAGE-1-INITIAL', action_type: 'ALLOCATE' }],
        });
      })
    );

    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AuthProvider initialUser={{ id: 3, username: 'user_b', role: 'USER' }} initialToken="token-user">
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Initial page 1 loads
    expect(await screen.findByText('PAGE-1-INITIAL')).toBeInTheDocument();

    // Click Next to request page 2 (slow, 150ms)
    const nextBtn = screen.getByRole('button', { name: 'Trang sau' });
    await user.click(nextBtn);

    // Rapidly click Prev back to page 1 before page 2 resolves
    const prevBtn = screen.getByRole('button', { name: 'Trang trước' });
    await user.click(prevBtn);

    // Page 1 should remain
    expect(await screen.findByText('PAGE-1-INITIAL')).toBeInTheDocument();

    // Wait for the delayed page 2 promise to resolve
    await new Promise((r) => setTimeout(r, 180));

    // Stale page 2 must NOT overwrite page 1
    expect(screen.queryByText('PAGE-2-STALE')).not.toBeInTheDocument();
    expect(screen.getByText('PAGE-1-INITIAL')).toBeInTheDocument();
  });

  it('Recovers automatically from 404 when records decrease or page is invalid', async () => {
    const requestedPages = [];

    server.use(
      http.get('/api/me/asset-history/', ({ request }) => {
        const url = new URL(request.url);
        const page = url.searchParams.get('page') || '1';
        requestedPages.push(page);

        // Initial page 1: returns count 25 so total_pages = 2 and "Sau" button is enabled
        if (page === '1' && requestedPages.length === 1) {
          return HttpResponse.json({
            count: 25,
            total_pages: 2,
            current_page: 1,
            results: Array.from({ length: 20 }, (_, i) => ({
              id: i + 1,
              asset_code_snapshot: `AST-P1-${i + 1}`,
              action_type: 'ALLOCATE',
            })),
          });
        }

        // Request page 2: records decreased on server, page 2 returns 404
        if (page === '2') {
          return new HttpResponse(null, { status: 404 });
        }

        // Recovery request page 1: returns updated count 10
        return HttpResponse.json({
          count: 10,
          total_pages: 1,
          current_page: 1,
          results: [{ id: 101, asset_code_snapshot: 'AST-RECOVERED-P1', action_type: 'ALLOCATE' }],
        });
      })
    );

    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AuthProvider initialUser={{ id: 3, username: 'user_b', role: 'USER' }} initialToken="token-user">
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Initial load: Page 1 with 25 records
    expect(await screen.findByText('AST-P1-1')).toBeInTheDocument();
    expect(screen.getByText('Đang xem 1–20 trên tổng 25 bản ghi')).toBeInTheDocument();

    // Verify "Sau" button is enabled
    const nextBtn = screen.getByRole('button', { name: 'Trang sau' });
    expect(nextBtn).toBeEnabled();

    // Click "Sau" to trigger page 2 request
    await user.click(nextBtn);

    // Frontend encounters 404 on page 2 and automatically recovers by fetching page 1
    await waitFor(() => {
      expect(screen.getByText('AST-RECOVERED-P1')).toBeInTheDocument();
      expect(screen.getByText('Đang xem 1–10 trên tổng 10 bản ghi')).toBeInTheDocument();
    });

    // Assert exact sequence of requests: ['1', '2', '1']
    expect(requestedPages).toEqual(['1', '2', '1']);
  });

  it('Preserves consistent page data/label on 500 error when changing pages and allows retry', async () => {
    let page2Attempts = 0;

    server.use(
      http.get('/api/me/asset-history/', ({ request }) => {
        const url = new URL(request.url);
        const page = url.searchParams.get('page') || '1';

        if (page === '1') {
          return HttpResponse.json({
            count: 25,
            total_pages: 2,
            current_page: 1,
            results: [{ id: 1, asset_code_snapshot: 'AST-PAGE1-ITEM', action_type: 'ALLOCATE' }],
          });
        }

        if (page === '2') {
          page2Attempts++;
          if (page2Attempts === 1) {
            // First attempt fails with 500
            return new HttpResponse(null, { status: 500 });
          }
          // Retry attempt succeeds
          return HttpResponse.json({
            count: 25,
            total_pages: 2,
            current_page: 2,
            results: [{ id: 2, asset_code_snapshot: 'AST-PAGE2-SUCCESS', action_type: 'RETURN' }],
          });
        }

        return new HttpResponse(null, { status: 404 });
      })
    );

    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AuthProvider initialUser={{ id: 3, username: 'user_b', role: 'USER' }} initialToken="token-user">
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Initial Page 1
    expect(await screen.findByText('AST-PAGE1-ITEM')).toBeInTheDocument();
    expect(screen.getByText('Đang xem 1–20 trên tổng 25 bản ghi')).toBeInTheDocument();

    // Click "Trang sau" -> request page 2 (fails with 500)
    const nextBtn = screen.getByRole('button', { name: 'Trang sau' });
    await user.click(nextBtn);

    // Must show error alert
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Không thể tải nhật ký lịch sử tài sản cá nhân (trang 2). Vui lòng thử lại.'
    );

    // CRITICAL: Must NOT show Page 1 data under Page 2 label!
    // Page label and data must remain consistent with Page 1:
    expect(screen.getByText('AST-PAGE1-ITEM')).toBeInTheDocument();
    expect(screen.getByText('Đang xem 1–20 trên tổng 25 bản ghi')).toBeInTheDocument();
    expect(screen.queryByText('Đang xem 21–25 trên tổng 25 bản ghi')).not.toBeInTheDocument();

    // Retry button is available
    const retryBtn = screen.getByRole('button', { name: 'Thử lại' });
    expect(retryBtn).toBeInTheDocument();

    // Click "Thử lại" -> retry page 2 succeeds!
    await user.click(retryBtn);

    // Now Page 2 data and Page 2 label are displayed consistently
    expect(await screen.findByText('AST-PAGE2-SUCCESS')).toBeInTheDocument();
    expect(screen.getByText('Đang xem 21–25 trên tổng 25 bản ghi')).toBeInTheDocument();
    expect(screen.queryByText('AST-PAGE1-ITEM')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('Renders empty state and error alert appropriately', async () => {
    // 1. Error state
    server.use(
      http.get('/api/me/asset-history/', () => {
        return new HttpResponse(null, { status: 500 });
      })
    );

    const { unmount } = render(
      <MemoryRouter>
        <AuthProvider initialUser={{ id: 3, username: 'user_b', role: 'USER' }} initialToken="token-user">
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Không thể tải nhật ký lịch sử tài sản cá nhân.');
    unmount();

    // 2. Empty state
    server.use(
      http.get('/api/me/asset-history/', () => {
        return HttpResponse.json({ count: 0, results: [] });
      })
    );

    render(
      <MemoryRouter>
        <AuthProvider initialUser={{ id: 3, username: 'user_b', role: 'USER' }} initialToken="token-user">
          <PersonalHistoryPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Chưa có nhật ký hoạt động tài sản nào.')).toBeInTheDocument();
    expect(screen.getByText('Đang xem 0–0 trên tổng 0 bản ghi')).toBeInTheDocument();
  });
});
