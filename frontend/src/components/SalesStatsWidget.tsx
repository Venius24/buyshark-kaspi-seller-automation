import React from 'react';
import { TrendingUp, Package } from 'lucide-react';
import type { SalesSummary } from '../hooks/useSalesStats';

const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n));

export const SalesStatsWidget: React.FC<{ data: SalesSummary }> = ({ data }) => {
  const hasData = data.daily.length > 0;
  const maxRevenue = Math.max(1, ...data.daily.map((d) => d.revenue));

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
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Package size={13} /> Топ товаров по выручке
              </span>
              {data.top_skus.slice(0, 5).map((sku) => (
                <div key={sku.sku} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <span style={{ color: '#e5e7eb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '60%' }}>
                    {sku.name}
                  </span>
                  <span style={{ display: 'flex', gap: '10px', color: 'var(--text-muted)' }}>
                    <span>{sku.quantity} шт</span>
                    <span style={{ color: '#10b981', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '2px' }}>
                      <TrendingUp size={11} /> {fmt(sku.revenue)} ₸
                    </span>
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};
