import React, { useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, Package, Wallet, Download } from 'lucide-react';
import { Area, AreaChart, Bar, BarChart, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis } from 'recharts';
import { useSalesStats } from '../hooks/useSalesStats';
import { apiFetch, API_URL } from '../api';

const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n));
const PIE_COLORS = ['#35c9a9', '#5fb4e8', '#e8c15f', '#e87f5f', '#a05fe8', '#5fe88f', '#e85f9e'];

const isoDate = (d: Date) => d.toISOString().slice(0, 10);
const daysAgo = (n: number) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDate(d);
};

type SortKey = 'revenue_desc' | 'revenue_asc' | 'qty_desc' | 'qty_asc';

const SORT_OPTIONS: [SortKey, string][] = [
  ['revenue_desc', 'Больше выручки'],
  ['revenue_asc', 'Меньше выручки'],
  ['qty_desc', 'Больше продаж'],
  ['qty_asc', 'Меньше продаж'],
];

interface SalesStatsWidgetProps {
  period?: number;
  chartType?: 'bar' | 'line' | 'area' | 'pie' | 'donut';
  metric?: 'revenue' | 'orders';
}

export const SalesStatsWidget: React.FC<SalesStatsWidgetProps> = ({ period = 30, chartType = 'bar', metric = 'revenue' }) => {
  const data = useSalesStats(period);
  const [sort, setSort] = useState<SortKey>('revenue_desc');
  const hasData = data.daily.length > 0;
  const isPieLike = chartType === 'pie' || chartType === 'donut';

  const [exportFrom, setExportFrom] = useState(() => daysAgo(period));
  const [exportTo, setExportTo] = useState(() => isoDate(new Date()));
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const handleExport = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const res = await apiFetch(
        `${API_URL}/api/analytics/sales-export?date_from=${exportFrom}&date_to=${exportTo}`
      );
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setExportError(body?.detail || 'Не удалось сформировать отчёт');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `sales_${exportFrom}_${exportTo}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setExportError('Не удалось скачать отчёт');
    } finally {
      setExporting(false);
    }
  };

  const sortedSkus = useMemo(() => {
    const copy = [...data.top_skus];
    switch (sort) {
      case 'revenue_asc':
        return copy.sort((a, b) => a.revenue - b.revenue);
      case 'qty_desc':
        return copy.sort((a, b) => b.quantity - a.quantity);
      case 'qty_asc':
        return copy.sort((a, b) => a.quantity - b.quantity);
      default:
        return copy.sort((a, b) => b.revenue - a.revenue);
    }
  }, [data.top_skus, sort]);

  const pieData = useMemo(() => {
    const valueKey = metric === 'revenue' ? 'revenue' : 'quantity';
    const sorted = [...data.top_skus].sort((a, b) => (b[valueKey] as number) - (a[valueKey] as number));
    const top = sorted.slice(0, 6).map((s) => ({ name: s.name, value: s[valueKey] as number }));
    const restValue = sorted.slice(6).reduce((sum, s) => sum + (s[valueKey] as number), 0);
    return restValue > 0 ? [...top, { name: 'Остальное', value: restValue }] : top;
  }, [data.top_skus, metric]);

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Wallet size={24} />
          <div>
            <h2 style={{ fontSize: '17px', fontWeight: 700, letterSpacing: '-0.3px' }}>
              Реальные продажи
            </h2>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              За последние {period} {period === 1 ? 'день' : period < 5 ? 'дня' : 'дней'}, из фактической истории завершённых заказов Kaspi
            </p>
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', padding: '10px', background: 'rgba(255,255,255,0.03)', borderRadius: '10px' }}>
        <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>ЭКСПОРТ В EXCEL</span>
        <input
          type="date"
          value={exportFrom}
          max={exportTo}
          onChange={(e) => setExportFrom(e.target.value)}
          className="input"
          style={{ fontSize: '12px', padding: '4px 8px', width: '140px' }}
        />
        <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>—</span>
        <input
          type="date"
          value={exportTo}
          min={exportFrom}
          max={isoDate(new Date())}
          onChange={(e) => setExportTo(e.target.value)}
          className="input"
          style={{ fontSize: '12px', padding: '4px 8px', width: '140px' }}
        />
        <button
          className="btn btn-secondary"
          style={{ padding: '5px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}
          onClick={handleExport}
          disabled={exporting}
        >
          <Download size={13} /> {exporting ? 'Формируем…' : 'Скачать .xlsx'}
        </button>
        {exportError && (
          <span style={{ fontSize: '11px', color: '#e87f5f' }}>{exportError}</span>
        )}
      </div>

      {!hasData ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          Данных пока нет — фоновый воркер синхронизирует историю заказов каждые 30 минут, первые цифры появятся после первого прохода
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '16px' }}>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>ВЫРУЧКА</div>
              <div style={{ fontSize: '22px', fontWeight: 800, color: '#fff' }}>{fmt(data.total_revenue)} ₸</div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>ЗАКАЗОВ</div>
              <div style={{ fontSize: '22px', fontWeight: 800, color: '#fff' }}>{data.total_orders}</div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>СРЕДНИЙ ЧЕК</div>
              <div style={{ fontSize: '22px', fontWeight: 800, color: '#fff' }}>{fmt(data.avg_order_value)} ₸</div>
            </div>
          </div>

          {/* Тип диаграммы, метрика и период настраиваются в режиме редактирования дашборда.
              Круговая/кольцевая показывают долю товаров в выручке/заказах, а не тренд по дням. */}
          <div style={{ height: isPieLike ? '160px' : '90px' }}>
            <ResponsiveContainer width="100%" height="100%">
              {isPieLike ? (
                <PieChart margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
                  <Tooltip
                    formatter={(value: number) => (metric === 'revenue' ? `${fmt(value)} ₸` : `${value} шт`)}
                    contentStyle={{ background: 'rgba(10,10,16,0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '12px' }}
                  />
                  <Pie
                    data={pieData}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={chartType === 'donut' ? '55%' : 0}
                    outerRadius="90%"
                    paddingAngle={2}
                  >
                    {pieData.map((entry, i) => (
                      <Cell key={entry.name} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                </PieChart>
              ) : chartType === 'line' ? (
                <LineChart data={data.daily} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
                  <XAxis dataKey="date" hide />
                  <Tooltip
                    formatter={(value: number) => (metric === 'revenue' ? `${fmt(value)} ₸` : `${value} шт`)}
                    labelFormatter={(label) => String(label)}
                    contentStyle={{ background: 'rgba(10,10,16,0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '12px' }}
                  />
                  <Line type="monotone" dataKey={metric} stroke="#35c9a9" strokeWidth={2} dot={false} />
                </LineChart>
              ) : chartType === 'area' ? (
                <AreaChart data={data.daily} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
                  <XAxis dataKey="date" hide />
                  <Tooltip
                    formatter={(value: number) => (metric === 'revenue' ? `${fmt(value)} ₸` : `${value} шт`)}
                    labelFormatter={(label) => String(label)}
                    contentStyle={{ background: 'rgba(10,10,16,0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '12px' }}
                  />
                  <Area type="monotone" dataKey={metric} stroke="#35c9a9" fill="#35c9a9" fillOpacity={0.25} strokeWidth={2} />
                </AreaChart>
              ) : (
                <BarChart data={data.daily} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
                  <XAxis dataKey="date" hide />
                  <Tooltip
                    formatter={(value: number) => (metric === 'revenue' ? `${fmt(value)} ₸` : `${value} шт`)}
                    labelFormatter={(label) => String(label)}
                    contentStyle={{ background: 'rgba(10,10,16,0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '12px' }}
                  />
                  <Bar dataKey={metric} fill="#35c9a9" radius={[3, 3, 0, 0]} />
                </BarChart>
              )}
            </ResponsiveContainer>
          </div>

          {data.top_skus.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Package size={13} /> Товары ({data.top_skus.length})
                </span>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {SORT_OPTIONS.map(([key, label]) => (
                    <button
                      key={key}
                      className="btn btn-secondary"
                      style={{
                        padding: '4px 10px',
                        fontSize: '11px',
                        ...(sort === key ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 } : {}),
                      }}
                      onClick={() => setSort(key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ maxHeight: '260px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {sortedSkus.map((sku, i) => {
                  const rankStyle =
                    i === 0
                      ? { background: 'var(--primary-glow)', color: 'var(--primary)' }
                      : i === 1
                      ? { background: 'rgba(47,143,216,0.18)', color: '#5fb4e8' }
                      : { background: 'rgba(255,255,255,0.08)', color: '#c9c9d0' };
                  return (
                    <div key={sku.sku} style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '12px', padding: '8px 4px', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                      <span style={{ width: '22px', height: '22px', flex: '0 0 auto', borderRadius: '6px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', fontWeight: 800, ...rankStyle }}>
                        {i + 1}
                      </span>
                      <span style={{ color: '#e5e7eb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                        {sku.name}
                      </span>
                      <span style={{ display: 'flex', gap: '10px', color: 'var(--text-muted)', flex: '0 0 auto' }}>
                        <span>{sku.quantity} шт</span>
                        <span style={{ color: sku.revenue > 0 ? 'var(--secondary)' : 'var(--text-muted)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '2px' }}>
                          {sku.revenue > 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />} {fmt(sku.revenue)} ₸
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};
