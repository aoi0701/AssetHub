import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

// Route gating only: the pages have their own suites, so they are replaced by markers here.
vi.mock('../pages/AssetAssistantPage', () => ({ default: () => <div>trang-hoi-dap</div> }));
vi.mock('../pages/AssistantSupportPage', () => ({ default: () => <div>trang-ho-tro</div> }));

const USERS = {
  ADMIN: { id: 1, username: 'a', full_name: 'A', role: 'ADMIN' },
  MANAGER: { id: 2, username: 'm', full_name: 'M', role: 'MANAGER' },
  USER: { id: 3, username: 'u', full_name: 'U', role: 'USER' },
};

const open = (path, role) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider initialUser={USERS[role]}>
        <App />
      </AuthProvider>
    </MemoryRouter>
  );

describe('assistant routes', () => {
  afterEach(() => vi.unstubAllEnvs());

  it.each(['/assistant', '/assistant/support'])('%s is a 404 when the flag is off', (path) => {
    vi.stubEnv('VITE_ASSISTANT_ENABLED', 'false');
    open(path, 'ADMIN');
    expect(screen.getByText('Trang không tồn tại')).toBeVisible();
    expect(screen.queryByText('trang-hoi-dap')).toBeNull();
    expect(screen.queryByText('trang-ho-tro')).toBeNull();
  });

  it.each(['ADMIN', 'MANAGER', 'USER'])('%s can open /assistant when the flag is on', (role) => {
    vi.stubEnv('VITE_ASSISTANT_ENABLED', 'true');
    open('/assistant', role);
    expect(screen.getByText('trang-hoi-dap')).toBeVisible();
  });

  it.each(['ADMIN', 'MANAGER'])('%s can open the support inbox when the flag is on', (role) => {
    vi.stubEnv('VITE_ASSISTANT_ENABLED', 'true');
    open('/assistant/support', role);
    expect(screen.getByText('trang-ho-tro')).toBeVisible();
  });

  it('a USER is refused the support inbox with 403', () => {
    vi.stubEnv('VITE_ASSISTANT_ENABLED', 'true');
    open('/assistant/support', 'USER');
    expect(screen.getByText(/403/)).toBeVisible();
    expect(screen.queryByText('trang-ho-tro')).toBeNull();
  });

  it('a guest is sent to the login page', () => {
    vi.stubEnv('VITE_ASSISTANT_ENABLED', 'true');
    render(
      <MemoryRouter initialEntries={['/assistant']}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    );
    expect(screen.queryByText('trang-hoi-dap')).toBeNull();
  });
});
