import { useQuery } from '@tanstack/react-query';
import { fetchProfile, fetchStores, fetchGroupsQuery, fetchProductsQuery, fetchChatbotHasSession } from '../queries';

/**
 * Всегда смонтирован (рендерится вне activeTab-условий в App.tsx), ничего
 * не рисует. Единственная цель — держать react-query наблюдателем на
 * запросах вкладок, которые сами по себе размонтируются при переключении
 * (StoreHub, MasterStockGroups) — иначе их refetchInterval останавливается,
 * как только уходишь с вкладки, и данные замирают на последнем значении.
 */
export function BackgroundSync() {
  useQuery({ queryKey: ['auth', 'me'], queryFn: fetchProfile, refetchInterval: 30_000 });
  useQuery({ queryKey: ['stores'], queryFn: fetchStores, refetchInterval: 30_000 });
  useQuery({ queryKey: ['stock-groups'], queryFn: fetchGroupsQuery, refetchInterval: 5_000 });
  // Full catalog (~800+ SKUs) — real changes already trigger an immediate
  // invalidateQueries(['products', 'all-for-stock-groups']) from
  // MasterStockGroups.tsx after any mutation, so this interval is only a
  // just-in-case safety net, not the primary freshness mechanism. It was
  // the single largest steady-state traffic source in the perf baseline
  // (backend/perf snapshot, 2026-07-31) — 30s was needlessly aggressive
  // for data that's invalidated on demand anyway.
  useQuery({ queryKey: ['products', 'all-for-stock-groups'], queryFn: fetchProductsQuery, refetchInterval: 300_000 });
  useQuery({ queryKey: ['chatbot-status'], queryFn: fetchChatbotHasSession, refetchInterval: 30_000 });
  return null;
}
