import React, { useMemo, useState } from 'react';
import { CalendarDays } from 'lucide-react';
import type { SalesDailyPoint } from '../hooks/useSalesStats';

const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n));

const DAY_MS = 24 * 60 * 60 * 1000;

function buildWeeks(daily: SalesDailyPoint[], weeksBack: number) {
  const byDate = new Map(daily.map((d) => [d.date, d]));
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);

  // Начинаем сетку с ближайшего прошлого воскресенья, чтобы недели были ровными столбцами.
  const start = new Date(today.getTime() - weeksBack * 7 * DAY_MS);
  const startDow = start.getUTCDay();
  start.setTime(start.getTime() - startDow * DAY_MS);

  const weeks: { date: string; revenue: number; orders: number }[][] = [];
  let cursor = new Date(start);
  while (cursor.getTime() <= today.getTime()) {
    const week: { date: string; revenue: number; orders: number }[] = [];
    for (let d = 0; d < 7; d++) {
      const key = cursor.toISOString().slice(0, 10);
      const point = byDate.get(key);
      week.push({ date: key, revenue: point?.revenue ?? 0, orders: point?.orders ?? 0 });
      cursor = new Date(cursor.getTime() + DAY_MS);
    }
    weeks.push(week);
  }
  return weeks;
}

const intensity = (revenue: number, max: number) => {
  if (revenue <= 0 || max <= 0) return 0;
  const ratio = revenue / max;
  if (ratio > 0.75) return 4;
  if (ratio > 0.5) return 3;
  if (ratio > 0.2) return 2;
  return 1;
};

const CELL_COLORS = [
  'rgba(255,255,255,0.05)',
  'rgba(16,185,129,0.25)',
  'rgba(16,185,129,0.48)',
  'rgba(16,185,129,0.72)',
  'rgba(16,185,129,1)',
];

const WEEKDAY_LABELS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

export const RevenueHeatmap: React.FC<{ heatmap: SalesDailyPoint[] }> = ({ heatmap }) => {
  const [hovered, setHovered] = useState<{ date: string; revenue: number; orders: number } | null>(null);
  const weeksBack = 24; // ~полгода
  const weeks = useMemo(() => buildWeeks(heatmap, weeksBack), [heatmap]);
  const maxRevenue = useMemo(() => Math.max(1, ...heatmap.map((d) => d.revenue)), [heatmap]);
  const hasData = heatmap.length > 0;

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <CalendarDays size={18} color="#00d2ff" />
          <div>
            <h2 style={{ fontSize: '16px', fontWeight: 700 }}>Карта активности продаж</h2>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Яркость = выручка за день, наведите на клетку для деталей</p>
          </div>
        </div>
        {hovered && (
          <div style={{ fontSize: '12px', color: '#e5e7eb', background: 'rgba(0,0,0,0.3)', padding: '6px 12px', borderRadius: '8px' }}>
            <b>{hovered.date}</b> — {fmt(hovered.revenue)} ₸, {hovered.orders} заказ(ов)
          </div>
        )}
      </div>

      {!hasData ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Карта заполнится по мере накопления истории продаж
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ display: 'flex', gap: '3px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginRight: '4px', paddingTop: '0px' }}>
              {WEEKDAY_LABELS.map((label, i) => (
                <div key={label} style={{ height: '12px', fontSize: '9px', color: 'var(--text-muted)', lineHeight: '12px', visibility: i % 2 ? 'visible' : 'hidden' }}>
                  {label}
                </div>
              ))}
            </div>
            {weeks.map((week, wi) => (
              <div key={wi} style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                {week.map((day) => {
                  const level = intensity(day.revenue, maxRevenue);
                  const isFuture = new Date(day.date).getTime() > Date.now();
                  return (
                    <div
                      key={day.date}
                      onMouseEnter={() => !isFuture && setHovered(day)}
                      onMouseLeave={() => setHovered(null)}
                      style={{
                        width: '12px',
                        height: '12px',
                        borderRadius: '3px',
                        background: isFuture ? 'transparent' : CELL_COLORS[level],
                        cursor: isFuture ? 'default' : 'pointer',
                        transition: 'transform 0.15s ease, outline 0.15s ease',
                        outline: hovered?.date === day.date ? '1.5px solid #00d2ff' : 'none',
                      }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
