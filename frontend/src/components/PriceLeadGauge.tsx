import React from 'react';

interface PriceLeadGaugeProps {
  percentage: number;
  winningCount: number;
  totalScanned: number;
}

export const PriceLeadGauge: React.FC<PriceLeadGaugeProps> = ({ percentage, winningCount, totalScanned }) => {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (percentage / 100) * circumference;
  const isWinning = percentage >= 80;

  return (
    <div
      className="glass-card"
      style={{
        padding: '28px',
        display: 'flex',
        flexDirection: 'column',
        ...(isWinning
          ? {
              backgroundImage:
                'radial-gradient(420px 260px at 85% 0%, rgba(255,255,255,.18), transparent 70%), linear-gradient(150deg, #35c9a9, #14513f)',
            }
          : {}),
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '14px', marginBottom: '18px' }}>
        <p className="bs-metric-label" style={{ margin: 0, color: isWinning ? 'rgba(255,255,255,.85)' : 'var(--text-muted)' }}>Доля лучшей цены</p>
        <span className="bs-tag" style={{ background: isWinning ? 'rgba(255,255,255,.2)' : 'var(--secondary-glow)', color: isWinning ? '#fff' : 'var(--secondary)', flexShrink: 0 }}>Live</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '18px' }}>
        <div style={{ position: 'relative', width: '78px', height: '78px', flex: '0 0 auto' }}>
          <svg width="78" height="78" viewBox="0 0 78 78" style={{ transform: 'rotate(-90deg)' }}>
            <circle cx="39" cy="39" r={radius} stroke="rgba(255,255,255,0.15)" strokeWidth="10" fill="transparent" />
            <circle
              cx="39"
              cy="39"
              r={radius}
              stroke={isWinning ? '#fff' : 'url(#priceLeadGradient)'}
              strokeWidth="10"
              fill="transparent"
              strokeDasharray={circumference}
              strokeDashoffset={strokeDashoffset}
              strokeLinecap="round"
              style={{ transition: 'stroke-dashoffset 0.8s ease-in-out' }}
            />
            <defs>
              <linearGradient id="priceLeadGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#2f8fd8" />
                <stop offset="100%" stopColor="#35c9a9" />
              </linearGradient>
            </defs>
          </svg>
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontSize: '17px', fontWeight: 800, color: '#fff', letterSpacing: '-0.02em' }}>{percentage}%</span>
          </div>
        </div>
        <p style={{ fontSize: '12.5px', color: isWinning ? 'rgba(255,255,255,.85)' : 'var(--text-muted)', margin: 0, flex: 1, minWidth: 0 }}>
          {winningCount} из {totalScanned} товаров держат 1-е место в выдаче
        </p>
      </div>
    </div>
  );
};
