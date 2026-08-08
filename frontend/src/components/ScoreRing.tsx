import React, { useState } from 'react';
import { Flame, ChevronDown, Target, TrendingUp, Activity } from 'lucide-react';
import type { ScoreStats } from '../hooks/useScoreStats';

const getColor = (pct: number) => {
  if (pct >= 80) return '#35c9a9';
  if (pct >= 50) return '#2c4ef5';
  if (pct >= 25) return '#e0a33e';
  return '#e2545f';
};

const getLabel = (pct: number) => {
  if (pct >= 80) return 'Отличная форма';
  if (pct >= 50) return 'Стабильно';
  if (pct >= 25) return 'Требует внимания';
  return 'Критично';
};

const Ring: React.FC<{ pct: number; size: number; stroke: number; color: string }> = ({ pct, size, stroke, color }) => {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (Math.max(0, Math.min(100, pct)) / 100) * circumference;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: 'rotate(-90deg)' }}>
      <circle cx={size / 2} cy={size / 2} r={radius} stroke="rgba(255,255,255,0.07)" strokeWidth={stroke} fill="transparent" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        stroke={color}
        strokeWidth={stroke}
        fill="transparent"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        strokeLinecap="round"
        style={{ transition: 'stroke-dashoffset 1s cubic-bezier(0.16, 1, 0.3, 1), stroke 0.5s ease' }}
      />
    </svg>
  );
};

const SubMetric: React.FC<{ icon: React.ReactNode; label: string; value: number; hint: string }> = ({ icon, label, value, hint }) => {
  const color = getColor(value);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '14px', padding: '10px 0' }}>
      <div style={{ position: 'relative', width: '52px', height: '52px', flexShrink: 0 }}>
        <Ring pct={value} size={52} stroke={6} color={color} />
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', fontWeight: 700, color }}>
          {value}
        </div>
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 600, color: '#fff' }}>
          {icon} {label}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>{hint}</div>
      </div>
    </div>
  );
};

export const ScoreRing: React.FC<{ data: ScoreStats }> = ({ data }) => {
  const [expanded, setExpanded] = useState(false);
  const composite = data.score.composite;
  const color = getColor(composite);
  const { price_leadership, revenue_trend, activity_consistency } = data.score.components;

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: expanded ? '20px' : '0px', height: '100%' }}>
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: '20px', flexWrap: 'wrap' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <div
            className={composite >= 80 ? 'score-ring-glow' : undefined}
            style={{ position: 'relative', width: '96px', height: '96px', flexShrink: 0, borderRadius: '50%' }}
          >
            <Ring pct={composite} size={96} stroke={9} color={color} />
            <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontSize: '26px', fontWeight: 800, color: '#fff', lineHeight: 1 }}>{composite}</span>
              <span style={{ fontSize: '9px', color: 'var(--text-muted)', fontWeight: 600, letterSpacing: '0.5px' }}>SCORE</span>
            </div>
          </div>
          <div>
            <span className="bs-tag" style={{ background: 'rgba(255,255,255,0.06)', color: '#98a2b8', marginBottom: '10px', display: 'inline-block' }}>
              Общее здоровье магазина
            </span>
            <div style={{ fontSize: '20px', fontWeight: 700, color, marginTop: '4px' }}>{getLabel(composite)}</div>
            <div style={{ display: 'flex', gap: '14px', marginTop: '8px', flexWrap: 'wrap' }}>
              {data.streaks.price_leadership_days > 0 && (
                <span className="bs-tag" style={{ background: 'var(--primary-glow)', color: '#8fa5ff', display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <Flame size={13} /> {data.streaks.price_leadership_days} дн. лучшей цены подряд
                </span>
              )}
              {data.streaks.sales_days > 0 && (
                <span className="bs-tag" style={{ background: 'var(--success-glow)', color: 'var(--success)', display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <Flame size={13} /> {data.streaks.sales_days} дн. продаж подряд
                </span>
              )}
            </div>
          </div>
        </div>
        <ChevronDown
          size={20}
          color="var(--text-muted)"
          style={{ transition: 'transform 0.3s cubic-bezier(0.16, 1, 0.3, 1)', transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)' }}
        />
      </button>

      <div style={{ display: 'grid', gridTemplateRows: expanded ? '1fr' : '0fr', transition: 'grid-template-rows 0.35s cubic-bezier(0.16, 1, 0.3, 1)' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '14px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '4px 24px' }}>
            <SubMetric icon={<Target size={13} color="var(--primary)" />} label="Ценовое лидерство" value={price_leadership} hint="Доля товаров с лучшей ценой на рынке" />
            <SubMetric icon={<TrendingUp size={13} color="var(--success)" />} label="Динамика продаж" value={revenue_trend} hint="Выручка за 7 дней к предыдущим 7 дням" />
            <SubMetric icon={<Activity size={13} color="var(--warning)" />} label="Стабильность потока" value={activity_consistency} hint="Доля дней с заказами за 30 дней" />
          </div>
          {data.history_points < 3 && (
            <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '10px' }}>
              История только начала копиться — точность score вырастет в течение первых дней работы.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};
