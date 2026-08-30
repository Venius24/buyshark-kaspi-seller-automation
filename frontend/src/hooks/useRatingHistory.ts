import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface RatingHistoryPoint {
  date: string;
  rating_percentage: number | null;
  review_count: number | null;
}

const fetchRatingHistory = async (days: number): Promise<RatingHistoryPoint[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/quality-metrics-history?days=${days}`);
    const data = await res.json();
    return res.ok && data.success ? data.history : [];
  } catch {
    return [];
  }
};

export function useRatingHistory(days: number = 30) {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['analytics-rating-history', activeStoreId, days],
    queryFn: () => fetchRatingHistory(days),
    // Данные обновляются раз в сутки ночным воркером — частый poll не нужен.
    refetchInterval: 5 * 60_000,
  });
  return data ?? [];
}
