import React from 'react';
import { Trophy, TrendingUp } from 'lucide-react';

interface PriceLeadGaugeProps {
  percentage: number;
  winningCount: number;
  totalScanned: number;
}

export const PriceLeadGauge: React.FC<PriceLeadGaugeProps> = ({ percentage, winningCount, totalScanned }) => {
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (percentage / 100) * circumference;

  const getColor = (pct: number) => {
    if (pct >= 80) return '#10b981'; // Green
    if (pct >= 50) return '#3b82f6'; // Blue
    if (pct >= 25) return '#f59e0b'; // Amber
    return '#ef4444'; // Red
  };

  const strokeColor = getColor(percentage);

  return (
    <div className="glass-panel" style={{ padding: '20px', borderRadius: '14px', position: 'relative', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: '13px', fontWeight: 600 }}>
          <Trophy size={16} color="#f59e0b" />
          <span>ДОЛЯ ЛУЧШЕЙ ЦЕНЫ</span>
        </div>
        <span className="badge badge-success" style={{ fontSize: '11px', display: 'flex', alignItems: 'center', gap: '4px' }}>
          <TrendingUp size={12} /> Live
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '10px' }}>
        <div>
          <div style={{ fontSize: '32px', fontWeight: 800, color: '#fff', fontFamily: 'system-ui, sans-serif' }}>
            {percentage}%
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {winningCount} из {totalScanned} товаров на 1-м месте
          </div>
        </div>

        {/* Circular Progress Ring */}
        <div style={{ position: 'relative', width: '96px', height: '96px' }}>
          <svg width="96" height="96" viewBox="0 0 100 100" style={{ transform: 'rotate(-90deg)' }}>
            <circle
              cx="50"
              cy="50"
              r={radius}
              stroke="rgba(255, 255, 255, 0.08)"
              strokeWidth="10"
              fill="transparent"
            />
            <circle
              cx="50"
              cy="50"
              r={radius}
              stroke={strokeColor}
              strokeWidth="10"
              fill="transparent"
              strokeDasharray={circumference}
              strokeDashoffset={strokeDashoffset}
              strokeLinecap="round"
              style={{ transition: 'stroke-dashoffset 0.8s ease-in-out, stroke 0.5s ease' }}
            />
          </svg>
          <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            alignContent: 'center',
            textAlign: 'center',
            fontSize: '14px',
            fontWeight: 700,
            color: strokeColor
          }}>
            <span style={{ margin: 'auto' }}>#1</span>
          </div>
        </div>
      </div>
    </div>
  );
};
