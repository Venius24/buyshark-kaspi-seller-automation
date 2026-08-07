import React, { useMemo, useState } from 'react';
import { Clock } from 'lucide-react';

const DAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const CELL_COLORS = [
  'rgba(255,255,255,0.05)',
  'rgba(0,210,255,0.2)',
  'rgba(0,210,255,0.4)',
  'rgba(0,210,255,0.65)',
  'rgba(0,210,255,1)',
];

const intensity = (count: number, max: number) => {
  if (count <= 0 || max <= 0) return 0;
  const ratio = count / max;
  if (ratio > 0.75) return 4;
  if (ratio > 0.5) return 3;
  if (ratio > 0.2) return 2;
  return 1;
};

export const HourlyHeatmap: React.FC<{ grid: number[][] }> = ({ grid }) => {
  const [hovered, setHovered] = useState<{ day: number; hour: number; count: number } | null>(null);
  const total = useMemo(() => grid.reduce((sum, row) => sum + row.reduce((s, v) => s + v, 0), 0), [grid]);
  const maxCount = useMemo(() => Math.max(1, ...grid.flat()), [grid]);

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Clock size={18} color="#00d2ff" />
          <div>
            <h2 style={{ fontSize: '16px', fontWeight: 700 }}>Продажи по часам и дням недели</h2>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>За последние 90 дней, из реальной истории заказов</p>
          </div>
        </div>
        {hovered && (
          <div style={{ fontSize: '12px', color: '#e5e7eb', background: 'rgba(0,0,0,0.3)', padding: '6px 12px', borderRadius: '8px' }}>
            <b>{DAY_LABELS[hovered.day]}, {hovered.hour}:00</b> — {hovered.count} заказ(ов)
          </div>
        )}
      </div>

      {total === 0 ? (
        <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Заполнится по мере накопления истории заказов
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: '620px' }}>
            <div style={{ display: 'flex', gap: '3px', paddingLeft: '28px' }}>
              {Array.from({ length: 24 }, (_, h) => (
                <div key={h} style={{ width: '20px', fontSize: '8px', color: 'var(--text-muted)', textAlign: 'center', visibility: h % 3 === 0 ? 'visible' : 'hidden' }}>
                  {h}
                </div>
              ))}
            </div>
            {grid.map((row, day) => (
              <div key={day} style={{ display: 'flex', gap: '3px', alignItems: 'center' }}>
                <div style={{ width: '24px', fontSize: '10px', color: 'var(--text-muted)' }}>{DAY_LABELS[day]}</div>
                {row.map((count, hour) => (
                  <div
                    key={hour}
                    onMouseEnter={() => setHovered({ day, hour, count })}
                    onMouseLeave={() => setHovered(null)}
                    style={{
                      width: '20px',
                      height: '20px',
                      borderRadius: '4px',
                      background: CELL_COLORS[intensity(count, maxCount)],
                      cursor: 'pointer',
                      outline: hovered?.day === day && hovered?.hour === hour ? '1.5px solid #00d2ff' : 'none',
                    }}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
