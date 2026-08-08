import React from 'react';

interface SavedMarginCardProps {
  totalSavedMargin: number;
}

export const SavedMarginCard: React.FC<SavedMarginCardProps> = ({ totalSavedMargin }) => {
  const formatted = new Intl.NumberFormat('ru-RU').format(Math.round(totalSavedMargin));

  return (
    <div
      className="glass-card"
      style={{
        padding: '30px 34px',
        position: 'relative',
        overflow: 'hidden',
        backgroundImage:
          'radial-gradient(420px 260px at 85% 0%, rgba(255,255,255,.22), transparent 70%), linear-gradient(150deg, #2c4ef5, #16246b)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
      }}
    >
      <p style={{ fontSize: '11.5px', letterSpacing: '0.04em', textTransform: 'uppercase', color: 'rgba(255,255,255,.85)', margin: '0 0 10px', fontWeight: 700, position: 'relative', zIndex: 1 }}>
        Победа дня
      </p>
      <p style={{ fontSize: '38px', fontWeight: 800, color: '#fff', margin: '0 0 6px', letterSpacing: '-0.03em', position: 'relative', zIndex: 1 }}>
        +{formatted} ₸
      </p>
      <p style={{ fontSize: '12.5px', color: 'rgba(255,255,255,.85)', margin: 0, position: 'relative', zIndex: 1 }}>
        спасено от лишнего демпинга ниже минимальной цены
      </p>
    </div>
  );
};
