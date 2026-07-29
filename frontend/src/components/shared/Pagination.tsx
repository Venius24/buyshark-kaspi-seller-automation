import { useState, useEffect } from 'react';

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  disabled: boolean;
  onPageChange: (page: number) => void;
}

export function Pagination({ currentPage, totalPages, disabled, onPageChange }: PaginationProps) {
  const [pageInputText, setPageInputText] = useState(String(currentPage));

  useEffect(() => {
    setPageInputText(String(currentPage));
  }, [currentPage]);

  const commitPageInput = () => {
    const pageNum = parseInt(pageInputText);
    if (pageNum >= 1 && pageNum <= totalPages) {
      onPageChange(pageNum);
    } else {
      setPageInputText(String(currentPage));
    }
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '16px', borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '16px', marginTop: '20px' }}>
      <button
        className="btn btn-secondary"
        style={{ padding: '6px 12px', fontSize: '12px' }}
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage === 1 || disabled}
      >
        ◀ Назад
      </button>

      <span style={{ fontSize: '13px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '8px' }}>
        Страница
        <input
          type="text"
          value={pageInputText}
          onChange={(e) => setPageInputText(e.target.value.replace(/\D/g, ''))}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitPageInput();
          }}
          onBlur={commitPageInput}
          className="form-input"
          style={{ width: '45px', padding: '4px 8px', textAlign: 'center', display: 'inline-block' }}
        />
        из <strong>{totalPages}</strong>
      </span>

      <button
        className="btn btn-secondary"
        style={{ padding: '6px 12px', fontSize: '12px' }}
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage === totalPages || disabled}
      >
        Вперед ▶
      </button>
    </div>
  );
}
