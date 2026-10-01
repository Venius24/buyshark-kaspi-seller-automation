import { apiFetch, API_URL } from './api';
import type { StoreItem, UserProfile } from './components/StoreHub';
import type { MasterStockGroup, ProductItem } from './components/MasterStockGroups';
import type { BotProfile, BotUsageSummary, OutboundQueueItem, OutboundStats, BotJournal, BillingSummary, BillingPlan, BillingTransaction, FeatureFlags } from './types';

export const fetchProfile = async (): Promise<UserProfile | null> => {
  const res = await apiFetch(`${API_URL}/api/v1/auth/me`);
  if (!res.ok) return null;
  return res.json();
};

export const completeOnboarding = async (): Promise<void> => {
  await apiFetch(`${API_URL}/api/v1/auth/onboarding-complete`, { method: 'POST' });
};

export const fetchStores = async (): Promise<StoreItem[]> => {
  const res = await apiFetch(`${API_URL}/api/v1/stores`);
  if (!res.ok) return [];
  return res.json();
};

export const fetchGroupsQuery = async (): Promise<MasterStockGroup[]> => {
  const res = await apiFetch(`${API_URL}/api/stock-groups`);
  if (!res.ok) {
    // Раньше любая ошибка (в т.ч. 401 из-за унаследованного чужого store_id
    // после смены аккаунта на общем браузере) тихо превращалась в "0 групп" —
    // баг был неотличим от реального пустого состояния. Пусть падает явной
    // ошибкой запроса, а не врёт нулём.
    throw new Error(`GET /api/stock-groups failed: ${res.status}`);
  }
  const data = await res.json();
  return data.success ? (data.groups || []) : [];
};

export const fetchProductsQuery = async (): Promise<ProductItem[]> => {
  // page_size с запасом: это не «страница», а весь кэш ассортимента разом —
  // из него строится пикер товаров и подписи чипов в Мастер-Группах. При 1000
  // у Бархана (1150 в продаже + 423 снятых) хвост каталога молча обрезался:
  // 150 активных карточек и весь архив в пикер не доезжали, а привязанные к
  // группам товары из хвоста показывались голым артикулом без названия.
  const res = await apiFetch(`${API_URL}/api/products?page=1&page_size=5000&status=all`);
  const data = await res.json();
  return data.success ? (data.products || []) : [];
};

export const fetchChatbotHasSession = async (): Promise<boolean> => {
  try {
    const res = await apiFetch(`${API_URL}/api/chatbot/status`);
    const data = await res.json();
    return res.ok && data.success ? !!data.has_session : false;
  } catch {
    return false;
  }
};

export const fetchBotProfile = async (): Promise<BotProfile | null> => {
  try {
    const res = await apiFetch(`${API_URL}/api/chatbot/profile`);
    const data = await res.json();
    return res.ok && data.success ? data.profile : null;
  } catch {
    return null;
  }
};

export const saveBotProfile = async (updates: Partial<BotProfile>): Promise<BotProfile | null> => {
  const res = await apiFetch(`${API_URL}/api/chatbot/profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  const data = await res.json();
  return res.ok && data.success ? data.profile : null;
};

export const fetchBotUsage = async (): Promise<BotUsageSummary | null> => {
  try {
    const res = await apiFetch(`${API_URL}/api/chatbot/usage`);
    const data = await res.json();
    if (!res.ok || !data.success) return null;
    return { total_billed_units: data.total_billed_units, by_kind: data.by_kind, period_days: data.period_days };
  } catch {
    return null;
  }
};

export const fetchFeatureFlags = async (): Promise<FeatureFlags | null> => {
  try {
    const res = await apiFetch(`${API_URL}/api/v1/feature-flags`);
    const data = await res.json();
    return res.ok && data.success ? data.flags : null;
  } catch {
    return null;
  }
};

// Все включено по умолчанию — сервер лениво создаёт строку flags с такими же
// дефолтами (backend/feature_flags.py), так что до первого реального ответа
// или при сбое сети сайдбар/вкладка честно показывают "всё как обычно
// работает", а не ложное "выключено".
export const DEFAULT_FEATURE_FLAGS: FeatureFlags = {
  dumping_enabled: true,
  stock_groups_enabled: true,
  autoassembly_enabled: true,
  chatbot_enabled: true,
};

// Общий queryFn для queryKey ['feature-flags', storeId] — используется и в
// App.tsx (для пилюли в сайдбаре), и в GlobalControlTab.tsx. Раньше у каждого
// был свой queryFn на один и тот же ключ (один отдавал null при ошибке,
// другой подставлял дефолт) — какой из них первым населял кэш, тот и решал
// форму данных для ОБОИХ подписчиков, и `null` мог долететь до места, которое
// его не ждало, и упасть на `flags[row.key]`.
export const fetchFeatureFlagsWithDefault = async (): Promise<FeatureFlags> => {
  return (await fetchFeatureFlags()) ?? DEFAULT_FEATURE_FLAGS;
};

export const saveFeatureFlags = async (updates: Partial<FeatureFlags>): Promise<FeatureFlags | null> => {
  const res = await apiFetch(`${API_URL}/api/v1/feature-flags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  const data = await res.json();
  return res.ok && data.success ? data.flags : null;
};

export const fetchBillingSummary = async (): Promise<BillingSummary | null> => {
  try {
    const res = await apiFetch(`${API_URL}/api/billing/summary`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
};

export const fetchBillingPlans = async (): Promise<BillingPlan[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/billing/plans`);
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
};

export const fetchBillingTransactions = async (): Promise<BillingTransaction[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/billing/transactions`);
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
};

export const fetchOutboundQueue = async (): Promise<OutboundQueueItem[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/chatbot/outbound/queue`);
    const data = await res.json();
    return res.ok && data.success ? data.items : [];
  } catch {
    return [];
  }
};

export const fetchOutboundStats = async (days: number = 30): Promise<OutboundStats | null> => {
  try {
    const res = await apiFetch(`${API_URL}/api/chatbot/outbound/stats?days=${days}`);
    const data = await res.json();
    if (!res.ok || !data.success) return null;
    return { period_days: data.period_days, by_campaign: data.by_campaign };
  } catch {
    return null;
  }
};

export const fetchBotJournal = async (days: number = 7, kind: string = ''): Promise<BotJournal | null> => {
  try {
    const q = kind ? `&kind=${encodeURIComponent(kind)}` : '';
    const res = await apiFetch(`${API_URL}/api/chatbot/journal?days=${days}${q}`);
    const data = await res.json();
    if (!res.ok || !data.success) return null;
    return data as BotJournal;
  } catch {
    return null;
  }
};

export const testSendOutbound = async (order_id: string, message_text: string): Promise<{ success: boolean; message: string }> => {
  const res = await apiFetch(`${API_URL}/api/chatbot/outbound/test-send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_id, message_text }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { success: false, message: data.detail || 'Ошибка отправки' };
  return { success: true, message: data.message || 'OK' };
};

export const testFirstContact = async (order_id: string, message_text: string, status = 'PICKUP'): Promise<{ success: boolean; message: string }> => {
  const res = await apiFetch(`${API_URL}/api/chatbot/outbound/first-contact-test?status=${encodeURIComponent(status)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_id, message_text }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { success: false, message: data.detail || 'Ошибка отправки' };
  return { success: true, message: data.message || 'OK' };
};

export const pricefeedPreview = async (
  phone: string,
  password: string,
  merchant_id?: string
): Promise<import('./types').PriceFeedPreviewResult> => {
  const res = await apiFetch(`${API_URL}/api/dumping/pricefeed/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone, password, merchant_id }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить импорт из PriceFeed';
    throw new Error(detail);
  }
  return data;
};

export const pricefeedApply = async (
  import_id: string,
  overwrite: boolean,
  activate: boolean
): Promise<import('./types').PriceFeedApplyResult> => {
  const res = await apiFetch(`${API_URL}/api/dumping/pricefeed/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ import_id, overwrite, activate }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : 'Не удалось применить импорт из PriceFeed';
    throw new Error(detail);
  }
  return data;
};
