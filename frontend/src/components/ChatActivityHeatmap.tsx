import React, { useMemo, useState } from 'react';
import { MessageCircle } from 'lucide-react';
import type { ChatActivityPoint } from '../hooks/useInsightsStats';

const DAY_MS = 24 * 60 * 60 * 1000;

function buildWeeks(daily: ChatActivityPoint[], weeksBack: number) {
  const byDate = new Map(daily.map((d) => [d.date, d]));
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);

  const start = new Date(today.getTime() - weeksBack * 7 * DAY_MS);
  const startDow = start.getUTCDay();
  start.setTime(start.getTime() - startDow * DAY_MS);

  const weeks: { date: string; count: number }[][] = [];
  let cursor = new Date(start);
  while (cursor.getTime() <= today.getTime()) {
    const week: { date: string; count: number }[] = [];
    for (let d = 0; d < 7; d++) {
      const key = cursor.toISOString().slice(0, 10);
      const point = byDate.get(key);
      week.push({ date: key, count: point?.count ?? 0 });
      cursor = new Date(cursor.getTime() + DAY_MS);
    }
    weeks.push(week);
  }
  return weeks;
}

const intensity = (count: number, max: number) => {
  if (count <= 0 || max <= 0) return 0;
  const ratio = count / max;
  if (ratio > 0.75) return 4;
  if (ratio > 0.5) return 3;
  if (ratio > 0.2) return 2;
  return 1;
};

const CELL_COLORS = [
  'rgba(255,255,255,0.05)',
  'rgba(168,85,247,0.25)',
  'rgba(168,85,247,0.48)',
  'rgba(168,85,247,0.72)',
  'rgba(168,85,247,1)',
];

export const ChatActivityHeatmap: React.FC<{ data: ChatActivityPoint[] }> = ({ data }) => {
  const [hovered, setHovered] = useState<{ date: string; count: number } | null>(null);
  const weeksBack = 24;
  const weeks = useMemo(() => buildWeeks(data, weeksBack), [data]);
  const maxCount = useMemo(() => Math.max(1, ...data.map((d) => d.count)), [data]);
  const hasData = data.length > 0;

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <MessageCircle size={18} color="#a855f7" />
          <div>
            <h2 style={{ fontSize: '16px', fontWeight: 700 }}>Активность авто-чата</h2>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Реальные авто-ответы ИИ клиентам, по дням</p>
          </div>
        </div>
        {hovered && (
          <div style={{ fontSize: '12px', color: '#e5e7eb', background: 'rgba(0,0,0,0.3)', padding: '6px 12px', borderRadius: '8px' }}>
            <b>{hovered.date}</b> — {hovered.count} ответ(ов)
          </div>
        )}
      </div>

      {!hasData ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Копится с момента деплоя — появится после первых авто-ответов чата
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ display: 'flex', gap: '3px' }}>
            {weeks.map((week, wi) => (
              <div key={wi} style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                {week.map((day) => {
                  const level = intensity(day.count, maxCount);
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
                        outline: hovered?.date === day.date ? '1.5px solid #a855f7' : 'none',
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
