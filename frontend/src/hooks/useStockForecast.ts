import { useQuery } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export type StockBucket = 'out_of_stock' | 'critical' | 'warning' | 'ok' | 'slow' | 'dead';

export interface StockForecastItem {
  kind: 'sku' | 'group';
  key: string;
  name: string;
  merchant_sku: string | null;
  kaspi_sku: string | null;
  external_code: string | null;
  image: string | null;
  status: string;
  in_catalog: boolean;
  members_count: number;
  deduct_per_unit: number;
  stock: number;
  sold: number;
  sold_recent: number;
  velocity: number;
  velocity_recent: number;
  velocity_long: number;
  days_to_zero: number | null;
  runs_out_on: string | null;
  bucket: StockBucket;
  recommended_qty: number;
  reorder_cost: number;
  price: number;
}

export interface StockForecast {
  items: StockForecastItem[];
  // true, если на большом каталоге бэкенд урезал items (summary при этом
  // всегда честный по полному набору) — см. _select_items_for_response.
  items_truncated: boolean;
  window: { from: string; to: string };
  params: {
    window_days: number;
    recent_days: number;
    lead_time_days: number;
    cover_days: number;
    horizon_days: number;
    slow_days: number;
  };
  summary: {
    out_of_stock: number;
    critical: number;
    warning: number;
    ok: number;
    slow: number;
    dead: number;
    total_items: number;
    reorder_positions: number;
    reorder_units: number;
    reorder_cost: number;
    frozen_cost: number;
    // Остаток в «медленных» позициях по цене продажи: живой товар, который
    // оборачивается слишком долго (в отличие от frozen_cost по «мёртвым»).
    slow_cost: number;
    // Артикулы из истории продаж, отсутствующие в текущем каталоге: в вёдра
    // не входят (заказать по ним нечего), но и пропасть молча не должны.
    unmatched_skus: number;
    unmatched_sold: number;
  };
}

export interface ForecastSettings {
  leadTimeDays: number;
  coverDays: number;
  horizonDays: number;
  slowDays: number;
}

// Срок поставки у каждого магазина (и каждого поставщика) свой, но менять его
// продавец будет раз в жизни — держим последний выбор локально, чтобы не
// заводить ради трёх чисел таблицу настроек на бэкенде.
const SETTINGS_KEY = 'stock_forecast_settings_v1';

export const DEFAULT_FORECAST_SETTINGS: ForecastSettings = {
  leadTimeDays: 14,
  coverDays: 14,
  horizonDays: 7,
  slowDays: 90,
};

export function loadForecastSettings(): ForecastSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_FORECAST_SETTINGS;
    const parsed = JSON.parse(raw);
    return {
      leadTimeDays: Number(parsed.leadTimeDays) || DEFAULT_FORECAST_SETTINGS.leadTimeDays,
      coverDays: Number(parsed.coverDays) || DEFAULT_FORECAST_SETTINGS.coverDays,
      horizonDays: Number(parsed.horizonDays) || DEFAULT_FORECAST_SETTINGS.horizonDays,
      slowDays: Number(parsed.slowDays) || DEFAULT_FORECAST_SETTINGS.slowDays,
    };
  } catch {
    return DEFAULT_FORECAST_SETTINGS;
  }
}

export function saveForecastSettings(settings: ForecastSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* приватный режим браузера — не повод ронять виджет */
  }
}

export const EMPTY_FORECAST: StockForecast = {
  items: [],
  items_truncated: false,
  window: { from: '', to: '' },
  params: { window_days: 60, recent_days: 14, lead_time_days: 14, cover_days: 14, horizon_days: 7, slow_days: 90 },
  summary: {
    out_of_stock: 0,
    critical: 0,
    warning: 0,
    ok: 0,
    slow: 0,
    dead: 0,
    total_items: 0,
    reorder_positions: 0,
    reorder_units: 0,
    reorder_cost: 0,
    frozen_cost: 0,
    slow_cost: 0,
    unmatched_skus: 0,
    unmatched_sold: 0,
  },
};

export function forecastQueryString(s: ForecastSettings): string {
  return `lead_time_days=${s.leadTimeDays}&cover_days=${s.coverDays}&horizon_days=${s.horizonDays}&slow_days=${s.slowDays}`;
}

const fetchForecast = async (s: ForecastSettings): Promise<StockForecast> => {
  try {
    const res = await apiFetch(`${API_URL}/api/analytics/stock-forecast?${forecastQueryString(s)}`);
    const data = await res.json();
    return res.ok && data.success ? data : EMPTY_FORECAST;
  } catch {
    return EMPTY_FORECAST;
  }
};

export function useStockForecast(settings: ForecastSettings) {
  const activeStoreId = useActiveStoreId();
  const { data, isFetching } = useQuery({
    queryKey: ['stock-forecast', activeStoreId, settings.leadTimeDays, settings.coverDays, settings.horizonDays, settings.slowDays],
    queryFn: () => fetchForecast(settings),
    refetchInterval: 300_000,
  });
  return { data: data ?? EMPTY_FORECAST, isFetching };
}
