import { apiFetch, API_URL } from './api';
import type { StoreItem, UserProfile } from './components/StoreHub';
import type { MasterStockGroup, ProductItem } from './components/MasterStockGroups';
import type { BotProfile, BotUsageSummary, OutboundQueueItem, OutboundStats, BillingSummary, BillingPlan, BillingTransaction } from './types';

export const fetchProfile = async (): Promise<UserProfile | null> => {
  const res = await apiFetch(`${API_URL}/api/v1/auth/me`);
  if (!res.ok) return null;
  return res.json();
};

export const fetchStores = async (): Promise<StoreItem[]> => {
  const res = await apiFetch(`${API_URL}/api/v1/stores`);
  if (!res.ok) return [];
  return res.json();
};

export const fetchGroupsQuery = async (): Promise<MasterStockGroup[]> => {
  const res = await apiFetch(`${API_URL}/api/stock-groups`);
  const data = await res.json();
  return data.success ? (data.groups || []) : [];
};

export const fetchProductsQuery = async (): Promise<ProductItem[]> => {
  const res = await apiFetch(`${API_URL}/api/products?page=1&page_size=1000&status=all`);
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
