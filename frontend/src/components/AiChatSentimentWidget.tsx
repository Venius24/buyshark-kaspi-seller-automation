import React from 'react';

export interface CategoryItem {
  name: string;
  percentage: number;
  color: string;
}

export interface HeatmapItem {
  hour: string;
  activity: number;
}

export interface AiChatSentimentProps {
  statusLabel?: string;
  categories?: CategoryItem[];
  hourlyHeatmap?: HeatmapItem[];
  pulseStatus?: {
    ai_worker_status: string;
    telegram_crm_status: string;
    response_avg_sec: number;
    equalizer_bars: number[];
  };
}

export const AiChatSentimentWidget: React.FC<AiChatSentimentProps> = ({
  statusLabel = "🔮 Оболочка будущего модуля ИИ-Чатов & CRM (Интеграция подготовлена)",
  categories = [
    { name: "Логистика & Доставка", percentage: 40, color: "#10B981" },
    { name: "Возвраты & Гарантия (CRM)", percentage: 30, color: "#F43F5E" },
    { name: "Вопросы о товарах (DeepSeek AI)", percentage: 30, color: "#3B82F6" }
  ],
  hourlyHeatmap = Array.from({ length: 24 }, (_, i) => ({
    hour: `${i.toString().padStart(2, '0')}:00`,
    activity: i >= 9 && i <= 21 ? Math.floor(40 + Math.random() * 55) : Math.floor(5 + Math.random() * 20)
  })),
  pulseStatus = {
    ai_worker_status: "ONLINE",
    telegram_crm_status: "READY",
    response_avg_sec: 1.4,
    equalizer_bars: [65, 80, 45, 90, 75, 60, 85, 95, 70, 50]
  }
}) => {
  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px', position: 'relative' }}>
      
      {/* Declarative Badge Overlay Banner */}
      <div style={{
        background: 'linear-gradient(90deg, rgba(139, 92, 246, 0.15), rgba(59, 130, 246, 0.15))',
        border: '1px solid rgba(139, 92, 246, 0.3)',
        borderRadius: '10px',
        padding: '8px 14px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        color: '#c084fc',
        fontSize: '12px',
        fontWeight: 600
      }}>
        <span>{statusLabel}</span>
        <span className="badge" style={{ background: 'rgba(139, 92, 246, 0.2)', color: '#e9d5ff' }}>
          UI Ready
        </span>
      </div>

      {/* Title & Store Pulse Equalizer */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '24px' }}>🧠</span>
          <div>
            <h2 style={{ fontSize: '17px', fontWeight: 700, letterSpacing: '-0.3px' }}>
              ИИ-Аналитика Чатов & Настроения
            </h2>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              DeepSeek AI + Telegram CRM Pulse Monitor
            </p>
          </div>
        </div>

        {/* ⚡ Live Equalizer / Store Pulse */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', background: 'rgba(0,0,0,0.3)', padding: '8px 14px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>Пульс Воркеров:</span>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: '3px', height: '18px' }}>
            {pulseStatus.equalizer_bars.map((barHeight, i) => (
              <div 
                key={i} 
                className="equalizer-bar"
                style={{
                  width: '3px',
                  height: `${barHeight}%`,
                  background: i % 2 === 0 ? 'var(--primary)' : 'var(--secondary)',
                  borderRadius: '2px',
                  animationDelay: `${i * 0.1}s`
                }}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Grid: Pie-Chart & Heatmap */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '20px' }}>
        
        {/* Category Breakdown (Pie-Chart style legend) */}
        <div style={{ background: 'rgba(0,0,0,0.22)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '12px', padding: '16px' }}>
          <h3 style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '14px' }}>
            Категории Обращений Покупателей
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {categories.map((cat, idx) => (
              <div key={idx} style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
                  <span style={{ color: '#fff', fontWeight: 500 }}>{cat.name}</span>
                  <span style={{ color: cat.color, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{cat.percentage}%</span>
                </div>
                <div style={{ width: '100%', height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '3px', overflow: 'hidden' }}>
                  <div style={{ width: `${cat.percentage}%`, height: '100%', background: cat.color, borderRadius: '3px', boxShadow: `0 0 8px ${cat.color}` }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Heatmap: 24-Hour Activity Grid */}
        <div style={{ background: 'rgba(0,0,0,0.22)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '12px', padding: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
            <h3 style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
              Heatmap Активности Покупателей (24h)
            </h3>
            <span style={{ fontSize: '10px', color: '#10B981', fontWeight: 700 }}>Пик: 14:00 - 18:00</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: '4px' }}>
            {hourlyHeatmap.map((item, idx) => {
              let opacity = item.activity / 100;
              if (opacity < 0.15) opacity = 0.15;
              return (
                <div
                  key={idx}
                  title={`${item.hour}: Активность ${item.activity}%`}
                  style={{
                    height: '24px',
                    borderRadius: '4px',
                    background: item.activity > 60 
                      ? `rgba(244, 63, 94, ${opacity})` 
                      : `rgba(0, 210, 255, ${opacity})`,
                    border: '1px solid rgba(255,255,255,0.05)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '9px',
                    color: '#fff',
                    fontFamily: 'var(--font-mono)'
                  }}
                >
                  {idx % 4 === 0 ? item.hour.split(':')[0] : ''}
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: 'var(--text-muted)', marginTop: '8px' }}>
            <span>00:00 (Низкая)</span>
            <span>12:00 (Высокая)</span>
            <span>23:00 (Средняя)</span>
          </div>
        </div>

      </div>
    </div>
  );
};
