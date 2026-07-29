import type { Transfer } from '../../types';

interface TransferTabProps {
  transfers: Transfer[];
  fetchingTransfers: boolean;
  transferFilter: 'ALL' | 'EXPRESS' | 'TODAY';
  onTransferFilterChange: (filter: 'ALL' | 'EXPRESS' | 'TODAY') => void;
  onRefresh: () => void;
}

export function TransferTab({ transfers, fetchingTransfers, transferFilter, onTransferFilterChange, onRefresh }: TransferTabProps) {
  return (
    <>
      {/* Header with Search, Filter & Limit */}
      <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <h2 style={{ fontSize: '16px' }}>
            🚚 Передача заказов курьеру ({transfers.length ? `${transfers.length} поз.` : '0 поз.'})
          </h2>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {/* Transfer Filter */}
            <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px' }}>
              {(['ALL', 'EXPRESS', 'TODAY'] as const).map((filterVal) => (
                <button
                  key={filterVal}
                  className="btn"
                  style={{
                    background: transferFilter === filterVal ? 'var(--primary)' : 'transparent',
                    padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                  }}
                  onClick={() => onTransferFilterChange(filterVal)}
                >
                  {filterVal === 'ALL' && 'Все'}
                  {filterVal === 'EXPRESS' && 'Экспресс'}
                  {filterVal === 'TODAY' && 'Сегодня'}
                </button>
              ))}
            </div>

            <button
              type="button"
              className="btn btn-secondary"
              style={{ padding: '8px 12px' }}
              onClick={onRefresh}
              disabled={fetchingTransfers}
            >
              {fetchingTransfers ? '🔄' : '♻️ Обновить'}
            </button>
          </div>
        </div>
      </div>

      {/* Transfers List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
        {fetchingTransfers && transfers.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
            <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
            <p>Загрузка передач через Kaspi API...</p>
          </div>
        ) : transfers.length === 0 && !fetchingTransfers ? (
          <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
            <p>Передачи не найдены. Нажмите «♻️ Обновить» или смените фильтр.</p>
          </div>
        ) : (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            opacity: fetchingTransfers ? 0.6 : 1,
            pointerEvents: fetchingTransfers ? 'none' : 'auto',
            transition: 'opacity 0.2s ease-in-out'
          }}>
            {transfers.map((transfer) => (
              <div
                key={transfer.id}
                style={{
                  padding: '20px',
                  background: 'rgba(255, 255, 255, 0.02)',
                  borderRadius: '16px',
                  border: '1px solid rgba(255, 255, 255, 0.05)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: '16px'
                }}
              >
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 700, color: '#fff', display: 'flex', alignItems: 'center', gap: '12px' }}>
                    Заказ #{transfer.id}
                    <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--error)' }}>
                      {transfer.deadline}
                    </span>
                  </h3>

                  <div style={{ display: 'flex', gap: '8px', marginTop: '6px', marginBottom: '8px' }}>
                    {transfer.deliveryType === 'Express' ? (
                      <span className="badge badge-error">⚡ Экспресс</span>
                    ) : transfer.deliveryType === 'Today' ? (
                      <span className="badge badge-warning">🕒 Сегодня</span>
                    ) : transfer.deliveryType === 'Tomorrow' ? (
                      <span className="badge badge-warning" style={{ background: 'rgba(59, 130, 246, 0.1)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.2)' }}>📅 Завтра</span>
                    ) : (
                      <span className="badge" style={{ background: 'rgba(255, 255, 255, 0.05)', color: 'var(--text-muted)', border: '1px solid rgba(255, 255, 255, 0.08)' }}>🚚 {transfer.deliveryType}</span>
                    )}
                  </div>

                  <p style={{ fontSize: '13px', color: '#fff' }}>
                    <strong style={{ color: 'var(--text-muted)' }}>Адрес:</strong> {transfer.address}
                  </p>
                </div>

                <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                  {transfer.waybillUrl ? (
                    <a
                      href={transfer.waybillUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="btn btn-primary"
                      style={{ padding: '10px 16px', fontSize: '13px', textDecoration: 'none', background: 'linear-gradient(135deg, #3b82f6, #2563eb)', boxShadow: '0 4px 12px rgba(59, 130, 246, 0.2)' }}
                    >
                      🖨️ Накладная ({transfer.waybillNumber || 'Открыть'})
                    </a>
                  ) : (
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Накладная отсутствует</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
