import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchProfile, fetchStores, fetchGroupsQuery, fetchProductsQuery, fetchChatbotHasSession } from '../queries';
import { useActiveStoreId } from '../storeContext';
import type { ActiveTab } from '../types';
import type { TelemetryEvent } from '../hooks/useWebSocket';

/**
 * Всегда смонтирован вне вкладок: поддерживает редкий резервный опрос и
 * отмечает данные групп устаревшими по WebSocket-событиям.
 */
export function BackgroundSync({ activeTab, wsEvent }: { activeTab: ActiveTab; wsEvent: TelemetryEvent | null }) {
  // Слайс 6: storeId в queryKey — переключение магазина в StoreHub не должно
  // держать эти фоновые запросы на данных ПРЕДЫДУЩЕГО магазина тёплыми.
  const activeStoreId = useActiveStoreId();
  const queryClient = useQueryClient();
  const groupRefreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (wsEvent?.type !== 'stock_group_updated') return;
    if (groupRefreshTimer.current) clearTimeout(groupRefreshTimer.current);
    // A sync can emit many group events in succession. Fetch one list after
    // the burst, and only when the groups screen is visible.
    groupRefreshTimer.current = setTimeout(() => {
      queryClient.invalidateQueries({
        queryKey: ['stock-groups', activeStoreId],
        refetchType: activeTab === 'stock-groups' && document.visibilityState === 'visible' ? 'active' : 'none',
      });
      groupRefreshTimer.current = null;
    }, 500);
  }, [wsEvent, queryClient, activeStoreId, activeTab]);

  useEffect(() => () => {
    if (groupRefreshTimer.current) clearTimeout(groupRefreshTimer.current);
  }, []);

  useQuery({ queryKey: ['auth', 'me'], queryFn: fetchProfile, refetchInterval: 30_000 });
  useQuery({ queryKey: ['stores'], queryFn: fetchStores, refetchInterval: 30_000 });
  useQuery({
    queryKey: ['stock-groups', activeStoreId],
    queryFn: fetchGroupsQuery,
    refetchInterval: activeTab === 'stock-groups' ? false : 300_000,
  });
  // Full catalog (~800+ SKUs) — real changes already trigger an immediate
  // invalidateQueries(['products', 'all-for-stock-groups']) from
  // MasterStockGroups.tsx after any mutation, so this interval is only a
  // just-in-case safety net, not the primary freshness mechanism. It was
  // the single largest steady-state traffic source in the perf baseline
  // (backend/perf snapshot, 2026-07-31) — 30s was needlessly aggressive
  // for data that's invalidated on demand anyway.
  useQuery({ queryKey: ['products', 'all-for-stock-groups', activeStoreId], queryFn: fetchProductsQuery, refetchInterval: 300_000 });
  useQuery({ queryKey: ['chatbot-status', activeStoreId], queryFn: fetchChatbotHasSession, refetchInterval: 30_000 });
  return null;
}
