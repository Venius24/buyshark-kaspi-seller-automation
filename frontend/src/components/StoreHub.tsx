import React, { useState, useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Sparkles,
  AlertTriangle,
  CheckCircle2,
  ShieldAlert,
  Store,
  Plus,
  Loader2,
  Rocket,
  XCircle,
  RefreshCw,
  KeyRound,
  Trash2,
  X,
  Send,
} from 'lucide-react';
import { apiFetch, API_URL } from '../api';
import { fetchProfile, fetchStores } from '../queries';
import { useActiveStoreId, setActiveStoreId as setSharedActiveStoreId } from '../storeContext';
import { waLink } from '../data/contacts';
import emptyStateIllustration from '../assets/sprites/storehub_empty_illustration.png';

export interface StoreItem {
  id: string;
  merchant_id: string;
  name: string;
  status: 'ACTIVE' | 'COOKIE_EXPIRED' | 'PAUSED';
  role: string;
  /** Разделы кабинета, доступные текущему пользователю в этом магазине
   *  (ключи совпадают с ActiveTab). Заполняет только списочный GET
   *  /api/v1/stores; null = «не рассчитано», гейт не применяем. */
  sections?: string[] | null;
  job_title?: string | null;
  last_sync_at: string | null;
  created_at: string;
}

export interface UserProfile {
  user: {
    id: string;
    email: string;
    full_name: string;
    is_superadmin?: boolean;
    is_referrer?: boolean;
    must_change_password?: boolean;
    onboarding_completed?: boolean;
  };
  subscription: {
    plan_type: string;
    max_stores: number;
    max_skus_in_auto_price: number;
    valid_until: string;
  };
}

export const StoreHub: React.FC = () => {
  const { t } = useTranslation('component_store_hub');
  const queryClient = useQueryClient();
  // Слайс 6: выбор магазина живёт в общем модуле (storeContext.ts), не в
  // локальном useState — переживает переключение вкладок и реально доходит
  // до apiFetch/getWsUrl через X-Store-Id/?store_id=, а не только до UI.
  const activeStoreId = useActiveStoreId() ?? '';

  // Modal State
  const [showAddModal, setShowAddModal] = useState<boolean>(false);

  const [toastMessage, setToastMessage] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);
  const showToast = (type: 'success' | 'error' | 'warning', text: string) => {
    setToastMessage({ type, text });
    setTimeout(() => setToastMessage(null), 4000);
  };

  // Подключение магазина — только "Сделайте всё за меня": SMS владельца, дальше
  // сервис сам заводит себе сотрудника на @buyshark.kz (HANDOFF §3.94). Ручной
  // ввод токена/email/пароля убран 16.09.2026: код подтверждения Kaspi на чужую
  // почту мы забрать не можем. Бэкенд пока отдаёт 503 (AUTO_CONNECT_ENABLED).
  const [ownerPhone, setOwnerPhone] = useState<string>('');
  const [smsStep, setSmsStep] = useState<'phone' | 'code' | 'merchant' | 'progress'>('phone');
  const [smsCode, setSmsCode] = useState<string>('');
  const [merchants, setMerchants] = useState<string[]>([]);
  const [jobStep, setJobStep] = useState<string>('prepare');
  const [autoSubmitting, setAutoSubmitting] = useState<boolean>(false);
  const [autoErrorMsg, setAutoErrorMsg] = useState<string | null>(null);
  // Опрос фоновой задачи подключения обрывается, если модалку закрыли или
  // начали заново — иначе старый цикл продолжил бы писать в новое состояние.
  const pollGeneration = useRef(0);

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

  // Самолечение унаследованного store_id: на общем браузере логаут раньше не
  // чистил active_store_id, и следующий вошедший юзер получал чужой store_id
  // из localStorage — get_store_context вечно 401'ил на все ручки без единого
  // видимого сообщения (фронт молча показывал везде "0"). Как только список
  // СВОИХ магазинов пришёл, если сохранённый id в нём не найден — переключаем
  // на первый доступный (тот же дефолт, что и на бэкенде при пустом X-Store-Id).
  useEffect(() => {
    if (loading || stores.length === 0) return;
    if (activeStoreId && stores.some((s) => s.id === activeStoreId)) return;
    setSharedActiveStoreId(stores[0].id);
  }, [loading, stores, activeStoreId]);

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

  const resetModalState = () => {
    pollGeneration.current += 1;
    setOwnerPhone('');
    setSmsStep('phone');
    setSmsCode('');
    setMerchants([]);
    setJobStep('prepare');
    setAutoErrorMsg(null);
  };

  const readError = async (res: Response, fallback: string) => {
    const errJson = await res.json().catch(() => ({}));
    return typeof errJson.detail === 'string' ? errJson.detail : fallback;
  };

  const pollConnectJob = async (jobId: string) => {
    const generation = ++pollGeneration.current;
    setJobStep('prepare');
    setSmsStep('progress');
    while (generation === pollGeneration.current) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      if (generation !== pollGeneration.current) return;
      try {
        const res = await apiFetch(`${API_URL}/api/v1/stores/auto-connect/jobs/${jobId}`);
        if (!res.ok) throw new Error(await readError(res, 'Не удалось узнать статус подключения'));
        const job = await res.json();
        setJobStep(job.step);
        if (job.status === 'done') {
          setShowAddModal(false);
          resetModalState();
          refetchStoresAndProfile();
          if (job.store?.id) setSharedActiveStoreId(job.store.id);
          showToast('success', `Магазин ${job.store?.name ?? ''} подключён`);
          return;
        }
        if (job.status === 'error') {
          setAutoErrorMsg(job.error || 'Не удалось подключить магазин');
          setSmsStep('phone');
          return;
        }
      } catch (err: any) {
        setAutoErrorMsg(err.message || 'Не удалось узнать статус подключения');
        setSmsStep('phone');
        return;
      }
    }
  };

  const handleStarted = (data: { status: string; job_id?: string; merchants?: string[] }) => {
    if (data.status === 'choose_merchant' && data.merchants) {
      setMerchants(data.merchants);
      setSmsStep('merchant');
    } else if (data.job_id) {
      pollConnectJob(data.job_id);
    }
  };

  const handleSendOwnerSms = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ownerPhone.trim()) {
      setAutoErrorMsg('Введите номер телефона владельца кабинета Kaspi');
      return;
    }
    setAutoSubmitting(true);
    setAutoErrorMsg(null);
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/auto-connect/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: ownerPhone.trim() }),
      });
      if (!res.ok) throw new Error(await readError(res, 'Не удалось отправить SMS'));
      setSmsCode('');
      setSmsStep('code');
    } catch (err: any) {
      setAutoErrorMsg(err.message || 'Не удалось отправить SMS');
    } finally {
      setAutoSubmitting(false);
    }
  };

  const handleVerifyOwnerSms = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!smsCode.trim()) {
      setAutoErrorMsg('Введите код из SMS');
      return;
    }
    setAutoSubmitting(true);
    setAutoErrorMsg(null);
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/auto-connect/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: ownerPhone.trim(), code: smsCode.trim() }),
      });
      if (!res.ok) throw new Error(await readError(res, 'Не удалось войти по коду из SMS'));
      handleStarted(await res.json());
    } catch (err: any) {
      setAutoErrorMsg(err.message || 'Не удалось войти по коду из SMS');
    } finally {
      setAutoSubmitting(false);
    }
  };

  const handleChooseMerchant = async (merchantId: string) => {
    setAutoSubmitting(true);
    setAutoErrorMsg(null);
    try {
      const res = await apiFetch(`${API_URL}/api/v1/stores/auto-connect/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ merchant_id: merchantId }),
      });
      if (!res.ok) throw new Error(await readError(res, 'Не удалось выбрать магазин'));
      handleStarted(await res.json());
    } catch (err: any) {
      setAutoErrorMsg(err.message || 'Не удалось выбрать магазин');
      setSmsStep('phone');
    } finally {
      setAutoSubmitting(false);
    }
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
        showToast('success', 'Куки успешно синхронизированы с магазином!');
        refetchStoresAndProfile();
      } else {
        const errData = await res.json();
        showToast('warning', `${errData.detail || 'Требуется повторный вход в Kaspi MC'}. Перейдите во вкладку «Подключения & Сессия» и нажмите «Войти в Kaspi MC».`);
      }
    } catch (err) {
      console.error('Error syncing store cookies:', err);
      showToast('error', 'Ошибка синхронизации кук. Перейдите во вкладку «Подключения & Сессия» и войдите в Kaspi MC заново.');
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
      {toastMessage && (
        <div
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            zIndex: 9999,
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            padding: '14px 20px',
            borderRadius: '12px',
            background:
              toastMessage.type === 'success'
                ? 'linear-gradient(135deg, rgba(16, 185, 129, 0.95), rgba(5, 150, 105, 0.95))'
                : toastMessage.type === 'warning'
                ? 'linear-gradient(135deg, rgba(245, 158, 11, 0.95), rgba(217, 119, 6, 0.95))'
                : 'linear-gradient(135deg, rgba(239, 68, 68, 0.95), rgba(220, 38, 38, 0.95))',
            color: '#ffffff',
            boxShadow: '0 10px 25px -5px rgba(0,0,0,0.5)',
            backdropFilter: 'blur(8px)',
            fontWeight: 500,
            fontSize: '14px',
            maxWidth: '420px',
          }}
        >
          {toastMessage.type === 'success' && <CheckCircle2 size={20} style={{ flexShrink: 0 }} />}
          {toastMessage.type === 'warning' && <AlertTriangle size={20} style={{ flexShrink: 0 }} />}
          {toastMessage.type === 'error' && <ShieldAlert size={20} style={{ flexShrink: 0 }} />}
          <span>{toastMessage.text}</span>
        </div>
      )}

      {/* Header Context Switcher Bar */}
      <div className="glass-card" style={styles.topContextBar}>
        <div style={styles.contextLeft}>
          <Store size={20} color="var(--primary)" />
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

        <button onClick={() => { resetModalState(); setShowAddModal(true); }} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Plus size={16} /> Подключить магазин Kaspi
        </button>
      </div>

      {/* Stores List Grid */}
      <div style={{ marginTop: '24px' }}>
        <h3 style={{ ...styles.sectionHeader, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Store size={18} /> Ваши магазины Kaspi:
        </h3>

        {loading ? (
          <div style={{ color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> Загрузка списка магазинов...
          </div>
        ) : stores.length === 0 ? (
          <div className="glass-card" style={styles.emptyState}>
            <img src={emptyStateIllustration} alt="" style={styles.emptyStateIllustration} />
            <div style={{ fontSize: '18px', fontWeight: 600, color: '#fff' }}>У вас пока нет подключенных магазинов Kaspi.</div>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: '8px 0 16px 0' }}>
              Подключите ваш магазин Kaspi по API-токену и email/паролю для автопечати, демпинга и заказов.
            </p>
            <button onClick={() => { resetModalState(); setShowAddModal(true); }} className="btn btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
              <Rocket size={16} /> Подключить первый магазин Kaspi
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
                  <span className={`badge ${s.status === 'ACTIVE' ? 'badge-success' : 'badge-error'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                    {s.status === 'ACTIVE' ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                    {s.status === 'ACTIVE' ? 'Подключен' : 'Требуется вход'}
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
                  <button onClick={() => handleVerifyStore(s.id)} className="btn btn-secondary" style={{ flex: '1 1 140px', padding: '8px', fontSize: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
                    <RefreshCw size={13} /> Проверить статус
                  </button>
                  <button onClick={() => handleSyncCookies(s)} className="btn btn-primary" style={{ flex: '1 1 120px', padding: '8px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
                    <KeyRound size={13} /> Обновить куки
                  </button>
                  {s.role === 'OWNER' && (
                    <button onClick={() => handleDeleteStore(s.id)} className="btn" style={{ ...styles.deleteBtn, flex: '1 1 90px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
                      <Trash2 size={13} /> Удалить
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Onboarding Wizard Modal — Single Unified Form */}
      {showAddModal && (
        <div className="modal-overlay" onClick={() => { if (smsStep !== 'progress') { resetModalState(); setShowAddModal(false); } }}>
          <div className="glass-card modal-content" style={styles.modalContent} onClick={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Sparkles size={18} color="var(--secondary)" />
                Подключение магазина Kaspi
              </span>
              <button onClick={() => { resetModalState(); setShowAddModal(false); }} disabled={smsStep === 'progress'} style={styles.closeBtn}>
                <X size={18} />
              </button>
            </div>

            <div style={{ padding: '20px' }}>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: '0 0 16px 0' }}>
                Введите номер телефона <strong>владельца</strong> кабинета Kaspi Merchant — мы пришлём SMS-код и
                подключим магазин сами, без ручного ввода токена и пароля.
              </p>
              {autoErrorMsg && (
                <div style={styles.errorBox}>
                  <AlertTriangle size={16} style={{ flexShrink: 0 }} />
                  <span>
                    {autoErrorMsg}{' '}
                    <a
                      href={waLink('storehub-error', `Здравствуйте! Не получается подключить магазин к BuyShark. Ошибка: ${autoErrorMsg}`)}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={styles.errorLink}
                    >
                      {t('storehub_error_help')}
                    </a>
                  </span>
                </div>
              )}

              {smsStep === 'phone' && (
                <form onSubmit={handleSendOwnerSms}>
                  <div style={styles.formGroup}>
                    <label style={styles.label}>Телефон владельца кабинета:</label>
                    <input
                      type="tel"
                      placeholder="+7 700 123 45 67"
                      value={ownerPhone}
                      onChange={(e) => setOwnerPhone(e.target.value)}
                      className="form-input"
                      required
                    />
                  </div>
                  <div style={{ marginTop: '24px' }}>
                    <button type="submit" disabled={autoSubmitting} className="btn btn-primary" style={styles.submitBtn}>
                      {autoSubmitting
                        ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> Отправляем SMS...</>
                        : <><Send size={16} /> Отправить SMS-код</>}
                    </button>
                  </div>
                </form>
              )}

              {smsStep === 'code' && (
                <form onSubmit={handleVerifyOwnerSms}>
                  <div style={styles.formGroup}>
                    <label style={styles.label}>Код из SMS:</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      placeholder="1234"
                      value={smsCode}
                      onChange={(e) => setSmsCode(e.target.value)}
                      className="form-input"
                      required
                    />
                  </div>
                  <div style={{ marginTop: '24px' }}>
                    <button type="submit" disabled={autoSubmitting} className="btn btn-primary" style={styles.submitBtn}>
                      {autoSubmitting
                        ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> Входим в кабинет Kaspi...</>
                        : <><CheckCircle2 size={16} /> Подтвердить и подключить</>}
                    </button>
                  </div>
                </form>
              )}

              {smsStep === 'merchant' && (
                <div>
                  <p style={{ fontSize: '13px', margin: '0 0 12px 0' }}>У владельца несколько магазинов. Какой подключаем?</p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {merchants.map((mid) => (
                      <button
                        key={mid}
                        type="button"
                        disabled={autoSubmitting}
                        onClick={() => handleChooseMerchant(mid)}
                        className="btn btn-secondary"
                        style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '12px 14px', justifyContent: 'flex-start' }}
                      >
                        <Store size={15} /> Магазин ID {mid}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {smsStep === 'progress' && (
                <ol style={styles.progressList}>
                  {CONNECT_STEPS.map((stepItem, index) => {
                    const current = CONNECT_STEPS.findIndex((x) => x.key === jobStep);
                    const state = index < current ? 'done' : index === current ? 'active' : 'pending';
                    return (
                      <li key={stepItem.key} style={{ ...styles.progressItem, opacity: state === 'pending' ? 0.45 : 1 }}>
                        {state === 'done' && <CheckCircle2 size={16} color="var(--success)" />}
                        {state === 'active' && <Loader2 size={16} color="var(--secondary)" style={{ animation: 'spin 1s linear infinite' }} />}
                        {state === 'pending' && <span style={styles.progressDot} />}
                        {stepItem.label}
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// Ключи — шаги фоновой задачи в backend/routers/stores_auto_connect.py.
const CONNECT_STEPS = [
  { key: 'prepare', label: 'Готовим подключение' },
  { key: 'token', label: 'Получаем API-токен' },
  { key: 'employee', label: 'Добавляем сотрудника BuyShark' },
  { key: 'email', label: 'Ждём письмо от Kaspi' },
  { key: 'login', label: 'Входим в кабинет' },
  { key: 'saving', label: 'Сохраняем магазин' },
];

const styles: Record<string, React.CSSProperties> = {
  submitBtn: {
    width: '100%',
    padding: '14px 24px',
    fontSize: '15px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
  },
  progressList: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  progressItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    fontSize: '14px',
  },
  progressDot: {
    width: '16px',
    height: '16px',
    borderRadius: '50%',
    border: '1.5px solid var(--card-border)',
    boxSizing: 'border-box',
    flexShrink: 0,
  },
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
    flexWrap: 'wrap',
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
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  errorLink: {
    color: '#f3a2a8',
    fontWeight: 700,
    textDecoration: 'underline',
  },
  emptyStateIllustration: {
    width: '200px',
    maxWidth: '100%',
    borderRadius: '12px',
    marginBottom: '16px',
  },
};
