import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import Pagination from '../components/Pagination';

describe('Pagination Component', () => {
  it('renders semantic nav with correct aria-label and summary text for page 1 with 40 records', () => {
    render(
      <Pagination
        currentPage={1}
        totalPages={3}
        totalCount={40}
        pageSize={15}
        onPageChange={() => {}}
      />
    );
    const nav = screen.getByRole('navigation', { name: /Phân trang/i });
    expect(nav).toBeInTheDocument();
    expect(screen.getByText(/Đang xem 1–15 trên tổng 40 bản ghi/i)).toBeInTheDocument();
    expect(screen.getByText(/\(Trang 1 \/ 3\)/i)).toBeInTheDocument();
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
    expect(prevBtn).toHaveAttribute('title', 'Trang trước');
    expect(nextBtn).toBeEnabled();
    expect(nextBtn).toHaveAttribute('title', 'Trang sau');
    expect(nextBtn.className).toContain('focus:ring-2 focus:ring-blue-500');

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

  it('renders numbered page buttons with aria-current and allows direct jump', async () => {
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

    const page1Btn = screen.getByRole('button', { name: 'Trang 1' });
    const page2Btn = screen.getByRole('button', { name: 'Trang 2' });

    expect(page1Btn).toHaveAttribute('aria-current', 'page');
    expect(page2Btn).not.toHaveAttribute('aria-current');
    expect(page2Btn).toHaveAttribute('title', 'Trang 2');
    expect(page2Btn.className).toContain('focus:ring-2 focus:ring-blue-500');

    await user.click(page2Btn);
    expect(handlePageChange).toHaveBeenCalledWith(2);
  });

  it('renders ellipsis when totalPages > 5', () => {
    render(
      <Pagination
        currentPage={5}
        totalPages={10}
        totalCount={150}
        pageSize={15}
        onPageChange={() => {}}
      />
    );

    const ellipses = screen.getAllByText('...');
    expect(ellipses.length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Trang 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Trang 5' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Trang 10' })).toBeInTheDocument();
  });

  it('disables all buttons when loading is true and ignores clicks', async () => {
    const user = userEvent.setup();
    const handlePageChange = vi.fn();

    render(
      <Pagination
        currentPage={2}
        totalPages={3}
        totalCount={40}
        pageSize={15}
        loading={true}
        onPageChange={handlePageChange}
      />
    );

    const prevBtn = screen.getByRole('button', { name: /Trước/i });
    const nextBtn = screen.getByRole('button', { name: /Sau/i });
    const page1Btn = screen.getByRole('button', { name: 'Trang 1' });

    expect(prevBtn).toBeDisabled();
    expect(nextBtn).toBeDisabled();
    expect(page1Btn).toBeDisabled();

    await user.click(page1Btn);
    expect(handlePageChange).not.toHaveBeenCalled();
  });
});
