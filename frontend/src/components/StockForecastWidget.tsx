import React, { useMemo, useState } from 'react';
import { PackageSearch, Download, Layers, Info } from 'lucide-react';
import { ExpandableCard } from './ExpandableCard';
import { apiFetch, API_URL } from '../api';
import {
  DEFAULT_FORECAST_SETTINGS,
  forecastQueryString,
  loadForecastSettings,
  saveForecastSettings,
  useStockForecast,
  type ForecastSettings,
  type StockBucket,
  type StockForecastItem,
} from '../hooks/useStockForecast';

const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n));

// Скорость бывает и 0.07 шт/день — округление до целого превратило бы её в
// «0 шт/день» рядом с непустым «дней до нуля», что читается как баг.
const fmtVelocity = (n: number) => (n >= 1 ? n.toFixed(1) : n.toFixed(2));

const BUCKET_META: Record<StockBucket, { label: string; short: string; color: string; hint: string }> = {
  out_of_stock: {
    label: 'Кончился',
    short: 'Ноль',
    color: '#ef4444',
    hint: 'Товар продавался и сейчас в нуле — продажи уже теряются',
  },
  critical: {
    label: 'Кончится за неделю',
    short: '≤ 7 дней',
    color: '#f97316',
    hint: 'Остатка хватит меньше чем на горизонт прогноза',
  },
  warning: {
    label: 'Не успеет приехать',
    short: 'Поставка',
    color: '#f59e0b',
    hint: 'Кончится раньше, чем придёт поставка, даже если заказать сегодня',
  },
  ok: { label: 'Запаса хватает', short: 'Норма', color: '#10b981', hint: 'Остатка хватает с запасом' },
  dead: {
    label: 'Не продаётся',
    short: 'Мёртвый',
    color: '#64748b',
    hint: 'Ни одной продажи за период при живом остатке — замороженные деньги',
  },
};

const BUCKET_ORDER: StockBucket[] = ['out_of_stock', 'critical', 'warning', 'ok', 'dead'];

const FORECAST_INFO_TEXT =
  'Как считаем:\n' +
  '• Скорость продаж — смесь 60% за последние 14 дней + 40% за последние 60 дней (свежий тренд весит больше).\n' +
  '• Дней до нуля = остаток ÷ скорость.\n' +
  '• «Кончится за неделю» — уложится в горизонт; «Не успеет приехать» — кончится раньше, чем придёт поставка.\n' +
  '• «Не продаётся» — 0 продаж за 60 дней при живом остатке.\n' +
  '• Заказать = скорость × (срок поставки + запас) − текущий остаток.\n' +
  '• Сумма заявки — по цене продажи (закупочных цен система не знает).\n' +
  '• Товары на одном складском остатке (мастер-группы) считаются одной строкой.';

const NumberField: React.FC<{
  label: string;
  title: string;
  value: number;
  onChange: (v: number) => void;
}> = ({ label, title, value, onChange }) => (
  <label title={title} style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '11px', color: 'var(--text-muted)' }}>
    {label}
    <input
      type="number"
      min={1}
      max={180}
      value={value}
      onChange={(e) => onChange(Math.max(1, Math.min(180, Number(e.target.value) || 1)))}
      style={{
        width: '72px',
        padding: '5px 8px',
        borderRadius: '8px',
        border: '1px solid rgba(255,255,255,0.12)',
        background: 'rgba(255,255,255,0.04)',
        color: '#e5e7eb',
        fontSize: '13px',
      }}
    />
  </label>
);

export const StockForecastWidget: React.FC = () => {
  const [settings, setSettings] = useState<ForecastSettings>(() => loadForecastSettings());
  const [bucketFilter, setBucketFilter] = useState<StockBucket | null>('critical');
  const [onlyReorder, setOnlyReorder] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const { data, isFetching } = useStockForecast(settings);
  const { summary, items } = data;
  const hasData = items.length > 0;

  const patch = (p: Partial<ForecastSettings>) => {
    const next = { ...settings, ...p };
    setSettings(next);
    saveForecastSettings(next);
  };

  const filtered = useMemo(() => {
    let rows: StockForecastItem[] = items;
    if (bucketFilter) rows = rows.filter((it) => it.bucket === bucketFilter);
    if (onlyReorder) rows = rows.filter((it) => it.recommended_qty > 0);
    return rows;
  }, [items, bucketFilter, onlyReorder]);

  const handleExport = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const res = await apiFetch(
        `${API_URL}/api/analytics/purchase-order-export?${forecastQueryString(settings)}&only_reorder=true`
      );
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setExportError(body?.detail || 'Не удалось сформировать заявку');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `zayavka_postavshchiku_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setExportError('Не удалось скачать заявку');
    } finally {
      setExporting(false);
    }
  };

  const urgentCount = summary.out_of_stock + summary.critical;

  return (
    <ExpandableCard
      icon={<PackageSearch size={20} color="#f97316" />}
      title="Прогноз остатков и закуп"
      subtitle={
        !hasData
          ? isFetching
            ? 'Считаем прогноз…'
            : 'Пока нет продаж для прогноза'
          : `${urgentCount} позиц. требуют закупа сейчас · заявка на ${fmt(summary.reorder_units)} шт.`
      }
      headerRight={
        <>
          <Info
            size={15}
            color="var(--text-muted)"
            style={{ flexShrink: 0, cursor: 'help' }}
            title={FORECAST_INFO_TEXT}
          />
          {urgentCount > 0 && (
            <span
              className="badge"
              style={{
                background: '#f9731622',
                color: '#f97316',
                border: '1px solid #f9731655',
                fontSize: '11px',
                fontWeight: 700,
              }}
            >
              {urgentCount}
            </span>
          )}
        </>
      }
    >
      {!hasData ? (
        <div style={{ padding: '12px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Появится, когда накопится история продаж и синхронизируется каталог
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {urgentCount > 0 && (
            <div
              style={{
                borderRadius: '12px',
                padding: '14px 18px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px',
                flexWrap: 'wrap',
                backgroundImage:
                  'radial-gradient(300px 160px at 90% 0%, rgba(255,255,255,.2), transparent 70%), linear-gradient(150deg, #f97316, #7c2d12)',
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: '10.5px',
                    letterSpacing: '0.05em',
                    textTransform: 'uppercase',
                    color: 'rgba(255,255,255,.85)',
                    fontWeight: 700,
                  }}
                >
                  Закупить сейчас
                </div>
                <div style={{ fontSize: '12px', color: 'rgba(255,255,255,.85)', marginTop: '2px' }}>
                  {summary.out_of_stock > 0 && `${summary.out_of_stock} уже в нуле · `}
                  {summary.critical} кончится в течение {settings.horizonDays} дн.
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: '30px', fontWeight: 800, color: '#fff', lineHeight: 1 }}>
                  {fmt(summary.reorder_units)}
                </div>
                <div style={{ fontSize: '11px', color: 'rgba(255,255,255,.85)' }}>
                  шт. в заявке · {summary.reorder_positions} позиц.
                </div>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: '14px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <NumberField
              label="Срок поставки, дн."
              title="Сколько дней идёт товар от поставщика. Позиции, которые кончатся раньше, помечаются «не успеет приехать»."
              value={settings.leadTimeDays}
              onChange={(v) => patch({ leadTimeDays: v })}
            />
            <NumberField
              label="Запас на, дн."
              title="На сколько дней продаж закупаемся сверх срока поставки — из этого считается рекомендованное количество."
              value={settings.coverDays}
              onChange={(v) => patch({ coverDays: v })}
            />
            <NumberField
              label="Горизонт, дн."
              title="Что считать «скоро кончится»."
              value={settings.horizonDays}
              onChange={(v) => patch({ horizonDays: v })}
            />
            <button
              className="btn btn-primary"
              style={{ padding: '8px 14px', fontSize: '12px' }}
              onClick={handleExport}
              disabled={exporting || summary.reorder_positions === 0}
              title={
                summary.reorder_positions === 0
                  ? 'Нечего заказывать — по всем позициям запаса хватает'
                  : 'Excel-заявка поставщику по позициям с рекомендованным закупом'
              }
            >
              <Download size={14} /> {exporting ? 'Готовим…' : 'Заявка поставщику'}
            </button>
            {settings.leadTimeDays !== DEFAULT_FORECAST_SETTINGS.leadTimeDays ||
            settings.coverDays !== DEFAULT_FORECAST_SETTINGS.coverDays ||
            settings.horizonDays !== DEFAULT_FORECAST_SETTINGS.horizonDays ? (
              <button
                className="btn btn-secondary"
                style={{ padding: '8px 12px', fontSize: '11px' }}
                onClick={() => {
                  setSettings(DEFAULT_FORECAST_SETTINGS);
                  saveForecastSettings(DEFAULT_FORECAST_SETTINGS);
                }}
              >
                Сбросить
              </button>
            ) : null}
          </div>
          {exportError && <div style={{ fontSize: '12px', color: '#ef4444' }}>{exportError}</div>}

          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {BUCKET_ORDER.map((b) => {
              const meta = BUCKET_META[b];
              const count = summary[b] || 0;
              const active = bucketFilter === b;
              return (
                <button
                  key={b}
                  title={meta.hint}
                  onClick={() => setBucketFilter(active ? null : b)}
                  style={{
                    all: 'unset',
                    cursor: 'pointer',
                    padding: '6px 10px',
                    borderRadius: '8px',
                    fontSize: '11px',
                    fontWeight: 700,
                    background: `${meta.color}${active ? '33' : '14'}`,
                    border: `1px solid ${meta.color}${active ? 'aa' : '33'}`,
                    color: count > 0 ? meta.color : 'var(--text-muted)',
                  }}
                >
                  {meta.label} · {count}
                </button>
              );
            })}
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                fontSize: '11px',
                color: 'var(--text-muted)',
                marginLeft: 'auto',
              }}
            >
              <input type="checkbox" checked={onlyReorder} onChange={(e) => setOnlyReorder(e.target.checked)} />
              только с закупом
            </label>
          </div>

          <div style={{ maxHeight: '340px', overflowY: 'auto' }}>
            {/* table-layout: fixed + colgroup — иначе браузер раздаёт ширину по
                содержимому и числовые колонки из 2-3 знаков забирают половину
                таблицы, а название товара схлопывается в «Форма для варе...»
                при полупустой строке. */}
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', tableLayout: 'fixed' }}>
              <colgroup>
                <col />
                <col style={{ width: '76px' }} />
                <col style={{ width: '76px' }} />
                <col style={{ width: '76px' }} />
                <col style={{ width: '84px' }} />
              </colgroup>
              <thead>
                <tr style={{ color: 'var(--text-muted)', fontSize: '10.5px', textAlign: 'right' }}>
                  <th style={{ textAlign: 'left', padding: '4px 6px', fontWeight: 600 }}>Товар</th>
                  <th style={{ padding: '4px 6px', fontWeight: 600 }}>Остаток</th>
                  <th style={{ padding: '4px 6px', fontWeight: 600 }}>шт./день</th>
                  <th style={{ padding: '4px 6px', fontWeight: 600 }}>Дней до 0</th>
                  <th style={{ padding: '4px 6px', fontWeight: 600 }}>Заказать</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={5} style={{ padding: '14px 6px', textAlign: 'center', color: 'var(--text-muted)' }}>
                      Нет позиций в этой категории
                    </td>
                  </tr>
                )}
                {filtered.map((it) => {
                  const meta = BUCKET_META[it.bucket];
                  return (
                    <tr key={`${it.kind}:${it.key}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                      <td style={{ padding: '6px' }}>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          <span
                            style={{ width: '4px', height: '4px', borderRadius: '50%', background: meta.color, flexShrink: 0 }}
                          />
                          {it.kind === 'group' && (
                            <Layers
                              size={12}
                              color="#22bdea"
                              style={{ flexShrink: 0 }}
                              // Мастер-группа делит один физический остаток на
                              // несколько карточек — считается одной строкой.
                            />
                          )}
                          <span
                            style={{ color: '#e5e7eb', overflow: 'hidden', textOverflow: 'ellipsis' }}
                            title={
                              it.kind === 'group'
                                ? `Мастер-группа, ${it.members_count} артикул(ов), общий остаток`
                                : it.merchant_sku ?? ''
                            }
                          >
                            {it.name}
                          </span>
                          {it.kind === 'sku' && it.status && it.status !== 'Активен' && (
                            // Карточка снята с витрины. Для «кончился» это
                            // обычно и есть причина снятия, но заказ по такой
                            // позиции требует ещё и вернуть её в продажу —
                            // молча смешивать её с активными нельзя.
                            <span
                              title="Карточка снята с продажи в Kaspi"
                              style={{
                                flexShrink: 0,
                                fontSize: '9.5px',
                                fontWeight: 700,
                                padding: '1px 5px',
                                borderRadius: '5px',
                                background: 'rgba(148,163,184,0.15)',
                                color: '#94a3b8',
                              }}
                            >
                              снят
                            </span>
                          )}
                        </div>
                      </td>
                      <td style={{ padding: '6px', textAlign: 'right', color: it.stock === 0 ? '#ef4444' : '#e5e7eb' }}>
                        {fmt(it.stock)}
                      </td>
                      <td style={{ padding: '6px', textAlign: 'right', color: 'var(--text-muted)' }}>
                        {it.velocity > 0 ? fmtVelocity(it.velocity) : '—'}
                      </td>
                      <td style={{ padding: '6px', textAlign: 'right', color: meta.color, fontWeight: 700 }}>
                        {it.days_to_zero === null ? '—' : it.days_to_zero}
                      </td>
                      <td style={{ padding: '6px', textAlign: 'right', color: it.recommended_qty > 0 ? '#e5e7eb' : 'var(--text-muted)', fontWeight: it.recommended_qty > 0 ? 700 : 400 }}>
                        {it.recommended_qty > 0 ? fmt(it.recommended_qty) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div style={{ fontSize: '10.5px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Скорость — по продажам за {data.window.from} — {data.window.to} с перевесом в сторону последних двух
            недель. Мастер-группы считаются одной строкой на общий остаток.
            {summary.dead > 0 && ` Не продаётся ${summary.dead} позиц. на ${fmt(summary.frozen_cost)} ₸ по цене продажи.`}
            {summary.unmatched_skus > 0 && (
              <>
                {' '}
                <span
                  title="Обычно это старые написания артикулов, под которыми товар продавался до переименования. Складского остатка у них нет, прогнозировать нечего."
                  style={{ borderBottom: '1px dotted var(--text-muted)', cursor: 'help' }}
                >
                  {summary.unmatched_skus} артикул(ов) из истории продаж нет в текущем каталоге
                </span>{' '}
                — они не учтены.
              </>
            )}
            {data.items_truncated && (
              <>
                {' '}Показаны все требующие внимания позиции и часть остальных — каталог большой, полный список есть
                в выгрузке.
              </>
            )}
          </div>
        </div>
      )}
    </ExpandableCard>
  );
};
