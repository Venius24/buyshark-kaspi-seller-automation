import { useEffect, useState } from 'react';
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

export function useAnalyticsWidgets(wsEvent: { type: string; data?: any } | null) {
  const [analytics, setAnalytics] = useState<Analytics>(INITIAL_ANALYTICS);
  const [widgetData, setWidgetData] = useState<any>(null);

  useEffect(() => {
    const fetchWidgets = async () => {
      try {
        const res = await apiFetch(`${API_URL}/api/analytics/widgets`);
        const data = await res.json();
        if (res.ok && data.success) {
          setWidgetData(data);
          if (data.analytics) {
            setAnalytics(prev => ({
              ...prev,
              buy_box_share: data.analytics.buy_box_share ?? prev.buy_box_share,
              winning_count: data.analytics.winning_count ?? prev.winning_count,
              total_scanned: data.analytics.total_scanned ?? prev.total_scanned,
              total_saved_margin: data.analytics.total_saved_margin ?? prev.total_saved_margin,
            }));
          }
        }
      } catch { /* ignore */ }
    };
    fetchWidgets();
    const interval = setInterval(fetchWidgets, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (wsEvent?.type === 'analytics_update' && wsEvent.data) {
      setAnalytics(wsEvent.data);
    }
  }, [wsEvent]);

  return { analytics, widgetData };
}
