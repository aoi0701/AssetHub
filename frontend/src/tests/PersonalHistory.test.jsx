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
});
