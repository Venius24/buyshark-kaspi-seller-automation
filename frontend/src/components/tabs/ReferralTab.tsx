import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { apiFetch, API_URL } from '../../api';
import { formatTenge } from '../../utils/money';

interface ReferralMe {
  code: string;
  commission_percent: number;
  commission_months: number;
  total_earned_minor: number;
  stores_count: number;
}

interface ReferralStoreRow {
  store_id: string;
  store_name: string;
  window_start: string;
  window_end: string;
  window_active: boolean;
  months_paid: number;
  months_total: number;
  earned_minor: number;
}

const fetchReferralMe = async (): Promise<ReferralMe | null> => {
  const res = await apiFetch(`${API_URL}/api/referral/me`);
  if (!res.ok) return null;
  return res.json();
};

const fetchReferralStores = async (): Promise<ReferralStoreRow[]> => {
  const res = await apiFetch(`${API_URL}/api/referral/stores`);
  if (!res.ok) return [];
  return res.json();
};

export const ReferralTab: React.FC = () => {
  const { t } = useTranslation('tab_referral');
  const [copied, setCopied] = useState(false);
  const { data: me } = useQuery({ queryKey: ['referral', 'me'], queryFn: fetchReferralMe });
  const { data: stores = [] } = useQuery({ queryKey: ['referral', 'stores'], queryFn: fetchReferralStores });

  if (!me) {
    return <div style={styles.wrap}><div className="glass-card" style={styles.card}>{t('loading')}</div></div>;
  }

  const link = `${window.location.origin}/?ref=${me.code}`;

  const copyLink = () => {
    navigator.clipboard.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div style={styles.wrap}>
      <div style={styles.grid}>
        <div className="glass-card" style={styles.card}>
          <div style={styles.cardLabel}>{t('your_link')}</div>
          <div style={styles.linkRow}>
            <code style={styles.linkText}>{link}</code>
            <button className="btn btn-secondary" style={styles.smallBtn} onClick={copyLink}>{copied ? t('btn_copied') : t('btn_copy')}</button>
          </div>
          <div style={styles.cardSub}>{t('code_info', { code: me.code, percent: `${me.commission_percent}%`, months: me.commission_months })}</div>
        </div>

        <div className="glass-card" style={styles.card}>
          <div style={styles.cardLabel}>{t('total_earned')}</div>
          <div style={styles.cardValue}>{formatTenge(me.total_earned_minor)}</div>
          <div style={styles.cardSub}>{t('stores_count', { count: me.stores_count })}</div>
        </div>
      </div>

      <div className="glass-card" style={styles.card}>
        <h3 style={styles.sectionTitle}>{t('referred_stores')}</h3>
        {stores.length === 0 ? (
          <div style={styles.cardSub}>{t('no_stores')}</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>{t('table_store')}</th>
                  <th style={styles.th}>{t('table_window')}</th>
                  <th style={styles.th}>{t('table_months_paid')}</th>
                  <th style={styles.th}>{t('table_earned')}</th>
                </tr>
              </thead>
              <tbody>
                {stores.map((s) => (
                  <tr key={s.store_id}>
                    <td style={styles.td}>{s.store_name}</td>
                    <td style={styles.td}>
                      <span className={`badge ${s.window_active ? 'badge-success' : 'badge-warning'}`}>
                        {s.window_active ? t('window_active') : t('window_completed')}
                      </span>
                    </td>
                    <td style={styles.td}>{s.months_paid} / {s.months_total}</td>
                    <td style={styles.td}>{formatTenge(s.earned_minor)}</td>
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
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '16px' },
  card: { padding: '20px' },
  cardLabel: { fontSize: '12px', color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em' },
  cardValue: { fontSize: '24px', fontWeight: 700, margin: '6px 0', color: '#f3f4f6' },
  cardSub: { fontSize: '12px', color: '#9ca3af', marginTop: '6px' },
  linkRow: { display: 'flex', gap: '8px', alignItems: 'center', margin: '8px 0' },
  linkText: { fontSize: '12px', color: '#d1d5db', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 },
  smallBtn: { padding: '4px 8px', fontSize: '11px', flexShrink: 0 },
  sectionTitle: { margin: '0 0 12px', fontSize: '16px', color: '#f3f4f6' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: '13px' },
  th: { textAlign: 'left', padding: '8px', color: '#9ca3af', borderBottom: '1px solid rgba(255,255,255,0.08)' },
  td: { padding: '8px', borderBottom: '1px solid rgba(255,255,255,0.04)', color: '#e5e7eb' },
};
