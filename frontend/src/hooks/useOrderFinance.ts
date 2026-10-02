import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface OrderFinance {
  period: { from: string; to: string; days: number; cohort: string };
  commission_table: string;
  orders: { completed: number; returned: number; cancelled: number };
  revenue: number;
  commission: {
    total: number;
    without_vat: number;
    vat: number;
    share_of_revenue_pct: number;
    is_estimate: boolean;
    uncertain_orders: number;
    unknown_categories: Record<string, number>;
    by_category: { category: string; revenue: number; commission: number }[];
  };
  delivery: { completed: number; returned: number; total: number; avg_per_order: number };
  returns: {
    count: number;
    amount: number;
    rate_pct: number;
    returned_to_warehouse: number;
    top_skus: { sku: string; qty: number }[];
  };
  cancellations: {
    count: number;
    amount: number;
    merchant_fault: number;
    by_reason: { reason: string; label: string; fault: string; count: number }[];
  };
  payment_modes: Record<string, number>;
  net_after_kaspi: number;
}

export const FINANCE_PERIODS = [7, 14, 30] as const;
export type FinancePeriod = (typeof FINANCE_PERIODS)[number];

// null — Kaspi не ответил (502) или сеть: виджет показывает ошибку, а не нули,
// иначе «комиссия 0 ₸» выглядела бы как правда.
const fetchFinance = async (days: number): Promise<OrderFinance | null> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/finance?days=${days}`);
    const data = await res.json();
    return res.ok && data.success ? data : null;
  } catch {
    return null;
  }
};

export function useOrderFinance(days: FinancePeriod) {
  const activeStoreId = useActiveStoreId();
  // Бэкенд считает вживую из Kaspi и держит ответ 30 минут — опрашивать
  // чаще бессмысленно, цифры по закрытым заказам за это время не меняются.
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['analytics-finance', activeStoreId, days],
    queryFn: () => fetchFinance(days),
    staleTime: 15 * 60_000,
    refetchInterval: 30 * 60_000,
  });
  return { data: data ?? null, isLoading, isFetching };
}
