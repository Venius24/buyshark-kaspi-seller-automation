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
  useQuery({ queryKey: ['products', 'all-for-stock-groups'], queryFn: fetchProductsQuery, refetchInterval: 30_000 });
  useQuery({ queryKey: ['chatbot-status'], queryFn: fetchChatbotHasSession, refetchInterval: 30_000 });
  return null;
}
