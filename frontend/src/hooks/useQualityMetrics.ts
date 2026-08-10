import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface QualityMetricsSnapshot {
  date: string;
  rating_percentage: number | null;
  returns_percentage: number | null;
  late_kd_percentage: number | null;
  cancelled_percentage: number | null;
  avg_chat_response_minutes: number | null;
  chat_response_rate_percent: number | null;
  raw_payload: {
    rating?: { details?: { goodValue?: number; violationValue?: number; notEnoughData?: boolean } | null };
    returns?: { details?: { goodValue?: number; violationValue?: number; notEnoughData?: boolean } | null };
    late_kaspi_delivery?: { details?: { goodValue?: number; violationValue?: number; notEnoughData?: boolean } | null };
    cancelled?: { details?: { goodValue?: number; violationValue?: number; notEnoughData?: boolean } | null };
    average_chat_response_time?: { thresholds?: { goodValue?: number; badValue?: number }; notEnoughData?: boolean };
    chat_response_rate?: { thresholds?: { goodValue?: number; badValue?: number }; notEnoughData?: boolean };
  };
  fetched_at: string | null;
}

export interface QualityMetricsStats {
  has_data: boolean;
  data: QualityMetricsSnapshot | null;
}

const EMPTY_QUALITY_METRICS: QualityMetricsStats = {
  has_data: false,
  data: null,
};

const fetchQualityMetrics = async (): Promise<QualityMetricsStats> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/quality-metrics`);
    const data = await res.json();
    return res.ok && data.success ? data : EMPTY_QUALITY_METRICS;
  } catch {
    return EMPTY_QUALITY_METRICS;
  }
};

export function useQualityMetrics() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['analytics-quality-metrics', activeStoreId],
    queryFn: fetchQualityMetrics,
    // Данные обновляются раз в сутки ночным воркером — частый poll не нужен.
    refetchInterval: 5 * 60_000,
  });
  return data ?? EMPTY_QUALITY_METRICS;
}
