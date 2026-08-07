import React from 'react';
import { ShieldCheck } from 'lucide-react';
import type { CatalogCoverage } from '../hooks/useInsightsStats';

const colorFor = (pct: number) => {
  if (pct >= 70) return '#10b981';
  if (pct >= 35) return '#f59e0b';
  return '#ef4444';
};

export const CatalogCoverageGauge: React.FC<{ data: CatalogCoverage }> = ({ data }) => {
  const color = colorFor(data.coverage_pct);
  const hasProducts = data.total_products > 0;

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <ShieldCheck size={18} color={color} />
        <div>
          <h2 style={{ fontSize: '16px', fontWeight: 700 }}>Покрытие каталога демпингом</h2>
          <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            {hasProducts ? `${data.dumping_enabled} из ${data.total_products} товаров` : 'Каталог пуст'}
          </p>
        </div>
      </div>

      <div style={{ height: '14px', borderRadius: '7px', background: 'rgba(255,255,255,0.06)', overflow: 'hidden', position: 'relative' }}>
        <div
          style={{
            width: `${data.coverage_pct}%`,
            height: '100%',
            background: `linear-gradient(90deg, ${color}, ${color}aa)`,
            borderRadius: '7px',
            transition: 'width 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        />
      </div>
      <div style={{ fontSize: '26px', fontWeight: 800, color }}>{data.coverage_pct}%</div>
    </div>
  );
};
