import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';
import type { SalesDailyPoint } from './useSalesStats';

export interface ScoreStats {
  score: {
    composite: number;
    components: {
      price_leadership: number;
      revenue_trend: number;
      activity_consistency: number;
    };
  };
  streaks: {
    price_leadership_days: number;
    sales_days: number;
  };
  heatmap: SalesDailyPoint[];
  history_points: number;
}

const EMPTY_SCORE: ScoreStats = {
  score: {
    composite: 0,
    components: { price_leadership: 0, revenue_trend: 0, activity_consistency: 0 },
  },
  streaks: { price_leadership_days: 0, sales_days: 0 },
  heatmap: [],
  history_points: 0,
};

const fetchScore = async (): Promise<ScoreStats> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/score`);
    const data = await res.json();
    return res.ok && data.success ? data : EMPTY_SCORE;
  } catch {
    return EMPTY_SCORE;
  }
};

export function useScoreStats() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['analytics-score', activeStoreId],
    queryFn: fetchScore,
    refetchInterval: 60_000,
  });
  return data ?? EMPTY_SCORE;
}
