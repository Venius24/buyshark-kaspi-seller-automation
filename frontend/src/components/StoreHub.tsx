import React, { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { fetchProfile, fetchStores } from '../queries';
import { useActiveStoreId, setActiveStoreId as setSharedActiveStoreId } from '../storeContext';

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
  // Слайс 6: выбор магазина живёт в общем модуле (storeContext.ts), не в
  // локальном useState — переживает переключение вкладок и реально доходит
  // до apiFetch/getWsUrl через X-Store-Id/?store_id=, а не только до UI.
  const activeStoreId = useActiveStoreId() ?? '';

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

  const selectStoreContext = (storeId: string) => {
    // Слайс 6: раньше это слал POST /select, который писал расшифрованный
    // API-токен выбранного магазина в общий states/session.json на диске —
    // тот самый глобальный файл, который эта же серия слайсов выпиливает
    // как источник смешения данных между магазинами. Теперь выбор живёт
    // только в браузере (localStorage) и уходит на бэкенд заголовком
    // X-Store-Id на каждый запрос — самого запроса на "выбор" вообще не нужно.
    setSharedActiveStoreId(storeId || null);
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
      <div className="glass-card" style={styles.topContextBar}>
        <div style={styles.contextLeft}>
          <span style={{ fontSize: '20px' }}>🏪</span>
          <div>
            <div style={styles.contextLabel}>Текущий активный магазин Kaspi:</div>
            <select
              value={activeStoreId}
              onChange={(e) => selectStoreContext(e.target.value)}
              className="form-input"
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
          <div className="bs-tag" style={styles.planBadge}>
            Тариф: <strong style={{ color: 'var(--secondary)' }}>{profile.subscription.plan_type}</strong> | Подключено: {stores.length}/{profile.subscription.max_stores} магазинов
          </div>
        )}

        <button onClick={() => { resetModalState(); setShowAddModal(true); }} className="btn btn-primary">
          ➕ Подключить магазин Kaspi
        </button>
      </div>

      {/* Stores List Grid */}
      <div style={{ marginTop: '24px' }}>
        <h3 style={styles.sectionHeader}>🏬 Ваши магазины Kaspi:</h3>

        {loading ? (
          <div style={{ color: 'var(--text-muted)' }}>⏳ Загрузка списка магазинов...</div>
        ) : stores.length === 0 ? (
          <div className="glass-card" style={styles.emptyState}>
            <div style={{ fontSize: '18px', fontWeight: 600, color: '#fff' }}>🛍️ У вас пока нет подключенных магазинов Kaspi.</div>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: '8px 0 16px 0' }}>
              Подключите ваш магазин Kaspi по API-токену и email/паролю для автопечати, демпинга и заказов.
            </p>
            <button onClick={() => { resetModalState(); setShowAddModal(true); }} className="btn btn-primary">
              🚀 Подключить первый магазин Kaspi
            </button>
          </div>
        ) : (
          <div style={styles.grid}>
            {stores.map((s) => (
              <div
                key={s.id}
                className="glass-card"
                style={{
                  ...styles.card,
                  ...(s.id === activeStoreId ? styles.activeCard : {}),
                }}
              >
                <div style={styles.cardHeader}>
                  <div style={styles.storeTitle}>{s.name}</div>
                  <span className={`badge ${s.status === 'ACTIVE' ? 'badge-success' : 'badge-error'}`}>
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
                  <button onClick={() => handleVerifyStore(s.id)} className="btn btn-secondary" style={{ flex: 1, padding: '8px', fontSize: '12px' }}>
                    🔄 Проверить статус
                  </button>
                  <button onClick={() => handleSyncCookies(s)} className="btn btn-primary" style={{ padding: '8px 12px', fontSize: '12px' }}>
                    🔑 Обновить куки
                  </button>
                  <button onClick={() => handleDeleteStore(s.id)} className="btn" style={styles.deleteBtn}>
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
        <div className="modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="glass-card modal-content" style={styles.modalContent} onClick={(e) => e.stopPropagation()}>
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
                  className="form-input"
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
                  className="form-input"
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
                  className="form-input"
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
                  className="form-input"
                />
              </div>

              <div style={{ marginTop: '24px' }}>
                <button type="submit" disabled={submitting} className="btn btn-primary" style={{ width: '100%', padding: '14px 24px', fontSize: '15px' }}>
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
    color: 'var(--text-color)',
    fontFamily: 'var(--font-sans)',
  },
  topContextBar: {
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
    color: 'var(--text-muted)',
    marginBottom: '4px',
  },
  storeSelect: {
    fontWeight: 600,
    width: 'auto',
  },
  planBadge: {
    background: 'rgba(255,255,255,0.06)',
    color: '#98a2b8',
    padding: '8px 16px',
    fontSize: '13px',
  },
  sectionHeader: {
    fontSize: '18px',
    fontWeight: 700,
    marginBottom: '16px',
    color: '#ffffff',
  },
  emptyState: {
    padding: '40px',
    textAlign: 'center',
    color: 'var(--text-muted)',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
    gap: '20px',
  },
  card: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  activeCard: {
    borderColor: 'var(--primary)',
    boxShadow: '0 0 16px var(--primary-glow)',
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
  cardDetail: {
    fontSize: '13px',
    color: '#cbd5e1',
  },
  cardActions: {
    display: 'flex',
    gap: '10px',
    marginTop: '12px',
  },
  deleteBtn: {
    background: 'rgba(226, 84, 95, 0.15)',
    color: '#f3a2a8',
    border: '1px solid rgba(226, 84, 95, 0.3)',
    padding: '8px 12px',
    fontSize: '12px',
  },
  modalContent: {
    width: '520px',
    maxWidth: '92%',
    padding: 0,
    overflow: 'hidden',
  },
  modalHeader: {
    background: 'rgba(255,255,255,0.03)',
    padding: '16px 20px',
    fontSize: '16px',
    fontWeight: 700,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottom: '1px solid var(--card-border)',
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    color: 'var(--text-muted)',
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
  errorBox: {
    background: 'rgba(226, 84, 95, 0.12)',
    border: '1px solid rgba(226, 84, 95, 0.35)',
    color: '#f3a2a8',
    padding: '10px 14px',
    borderRadius: '8px',
    fontSize: '13px',
    marginBottom: '16px',
  },
};
