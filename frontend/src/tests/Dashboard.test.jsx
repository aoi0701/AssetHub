import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider } from '../context/AuthContext';
import DashboardPage from '../pages/DashboardPage';

let mockKPIs = {
  total_active: 50,
  available: 20,
  in_use: 25,
  damaged_under_maintenance: 3,
  pending_disposal: 2,
  pending_requests: 4,
  last_updated: '2026-10-29T12:00:00Z',
};

let mockCharts = {
  status_distribution: [
    { status: 'AVAILABLE', label: 'Sẵn sàng', count: 20 },
    { status: 'IN_USE', label: 'Đang sử dụng', count: 25 },
  ],
  category_distribution: [
    { category_id: 1, category_name: 'Laptop', count: 30 },
    { category_id: 2, category_name: 'Màn hình', count: 20 },
  ],
  department_distribution: [
    { department_id: 1, department_name: 'Phòng IT', count: 35 },
    { department_id: 2, department_name: 'Phòng HR', count: 15 },
  ],
  monthly_trend: [
    { month: '2026-09', new_assets: 10, disposed_assets: 2 },
    { month: '2026-10', new_assets: 8, disposed_assets: 1 },
  ],
};

let lastRequestedChartParams = null;

const server = setupServer(
  http.get('/api/categories/', () => {
    return HttpResponse.json([
      { id: 1, name: 'Laptop' },
      { id: 2, name: 'Màn hình' },
    ]);
  }),

  http.get('/api/departments/', () => {
    return HttpResponse.json([
      { id: 1, name: 'Phòng IT', code: 'IT' },
      { id: 2, name: 'Phòng HR', code: 'HR' },
    ]);
  }),

  http.get('/api/reports/kpis/', () => {
    return HttpResponse.json(mockKPIs);
  }),

  http.get('/api/reports/charts/', ({ request }) => {
    const url = new URL(request.url);
    lastRequestedChartParams = {
      category_id: url.searchParams.get('category_id'),
      department_id: url.searchParams.get('department_id'),
      start_month: url.searchParams.get('start_month'),
      end_month: url.searchParams.get('end_month'),
    };
    return HttpResponse.json(mockCharts);
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  lastRequestedChartParams = null;
});
afterAll(() => server.close());

describe('Dashboard Integration Tests', () => {
  it('Renders 6 KPI cards with correct metric values', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <DashboardPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Verify 6 KPI metrics
    expect(await screen.findByText('50')).toBeInTheDocument(); // total_active
    expect(screen.getByText('20')).toBeInTheDocument(); // available
    expect(screen.getByText('25')).toBeInTheDocument(); // in_use
    expect(screen.getByText('3')).toBeInTheDocument(); // damaged_under_maintenance
    expect(screen.getByText('2')).toBeInTheDocument(); // pending_disposal
    expect(screen.getByText('4')).toBeInTheDocument(); // pending_requests
  });

  it('Admin sees all 4 charts including department distribution chart', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <DashboardPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('50')).toBeInTheDocument();

    // Verify Department Bar Chart is rendered for Admin
    expect(screen.getByText('Phân bố tài sản giữa các phòng ban')).toBeInTheDocument();
    expect(screen.getByTestId('department-bar-chart')).toBeInTheDocument();
    expect(screen.getByTestId('status-pie-chart')).toBeInTheDocument();
    expect(screen.getByTestId('category-bar-chart')).toBeInTheDocument();
    expect(screen.getByTestId('monthly-trend-chart')).toBeInTheDocument();
  });

  it('Manager DOES NOT see department distribution chart', async () => {
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
          <DashboardPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('50')).toBeInTheDocument();

    // STRICT CHECK: Department Distribution Chart is NOT displayed for Manager
    expect(screen.queryByText('Phân bố tài sản giữa các phòng ban')).toBeNull();
    expect(screen.queryByTestId('department-bar-chart')).toBeNull();

    // But other charts are rendered
    expect(screen.getByTestId('status-pie-chart')).toBeInTheDocument();
    expect(screen.getByTestId('category-bar-chart')).toBeInTheDocument();
    expect(screen.getByTestId('monthly-trend-chart')).toBeInTheDocument();
  });

  it('Filters update chart query parameters', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <DashboardPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('50')).toBeInTheDocument();

    // Change category filter
    const categorySelect = screen.getByLabelText(/Lọc theo danh mục/i);
    await userEvent.selectOptions(categorySelect, '1');

    await waitFor(() => {
      expect(lastRequestedChartParams?.category_id).toBe('1');
    });
  });

  it('Month range validation error displayed if invalid', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'adminuser', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <DashboardPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('50')).toBeInTheDocument();

    const startInput = screen.getByLabelText(/Tháng bắt đầu/i);
    const endInput = screen.getByLabelText(/Tháng kết thúc/i);

    // 1. Invalid: Start > End
    await userEvent.type(startInput, '2026-12');
    await userEvent.type(endInput, '2026-05');

    const alertStartGreater = await screen.findByRole('alert');
    expect(alertStartGreater).toHaveTextContent('Tháng bắt đầu không được lớn hơn tháng kết thúc.');

    // 2. Invalid: Range > 12 months
    await userEvent.clear(startInput);
    await userEvent.clear(endInput);
    await userEvent.type(startInput, '2025-01');
    await userEvent.type(endInput, '2026-06'); // 18 months

    const alertRangeTooLarge = await screen.findByRole('alert');
    expect(alertRangeTooLarge).toHaveTextContent('Khoảng thời gian thống kê không được vượt quá 12 tháng.');
  });
});
