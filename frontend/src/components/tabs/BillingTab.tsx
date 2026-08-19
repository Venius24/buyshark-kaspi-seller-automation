import React from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useActiveStoreId } from '../../storeContext';
import { fetchBillingSummary, fetchBillingPlans, fetchBillingTransactions } from '../../queries';
import { formatTenge } from '../../utils/money';

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: 'tab_billing.status_active',
  BLOCKED_LIMIT: 'tab_billing.status_blocked_limit',
  BLOCKED_BALANCE: 'tab_billing.status_blocked_balance',
};

const KIND_LABEL: Record<string, string> = {
  topup_manual: 'tab_billing.kind_topup_manual',
  topup_gateway: 'tab_billing.kind_topup_gateway',
  charge_subscription: 'tab_billing.kind_charge_subscription',
  charge_prompt_addon: 'tab_billing.kind_charge_prompt',
  referral_payout: 'tab_billing.kind_referral_payout',
  adjustment: 'tab_billing.kind_adjustment',
  refund: 'tab_billing.kind_refund',
};

export const BillingTab: React.FC = () => {
  const { t } = useTranslation();
  const activeStoreId = useActiveStoreId() ?? '';

  const { data: summary } = useQuery({
    queryKey: ['billing', 'summary', activeStoreId],
    queryFn: fetchBillingSummary,
    enabled: !!activeStoreId,
  });

  const { data: plans = [] } = useQuery({
    queryKey: ['billing', 'plans'],
    queryFn: fetchBillingPlans,
    staleTime: Infinity,
  });

  const { data: transactions = [] } = useQuery({
    queryKey: ['billing', 'transactions', activeStoreId],
    queryFn: fetchBillingTransactions,
    enabled: !!activeStoreId,
  });

  if (!summary) {
    return <div style={styles.wrap}><div className="glass-card" style={styles.card}>{t('tab_billing.loading')}</div></div>;
  }

  const blocked = summary.billing_status !== 'ACTIVE';
  const creditsPercent = summary.credits_limit_milli > 0
    ? Math.min(100, Math.round((summary.credits_used_milli / summary.credits_limit_milli) * 100))
    : 0;

  return (
    <div style={styles.wrap}>
      {blocked && (
        <div className="glass-card" style={{ ...styles.card, borderColor: 'var(--error)', background: 'rgba(239, 68, 68, 0.08)' }}>
          <strong style={{ color: 'var(--error)' }}>🔒 {t('tab_billing.service_blocked')}</strong>
          <p style={{ margin: '6px 0 0', color: '#d1d5db' }}>{summary.blocked_reason || t(STATUS_LABEL[summary.billing_status])}</p>
        </div>
      )}

      <div style={styles.grid}>
        <div className="glass-card" style={styles.card}>
          <div style={styles.cardLabel}>{t('tab_billing.balance_label')}</div>
          <div style={styles.cardValue}>{formatTenge(summary.balance_minor)}</div>
          <div style={styles.cardSub}>{t('tab_billing.block_threshold')} {formatTenge(summary.block_threshold_minor)}</div>
        </div>

        <div className="glass-card" style={styles.card}>
          <div style={styles.cardLabel}>{t('tab_billing.current_plan')}</div>
          <div style={styles.cardValue}>{summary.plan_name}</div>
          <div style={styles.cardSub}>
            {summary.max_products ? t('tab_billing.up_to_products', { count: summary.max_products }) : t('tab_billing.unlimited_products')} · {formatTenge(summary.monthly_price_minor)}{t('tab_billing.per_month')}
          </div>
        </div>

        <div className="glass-card" style={styles.card}>
          <div style={styles.cardLabel}>{t('tab_billing.ai_credits_today')}</div>
          <div style={styles.cardValue}>{(summary.credits_used_milli / 1000).toFixed(1)} / {(summary.credits_limit_milli / 1000).toFixed(0)}</div>
          <div style={styles.progressTrack}>
            <div style={{ ...styles.progressFill, width: `${creditsPercent}%` }} />
          </div>
          <div style={styles.cardSub}>{t('tab_billing.prompt_multiplier')} {(summary.prompt_multiplier_milli / 1000).toFixed(1)}×</div>
        </div>
      </div>

      <div className="glass-card" style={styles.card}>
        <h3 style={styles.sectionTitle}>{t('tab_billing.tariffs_section')}</h3>
        {/* plans-grid: на телефоне превращается в карусель со свайпом
            (см. mobile.css) — пять тарифов в колонку занимали три экрана. */}
        <div className="plans-grid" style={styles.plansGrid}>
          {plans.map((plan) => (
            <div key={plan.code} className="plan-card" style={{ ...styles.planCard, ...(plan.code === summary.plan_code ? styles.planCardActive : {}) }}>
              {plan.badge && <span className="badge badge-warning" style={styles.planBadge}>{plan.badge}</span>}
              <div style={styles.planName}>{plan.name}</div>
              <div style={styles.planPrice}>{plan.monthly_price_minor != null ? `${formatTenge(plan.monthly_price_minor)}${t('tab_billing.per_month')}` : t('tab_billing.custom_price')}</div>
              <div style={styles.cardSub}>{plan.max_products ? t('tab_billing.up_to_products', { count: plan.max_products }) : t('tab_billing.unlimited_products')}</div>
              <div style={styles.cardSub}>{plan.daily_credit_limit_milli != null ? t('tab_billing.daily_ai_limit', { count: plan.daily_credit_limit_milli / 1000 }) : t('tab_billing.custom_ai_limit')}</div>
              {plan.code === summary.plan_code && <div style={styles.planCurrentTag}>{t('tab_billing.your_tariff')}</div>}
            </div>
          ))}
        </div>
        {/* Виден только на телефоне (см. .plans-swipe-hint в mobile.css). */}
        <p className="plans-swipe-hint">{t('tab_billing.scroll_hint')}</p>
      </div>

      <div className="glass-card" style={styles.card}>
        <h3 style={styles.sectionTitle}>{t('tab_billing.transactions_history')}</h3>
        {transactions.length === 0 ? (
          <div style={styles.cardSub}>{t('tab_billing.no_transactions')}</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>{t('tab_billing.table_date')}</th>
                  <th style={styles.th}>{t('tab_billing.table_type')}</th>
                  <th style={styles.th}>{t('tab_billing.table_amount')}</th>
                  <th style={styles.th}>{t('tab_billing.table_balance_after')}</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((tx) => (
                  <tr key={tx.id}>
                    <td style={styles.td}>{tx.created_at ? new Date(tx.created_at).toLocaleString('ru-RU') : '—'}</td>
                    <td style={styles.td}>{t(KIND_LABEL[tx.kind] || tx.kind)}</td>
                    <td style={{ ...styles.td, color: tx.amount_minor >= 0 ? 'var(--success)' : 'var(--error)' }}>
                      {tx.amount_minor >= 0 ? '+' : ''}{formatTenge(tx.amount_minor)}
                    </td>
                    <td style={styles.td}>{tx.balance_after_minor != null ? formatTenge(tx.balance_after_minor) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'flex', flexDirection: 'column', gap: '16px', padding: '24px' },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px' },
  card: { padding: '20px' },
  cardLabel: { fontSize: '12px', color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em' },
  cardValue: { fontSize: '24px', fontWeight: 700, margin: '6px 0', color: '#f3f4f6' },
  cardSub: { fontSize: '12px', color: '#9ca3af' },
  progressTrack: { height: '6px', borderRadius: '3px', background: 'rgba(255,255,255,0.08)', margin: '8px 0', overflow: 'hidden' },
  progressFill: { height: '100%', background: 'var(--accent, #6366f1)', borderRadius: '3px' },
  sectionTitle: { margin: '0 0 12px', fontSize: '16px', color: '#f3f4f6' },
  plansGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' },
  planCard: { position: 'relative', padding: '14px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.02)' },
  planCardActive: { borderColor: 'var(--accent, #6366f1)' },
  planBadge: { position: 'absolute', top: '10px', right: '10px', fontSize: '10px' },
  planName: { fontWeight: 600, color: '#f3f4f6', marginBottom: '4px' },
  planPrice: { fontSize: '13px', color: '#d1d5db', marginBottom: '4px' },
  planCurrentTag: { marginTop: '8px', fontSize: '11px', color: 'var(--accent, #6366f1)', fontWeight: 600 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: '13px' },
  th: { textAlign: 'left', padding: '8px', color: '#9ca3af', borderBottom: '1px solid rgba(255,255,255,0.08)' },
  td: { padding: '8px', borderBottom: '1px solid rgba(255,255,255,0.04)', color: '#e5e7eb' },
};
