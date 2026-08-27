import React, { useMemo, useState } from 'react';
import { Swords, Crown, AlertTriangle, Link2 } from 'lucide-react';
import { ExpandableCard } from './ExpandableCard';
import { leaderPrice, type PriceWarStats } from '../hooks/usePriceWarStats';

type SortKey = 'rank_worst' | 'rank_best' | 'gap';

const rankColor = (rank: number | null) => {
  if (rank === 1) return '#10b981';
  if (rank === null) return 'var(--text-muted)';
  if (rank <= 3) return '#f59e0b';
  return '#ef4444';
};

export const PriceWarSummary: React.FC<{ data: PriceWarStats }> = ({ data }) => {
  const [sort, setSort] = useState<SortKey>('rank_worst');
  const { summary, products } = data;
  const hasData = products.length > 0;

  const sorted = useMemo(() => {
    const copy = [...products];
    if (sort === 'rank_worst') {
      copy.sort((a, b) => (b.our_rank ?? 999) - (a.our_rank ?? 999));
    } else if (sort === 'rank_best') {
      copy.sort((a, b) => (a.our_rank ?? 999) - (b.our_rank ?? 999));
    } else {
      copy.sort((a, b) => {
        const gapA = a.best_competitor_price !== null ? a.our_price - a.best_competitor_price : -Infinity;
        const gapB = b.best_competitor_price !== null ? b.our_price - b.best_competitor_price : -Infinity;
        return gapB - gapA;
      });
    }
    return copy;
  }, [products, sort]);

  return (
    <ExpandableCard
      icon={<Swords size={20} color="#17b8a6" />}
      title="Сводка демпинг-войны"
      subtitle={hasData ? `${summary.total_in_war} товаров в войне, средняя позиция ${summary.avg_rank}` : 'Пока нет данных цикла демпинга'}
      headerRight={
        hasData && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <Crown size={14} /> {summary.winning_count}
            </span>
            <span className="badge" style={{ background: 'rgba(239,68,68,0.15)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <AlertTriangle size={14} /> {summary.losing_count}
            </span>
          </div>
        )
      }
    >
      {!hasData ? (
        <div style={{ padding: '12px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Сводка заполнится после первого цикла демпинга (~15 минут)
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {([
              ['rank_worst', 'Сначала худшие'],
              ['rank_best', 'Сначала лучшие'],
              ['gap', 'По разрыву в цене'],
            ] as [SortKey, string][]).map(([key, label]) => (
              <button
                key={key}
                className="btn btn-secondary"
                style={{
                  padding: '6px 12px',
                  fontSize: '12px',
                  ...(sort === key ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 } : {}),
                }}
                onClick={() => setSort(key)}
              >
                {label}
              </button>
            ))}
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr auto auto auto auto',
              gap: '12px',
              padding: '0 10px',
            }}
          >
            <span />
            <span style={{ fontSize: '10px', color: 'var(--text-muted)', textAlign: 'right', whiteSpace: 'nowrap' }}>Цена 1-го места</span>
            <span style={{ fontSize: '10px', color: 'var(--text-muted)', textAlign: 'right', whiteSpace: 'nowrap' }}>Текущая цена</span>
            <span />
            <span />
          </div>

          <div style={{ maxHeight: '360px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {sorted.map((p) => (
              <div
                key={p.sku}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto auto auto auto',
                  gap: '12px',
                  alignItems: 'center',
                  padding: '8px 10px',
                  borderRadius: '8px',
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.05)',
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '12px', color: '#e5e7eb', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {p.name}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {p.best_competitor_name && (
                      <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                        конкурент: {p.best_competitor_name} ({p.best_competitor_price?.toLocaleString('ru-RU')} ₸)
                      </div>
                    )}
                    {p.url && (
                      <a
                        href={p.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ fontSize: '10px', color: '#3b82f6', textDecoration: 'none', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                      >
                        <Link2 size={11} /> Карточка товара
                      </a>
                    )}
                  </div>
                </div>
                <div style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', color: '#9ca3af', whiteSpace: 'nowrap', textAlign: 'right' }}>
                  {leaderPrice(p) !== null ? `${leaderPrice(p)!.toLocaleString('ru-RU')} ₸` : '—'}
                </div>
                <div style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', color: '#e5e7eb', whiteSpace: 'nowrap', textAlign: 'right' }}>
                  {p.our_price.toLocaleString('ru-RU')} ₸
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                  {p.competitors_count} конк.
                </div>
                <span
                  className="badge"
                  style={{
                    background: `${rankColor(p.our_rank)}22`,
                    color: rankColor(p.our_rank),
                    border: `1px solid ${rankColor(p.our_rank)}55`,
                    fontWeight: 700,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {p.our_rank ? `#${p.our_rank}` : 'Н/Д'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </ExpandableCard>
  );
};
