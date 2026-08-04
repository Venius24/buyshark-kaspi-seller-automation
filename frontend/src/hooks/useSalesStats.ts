import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface SalesDailyPoint {
  date: string;
  revenue: number;
  orders: number;
}

export interface SalesTopSku {
  sku: string;
  name: string;
  revenue: number;
  quantity: number;
}

export interface SalesSummary {
  daily: SalesDailyPoint[];
  top_skus: SalesTopSku[];
  total_revenue: number;
  total_orders: number;
  avg_order_value: number;
}

const EMPTY_SUMMARY: SalesSummary = {
  daily: [],
  top_skus: [],
  total_revenue: 0,
  total_orders: 0,
  avg_order_value: 0,
};

const fetchSales = async (): Promise<SalesSummary> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/sales?days=30`);
    const data = await res.json();
    return res.ok && data.success ? data : EMPTY_SUMMARY;
  } catch {
    return EMPTY_SUMMARY;
  }
};

export function useSalesStats() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['analytics-sales', activeStoreId],
    queryFn: fetchSales,
    refetchInterval: 60_000,
  });
  return data ?? EMPTY_SUMMARY;
}
