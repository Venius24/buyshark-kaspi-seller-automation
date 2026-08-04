import { useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

interface Analytics {
  best_price_share: number;
  winning_count: number;
  total_scanned: number;
  total_saved_margin: number;
}

// Честный ноль до первого реального ответа бэкенда — раньше здесь были
// выдуманные цифры (80%, 145000 ₸), которые показывались новым пользователям
// как будто это уже реальная статистика их магазина.
const INITIAL_ANALYTICS: Analytics = {
  best_price_share: 0,
  winning_count: 0,
  total_scanned: 0,
  total_saved_margin: 0,
};

const fetchWidgets = async (): Promise<any> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/widgets`);
    const data = await res.json();
    return res.ok && data.success ? data : null;
  } catch {
    return null;
  }
};

export function useAnalyticsWidgets(wsEvent: { type: string; data?: any } | null) {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const widgetsQueryKey = ['analytics-widgets', activeStoreId];

  const { data: widgetData = null } = useQuery({
    queryKey: widgetsQueryKey,
    queryFn: fetchWidgets,
    refetchInterval: 10_000,
  });

  const analytics: Analytics = useMemo(() => {
    if (widgetData?.analytics) {
      return {
        best_price_share: widgetData.analytics.best_price_share ?? INITIAL_ANALYTICS.best_price_share,
        winning_count: widgetData.analytics.winning_count ?? INITIAL_ANALYTICS.winning_count,
        total_scanned: widgetData.analytics.total_scanned ?? INITIAL_ANALYTICS.total_scanned,
        total_saved_margin: widgetData.analytics.total_saved_margin ?? INITIAL_ANALYTICS.total_saved_margin,
      };
    }
    return INITIAL_ANALYTICS;
  }, [widgetData]);

  useEffect(() => {
    if (wsEvent?.type === 'analytics_update' && wsEvent.data) {
      queryClient.setQueryData(widgetsQueryKey, (prev: any) => ({
        ...(prev || {}),
        success: true,
        analytics: wsEvent.data,
      }));
    }
  }, [wsEvent, queryClient, activeStoreId]);

  return { analytics, widgetData };
}
