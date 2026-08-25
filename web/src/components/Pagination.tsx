import type { JSX } from 'react';

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  pageSizeOptions?: number[];
}

function range(start: number, end: number): number[] {
  return Array.from({ length: end - start + 1 }, (_, i) => start + i);
}

function pageNumbers(current: number, total: number): (number | '...')[] {
  if (total <= 5) return range(1, total);

  const pages: (number | '...')[] = [1];

  if (current > 3) pages.push('...');

  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  for (let i = start; i <= end; i++) pages.push(i);

  if (current < total - 2) pages.push('...');

  pages.push(total);
  return pages;
}

export function Pagination({
  currentPage,
  totalPages,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [12, 24, 48],
}: PaginationProps): JSX.Element | null {
  if (totalPages <= 1) return null;

  const start = (currentPage - 1) * pageSize + 1;
  const end = Math.min(currentPage * pageSize, totalItems);

  return (
    <div className="mt-6 pt-4 border-t border-gray-800">
      <p className="text-xs text-gray-500 mb-3">
        显示 {start}-{end} / 共 {totalItems} 条
      </p>

      <div className="flex items-center justify-between flex-wrap gap-3">
        {/* Page size selector */}
        <div className="flex items-center gap-1.5">
          {pageSizeOptions.map(size => (
            <button
              key={size}
              onClick={() => onPageSizeChange(size)}
              className={`px-3 py-1.5 text-xs rounded-lg transition-colors border ${
                pageSize === size
                  ? 'bg-blue-600/10 text-blue-400 border-blue-600/30'
                  : 'bg-gray-900 text-gray-400 border-gray-800 hover:border-gray-700'
              }`}
            >
              {size}
            </button>
          ))}
        </div>

        {/* Page numbers */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => onPageChange(currentPage - 1)}
            disabled={currentPage <= 1}
            className="p-1.5 rounded-lg border border-gray-800 bg-gray-900 text-gray-400 hover:border-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>

          {pageNumbers(currentPage, totalPages).map((p, i) =>
            p === '...' ? (
              <span key={`ellipsis-${i}`} className="w-8 text-center text-gray-600 text-sm">...</span>
            ) : (
              <button
                key={p}
                onClick={() => onPageChange(p)}
                className={`w-8 h-8 text-sm rounded-lg transition-colors ${
                  currentPage === p
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >
                {p}
              </button>
            )
          )}

          <button
            onClick={() => onPageChange(currentPage + 1)}
            disabled={currentPage >= totalPages}
            className="p-1.5 rounded-lg border border-gray-800 bg-gray-900 text-gray-400 hover:border-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
        </div>

        {/* Spacer to balance layout */}
        <div className="w-[72px]" />
      </div>
    </div>
  );
}