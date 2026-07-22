import React from 'react';

export interface CompetitorItem {
  name: string;
  price: number;
  rank: number;
  diff?: number;
  is_self?: boolean;
}

export interface RadarEvent {
  timestamp: string;
  competitor: string;
  old_price: number;
  new_price: number;
  our_reaction_price: number;
  status: string;
  saved_margin: number;
  message: string;
}

export interface PriceRadarProps {
  topProduct?: {
    name: string;
    our_price: number;
    our_rank: number;
    competitors: CompetitorItem[];
  };
  events?: RadarEvent[];
}

export const PriceRadarWidget: React.FC<PriceRadarProps> = ({ topProduct, events = [] }) => {
  const defaultProduct = topProduct || {
    name: "Смартфон Apple iPhone 15 128GB Black",
    our_price: 389990,
    our_rank: 1,
    competitors: [
      { name: "Мой Магазин (Вы)", price: 389990, rank: 1, is_self: true },
      { name: "iSpace Online", price: 390000, rank: 2, diff: 10, is_self: false },
      { name: "Sulpak Digital", price: 392500, rank: 3, diff: 2510, "is_self": false },
      { name: "Technodom Kaspi", price: 395000, rank: 4, diff: 5010, "is_self": false },
    ]
  };

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px', position: 'relative' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '24px' }}>⚔️</span>
          <div>
            <h2 style={{ fontSize: '17px', fontWeight: 700, letterSpacing: '-0.3px' }}>
              Live-Монитор Демпинг-Войн
            </h2>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Real-Time Price Radar & Sniper Engine
            </p>
          </div>
        </div>
        <div className="badge badge-success" style={{ padding: '6px 12px', gap: '6px' }}>
          <span className="glow-dot glow-dot-success"></span>
          <span>Радар активен</span>
        </div>
      </div>

      {/* 🎯 Position Track Visual Scale */}
      <div style={{ background: 'rgba(0, 0, 0, 0.35)', border: '1px solid rgba(255, 255, 255, 0.06)', borderRadius: '14px', padding: '18px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)' }}>
            Топ-Товар войн: <strong style={{ color: '#fff' }}>{defaultProduct.name}</strong>
          </span>
          <span className="badge" style={{ background: 'rgba(16, 185, 129, 0.15)', color: '#34d399', border: '1px solid rgba(16, 185, 129, 0.3)' }}>
            🏆 1-е место на Kaspi
          </span>
        </div>

        {/* Visual Track */}
        <div style={{ display: 'flex', gap: '12px', overflowX: 'auto', paddingBottom: '8px' }}>
          {defaultProduct.competitors.map((comp, idx) => (
            <div 
              key={idx}
              style={{
                flex: '1 0 140px',
                padding: '12px 14px',
                borderRadius: '12px',
                background: comp.is_self 
                  ? 'linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(0, 210, 255, 0.1))' 
                  : 'rgba(255, 255, 255, 0.03)',
                border: comp.is_self 
                  ? '1px solid rgba(16, 185, 129, 0.5)' 
                  : '1px solid rgba(255, 255, 255, 0.06)',
                boxShadow: comp.is_self ? '0 0 16px rgba(16, 185, 129, 0.2)' : 'none',
                position: 'relative'
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <span className="badge" style={{ 
                  background: comp.is_self ? '#10b981' : 'rgba(255,255,255,0.1)', 
                  color: comp.is_self ? '#000' : '#fff',
                  fontSize: '10px',
                  fontWeight: 800
                }}>
                  #{comp.rank} {comp.is_self && 'ВЫ'}
                </span>
                {comp.diff !== undefined && (
                  <span style={{ fontSize: '10px', color: 'var(--warning)', fontWeight: 600 }}>
                    +{comp.diff} ₸
                  </span>
                )}
              </div>

              <div style={{ fontSize: '12px', fontWeight: 600, color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {comp.name}
              </div>

              <div style={{ fontSize: '15px', fontWeight: 700, color: comp.is_self ? '#34d399' : '#e5e7eb', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>
                {comp.price.toLocaleString('ru-RU')} ₸
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 🎯 Sniper Feed (Terminal Style Log) */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)' }}>
            🎯 Лог Снайпера (Авто-Перерасчет)
          </span>
          <span style={{ fontSize: '11px', color: 'var(--secondary)', fontFamily: 'var(--font-mono)' }}>
            Спасенная маржа: +184 500 ₸
          </span>
        </div>

        <div className="console-box" style={{ height: '150px', background: 'rgba(5, 7, 13, 0.8)' }}>
          {events.length > 0 ? (
            events.map((evt, index) => (
              <div key={index} className="console-line" style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                <span className="console-timestamp">[{evt.timestamp}]</span>
                <span style={{ color: '#34d399', fontWeight: 600 }}>🎯</span>
                <span style={{ color: '#e5e7eb', flex: 1 }}>{evt.message}</span>
                <span className="badge badge-success" style={{ fontSize: '10px', padding: '2px 6px' }}>
                  +{evt.saved_margin} ₸
                </span>
              </div>
            ))
          ) : (
            <>
              <div className="console-line" style={{ color: '#34d399' }}>
                <span className="console-timestamp">[15:32:01]</span>
                <span>🎯 Конкурент "Shop_Kaspi_Master" сбросил цену до 14 480 ₸</span>
              </div>
              <div className="console-line" style={{ color: '#00d2ff' }}>
                <span className="console-timestamp">[15:32:02]</span>
                <span>🧠 Воркер-Мозг пересчитал цену: 14 478 ₸ (Защищенная маржа: +1 478 ₸)</span>
              </div>
              <div className="console-line" style={{ color: '#34d399' }}>
                <span className="console-timestamp">[15:32:04]</span>
                <span>✅ Цена 14 478 ₸ успешно обновлена на Kaspi</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
