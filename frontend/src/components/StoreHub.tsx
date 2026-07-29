import React, { useState, useEffect } from 'react';
import { apiFetch, API_URL } from '../api';

interface StoreItem {
  id: string;
  merchant_id: string;
  name: string;
  status: 'ACTIVE' | 'COOKIE_EXPIRED' | 'PAUSED';
  role: string;
  last_sync_at: string | null;
  created_at: string;
}

interface UserProfile {
  user: {
    id: string;
    email: string;
    full_name: string;
  };
  subscription: {
    plan_type: string;
    max_stores: number;
    max_skus_in_auto_price: number;
    valid_until: string;
  };
}

export const StoreHub: React.FC = () => {
  const [stores, setStores] = useState<StoreItem[]>([]);
  const [activeStoreId, setActiveStoreId] = useState<string>('');
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  // Modal State
  const [showAddModal, setShowAddModal] = useState<boolean>(false);

  // Unified Single Form States
  const [storeName, setStoreName] = useState<string>('');
  const [apiToken, setApiToken] = useState<string>('');
  const [phone, setPhone] = useState<string>('+7');
  const [smsCode, setSmsCode] = useState<string>('');
  const [smsSent, setSmsSent] = useState<boolean>(false);

  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const fetchStoresAndProfile = async () => {
    setLoading(true);
    try {
      // Profile
      const pRes = await apiFetch(`${API_URL}/api/v1/auth/me`);
      if (pRes.ok) {
        const pData = await pRes.json();
        setProfile(pData);
      }

      // Stores
      const sRes = await apiFetch(`${API_URL}/api/v1/stores`);
      if (sRes.ok) {
        const sData: StoreItem[] = await sRes.json();
        setStores(sData);
        if (sData.length > 0 && !activeStoreId) {
          selectStoreContext(sData[0].id);
        }
      }
    } catch (err) {
      console.error('Error loading Store Hub:', err);
    } finally {
      setLoading(false);
    }
  };

  const selectStoreContext = async (storeId: string) => {
    setActiveStoreId(storeId);
    if (!storeId) return;
    try {
      await apiFetch(`${API_URL}/api/v1/stores/${storeId}/select`, {
        method: 'POST',
      });
    } catch (err) {
      console.error('Error selecting store context:', err);
    }
  };

  useEffect(() => {
    fetchStoresAndProfile();
  }, []);

  // Step 1: Request SMS Code
  const handleStartSms = async () => {
    if (!phone.trim() || phone.trim().length < 6) {
      setErrorMsg('Укажите корректный номер телефона (+7...)');
      return;
    }

    setSubmitting(true);
    setErrorMsg(null);
    setSuccessMsg(null);

    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/start-sms-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone.trim() }),
      });

      if (!res.ok) {
        const errJson = await res.json();
        throw new Error(errJson.detail || 'Ошибка отправки SMS');
      }

      setSmsSent(true);
      setSuccessMsg('SMS-код успешно отправлен на ваш номер телефона!');
    } catch (err: any) {
      setErrorMsg(err.message || 'Ошибка связи с бэкендом');
    } finally {
      setSubmitting(false);
    }
  };

  // Step 2: Unified Form Submit (API Token + SMS Code)
  const handleConnectFullStore = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!storeName.trim()) {
      setErrorMsg('Укажите название магазина (напр. Barkhan)');
      return;
    }
    if (!apiToken.trim()) {
      setErrorMsg('Введите API Токен (X-Auth-Token) из кабинета Kaspi');
      return;
    }

    setSubmitting(true);
    setErrorMsg(null);
    setSuccessMsg(null);

    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/connect-full-store`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: storeName.trim(),
          api_token: apiToken.trim(),
          phone: phone.trim(),
          sms: smsCode.trim(),
        }),
      });

      if (!res.ok) {
        const errJson = await res.json();
        throw new Error(errJson.detail || 'Ошибка подключения магазина');
      }

      setShowAddModal(false);
      resetModalState();
      fetchStoresAndProfile();
    } catch (err: any) {
      setErrorMsg(err.message || 'Ошибка подключения магазина');
    } finally {
      setSubmitting(false);
    }
  };

  const resetModalState = () => {
    setStoreName('');
    setApiToken('');
    setPhone('+7');
    setSmsCode('');
    setSmsSent(false);
    setErrorMsg(null);
    setSuccessMsg(null);
  };

  const handleVerifyStore = async (storeId: string) => {
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/${storeId}/verify`, {
        method: 'POST',
      });
      if (res.ok) {
        fetchStoresAndProfile();
      }
    } catch (err) {
      console.error('Error verifying store:', err);
    }
  };

  const handleSyncCookies = async (store: StoreItem) => {
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/${store.id}/sync-cookies`, {
        method: 'POST',
      });
      if (res.ok) {
        alert('✅ Куки успешно синхронизированы с магазином!');
        fetchStoresAndProfile();
      } else {
        const errData = await res.json();
        // Предзаполняем форму для быстрого SMS-входа без повторного ввода API Токена
        resetModalState();
        setStoreName(store.name);
        setApiToken('PRESERVE_EXISTING');
        setErrorMsg(`⚠️ ${errData.detail || 'Требуется SMS-авторизация'}. Введите номер телефона и нажмите «Отправить СМС».`);
        setShowAddModal(true);
      }
    } catch (err) {
      console.error('Error syncing store cookies:', err);
      resetModalState();
      setStoreName(store.name);
      setApiToken('PRESERVE_EXISTING');
      setErrorMsg('⚠️ Ошибка синхронизации. Введите номер телефона и нажмите «Отправить СМС» для входа.');
      setShowAddModal(true);
    }
  };

  const handleDeleteStore = async (storeId: string) => {
    if (!window.confirm('Вы уверены, что хотите удалить этот магазин?')) return;
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/${storeId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        fetchStoresAndProfile();
      }
    } catch (err) {
      console.error('Error deleting store:', err);
    }
  };

  return (
    <div style={styles.hubContainer}>
      {/* Header Context Switcher Bar */}
      <div style={styles.topContextBar}>
        <div style={styles.contextLeft}>
          <span style={{ fontSize: '20px' }}>🏪</span>
          <div>
            <div style={styles.contextLabel}>Текущий активный магазин Kaspi:</div>
            <select
              value={activeStoreId}
              onChange={(e) => selectStoreContext(e.target.value)}
              style={styles.storeSelect}
            >
              {stores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.merchant_id}) — {s.status === 'ACTIVE' ? '🟢 Активен' : '🔴 Требуется вход'}
                </option>
              ))}
              {stores.length === 0 && <option value="">(Магазины не подключены)</option>}
            </select>
          </div>
        </div>

        {profile && (
          <div style={styles.planBadge}>
            Тариф: <strong style={{ color: '#6366f1' }}>{profile.subscription.plan_type}</strong> | Подключено: {stores.length}/{profile.subscription.max_stores} магазинов
          </div>
        )}

        <button onClick={() => { resetModalState(); setShowAddModal(true); }} style={styles.addStoreBtn}>
          ➕ Подключить магазин Kaspi
        </button>
      </div>

      {/* Stores List Grid */}
      <div style={{ marginTop: '24px' }}>
        <h3 style={styles.sectionHeader}>🏬 Ваши магазины Kaspi:</h3>

        {loading ? (
          <div style={{ color: '#94a3b8' }}>⏳ Загрузка списка магазинов...</div>
        ) : stores.length === 0 ? (
          <div style={styles.emptyState}>
            <div style={{ fontSize: '18px', fontWeight: 600, color: '#f8fafc' }}>🛍️ У вас пока нет подключенных магазинов Kaspi.</div>
            <p style={{ fontSize: '13px', color: '#94a3b8', margin: '8px 0 16px 0' }}>
              Подключите ваш магазин Kaspi по API-току и СМС для автопечати, демпинга и заказов.
            </p>
            <button onClick={() => { resetModalState(); setShowAddModal(true); }} style={styles.addStoreBtn}>
              🚀 Подключить первый магазин Kaspi
            </button>
          </div>
        ) : (
          <div style={styles.grid}>
            {stores.map((s) => (
              <div
                key={s.id}
                style={{
                  ...styles.card,
                  ...(s.id === activeStoreId ? styles.activeCard : {}),
                }}
              >
                <div style={styles.cardHeader}>
                  <div style={styles.storeTitle}>{s.name}</div>
                  <span
                    style={{
                      ...styles.statusBadge,
                      ...(s.status === 'ACTIVE' ? styles.statusActive : styles.statusExpired),
                    }}
                  >
                    {s.status === 'ACTIVE' ? '🟢 Подключен' : '🔴 Требуется вход'}
                  </span>
                </div>

                <div style={styles.cardDetail}>
                  <span>Merchant ID:</span> <code>{s.merchant_id}</code>
                </div>
                <div style={styles.cardDetail}>
                  <span>Доступ:</span> <strong>{s.role}</strong>
                </div>
                <div style={styles.cardDetail}>
                  <span>Последний доступ:</span> {s.last_sync_at ? new Date(s.last_sync_at).toLocaleString('ru') : 'Не проводился'}
                </div>

                <div style={styles.cardActions}>
                  <button onClick={() => handleVerifyStore(s.id)} style={styles.verifyBtn}>
                    🔄 Проверить статус
                  </button>
                  <button onClick={() => handleSyncCookies(s)} style={styles.syncBtn}>
                    🔑 Обновить куки
                  </button>
                  <button onClick={() => handleDeleteStore(s.id)} style={styles.deleteBtn}>
                    🗑️ Удалить
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Onboarding Wizard Modal — Single Unified Form */}
      {showAddModal && (
        <div style={styles.modalOverlay} onClick={() => setShowAddModal(false)}>
          <div style={styles.modalContent} onClick={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <span>✨ Подключение магазина Kaspi</span>
              <button onClick={() => setShowAddModal(false)} style={styles.closeBtn}>
                ✖
              </button>
            </div>

            <form onSubmit={handleConnectFullStore} style={{ padding: '20px' }}>
              {errorMsg && <div style={styles.errorBox}>⚠️ {errorMsg}</div>}
              {successMsg && <div style={styles.successBox}>✅ {successMsg}</div>}

              {/* Field 1: Store Name */}
              <div style={styles.formGroup}>
                <label style={styles.label}>1. Название магазина (например: Barkhan):</label>
                <input
                  type="text"
                  placeholder="Barkhan"
                  value={storeName}
                  onChange={(e) => setStoreName(e.target.value)}
                  style={styles.input}
                  required
                />
              </div>

              {/* Field 2: Kaspi Public API Token */}
              <div style={styles.formGroup}>
                <label style={styles.label}>2. API Токен Kaspi (X-Auth-Token из Кабинета Kaspi ➔ Настройки ➔ API):</label>
                <input
                  type="text"
                  placeholder="Вставьте ваш токен из кабинета Kaspi"
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  style={styles.input}
                  required
                />
              </div>

              {/* Field 3: SMS Phone Number */}
              <div style={styles.formGroup}>
                <label style={styles.label}>3. Номер телефона личного кабинета Kaspi Pay / Merchant:</label>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <input
                    type="text"
                    placeholder="+7 (707) 000-0000"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    style={{ ...styles.input, flex: 1 }}
                  />
                  <button
                    type="button"
                    onClick={handleStartSms}
                    disabled={submitting}
                    style={styles.smsBtn}
                  >
                    {submitting ? '⏳...' : '📲 Отправить СМС'}
                  </button>
                </div>
              </div>

              {/* Field 4: SMS Code (if sent or optional) */}
              <div style={styles.formGroup}>
                <label style={styles.label}>
                  4. СМС-код от Kaspi {smsSent ? <span style={{ color: '#34d399', fontWeight: 600 }}> (🟢 СМС отправлен на номер)</span> : ''}:
                </label>
                <input
                  type="text"
                  placeholder="1234"
                  value={smsCode}
                  onChange={(e) => setSmsCode(e.target.value)}
                  style={{ ...styles.input, fontSize: '18px', letterSpacing: '4px', textAlign: 'center' }}
                />
              </div>

              <div style={{ marginTop: '24px' }}>
                <button type="submit" disabled={submitting} style={styles.submitBtn}>
                  {submitting ? '⏳ Подключение магазина и проверка данных...' : '🚀 Подключить магазин Kaspi'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  hubContainer: {
    color: '#f8fafc',
    fontFamily: 'Inter, system-ui, sans-serif',
  },
  topContextBar: {
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: '14px',
    padding: '16px 24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: '16px',
  },
  contextLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
  },
  contextLabel: {
    fontSize: '12px',
    color: '#94a3b8',
    marginBottom: '4px',
  },
  storeSelect: {
    background: '#0f172a',
    color: '#f8fafc',
    border: '1px solid #475569',
    borderRadius: '8px',
    padding: '8px 12px',
    fontSize: '14px',
    fontWeight: 600,
    outline: 'none',
  },
  planBadge: {
    background: '#0f172a',
    border: '1px solid #334155',
    padding: '8px 16px',
    borderRadius: '8px',
    fontSize: '13px',
    color: '#cbd5e1',
  },
  addStoreBtn: {
    background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)',
    color: '#ffffff',
    border: 'none',
    borderRadius: '10px',
    padding: '10px 20px',
    fontSize: '14px',
    fontWeight: 600,
    cursor: 'pointer',
    boxShadow: '0 4px 12px rgba(99, 102, 241, 0.3)',
  },
  sectionHeader: {
    fontSize: '18px',
    fontWeight: 700,
    marginBottom: '16px',
    color: '#ffffff',
  },
  emptyState: {
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: '14px',
    padding: '40px',
    textAlign: 'center',
    color: '#94a3b8',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
    gap: '20px',
  },
  card: {
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: '14px',
    padding: '20px',
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  activeCard: {
    borderColor: '#6366f1',
    boxShadow: '0 0 16px rgba(99, 102, 241, 0.25)',
  },
  cardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  storeTitle: {
    fontSize: '17px',
    fontWeight: 700,
    color: '#ffffff',
  },
  statusBadge: {
    padding: '4px 10px',
    borderRadius: '6px',
    fontSize: '12px',
    fontWeight: 600,
  },
  statusActive: {
    background: '#064e3b',
    color: '#6ee7b7',
  },
  statusExpired: {
    background: '#451a1a',
    color: '#fca5a5',
  },
  cardDetail: {
    fontSize: '13px',
    color: '#cbd5e1',
  },
  cardActions: {
    display: 'flex',
    gap: '10px',
    marginTop: '12px',
  },
  verifyBtn: {
    flex: 1,
    background: '#334155',
    color: '#f8fafc',
    border: 'none',
    borderRadius: '8px',
    padding: '8px',
    fontSize: '12px',
    fontWeight: 600,
    cursor: 'pointer',
  },
  syncBtn: {
    background: '#2563eb',
    color: '#ffffff',
    border: 'none',
    borderRadius: '8px',
    padding: '8px 12px',
    fontSize: '12px',
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
  deleteBtn: {
    background: 'rgba(239, 68, 68, 0.15)',
    color: '#fca5a5',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: '8px',
    padding: '8px 12px',
    fontSize: '12px',
    cursor: 'pointer',
  },
  modalOverlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'rgba(0,0,0,0.8)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 9999,
  },
  modalContent: {
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: '16px',
    width: '520px',
    maxWidth: '92%',
    overflow: 'hidden',
  },
  modalHeader: {
    background: '#1e293b',
    padding: '16px 20px',
    fontSize: '16px',
    fontWeight: 700,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    color: '#94a3b8',
    fontSize: '18px',
    cursor: 'pointer',
  },
  formGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
    marginBottom: '16px',
  },
  label: {
    fontSize: '13px',
    fontWeight: 600,
    color: '#cbd5e1',
  },
  input: {
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: '8px',
    padding: '10px 14px',
    color: '#f8fafc',
    fontSize: '14px',
    outline: 'none',
  },
  smsBtn: {
    background: '#3b82f6',
    color: '#ffffff',
    border: 'none',
    borderRadius: '8px',
    padding: '10px 16px',
    fontSize: '13px',
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
  errorBox: {
    background: '#451a1a',
    border: '1px solid #991b1b',
    color: '#fca5a5',
    padding: '10px 14px',
    borderRadius: '8px',
    fontSize: '13px',
    marginBottom: '16px',
  },
  successBox: {
    background: '#064e3b',
    border: '1px solid #059669',
    color: '#6ee7b7',
    padding: '10px 14px',
    borderRadius: '8px',
    fontSize: '13px',
    marginBottom: '16px',
  },
  submitBtn: {
    width: '100%',
    background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
    color: '#ffffff',
    border: 'none',
    borderRadius: '10px',
    padding: '14px 24px',
    fontSize: '15px',
    fontWeight: 700,
    cursor: 'pointer',
    boxShadow: '0 4px 14px rgba(16, 185, 129, 0.4)',
  },
};
