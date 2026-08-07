import React, { useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, Package } from 'lucide-react';
import type { SalesSummary } from '../hooks/useSalesStats';

const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n));

type SortKey = 'revenue_desc' | 'revenue_asc' | 'qty_desc' | 'qty_asc';

const SORT_OPTIONS: [SortKey, string][] = [
  ['revenue_desc', 'Больше выручки'],
  ['revenue_asc', 'Меньше выручки'],
  ['qty_desc', 'Больше продаж'],
  ['qty_asc', 'Меньше продаж'],
];

export const SalesStatsWidget: React.FC<{ data: SalesSummary }> = ({ data }) => {
  const [sort, setSort] = useState<SortKey>('revenue_desc');
  const hasData = data.daily.length > 0;
  const maxRevenue = Math.max(1, ...data.daily.map((d) => d.revenue));

  const sortedSkus = useMemo(() => {
    const copy = [...data.top_skus];
    switch (sort) {
      case 'revenue_asc':
        return copy.sort((a, b) => a.revenue - b.revenue);
      case 'qty_desc':
        return copy.sort((a, b) => b.quantity - a.quantity);
      case 'qty_asc':
        return copy.sort((a, b) => a.quantity - b.quantity);
      default:
        return copy.sort((a, b) => b.revenue - a.revenue);
    }
  }, [data.top_skus, sort]);

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '24px' }}>💰</span>
          <div>
            <h2 style={{ fontSize: '17px', fontWeight: 700, letterSpacing: '-0.3px' }}>
              Реальные продажи
            </h2>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              За последние 30 дней, из фактической истории завершённых заказов Kaspi
            </p>
          </div>
        </div>
      </div>

      {!hasData ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Данных пока нет — фоновый воркер синхронизирует историю заказов каждые 30 минут, первые цифры появятся после первого прохода
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '16px' }}>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>ВЫРУЧКА</div>
              <div style={{ fontSize: '22px', fontWeight: 800, color: '#10b981' }}>{fmt(data.total_revenue)} ₸</div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>ЗАКАЗОВ</div>
              <div style={{ fontSize: '22px', fontWeight: 800, color: '#fff' }}>{data.total_orders}</div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>СРЕДНИЙ ЧЕК</div>
              <div style={{ fontSize: '22px', fontWeight: 800, color: '#fff' }}>{fmt(data.avg_order_value)} ₸</div>
            </div>
          </div>

          {/* Daily revenue bars */}
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: '4px', height: '80px', overflowX: 'auto', paddingBottom: '4px' }}>
            {data.daily.map((d) => (
              <div
                key={d.date}
                title={`${d.date}: ${fmt(d.revenue)} ₸, ${d.orders} заказ(ов)`}
                style={{
                  flex: '0 0 10px',
                  height: `${Math.max(4, (d.revenue / maxRevenue) * 80)}px`,
                  background: 'linear-gradient(180deg, #10b981, rgba(16,185,129,0.3))',
                  borderRadius: '3px 3px 0 0',
                }}
              />
            ))}
          </div>

          {data.top_skus.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Package size={13} /> Товары ({data.top_skus.length})
                </span>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {SORT_OPTIONS.map(([key, label]) => (
                    <button
                      key={key}
                      className="btn btn-secondary"
                      style={{
                        padding: '4px 10px',
                        fontSize: '11px',
                        ...(sort === key ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 } : {}),
                      }}
                      onClick={() => setSort(key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ maxHeight: '260px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {sortedSkus.map((sku) => (
                  <div key={sku.sku} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', padding: '6px 4px', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                    <span style={{ color: '#e5e7eb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '55%' }}>
                      {sku.name}
                    </span>
                    <span style={{ display: 'flex', gap: '10px', color: 'var(--text-muted)' }}>
                      <span>{sku.quantity} шт</span>
                      <span style={{ color: sku.revenue > 0 ? '#10b981' : 'var(--text-muted)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '2px' }}>
                        {sku.revenue > 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />} {fmt(sku.revenue)} ₸
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};
