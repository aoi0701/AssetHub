import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import AssetCreateModal from '../components/AssetCreateModal';

const mockCategories = [
  { id: 1, name: 'Laptop' },
  { id: 2, name: 'Màn hình' },
  { id: 3, name: 'Bàn phím cơ' },
];

const mockDepartments = [
  { id: 1, code: 'IT', name: 'Phòng IT' },
  { id: 2, code: 'HR', name: 'Phòng HR' },
];

let lastAiRequestBody = null;

const server = setupServer(
  http.post('/api/ai/suggest-category/', async ({ request }) => {
    lastAiRequestBody = await request.json();
    return HttpResponse.json({
      request_id: 'ai-req-001',
      suggested_category_id: 1,
      suggested_category_name: 'Laptop',
      explanation: 'Dựa trên tên MacBook Pro, danh mục phù hợp là Laptop.',
      log_saved: true,
    });
  }),
  http.post('/api/assets/', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json({ id: 10, ...body }, { status: 201 });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  lastAiRequestBody = null;
});
afterAll(() => server.close());

describe('AssetCreateModal AI Category Suggestion', () => {
  it('calls /api/ai/suggest-category/ when clicking AI button and applies suggestion', async () => {
    const handleClose = vi.fn();
    const handleSuccess = vi.fn();

    render(
      <AssetCreateModal
        isOpen={true}
        onClose={handleClose}
        onSuccess={handleSuccess}
        categories={mockCategories}
        departments={mockDepartments}
      />
    );

    const nameInput = screen.getByLabelText(/Tên tài sản/i);
    const categorySelect = screen.getByLabelText(/Danh mục/i);
    const aiButton = screen.getByRole('button', { name: /Gợi ý danh mục \(AI\)/i });

    expect(categorySelect.value).toBe('');

    // Fill name
    await userEvent.type(nameInput, 'MacBook Pro M3 Max 16 inch');

    // Click AI button
    await userEvent.click(aiButton);

    // Wait for suggestion box to appear
    expect(await screen.findByText(/Dựa trên tên MacBook Pro/i)).toBeInTheDocument();
    expect(screen.getByText('Gợi ý:')).toBeInTheDocument();
    expect(lastAiRequestBody).toMatchObject({
      name: 'MacBook Pro M3 Max 16 inch',
    });

    // Click "Áp dụng"
    const applyButton = screen.getByRole('button', { name: /Áp dụng/i });
    await userEvent.click(applyButton);

    // Selected category should now be '1' (Laptop)
    expect(categorySelect.value).toBe('1');
  });

  it('handles AI failure gracefully with warning notice and allows manual submission', async () => {
    server.use(
      http.post('/api/ai/suggest-category/', () => {
        return HttpResponse.json(
          { detail: 'AI service circuit breaker is open.' },
          { status: 503 }
        );
      })
    );

    const handleClose = vi.fn();
    const handleSuccess = vi.fn();

    render(
      <AssetCreateModal
        isOpen={true}
        onClose={handleClose}
        onSuccess={handleSuccess}
        categories={mockCategories}
        departments={mockDepartments}
      />
    );

    const nameInput = screen.getByLabelText(/Tên tài sản/i);
    const aiButton = screen.getByRole('button', { name: /Gợi ý danh mục \(AI\)/i });

    await userEvent.type(nameInput, 'Dell UltraSharp 32 inch');
    await userEvent.click(aiButton);

    // Shows yellow notice
    const notice = await screen.findByRole('status');
    expect(notice).toHaveTextContent(/Dịch vụ AI tạm thời không khả dụng, vui lòng chọn thủ công/i);

    // User can manually select category
    const categorySelect = screen.getByLabelText(/Danh mục/i);
    await userEvent.selectOptions(categorySelect, '2');
    expect(categorySelect.value).toBe('2');

    // Form can still be submitted
    const submitButton = screen.getByRole('button', { name: /Tạo tài sản/i });
    await userEvent.click(submitButton);

    await waitFor(() => {
      expect(handleSuccess).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Dell UltraSharp 32 inch',
          category_id: 2,
        })
      );
      expect(handleClose).toHaveBeenCalled();
    });
  });
});
