import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface CompetitorLeaderboardEntry {
  name: string;
  count: number;
}

export interface CatalogCoverage {
  total_products: number;
  dumping_enabled: number;
  coverage_pct: number;
}

export interface ChatActivityPoint {
  date: string;
  count: number;
}

export interface InsightsStats {
  competitor_leaderboard: CompetitorLeaderboardEntry[];
  catalog_coverage: CatalogCoverage;
  hourly_heatmap: number[][];
  chat_activity: ChatActivityPoint[];
}

const EMPTY_INSIGHTS: InsightsStats = {
  competitor_leaderboard: [],
  catalog_coverage: { total_products: 0, dumping_enabled: 0, coverage_pct: 0 },
  hourly_heatmap: Array.from({ length: 7 }, () => Array(24).fill(0)),
  chat_activity: [],
};

const fetchInsights = async (): Promise<InsightsStats> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/insights`);
    const data = await res.json();
    return res.ok && data.success ? data : EMPTY_INSIGHTS;
  } catch {
    return EMPTY_INSIGHTS;
  }
};

export function useInsightsStats() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['analytics-insights', activeStoreId],
    queryFn: fetchInsights,
    refetchInterval: 120_000,
  });
  return data ?? EMPTY_INSIGHTS;
}
