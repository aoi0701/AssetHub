import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import Pagination from '../components/Pagination';

describe('Pagination Component', () => {
  it('renders summary text correctly for page 1 with 40 records', () => {
    render(
      <Pagination
        currentPage={1}
        totalPages={3}
        totalCount={40}
        pageSize={15}
        onPageChange={() => {}}
      />
    );
    expect(screen.getByText(/Đang xem 1–15 trên tổng 40 bản ghi/i)).toBeInTheDocument();
  });

  it('disables "Trước" button on page 1 and calls onPageChange on "Sau" click', async () => {
    const user = userEvent.setup();
    const handlePageChange = vi.fn();

    render(
      <Pagination
        currentPage={1}
        totalPages={3}
        totalCount={40}
        pageSize={15}
        onPageChange={handlePageChange}
      />
    );

    const prevBtn = screen.getByRole('button', { name: /Trước/i });
    const nextBtn = screen.getByRole('button', { name: /Sau/i });

    expect(prevBtn).toBeDisabled();
    expect(nextBtn).toBeEnabled();

    await user.click(nextBtn);
    expect(handlePageChange).toHaveBeenCalledWith(2);
  });

  it('disables "Sau" button on the last page', async () => {
    const user = userEvent.setup();
    const handlePageChange = vi.fn();

    render(
      <Pagination
        currentPage={3}
        totalPages={3}
        totalCount={40}
        pageSize={15}
        onPageChange={handlePageChange}
      />
    );

    const prevBtn = screen.getByRole('button', { name: /Trước/i });
    const nextBtn = screen.getByRole('button', { name: /Sau/i });

    expect(prevBtn).toBeEnabled();
    expect(nextBtn).toBeDisabled();

    await user.click(prevBtn);
    expect(handlePageChange).toHaveBeenCalledWith(2);
  });

  it('renders summary 0–0 for totalCount = 0 and disables buttons', () => {
    render(
      <Pagination
        currentPage={1}
        totalPages={0}
        totalCount={0}
        pageSize={15}
        onPageChange={() => {}}
      />
    );

    expect(screen.getByText(/Đang xem 0–0 trên tổng 0 bản ghi/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Trước/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Sau/i })).toBeDisabled();
  });

  it('renders numbered page buttons and allows direct jump', async () => {
    const user = userEvent.setup();
    const handlePageChange = vi.fn();

    render(
      <Pagination
        currentPage={1}
        totalPages={3}
        totalCount={40}
        pageSize={15}
        onPageChange={handlePageChange}
      />
    );

    const page2Btn = screen.getByRole('button', { name: '2' });
    expect(page2Btn).toBeInTheDocument();

    await user.click(page2Btn);
    expect(handlePageChange).toHaveBeenCalledWith(2);
  });
});
