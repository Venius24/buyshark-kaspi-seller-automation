import React from 'react';
import { DollarSign, ShieldCheck } from 'lucide-react';

interface SavedMarginCardProps {
  totalSavedMargin: number;
}

export const SavedMarginCard: React.FC<SavedMarginCardProps> = ({ totalSavedMargin }) => {
  const formatted = new Intl.NumberFormat('ru-RU').format(Math.round(totalSavedMargin));

  return (
    <div className="glass-panel" style={{ padding: '20px', borderRadius: '14px', position: 'relative', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: '13px', fontWeight: 600 }}>
          <DollarSign size={16} color="#10b981" />
          <span>СОХРАНЕННАЯ МАРЖА</span>
        </div>
        <span className="badge" style={{ background: 'rgba(16, 185, 129, 0.12)', color: '#10b981', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '4px' }}>
          <ShieldCheck size={12} /> Margin Safe
        </span>
      </div>

      <div style={{ marginTop: '10px' }}>
        <div style={{ fontSize: '32px', fontWeight: 800, color: '#10b981', fontFamily: 'system-ui, sans-serif' }}>
          +{formatted} ₸
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
          Спасенный доход от лишнего демпинга ниже порога min_price
        </div>
      </div>
    </div>
  );
};
