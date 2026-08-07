import { useEffect, useState } from 'react';
import { apiFetch, API_URL } from '../api';

interface Prefs {
  linked: boolean;
  store_id?: string;
  chats?: boolean;
  problems?: boolean;
  money?: boolean;
  new_order_min_sum?: number | null;
  quiet_from?: string | null;
  quiet_to?: string | null;
}

const GROUPS: { key: 'chats' | 'problems' | 'money'; title: string; hint: string }[] = [
  { key: 'chats', title: '💬 Чаты', hint: 'Новые сообщения от покупателей' },
  { key: 'problems', title: '⚠️ Проблемы', hint: 'Сбои автосборки, ошибки, требующие внимания' },
  { key: 'money', title: '💰 Деньги', hint: 'Новые заказы, демпинг подвинул цену (по умолчанию выкл.)' },
];

export function NotificationSettingsPanel() {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [quietFrom, setQuietFrom] = useState('');
  const [quietTo, setQuietTo] = useState('');
  const [minSum, setMinSum] = useState('');

  const load = () => {
    setLoading(true);
    apiFetch(`${API_URL}/api/v1/telegram/notification-prefs`)
      .then(r => r.json())
      .then(d => {
        setPrefs(d);
        setQuietFrom(d.quiet_from || '');
        setQuietTo(d.quiet_to || '');
        setMinSum(d.new_order_min_sum != null ? String(d.new_order_min_sum) : '');
      })
      .catch(() => setError('Не удалось загрузить настройки уведомлений.'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const patch = (body: Record<string, unknown>, savingKey: string) => {
    setError(null);
    setSaving(savingKey);
    apiFetch(`${API_URL}/api/v1/telegram/notification-prefs`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(d => {
        if (d.success) setPrefs(prev => (prev ? { ...prev, ...d } : d));
        else setError('Не удалось сохранить настройки.');
      })
      .catch(() => setError('Не удалось сохранить настройки.'))
      .finally(() => setSaving(null));
  };

  const toggleGroup = (key: 'chats' | 'problems' | 'money') => {
    if (!prefs) return;
    patch({ [key]: !prefs[key] }, key);
  };

  const saveQuietHours = () => {
    const from = quietFrom.trim() || null;
    const to = quietTo.trim() || null;
    patch({ quiet_from: from, quiet_to: to }, 'quiet');
  };

  const saveMinSum = () => {
    const val = minSum.trim() === '' ? null : parseInt(minSum.trim(), 10);
    if (val !== null && (isNaN(val) || val < 0)) {
      setError('Порог суммы должен быть неотрицательным числом.');
      return;
    }
    patch({ new_order_min_sum: val }, 'minsum');
  };

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2>Уведомления Telegram</h2>
        <p style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
          Те же настройки, что в боте (⚙️ Настройки → 🔔 Уведомления) — изменения синхронизируются в обе стороны.
        </p>
      </div>

      {error && <div className="badge badge-warning" style={{ width: 'fit-content' }}>{error}</div>}
      {loading && <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Загрузка...</p>}

      {!loading && prefs && !prefs.linked && (
        <div className="status-widget">
          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Сначала привяжите свой Telegram к этому магазину — панель выше («Telegram-бот»).
          </p>
        </div>
      )}

      {!loading && prefs && prefs.linked && (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {GROUPS.map(g => (
              <div key={g.key} className="status-widget">
                <div>
                  <span style={{ fontWeight: 600 }}>{g.title}</span>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{g.hint}</div>
                </div>
                <button
                  className={`btn ${prefs[g.key] ? 'btn-primary' : 'btn-secondary'}`}
                  disabled={saving === g.key}
                  onClick={() => toggleGroup(g.key)}
                >
                  {saving === g.key ? '...' : prefs[g.key] ? 'Вкл' : 'Выкл'}
                </button>
              </div>
            ))}
          </div>

          <div className="status-widget" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '10px' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Порог суммы для «Деньги»</h3>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              Уведомлять о новых заказах только от этой суммы (₸). Пусто — уведомлять о любой сумме.
            </p>
            <div style={{ display: 'flex', gap: '10px' }}>
              <input
                type="number"
                min={0}
                className="form-input"
                style={{ width: '140px' }}
                placeholder="напр. 10000"
                value={minSum}
                onChange={e => setMinSum(e.target.value)}
              />
              <button className="btn btn-secondary" disabled={saving === 'minsum'} onClick={saveMinSum}>
                {saving === 'minsum' ? 'Сохранение...' : '💾 Сохранить'}
              </button>
            </div>
          </div>

          <div className="status-widget" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '10px' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Тихие часы</h3>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              В это время не-срочные уведомления копятся и приходят одним пакетом после окончания. Формат ЧЧ:ММ, поддерживается интервал через полночь.
            </p>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
              <input
                type="time"
                className="form-input"
                style={{ width: 'auto' }}
                value={quietFrom}
                onChange={e => setQuietFrom(e.target.value)}
              />
              <span style={{ color: 'var(--text-muted)' }}>—</span>
              <input
                type="time"
                className="form-input"
                style={{ width: 'auto' }}
                value={quietTo}
                onChange={e => setQuietTo(e.target.value)}
              />
              <button className="btn btn-secondary" disabled={saving === 'quiet'} onClick={saveQuietHours}>
                {saving === 'quiet' ? 'Сохранение...' : '💾 Сохранить'}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
