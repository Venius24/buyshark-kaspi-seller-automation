import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';
import type { AttackReport, GuardDecision, GuardSettings } from '../types';

/** Защита демпинга от оттеснения: общие настройки магазина и отчёт «Под атакой».
 *  Бэкенд — /api/dumping/guard и /api/dumping/attacks (backend/routers/dumping.py). */

/** Совпадает с умолчаниями guard.GuardConfig — цифры, согласованные с владельцем 03.10.2026. */
export const DEFAULT_GUARD: GuardSettings = {
  enabled: false,
  observe_only: false,
  min_reviews: 50,
  no_rating_action: 'ASK',
  min_rating: null,
  min_merchant_age_days: null,
  ignore_intercity: false,
  max_extra_delivery_days: null,
  ignore_preorder: false,
  blacklist: [],
  whitelist: [],
  all_ignored_action: 'HOLD',
  floor_below_next_pct: null,
  max_daily_drop_pct: 15,
  drop_pause_hours: 3,
  drop_alert: true,
  ladder_min_moves: 10,
  ladder_window_hours: 24,
  ladder_min_slide_pct: 10,
  ladder_response: 'RESET_MAX',
  ladder_cooldown_hours: 12,
};

export interface GuardMeta {
  settings: GuardSettings;
  reasonLabels: Record<string, string>;
}

const fetchGuard = async (): Promise<GuardMeta> => {
  const res = await apiFetch(`${API_URL}/api/dumping/guard`);
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.detail || 'guard load failed');
  return { settings: { ...DEFAULT_GUARD, ...data.settings }, reasonLabels: data.reason_labels || {} };
};

const fetchAttacks = async (): Promise<AttackReport> => {
  const res = await apiFetch(`${API_URL}/api/dumping/attacks`);
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.detail || 'attacks load failed');
  return data;
};

export function useGuardSettings() {
  const storeId = useActiveStoreId();
  return useQuery({ queryKey: ['dumping-guard', storeId], queryFn: fetchGuard, staleTime: 60_000 });
}

export function useAttackReport() {
  const storeId = useActiveStoreId();
  return useQuery({ queryKey: ['dumping-attacks', storeId], queryFn: fetchAttacks, refetchInterval: 120_000 });
}

/** detail бывает строкой, списком ошибок валидации (422) или объектом
 *  {code, reason} (магазин на паузе / заблокирован биллингом). */
function errorDetail(d: unknown): string {
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).join('; ');
  if (d && typeof d === 'object') {
    const o = d as { reason?: string; code?: string };
    return o.reason || o.code || '';
  }
  return typeof d === 'string' ? d : '';
}

export function useSaveGuardSettings() {
  const storeId = useActiveStoreId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (settings: GuardSettings) => {
      const res = await apiFetch(`${API_URL}/api/dumping/guard`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(errorDetail(data.detail) || 'Не удалось сохранить');
      return data.settings as GuardSettings;
    },
    onSuccess: (settings) => {
      qc.setQueryData<GuardMeta>(['dumping-guard', storeId], (prev) => ({
        settings: { ...DEFAULT_GUARD, ...settings },
        reasonLabels: prev?.reasonLabels || {},
      }));
    },
  });
}

export interface PriceMove {
  created_at: string;
  our_old: number;
  our_new: number;
  rival_id: string | null;
  rival_name: string | null;
  rival_price: number | null;
  action: string | null;
}

export function usePriceMoves(sku: string | null, days = 14) {
  const storeId = useActiveStoreId();
  return useQuery({
    queryKey: ['dumping-moves', storeId, sku, days],
    enabled: !!sku,
    queryFn: async (): Promise<PriceMove[]> => {
      const res = await apiFetch(`${API_URL}/api/dumping/attacks/${encodeURIComponent(sku || '')}/moves?days=${days}`);
      const data = await res.json();
      return res.ok && data.success ? data.moves : [];
    },
  });
}

export interface GuardDecisions {
  pending: GuardDecision[];
  decided: GuardDecision[];
}

/** Продавцы без рейтинга рядом с нами: ждут решения менеджера и уже решённые. */
export function useGuardDecisions(enabled = true) {
  const storeId = useActiveStoreId();
  return useQuery({
    queryKey: ['dumping-guard-decisions', storeId],
    enabled,
    queryFn: async (): Promise<GuardDecisions> => {
      const res = await apiFetch(`${API_URL}/api/dumping/guard/decisions`);
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(errorDetail(data.detail) || 'decisions load failed');
      return { pending: data.pending || [], decided: data.decided || [] };
    },
  });
}

export type DecisionChoice = 'IGNORE' | 'CONSIDER' | 'IGNORE_ALL' | 'ASK';

export function useDecideRival() {
  const storeId = useActiveStoreId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, decision }: { id: number; decision: DecisionChoice }) => {
      const res = await apiFetch(`${API_URL}/api/dumping/guard/decisions/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(errorDetail(data.detail) || 'Не удалось сохранить решение');
      return data.decision as GuardDecision;
    },
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: ['dumping-guard-decisions', storeId] });
      qc.invalidateQueries({ queryKey: ['dumping-attacks', storeId] });
      // «Не учитывать нигде» дописывает чёрный список магазина.
      if (vars.decision === 'IGNORE_ALL') qc.invalidateQueries({ queryKey: ['dumping-guard', storeId] });
    },
  });
}
