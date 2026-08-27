import React from 'react';
import { Swords, Trophy, Target } from 'lucide-react';

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
  const hasProduct = !!topProduct && topProduct.competitors?.length > 0;
  const savedMarginFromEvents = events.reduce((sum, evt) => sum + (evt.saved_margin || 0), 0);

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px', position: 'relative' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Swords size={24} />
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
        {!hasProduct ? (
          <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            Нет активных товаров в демпинге — включите демпинг хотя бы для одного SKU, чтобы увидеть реальную позицию на рынке
          </div>
        ) : (
        <>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)' }}>
            Топ-Товар войн: <strong style={{ color: '#fff' }}>{topProduct!.name}</strong>
          </span>
          <span className="bs-tag" style={{ background: 'linear-gradient(100deg, var(--primary), var(--secondary))', color: '#fff', boxShadow: '0 4px 12px var(--primary-glow)', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            <Trophy size={14} /> {topProduct!.our_rank}-е место на Kaspi
          </span>
        </div>

        {/* Visual Track */}
        <div style={{ display: 'flex', gap: '12px', overflowX: 'auto', paddingBottom: '8px' }}>
          {topProduct!.competitors.map((comp, idx) => (
            <div 
              key={idx}
              style={{
                flex: '1 0 140px',
                padding: '12px 14px',
                borderRadius: '12px',
                background: comp.is_self
                  ? 'linear-gradient(150deg, #2c4ef5, #16246b)'
                  : 'rgba(255, 255, 255, 0.03)',
                border: comp.is_self
                  ? '1px solid rgba(255, 255, 255, 0.2)'
                  : '1px solid rgba(255, 255, 255, 0.06)',
                boxShadow: comp.is_self ? '0 8px 20px var(--primary-glow)' : 'none',
                position: 'relative'
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <span className="badge" style={{
                  background: comp.is_self ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.1)',
                  color: '#fff',
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

              <div style={{ fontSize: '15px', fontWeight: 700, color: comp.is_self ? '#fff' : '#e5e7eb', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>
                {comp.price.toLocaleString('ru-RU')} ₸
              </div>
            </div>
          ))}
        </div>
        </>
        )}
      </div>

      {/* 🎯 Sniper Feed (Terminal Style Log) */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            <Target size={13} /> Лог Снайпера (Авто-Перерасчет)
          </span>
          {events.length > 0 && (
            <span style={{ fontSize: '11px', color: 'var(--secondary)', fontFamily: 'var(--font-mono)' }}>
              За последние события: +{savedMarginFromEvents.toLocaleString('ru-RU')} ₸
            </span>
          )}
        </div>

        <div className="console-box" style={{ height: '150px', background: 'rgba(5, 7, 13, 0.8)' }}>
          {events.length > 0 ? (
            events.map((evt, index) => (
              <div key={index} className="console-line" style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                <span className="console-timestamp">[{evt.timestamp}]</span>
                <span style={{ color: '#34d399', fontWeight: 600, display: 'inline-flex', alignItems: 'center' }}><Target size={12} /></span>
                <span style={{ color: '#e5e7eb', flex: 1 }}>{evt.message}</span>
                <span className="badge badge-success" style={{ fontSize: '10px', padding: '2px 6px' }}>
                  +{evt.saved_margin} ₸
                </span>
              </div>
            ))
          ) : (
            <div className="console-line" style={{ color: 'var(--text-muted)' }}>
              <span>Демпинг ещё не сработал ни разу — события появятся здесь после первого реального изменения цены</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
