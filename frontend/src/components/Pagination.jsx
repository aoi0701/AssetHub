import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

const Pagination = ({
  currentPage = 1,
  totalPages = 1,
  totalCount = 0,
  pageSize = 15,
  onPageChange,
  loading = false,
}) => {
  const safeTotalPages = Math.max(1, totalPages || 1);
  const start = totalCount === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const end = Math.min(currentPage * pageSize, totalCount);

  // Generate page numbers to display
  const getPageNumbers = () => {
    if (safeTotalPages <= 5) {
      return Array.from({ length: safeTotalPages }, (_, i) => i + 1);
    }
    const pages = [];
    if (currentPage <= 3) {
      pages.push(1, 2, 3, '...', safeTotalPages);
    } else if (currentPage >= safeTotalPages - 2) {
      pages.push(1, '...', safeTotalPages - 2, safeTotalPages - 1, safeTotalPages);
    } else {
      pages.push(1, '...', currentPage - 1, currentPage, currentPage + 1, '...', safeTotalPages);
    }
    return pages;
  };

  const handlePrev = () => {
    if (currentPage > 1 && !loading && onPageChange) {
      onPageChange(currentPage - 1);
    }
  };

  const handleNext = () => {
    if (currentPage < safeTotalPages && !loading && onPageChange) {
      onPageChange(currentPage + 1);
    }
  };

  const isPrevDisabled = currentPage <= 1 || loading || totalCount === 0;
  const isNextDisabled = currentPage >= safeTotalPages || loading || totalCount === 0;

  return (
    <nav
      aria-label="Phân trang"
      className="flex flex-col sm:flex-row items-center justify-between gap-3 px-4 py-3 bg-white border-t border-slate-200/90 text-sm text-slate-500"
    >
      <div className="text-xs sm:text-sm text-slate-600">
        {`Đang xem ${start}–${end} trên tổng ${totalCount} bản ghi`}
        {safeTotalPages > 1 && (
          <span className="ml-2 text-slate-400 hidden sm:inline">
            {` (Trang ${currentPage} / ${safeTotalPages})`}
          </span>
        )}
      </div>

      <div className="flex items-center space-x-1.5 self-center sm:self-auto">
        <button
          type="button"
          onClick={handlePrev}
          disabled={isPrevDisabled}
          aria-label="Trang trước"
          className="inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-lg border border-slate-300 text-slate-700 bg-white hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-2xs"
        >
          <ChevronLeft className="w-3.5 h-3.5 mr-1" />
          Trước
        </button>

        {totalCount > 0 && (
          <div className="hidden sm:flex items-center space-x-1">
            {getPageNumbers().map((page, idx) => {
              if (page === '...') {
                return (
                  <span key={`ellipsis-${idx}`} className="px-2 py-1 text-xs text-slate-400">
                    ...
                  </span>
                );
              }
              const isCurrent = page === currentPage;
              return (
                <button
                  key={`page-${page}`}
                  type="button"
                  onClick={() => onPageChange && onPageChange(page)}
                  disabled={loading}
                  aria-current={isCurrent ? 'page' : undefined}
                  className={`inline-flex items-center justify-center min-w-[28px] h-7 px-2 text-xs font-medium rounded-lg border transition-colors ${
                    isCurrent
                      ? 'bg-blue-50 text-blue-700 border-blue-200 font-semibold shadow-2xs'
                      : 'text-slate-600 border-slate-200 bg-white hover:bg-slate-50 hover:text-slate-900'
                  }`}
                >
                  {page}
                </button>
              );
            })}
          </div>
        )}

        <button
          type="button"
          onClick={handleNext}
          disabled={isNextDisabled}
          aria-label="Trang sau"
          className="inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-lg border border-slate-300 text-slate-700 bg-white hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-2xs"
        >
          Sau
          <ChevronRight className="w-3.5 h-3.5 ml-1" />
        </button>
      </div>
    </nav>
  );
};

export default Pagination;
