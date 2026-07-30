import { useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';

interface Analytics {
  buy_box_share: number;
  winning_count: number;
  total_scanned: number;
  total_saved_margin: number;
}

const INITIAL_ANALYTICS: Analytics = {
  buy_box_share: 80.0,
  winning_count: 8,
  total_scanned: 10,
  total_saved_margin: 145000,
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

  const { data: widgetData = null } = useQuery({
    queryKey: ['analytics-widgets'],
    queryFn: fetchWidgets,
    refetchInterval: 10_000,
  });

  const analytics: Analytics = useMemo(() => {
    if (widgetData?.analytics) {
      return {
        buy_box_share: widgetData.analytics.buy_box_share ?? INITIAL_ANALYTICS.buy_box_share,
        winning_count: widgetData.analytics.winning_count ?? INITIAL_ANALYTICS.winning_count,
        total_scanned: widgetData.analytics.total_scanned ?? INITIAL_ANALYTICS.total_scanned,
        total_saved_margin: widgetData.analytics.total_saved_margin ?? INITIAL_ANALYTICS.total_saved_margin,
      };
    }
    return INITIAL_ANALYTICS;
  }, [widgetData]);

  useEffect(() => {
    if (wsEvent?.type === 'analytics_update' && wsEvent.data) {
      queryClient.setQueryData(['analytics-widgets'], (prev: any) => ({
        ...(prev || {}),
        success: true,
        analytics: wsEvent.data,
      }));
    }
  }, [wsEvent, queryClient]);

  return { analytics, widgetData };
}
