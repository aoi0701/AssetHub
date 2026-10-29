import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider } from '../context/AuthContext';
import axiosClient from '../api/axiosClient';
import AssetAttachments from '../components/AssetAttachments';
import AssetDetailPage from '../pages/AssetDetailPage';

let mockAsset = {
  id: 10,
  asset_code: 'AST-202610-0010',
  name: 'MacBook Pro M3 Max',
  category_id: 1,
  category_name: 'Laptop',
  department_id: 1,
  department_name: 'Phòng IT',
  department_code: 'IT',
  assigned_to_user_id: 2,
  assigned_to_user_name: 'Nhân Viên IT',
  assigned_to_user_username: 'staff_it',
  status: 'IN_USE',
  image: 'assets/10/image/photo.png',
  document: 'assets/10/document/manual.pdf',
  created_at: '2026-10-15T08:00:00Z',
};

const server = setupServer(
  http.get('/api/assets/:id/', () => HttpResponse.json(mockAsset)),
  http.get('/api/assets/:id/history/', () => HttpResponse.json([])),
  http.get('/api/categories/', () => HttpResponse.json([])),
  http.get('/api/departments/', () => HttpResponse.json([])),
  http.get('/api/users/', () => HttpResponse.json([])),

  http.get('/api/assets/:id/attachments/:kind/download/', ({ params }) => {
    return HttpResponse.json({
      download_url: `https://fake.r2.cloudflarestorage.com/download/${params.kind}?token=signed_123`,
      expires_in: 60,
      kind: params.kind,
    });
  }),

  http.post('/api/assets/:id/attachments/', () => {
    return HttpResponse.json({
      message: 'Tải tệp lên thành công',
      kind: 'image',
      key: 'assets/10/image/uploaded_laptop.png',
    });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

describe('AssetAttachments Component & Integration Tests', () => {
  it('renders attachments component and shows image and document cards', async () => {
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'admin', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <AssetAttachments asset={mockAsset} />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText(/Tệp đính kèm & Tài liệu/i)).toBeInTheDocument();
    expect(screen.getByText(/Hình ảnh tài sản/i)).toBeInTheDocument();
    expect(screen.getByText(/Tài liệu \/ Hướng dẫn/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Tải ảnh xuống/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Tải tài liệu PDF/i })).toBeInTheDocument();
  });

  it('Admin can upload file successfully', async () => {
    const postSpy = vi.spyOn(axiosClient, 'post').mockImplementation(async (url, data) => {
      const kind = data instanceof FormData ? data.get('kind') : 'image';
      const file = data instanceof FormData ? data.get('file') : null;
      return {
        status: 200,
        data: {
          message: 'Tải tệp lên thành công',
          kind: kind,
          key: `assets/10/${kind}/uploaded_${file?.name || 'file.png'}`,
        },
      };
    });

    const onUploadSuccess = vi.fn();
    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'admin', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <AssetAttachments asset={mockAsset} onUploadSuccess={onUploadSuccess} />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText(/Tải lên tệp đính kèm mới \(Chỉ Admin\)/i)).toBeInTheDocument();

    const file = new File(['valid png bytes'], 'laptop.png', { type: 'image/png' });
    const fileInput = screen.getByLabelText(/Chọn tệp/i);
    fireEvent.change(fileInput, { target: { files: [file] } });

    const uploadBtn = screen.getByRole('button', { name: /Tải lên/i });
    expect(uploadBtn).not.toBeDisabled();
    await userEvent.click(uploadBtn);

    expect(await screen.findByRole('status')).toHaveTextContent(/Tải tệp lên thành công/i);
    expect(postSpy).toHaveBeenCalled();
    expect(onUploadSuccess).toHaveBeenCalled();
  });

  it('User can download held asset attachment via presigned link', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => {});

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 2, username: 'staff_it', role: 'USER' }}
          initialToken="token-user"
        >
          <AssetAttachments asset={mockAsset} />
        </AuthProvider>
      </MemoryRouter>
    );

    // Standard user does NOT see upload section
    expect(screen.queryByText(/Tải lên tệp đính kèm mới/i)).toBeNull();

    const downloadDocBtn = screen.getByRole('button', { name: /Tải tài liệu PDF/i });
    await userEvent.click(downloadDocBtn);

    await waitFor(() => {
      expect(openSpy).toHaveBeenCalledWith(
        expect.stringContaining('https://fake.r2.cloudflarestorage.com/download/document'),
        '_blank'
      );
    });
  });

  it('Error state maintains form intact without clearing user inputs', async () => {
    vi.spyOn(axiosClient, 'post').mockImplementation(async () => {
      const err = new Error('Request failed with status code 400');
      err.response = {
        status: 400,
        data: { detail: 'Tệp không hợp lệ hoặc bị lỗi cấu trúc.' },
      };
      throw err;
    });

    render(
      <MemoryRouter>
        <AuthProvider
          initialUser={{ id: 1, username: 'admin', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <AssetAttachments asset={mockAsset} />
        </AuthProvider>
      </MemoryRouter>
    );

    // Select document kind
    const kindSelect = screen.getByLabelText(/Loại tệp/i);
    await userEvent.selectOptions(kindSelect, 'document');

    // Upload bad file
    const badFile = new File(['corrupt text'], 'bad.pdf', { type: 'application/pdf' });
    const fileInput = screen.getByLabelText(/Chọn tệp/i);
    fireEvent.change(fileInput, { target: { files: [badFile] } });

    const uploadBtn = screen.getByRole('button', { name: /Tải lên/i });
    await userEvent.click(uploadBtn);

    // Error alert is displayed
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Tệp không hợp lệ hoặc bị lỗi cấu trúc/i);

    // Form is still present with kind selector still set to document
    expect(screen.getByLabelText(/Loại tệp/i)).toHaveValue('document');
    expect(screen.getByRole('button', { name: /Tải lên/i })).toBeInTheDocument();
  });

  it('Integrated in AssetDetailPage renders attachments card properly', async () => {
    render(
      <MemoryRouter initialEntries={['/assets/10']}>
        <AuthProvider
          initialUser={{ id: 1, username: 'admin', role: 'ADMIN' }}
          initialToken="token-admin"
        >
          <Routes>
            <Route path="/assets/:id" element={<AssetDetailPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('MacBook Pro M3 Max')).toBeInTheDocument();
    expect(screen.getByText(/Tệp đính kèm & Tài liệu/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Tải ảnh xuống/i })).toBeInTheDocument();
  });
});
