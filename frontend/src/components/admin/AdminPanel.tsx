import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../../api';
import { formatTenge } from '../../utils/money';
import type { BillingPlan } from '../../types';
import { fetchBillingPlans } from '../../queries';

interface AdminStoreRow {
  store_id: string;
  name: string;
  merchant_id: string;
  owner_email: string | null;
  plan_code: string;
  plan_name: string;
  billing_status: string;
  balance_minor: number;
  credit_addon_code: string;
  next_charge_at: string | null;
  referrer_user_id: string | null;
}

interface ReferrerRow {
  user_id: string;
  email: string;
  code: string;
  commission_percent: number;
  commission_months: number;
  is_active: boolean;
}

const fetchAdminStores = async (): Promise<AdminStoreRow[]> => {
  const res = await apiFetch(`${API_URL}/api/admin/stores`);
  if (!res.ok) return [];
  return res.json();
};

const fetchReferrers = async (): Promise<ReferrerRow[]> => {
  const res = await apiFetch(`${API_URL}/api/admin/referrers`);
  if (!res.ok) return [];
  return res.json();
};

type SubTab = 'stores' | 'referrers';

export const AdminPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const [subTab, setSubTab] = useState<SubTab>('stores');
  const [tariffModalStore, setTariffModalStore] = useState<AdminStoreRow | null>(null);
  const [topupModalStore, setTopupModalStore] = useState<AdminStoreRow | null>(null);
  const [referrerModalStore, setReferrerModalStore] = useState<AdminStoreRow | null>(null);

  const { data: stores = [], isLoading } = useQuery({ queryKey: ['admin', 'stores'], queryFn: fetchAdminStores, refetchInterval: 30_000 });
  const { data: referrers = [] } = useQuery({ queryKey: ['admin', 'referrers'], queryFn: fetchReferrers });
  const { data: plans = [] } = useQuery({ queryKey: ['billing', 'plans'], queryFn: fetchBillingPlans, staleTime: Infinity });

  const invalidateStores = () => queryClient.invalidateQueries({ queryKey: ['admin', 'stores'] });

  const reevaluate = async (storeId: string) => {
    await apiFetch(`${API_URL}/api/admin/stores/${storeId}/reevaluate`, { method: 'POST' });
    invalidateStores();
  };

  const chargeNow = async (storeId: string) => {
    await apiFetch(`${API_URL}/api/admin/stores/${storeId}/charge-now`, { method: 'POST' });
    invalidateStores();
  };

  return (
    <div style={styles.wrap}>
      <div style={styles.subTabs}>
        <button className={`btn ${subTab === 'stores' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('stores')}>Магазины</button>
        <button className={`btn ${subTab === 'referrers' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('referrers')}>Рефереры</button>
      </div>

      {subTab === 'stores' && (
        <div className="glass-card" style={styles.card}>
          {isLoading ? <div>Загрузка...</div> : (
            <div style={{ overflowX: 'auto' }}>
              <table style={styles.table}>
                <thead>
                  <tr>
                    <th style={styles.th}>Магазин</th>
                    <th style={styles.th}>Владелец</th>
                    <th style={styles.th}>Тариф</th>
                    <th style={styles.th}>Статус</th>
                    <th style={styles.th}>Баланс</th>
                    <th style={styles.th}>Реферер</th>
                    <th style={styles.th}>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {stores.map((s) => (
                    <tr key={s.store_id}>
                      <td style={styles.td}>{s.name}</td>
                      <td style={styles.td}>{s.owner_email || '—'}</td>
                      <td style={styles.td}>{s.plan_name}</td>
                      <td style={styles.td}>
                        <span className={`badge ${s.billing_status === 'ACTIVE' ? 'badge-success' : 'badge-error'}`}>{s.billing_status}</span>
                      </td>
                      <td style={{ ...styles.td, color: s.balance_minor < 0 ? 'var(--error)' : '#e5e7eb' }}>{formatTenge(s.balance_minor)}</td>
                      <td style={styles.td}>{s.referrer_user_id ? (referrers.find(r => r.user_id === s.referrer_user_id)?.email || s.referrer_user_id) : '—'}</td>
                      <td style={styles.td}>
                        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                          <button className="btn btn-secondary" style={styles.smallBtn} onClick={() => setTariffModalStore(s)}>Тариф</button>
                          <button className="btn btn-secondary" style={styles.smallBtn} onClick={() => setTopupModalStore(s)}>Пополнить</button>
                          <button className="btn btn-secondary" style={styles.smallBtn} onClick={() => setReferrerModalStore(s)}>Реферер</button>
                          <button className="btn btn-secondary" style={styles.smallBtn} onClick={() => reevaluate(s.store_id)}>Пересчитать</button>
                          <button className="btn btn-secondary" style={styles.smallBtn} onClick={() => chargeNow(s.store_id)}>Списать сейчас</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {subTab === 'referrers' && (
        <div className="glass-card" style={styles.card}>
          <CreateReferrerForm onCreated={() => queryClient.invalidateQueries({ queryKey: ['admin', 'referrers'] })} />
          <div style={{ overflowX: 'auto', marginTop: '16px' }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Email</th>
                  <th style={styles.th}>Код</th>
                  <th style={styles.th}>%</th>
                  <th style={styles.th}>Месяцев</th>
                  <th style={styles.th}>Активен</th>
                </tr>
              </thead>
              <tbody>
                {referrers.map((r) => (
                  <tr key={r.user_id}>
                    <td style={styles.td}>{r.email}</td>
                    <td style={styles.td}><code>{r.code}</code></td>
                    <td style={styles.td}>{r.commission_percent}%</td>
                    <td style={styles.td}>{r.commission_months}</td>
                    <td style={styles.td}>{r.is_active ? 'Да' : 'Нет'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tariffModalStore && (
        <TariffModal
          store={tariffModalStore}
          plans={plans}
          onClose={() => setTariffModalStore(null)}
          onSaved={() => { setTariffModalStore(null); invalidateStores(); }}
        />
      )}

      {topupModalStore && (
        <TopupModal
          store={topupModalStore}
          onClose={() => setTopupModalStore(null)}
          onSaved={() => { setTopupModalStore(null); invalidateStores(); }}
        />
      )}

      {referrerModalStore && (
        <ReferrerAssignModal
          store={referrerModalStore}
          referrers={referrers}
          onClose={() => setReferrerModalStore(null)}
          onSaved={() => { setReferrerModalStore(null); invalidateStores(); }}
        />
      )}
    </div>
  );
};

const CreateReferrerForm: React.FC<{ onCreated: () => void }> = ({ onCreated }) => {
  const [email, setEmail] = useState('');
  const [commissionPercent, setCommissionPercent] = useState('50');
  const [commissionMonths, setCommissionMonths] = useState('3');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!email.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const lookupRes = await apiFetch(`${API_URL}/api/admin/users/lookup?email=${encodeURIComponent(email.trim())}`);
      if (!lookupRes.ok) {
        const body = await lookupRes.json().catch(() => null);
        setError(body?.detail || 'Пользователь не найден');
        return;
      }
      const { user_id } = await lookupRes.json();
      const createRes = await apiFetch(`${API_URL}/api/admin/referrers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id,
          commission_percent: parseInt(commissionPercent, 10) || 50,
          commission_months: parseInt(commissionMonths, 10) || 3,
        }),
      });
      if (!createRes.ok) {
        const body = await createRes.json().catch(() => null);
        setError(body?.detail || 'Не удалось создать реферера');
        return;
      }
      setEmail('');
      onCreated();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
      <div>
        <div style={styles.formLabel}>Email пользователя</div>
        <input className="form-input" placeholder="friend@example.com" value={email} onChange={(e) => setEmail(e.target.value)} style={{ width: '240px' }} />
      </div>
      <div>
        <div style={styles.formLabel}>Комиссия, %</div>
        <input className="form-input" type="number" value={commissionPercent} onChange={(e) => setCommissionPercent(e.target.value)} style={{ width: '90px' }} />
      </div>
      <div>
        <div style={styles.formLabel}>Месяцев</div>
        <input className="form-input" type="number" value={commissionMonths} onChange={(e) => setCommissionMonths(e.target.value)} style={{ width: '90px' }} />
      </div>
      <button className="btn btn-primary" disabled={saving || !email.trim()} onClick={submit}>Добавить реферера</button>
      {error && <div style={{ color: 'var(--error)', fontSize: '13px' }}>{error}</div>}
    </div>
  );
};

const ReferrerAssignModal: React.FC<{ store: AdminStoreRow; referrers: ReferrerRow[]; onClose: () => void; onSaved: () => void }> = ({ store, referrers, onClose, onSaved }) => {
  const [referrerUserId, setReferrerUserId] = useState(store.referrer_user_id || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const assign = async () => {
    if (!referrerUserId) return;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`${API_URL}/api/admin/stores/${store.store_id}/referrer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ referrer_user_id: referrerUserId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(body?.detail || 'Не удалось привязать реферера');
        return;
      }
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setSaving(true);
    setError(null);
    try {
      await apiFetch(`${API_URL}/api/admin/stores/${store.store_id}/referrer`, { method: 'DELETE' });
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '420px' }}>
        <h3>Реферер для «{store.name}»</h3>
        {referrers.length === 0 ? (
          <div style={{ color: '#9ca3af', fontSize: '13px' }}>Сначала добавьте реферера на вкладке «Рефереры».</div>
        ) : (
          <select className="form-input" value={referrerUserId} onChange={(e) => setReferrerUserId(e.target.value)}>
            <option value="">— выбрать —</option>
            {referrers.map((r) => <option key={r.user_id} value={r.user_id}>{r.email} ({r.code})</option>)}
          </select>
        )}
        {error && <div style={{ color: 'var(--error)', fontSize: '13px', marginTop: '8px' }}>{error}</div>}
        <div style={{ display: 'flex', gap: '8px', marginTop: '16px', justifyContent: 'space-between' }}>
          {store.referrer_user_id ? (
            <button className="btn btn-secondary" disabled={saving} onClick={remove}>Отвязать</button>
          ) : <span />}
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
            <button className="btn btn-primary" disabled={saving || !referrerUserId} onClick={assign}>Сохранить</button>
          </div>
        </div>
      </div>
    </div>
  );
};

const TariffModal: React.FC<{ store: AdminStoreRow; plans: BillingPlan[]; onClose: () => void; onSaved: () => void }> = ({ store, plans, onClose, onSaved }) => {
  const [planCode, setPlanCode] = useState(store.plan_code);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    try {
      await apiFetch(`${API_URL}/api/admin/stores/${store.store_id}/tariff`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_code: planCode }),
      });
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '420px' }}>
        <h3>Тариф для «{store.name}»</h3>
        <select className="form-input" value={planCode} onChange={(e) => setPlanCode(e.target.value)}>
          {plans.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
          <option value="DEV">DEV</option>
        </select>
        <div style={{ display: 'flex', gap: '8px', marginTop: '16px', justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
          <button className="btn btn-primary" disabled={saving} onClick={submit}>Сохранить</button>
        </div>
      </div>
    </div>
  );
};

const TopupModal: React.FC<{ store: AdminStoreRow; onClose: () => void; onSaved: () => void }> = ({ store, onClose, onSaved }) => {
  const [amount, setAmount] = useState('');
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [idempotencyKey] = useState(() => crypto.randomUUID());

  const submit = async () => {
    const amountMinor = Math.round(parseFloat(amount) * 100);
    if (!amountMinor || amountMinor <= 0) return;
    setSaving(true);
    try {
      await apiFetch(`${API_URL}/api/admin/stores/${store.store_id}/topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount_minor: amountMinor, idempotency_key: idempotencyKey, comment }),
      });
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '420px' }}>
        <h3>Пополнить «{store.name}»</h3>
        <input className="form-input" type="number" placeholder="Сумма, ₸" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <input className="form-input" style={{ marginTop: '8px' }} placeholder="Комментарий (необязательно)" value={comment} onChange={(e) => setComment(e.target.value)} />
        <div style={{ display: 'flex', gap: '8px', marginTop: '16px', justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
          <button className="btn btn-primary" disabled={saving} onClick={submit}>Пополнить</button>
        </div>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'flex', flexDirection: 'column', gap: '16px', padding: '24px' },
  subTabs: { display: 'flex', gap: '8px' },
  card: { padding: '20px' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: '13px' },
  th: { textAlign: 'left', padding: '8px', color: '#9ca3af', borderBottom: '1px solid rgba(255,255,255,0.08)' },
  td: { padding: '8px', borderBottom: '1px solid rgba(255,255,255,0.04)', color: '#e5e7eb' },
  smallBtn: { padding: '4px 8px', fontSize: '11px' },
  formLabel: { fontSize: '11px', color: '#9ca3af', marginBottom: '4px' },
};
