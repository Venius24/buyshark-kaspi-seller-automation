import React from 'react';
import { Trophy } from 'lucide-react';
import type { CompetitorLeaderboardEntry } from '../hooks/useInsightsStats';

export const CompetitorLeaderboard: React.FC<{ data: CompetitorLeaderboardEntry[] }> = ({ data }) => {
  const hasData = data.length > 0;
  const maxCount = Math.max(1, ...data.map((d) => d.count));

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <Trophy size={18} color="#f59e0b" />
        <div>
          <h2 style={{ fontSize: '16px', fontWeight: 700 }}>Лидерборд конкурентов</h2>
          <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Кто чаще всего обходит нас в демпинге, за 30 дней</p>
        </div>
      </div>

      {!hasData ? (
        <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Копится с момента первого цикла демпинга — пока никто не обходил
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {data.map((entry, idx) => (
            <div key={entry.name} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span style={{ width: '18px', fontSize: '11px', color: 'var(--text-muted)', fontWeight: 700 }}>#{idx + 1}</span>
              <span style={{ flex: '0 0 40%', fontSize: '12px', color: '#e5e7eb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {entry.name}
              </span>
              <div style={{ flex: 1, height: '8px', borderRadius: '4px', background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                <div
                  style={{
                    width: `${(entry.count / maxCount) * 100}%`,
                    height: '100%',
                    background: idx === 0 ? '#ef4444' : 'linear-gradient(90deg, #f59e0b, rgba(245,158,11,0.4))',
                    borderRadius: '4px',
                  }}
                />
              </div>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', width: '24px', textAlign: 'right' }}>
                {entry.count}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
