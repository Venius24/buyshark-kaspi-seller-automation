import React, { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { fetchProfile, fetchStores } from '../queries';

export interface StoreItem {
  id: string;
  merchant_id: string;
  name: string;
  status: 'ACTIVE' | 'COOKIE_EXPIRED' | 'PAUSED';
  role: string;
  last_sync_at: string | null;
  created_at: string;
}

export interface UserProfile {
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
  const queryClient = useQueryClient();
  const [activeStoreId, setActiveStoreId] = useState<string>('');

  // Modal State
  const [showAddModal, setShowAddModal] = useState<boolean>(false);

  // Unified Single Form States
  const [storeName, setStoreName] = useState<string>('');
  const [apiToken, setApiToken] = useState<string>('');
  const [mcEmail, setMcEmail] = useState<string>('');
  const [mcPassword, setMcPassword] = useState<string>('');

  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const { data: profile } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: fetchProfile,
    refetchInterval: 30_000,
  });

  const { data: stores = [], isLoading: loading } = useQuery({
    queryKey: ['stores'],
    queryFn: fetchStores,
    refetchInterval: 30_000,
  });

  const refetchStoresAndProfile = () => {
    queryClient.invalidateQueries({ queryKey: ['stores'] });
    queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
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

  // Auto-select the first store once the list loads, if nothing selected yet.
  useEffect(() => {
    if (stores.length > 0 && !activeStoreId) {
      selectStoreContext(stores[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stores]);

  // Unified Form Submit: API Token (Public REST) + Email/Password (Kaspi MC login)
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

    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/connect-full-store`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: storeName.trim(),
          api_token: apiToken.trim(),
          email: mcEmail.trim() || undefined,
          password: mcPassword || undefined,
        }),
      });

      if (!res.ok) {
        const errJson = await res.json();
        throw new Error(errJson.detail || 'Ошибка подключения магазина');
      }

      setShowAddModal(false);
      resetModalState();
      refetchStoresAndProfile();
    } catch (err: any) {
      setErrorMsg(err.message || 'Ошибка подключения магазина');
    } finally {
      setSubmitting(false);
    }
  };

  const resetModalState = () => {
    setStoreName('');
    setApiToken('');
    setMcEmail('');
    setMcPassword('');
    setErrorMsg(null);
  };

  const handleVerifyStore = async (storeId: string) => {
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/${storeId}/verify`, {
        method: 'POST',
      });
      if (res.ok) {
        refetchStoresAndProfile();
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
        refetchStoresAndProfile();
      } else {
        const errData = await res.json();
        alert(`⚠️ ${errData.detail || 'Требуется повторный вход в Kaspi MC'}. Перейдите во вкладку «Подключения & Сессия» и нажмите «Войти в Kaspi MC».`);
      }
    } catch (err) {
      console.error('Error syncing store cookies:', err);
      alert('⚠️ Ошибка синхронизации кук. Перейдите во вкладку «Подключения & Сессия» и войдите в Kaspi MC заново.');
    }
  };

  const handleDeleteStore = async (storeId: string) => {
    if (!window.confirm('Вы уверены, что хотите удалить этот магазин?')) return;
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/${storeId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        refetchStoresAndProfile();
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
              Подключите ваш магазин Kaspi по API-токену и email/паролю для автопечати, демпинга и заказов.
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

              {/* Field 3: Kaspi MC Email (optional — needed for pricing/stock/orders/chat) */}
              <div style={styles.formGroup}>
                <label style={styles.label}>3. Email от личного кабинета Kaspi Merchant (для демпинга, остатков и заказов):</label>
                <input
                  type="email"
                  placeholder="you@example.com"
                  value={mcEmail}
                  onChange={(e) => setMcEmail(e.target.value)}
                  style={styles.input}
                />
              </div>

              {/* Field 4: Kaspi MC Password */}
              <div style={styles.formGroup}>
                <label style={styles.label}>4. Пароль от личного кабинета Kaspi Merchant:</label>
                <input
                  type="password"
                  placeholder="••••••••"
                  value={mcPassword}
                  onChange={(e) => setMcPassword(e.target.value)}
                  style={styles.input}
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
  errorBox: {
    background: '#451a1a',
    border: '1px solid #991b1b',
    color: '#fca5a5',
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
