import React, { useState } from 'react';
import { Flame, ChevronDown, Target, TrendingUp, Activity, Star, RotateCcw, Clock, XCircle, MessageCircle } from 'lucide-react';
import type { ScoreStats } from '../hooks/useScoreStats';
import type { QualityMetricsStats } from '../hooks/useQualityMetrics';

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

// Kaspi сам определяет "хорошо"/"плохо" по СВОИМ порогам на СВОЕЙ шкале
// (0-5 для рейтинга, минуты для времени ответа, % для остального) — в отличие
// от getColor выше (наш composite score всегда 0-100), поэтому цвет считаем
// от goodValue/badValue, присланных Kaspi для конкретной метрики, а не от
// фиксированной 0-100 шкалы.
const getQualityColor = (
  value: number | null | undefined,
  goodValue: number | null | undefined,
  badValue: number | null | undefined,
  higherIsBetter: boolean
): string => {
  if (value == null || goodValue == null || badValue == null) return '#98a2b8';
  const isGood = higherIsBetter ? value >= goodValue : value <= goodValue;
  const isBad = higherIsBetter ? value <= badValue : value >= badValue;
  if (isGood) return '#35c9a9';
  if (isBad) return '#e2545f';
  return '#e0a33e';
};

const QualityMetricRow: React.FC<{ icon: React.ReactNode; label: string; value: string; color: string; hint: string }> = ({
  icon,
  label,
  value,
  color,
  hint,
}) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: '14px', padding: '10px 0' }}>
    <div
      style={{
        width: '52px',
        height: '52px',
        flexShrink: 0,
        borderRadius: '50%',
        background: `${color}22`,
        border: `2px solid ${color}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '12px',
        fontWeight: 700,
        color,
        textAlign: 'center',
        lineHeight: 1.1,
      }}
    >
      {value}
    </div>
    <div style={{ minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 600, color: '#fff' }}>
        {icon} {label}
      </div>
      <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>{hint}</div>
    </div>
  </div>
);

export const ScoreRing: React.FC<{ data: ScoreStats; quality?: QualityMetricsStats }> = ({ data, quality }) => {
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

            {quality?.has_data && quality.data && (() => {
              const q = quality.data;
              const rp = q.raw_payload || {};
              return (
                <>
                  <QualityMetricRow
                    icon={<Star size={13} color="var(--primary)" />}
                    label="Рейтинг"
                    value={q.rating_percentage != null ? q.rating_percentage.toFixed(1) : '—'}
                    color={getQualityColor(q.rating_percentage, rp.rating?.details?.goodValue, rp.rating?.details?.violationValue, true)}
                    hint="Средняя оценка за 90 дней"
                  />
                  <QualityMetricRow
                    icon={<RotateCcw size={13} color="var(--warning)" />}
                    label="Возвраты по качеству"
                    value={q.returns_percentage != null ? `${q.returns_percentage.toFixed(1)}%` : '—'}
                    color={getQualityColor(q.returns_percentage, rp.returns?.details?.goodValue, rp.returns?.details?.violationValue, false)}
                    hint="Доля возвратов за 30 дней"
                  />
                  <QualityMetricRow
                    icon={<Clock size={13} color="var(--warning)" />}
                    label="Задержки при передачах"
                    value={q.late_kd_percentage != null ? `${q.late_kd_percentage.toFixed(1)}%` : '—'}
                    color={getQualityColor(q.late_kd_percentage, rp.late_kaspi_delivery?.details?.goodValue, rp.late_kaspi_delivery?.details?.violationValue, false)}
                    hint="Просрочка передачи курьеру Kaspi Доставки"
                  />
                  <QualityMetricRow
                    icon={<XCircle size={13} color="#e2545f" />}
                    label="Отмены по вашей вине"
                    value={q.cancelled_percentage != null ? `${q.cancelled_percentage.toFixed(1)}%` : '—'}
                    color={getQualityColor(q.cancelled_percentage, rp.cancelled?.details?.goodValue, rp.cancelled?.details?.violationValue, false)}
                    hint="Доля отмен за 30 дней"
                  />
                  <QualityMetricRow
                    icon={<Clock size={13} color="var(--primary)" />}
                    label="Среднее время ответов"
                    value={q.avg_chat_response_minutes != null ? `${Math.round(q.avg_chat_response_minutes)} мин` : '—'}
                    color={getQualityColor(q.avg_chat_response_minutes, rp.average_chat_response_time?.thresholds?.goodValue, rp.average_chat_response_time?.thresholds?.badValue, false)}
                    hint="За последние 30 дней"
                  />
                  <QualityMetricRow
                    icon={<MessageCircle size={13} color="var(--success)" />}
                    label="Доля ответов"
                    value={q.chat_response_rate_percent != null ? `${Math.round(q.chat_response_rate_percent)}%` : '—'}
                    color={getQualityColor(q.chat_response_rate_percent, rp.chat_response_rate?.thresholds?.goodValue, rp.chat_response_rate?.thresholds?.badValue, true)}
                    hint="Доля отвеченных чатов за 30 дней"
                  />
                </>
              );
            })()}
          </div>

          {data.history_points < 3 && (
            <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '10px' }}>
              История только начала копиться — точность score вырастет в течение первых дней работы.
            </p>
          )}
          {!(quality?.has_data && quality.data) && (
            <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
              Показатели Kaspi (рейтинг, возвраты, задержки, отмены, чат) появятся после первого ночного скрапа — раз в сутки, ночью.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};
