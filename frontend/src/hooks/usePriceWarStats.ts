import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface PriceWarProduct {
  sku: string;
  name: string;
  url: string | null;
  our_price: number;
  our_rank: number | null;
  competitors_count: number;
  best_competitor_price: number | null;
  best_competitor_name: string | null;
  updated_at: string | null;
}

export interface PriceWarStats {
  summary: {
    total_in_war: number;
    avg_rank: number;
    winning_count: number;
    losing_count: number;
  };
  products: PriceWarProduct[];
}

// Цена лидера рынка: если 1-е место — это мы, лидер = наша цена, иначе — лучший конкурент.
export const leaderPrice = (p: Pick<PriceWarProduct, 'our_rank' | 'our_price' | 'best_competitor_price'>): number | null =>
  p.our_rank === 1 ? p.our_price : p.best_competitor_price;

const EMPTY_PRICE_WAR: PriceWarStats = {
  summary: { total_in_war: 0, avg_rank: 0, winning_count: 0, losing_count: 0 },
  products: [],
};

const fetchPriceWar = async (): Promise<PriceWarStats> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/price-war`);
    const data = await res.json();
    return res.ok && data.success ? data : EMPTY_PRICE_WAR;
  } catch {
    return EMPTY_PRICE_WAR;
  }
};

export function usePriceWarStats() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['analytics-price-war', activeStoreId],
    queryFn: fetchPriceWar,
    refetchInterval: 60_000,
  });
  return data ?? EMPTY_PRICE_WAR;
}
