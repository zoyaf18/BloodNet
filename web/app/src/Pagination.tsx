import { useEffect, useState, type ReactNode } from "react";

export function usePagination<T>(items: T[], pageSize = 10): [T[], number, ReactNode] {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);

  const start = (page - 1) * pageSize;
  const visible = items.slice(start, start + pageSize);
  const controls = items.length > pageSize ? (
    <nav className="pagination" aria-label="Pagination">
      <button type="button" className="secondary compact" disabled={page === 1} onClick={() => setPage((current) => current - 1)}>
        Previous
      </button>
      <span aria-live="polite">Page {page} of {pageCount}</span>
      <button type="button" className="secondary compact" disabled={page === pageCount} onClick={() => setPage((current) => current + 1)}>
        Next
      </button>
    </nav>
  ) : null;

  return [visible, page, controls];
}
