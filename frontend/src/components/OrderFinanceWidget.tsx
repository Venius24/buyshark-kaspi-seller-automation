import React, { useState } from 'react';
import { Wallet } from 'lucide-react';
import { ExpandableCard } from './ExpandableCard';
import { HelpHint } from './shared/HelpHint';
import { FINANCE_PERIODS, useOrderFinance, type FinancePeriod } from '../hooks/useOrderFinance';

const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n));

const PERIOD_STORAGE_KEY = 'order_finance_period';

const readStoredPeriod = (): FinancePeriod => {
  try {
    const v = Number(localStorage.getItem(PERIOD_STORAGE_KEY));
    return (FINANCE_PERIODS as readonly number[]).includes(v) ? (v as FinancePeriod) : 14;
  } catch {
    return 14;
  }
};

const COLORS = {
  net: '#10b981',
  commission: '#f59e0b',
  delivery: '#22bdea',
  returns: '#ef4444',
};

const FAULT_COLORS: Record<string, string> = {
  merchant: '#ef4444',
  buyer: 'var(--text-muted)',
  kaspi: '#22bdea',
};

const Tile: React.FC<{ label: string; value: string; hint?: string; color?: string; note?: string }> = ({
  label, value, hint, color, note,
}) => (
  <div style={{ padding: '12px 14px', borderRadius: '12px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
    <div style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px' }}>
      {label}
      {hint && <HelpHint text={hint} />}
    </div>
    <div style={{ fontSize: '19px', fontWeight: 800, color: color || '#e5e7eb', marginTop: '4px' }}>{value}</div>
    {note && <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>{note}</div>}
  </div>
);

const SectionTitle: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontSize: '12px', fontWeight: 700, color: '#e5e7eb', marginBottom: '8px' }}>{children}</div>
);

const Row: React.FC<{ left: React.ReactNode; right: React.ReactNode }> = ({ left, right }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '12px', padding: '6px 4px', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
    <span style={{ color: '#e5e7eb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{left}</span>
    <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{right}</span>
  </div>
);

export const OrderFinanceWidget: React.FC = () => {
  const [period, setPeriod] = useState<FinancePeriod>(readStoredPeriod);
  const { data, isLoading } = useOrderFinance(period);

  const changePeriod = (p: FinancePeriod) => {
    setPeriod(p);
    try {
      localStorage.setItem(PERIOD_STORAGE_KEY, String(p));
    } catch {
      /* без localStorage выбор просто не запомнится */
    }
  };

  const periodToggle = (
    <div style={{ display: 'flex', gap: '6px', marginBottom: '14px' }}>
      {FINANCE_PERIODS.map((p) => (
        <button
          key={p}
          className={period === p ? 'btn btn-primary' : 'btn btn-secondary'}
          style={{ padding: '4px 12px', fontSize: '12px' }}
          onClick={() => changePeriod(p)}
        >
          {p} дн.
        </button>
      ))}
    </div>
  );

  const subtitle = isLoading
    ? 'Считаем по заказам из Kaspi…'
    : !data
    ? 'Kaspi не отдал заказы, попробуйте позже'
    : `${fmt(data.revenue)} ₸ выручки, после удержаний Kaspi ${fmt(data.net_after_kaspi)} ₸`;

  let body: React.ReactNode;
  if (isLoading) {
    body = <div style={{ padding: '12px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>Загружаем закрытые заказы за {period} дн. — до полуминуты на большом магазине</div>;
  } else if (!data) {
    body = <div style={{ padding: '12px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>Не удалось получить заказы из Kaspi. Данные обновятся автоматически.</div>;
  } else {
    const { revenue, commission, delivery, returns, cancellations, orders } = data;
    const split = [
      { key: 'net', label: 'Остаётся вам', value: Math.max(0, data.net_after_kaspi), color: COLORS.net },
      { key: 'commission', label: 'Комиссия', value: commission.total, color: COLORS.commission },
      { key: 'delivery', label: 'Доставка', value: delivery.total, color: COLORS.delivery },
    ];
    const splitTotal = split.reduce((s, x) => s + x.value, 0) || 1;
    const unknownCount = Object.keys(commission.unknown_categories).length;

    body = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '10px' }}>
          <Tile label="Выручка" value={`${fmt(revenue)} ₸`} note={`${fmt(orders.completed)} выполненных заказов`} />
          <Tile
            label="Комиссия Kaspi ≈"
            value={`${fmt(commission.total)} ₸`}
            color={COLORS.commission}
            note={`${commission.share_of_revenue_pct}% выручки, с НДС`}
            hint="Оценка: Kaspi не отдаёт комиссию через API. Считаем по официальной таблице тарифов Kaspi 2026 года по категории каждого товара в заказе, с НДС. Точная сумма — в кабинете Kaspi, в начислениях по заказу."
          />
          <Tile
            label="Доставка"
            value={`${fmt(delivery.total)} ₸`}
            color={COLORS.delivery}
            note={`≈${fmt(delivery.avg_per_order)} ₸ на заказ`}
            hint="Точная сумма: Kaspi отдаёт по каждому заказу, сколько спишет с продавца за доставку, уже с НДС. Включает доставку по возвратам."
          />
          <Tile
            label="Возвраты"
            value={`${returns.count} шт.`}
            color={returns.count ? COLORS.returns : undefined}
            note={`${fmt(returns.amount)} ₸ · ${returns.rate_pct}% продаж`}
          />
          <Tile
            label="Отмены"
            value={`${cancellations.count} шт.`}
            note={`${fmt(cancellations.amount)} ₸ · по вашей вине ${cancellations.merchant_fault}`}
          />
          <Tile label="Остаётся после Kaspi" value={`${fmt(data.net_after_kaspi)} ₸`} color={COLORS.net} note="без себестоимости и налогов" />
        </div>

        <div>
          <SectionTitle>Куда уходит выручка</SectionTitle>
          <div style={{ display: 'flex', height: '14px', borderRadius: '7px', overflow: 'hidden', background: 'rgba(255,255,255,0.05)' }}>
            {split.map((s) => (
              <div key={s.key} title={`${s.label}: ${fmt(s.value)} ₸`} style={{ width: `${(s.value / splitTotal) * 100}%`, background: s.color }} />
            ))}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', marginTop: '8px', fontSize: '11px', color: 'var(--text-muted)' }}>
            {split.map((s) => (
              <span key={s.key} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: s.color }} />
                {s.label} {((s.value / splitTotal) * 100).toFixed(1)}%
              </span>
            ))}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '18px' }}>
          <div>
            <SectionTitle>Комиссия по категориям</SectionTitle>
            <div style={{ maxHeight: '240px', overflowY: 'auto' }}>
              {commission.by_category.map((c) => (
                <Row key={c.category} left={c.category} right={`${fmt(c.commission)} ₸ из ${fmt(c.revenue)} ₸`} />
              ))}
            </div>
          </div>

          <div>
            <SectionTitle>Отмены по причинам</SectionTitle>
            <div style={{ maxHeight: '240px', overflowY: 'auto' }}>
              {cancellations.by_reason.length === 0 ? (
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Отмен нет</div>
              ) : (
                cancellations.by_reason.map((r) => (
                  <Row
                    key={r.reason}
                    left={<span style={{ color: FAULT_COLORS[r.fault] === 'var(--text-muted)' ? '#e5e7eb' : FAULT_COLORS[r.fault] || '#e5e7eb' }}>{r.label}</span>}
                    right={r.count}
                  />
                ))
              )}
            </div>
          </div>

          <div>
            <SectionTitle>Чаще всего возвращают</SectionTitle>
            <div style={{ maxHeight: '240px', overflowY: 'auto' }}>
              {returns.top_skus.length === 0 ? (
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Возвратов нет</div>
              ) : (
                returns.top_skus.map((s) => <Row key={s.sku} left={s.sku} right={`${s.qty} шт.`} />)
              )}
            </div>
          </div>
        </div>

        <div style={{ fontSize: '11px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
          Заказы, созданные за последние {data.period.days} дн. и уже закрытые. Заказы, которые ещё в пути, появятся после закрытия.
          {commission.uncertain_orders > 0 && ` ${commission.uncertain_orders} заказ(ов) в рассрочку на 24 мес.: по ним Kaspi может брать другую ставку.`}
          {unknownCount > 0 && ` ${unknownCount} категори(й) нет в таблице Kaspi — для них взята ставка 12,5%.`}
        </div>
      </div>
    );
  }

  return (
    <ExpandableCard icon={<Wallet size={20} color="#10b981" />} title="Финансы заказов" subtitle={subtitle}>
      {periodToggle}
      {body}
    </ExpandableCard>
  );
};
