import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface AbcXyzItem {
  sku: string;
  name: string;
  revenue: number;
  revenue_share: number;
  quantity: number;
  abc_class: 'A' | 'B' | 'C';
  xyz_class: 'X' | 'Y' | 'Z' | null;
  cv_pct: number | null;
}

export interface AbcXyzStats {
  items: AbcXyzItem[];
  matrix: Record<string, number>;
  weeks_covered: number;
  total_revenue: number;
}

const EMPTY_ABC_XYZ: AbcXyzStats = {
  items: [],
  matrix: {},
  weeks_covered: 0,
  total_revenue: 0,
};

const fetchAbcXyz = async (): Promise<AbcXyzStats> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/abc-xyz?days=90`);
    const data = await res.json();
    return res.ok && data.success ? data : EMPTY_ABC_XYZ;
  } catch {
    return EMPTY_ABC_XYZ;
  }
};

export function useAbcXyzStats() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['analytics-abc-xyz', activeStoreId],
    queryFn: fetchAbcXyz,
    refetchInterval: 120_000,
  });
  return data ?? EMPTY_ABC_XYZ;
}
