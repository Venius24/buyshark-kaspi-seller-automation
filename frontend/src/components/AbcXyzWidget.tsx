import React, { useMemo, useState } from 'react';
import { Grid3x3 } from 'lucide-react';
import { ExpandableCard } from './ExpandableCard';
import { useAbcXyzStats, type AbcXyzGroupBy } from '../hooks/useAbcXyzStats';

const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n));

const ABC_COLORS: Record<string, string> = { A: '#10b981', B: '#22bdea', C: '#f59e0b' };
const XYZ_LABELS: Record<string, string> = { X: 'стабильный', Y: 'колеблется', Z: 'нестабильный' };

// Классика ABC/XYZ: AX — лучшая ячейка (высокая выручка + стабильный спрос),
// CZ — худшая (низкая выручка + хаотичный спрос). Цвет матрицы — от зелёного к красному по диагонали.
const CELL_TONE: Record<string, string> = {
  AX: '#10b981', AY: '#34d399', AZ: '#f59e0b',
  BX: '#34d399', BY: '#22bdea', BZ: '#f59e0b',
  CX: '#f59e0b', CY: '#f59e0b', CZ: '#ef4444',
};

const GROUP_BY_STORAGE_KEY = 'abc_xyz_group_by';

const readStoredGroupBy = (): AbcXyzGroupBy => {
  try {
    return localStorage.getItem(GROUP_BY_STORAGE_KEY) === 'group' ? 'group' : 'sku';
  } catch {
    return 'sku';
  }
};

export const AbcXyzWidget: React.FC = () => {
  const [groupBy, setGroupBy] = useState<AbcXyzGroupBy>(readStoredGroupBy);
  const data = useAbcXyzStats(groupBy);
  const [classFilter, setClassFilter] = useState<string | null>(null);
  const unit = groupBy === 'group' ? 'позиций' : 'товаров';

  const changeGroupBy = (next: AbcXyzGroupBy) => {
    setGroupBy(next);
    setClassFilter(null);
    try {
      localStorage.setItem(GROUP_BY_STORAGE_KEY, next);
    } catch {
      /* без localStorage выбор просто не запомнится */
    }
  };
  const hasData = data.items.length > 0;
  const enoughHistory = data.weeks_covered >= 4;

  const filteredItems = useMemo(() => {
    if (!classFilter) return data.items;
    return data.items.filter((it) => `${it.abc_class}${it.xyz_class ?? ''}` === classFilter);
  }, [data.items, classFilter]);

  const groupToggle = (
    <div style={{ display: 'flex', gap: '6px', marginBottom: '12px' }}>
      {([['sku', 'По товарам'], ['group', 'По мастер-группам']] as const).map(([mode, label]) => (
        <button
          key={mode}
          className={groupBy === mode ? 'btn btn-primary' : 'btn btn-secondary'}
          style={{ padding: '4px 12px', fontSize: '12px' }}
          onClick={() => changeGroupBy(mode)}
        >
          {label}
        </button>
      ))}
    </div>
  );

  return (
    <ExpandableCard
      icon={<Grid3x3 size={20} color="#22bdea" />}
      title="ABC/XYZ-анализ товаров"
      subtitle={
        !hasData
          ? 'Пока нет продаж для анализа'
          : enoughHistory
          ? `${data.items.length} ${unit}, ${data.weeks_covered} нед. истории`
          : `Копим историю для XYZ (нужно ≥4 недель, сейчас ${data.weeks_covered})`
      }
    >
      {groupToggle}
      {!hasData ? (
        <div style={{ padding: '12px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Появится, когда накопятся продажи из sales_orders
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {(data.matrix['AX'] || 0) > 0 && (
            <div
              style={{
                borderRadius: '12px',
                padding: '14px 18px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px',
                backgroundImage: 'radial-gradient(300px 160px at 90% 0%, rgba(255,255,255,.2), transparent 70%), linear-gradient(150deg, #35c9a9, #14513f)',
              }}
            >
              <div>
                <div style={{ fontSize: '10.5px', letterSpacing: '0.05em', textTransform: 'uppercase', color: 'rgba(255,255,255,.85)', fontWeight: 700 }}>
                  Звёзды каталога
                </div>
                <div style={{ fontSize: '12px', color: 'rgba(255,255,255,.85)', marginTop: '2px' }}>
                  AX — высокая выручка + стабильный спрос
                </div>
              </div>
              <div style={{ fontSize: '30px', fontWeight: 800, color: '#fff' }}>{data.matrix['AX']}</div>
            </div>
          )}
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '8px' }}>
              A/B/C — доля в выручке (80% / 15% / 5%), X/Y/Z — стабильность спроса по неделям
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'auto repeat(3, 1fr)', gap: '4px', maxWidth: '420px' }}>
              <div />
              {(['X', 'Y', 'Z'] as const).map((x) => (
                <div key={x} style={{ fontSize: '10px', color: 'var(--text-muted)', textAlign: 'center', fontWeight: 700 }}>
                  {x}<span style={{ display: 'block', fontWeight: 400 }}>{XYZ_LABELS[x]}</span>
                </div>
              ))}
              {(['A', 'B', 'C'] as const).map((a) => (
                <React.Fragment key={a}>
                  <div style={{ fontSize: '10px', color: ABC_COLORS[a], fontWeight: 700, display: 'flex', alignItems: 'center' }}>{a}</div>
                  {(['X', 'Y', 'Z'] as const).map((x) => {
                    const key = `${a}${x}`;
                    const count = data.matrix[key] || 0;
                    const tone = CELL_TONE[key];
                    const active = classFilter === key;
                    return (
                      <button
                        key={key}
                        onClick={() => setClassFilter(active ? null : key)}
                        style={{
                          all: 'unset',
                          cursor: count > 0 ? 'pointer' : 'default',
                          textAlign: 'center',
                          padding: '10px 4px',
                          borderRadius: '8px',
                          background: `${tone}${active ? '44' : count > 0 ? '22' : '0a'}`,
                          border: `1px solid ${tone}${active ? 'aa' : '33'}`,
                          fontSize: '15px',
                          fontWeight: 800,
                          color: count > 0 ? tone : 'var(--text-muted)',
                        }}
                      >
                        {count}
                      </button>
                    );
                  })}
                </React.Fragment>
              ))}
            </div>
            {classFilter && (
              <button
                className="btn btn-secondary"
                style={{ marginTop: '8px', padding: '4px 10px', fontSize: '11px' }}
                onClick={() => setClassFilter(null)}
              >
                Сбросить фильтр ({classFilter})
              </button>
            )}
          </div>

          <div style={{ maxHeight: '280px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {filteredItems.map((it) => (
              <div key={it.sku} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', padding: '6px 4px', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                <span style={{ color: '#e5e7eb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '50%' }}>
                  {it.name}
                  {it.kind === 'group' && (
                    <span style={{ color: 'var(--text-muted)', marginLeft: '6px' }}>· {it.members_count} арт.</span>
                  )}
                </span>
                <span style={{ display: 'flex', gap: '8px', alignItems: 'center', color: 'var(--text-muted)' }}>
                  <span>{fmt(it.revenue)} ₸ ({it.revenue_share}%)</span>
                  <span className="badge" style={{ background: `${ABC_COLORS[it.abc_class]}22`, color: ABC_COLORS[it.abc_class], border: `1px solid ${ABC_COLORS[it.abc_class]}55`, fontSize: '10px' }}>
                    {it.abc_class}{it.xyz_class ?? '?'}
                  </span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </ExpandableCard>
  );
};
