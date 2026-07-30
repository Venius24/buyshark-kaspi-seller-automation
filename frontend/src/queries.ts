import { apiFetch, API_URL } from './api';
import type { StoreItem, UserProfile } from './components/StoreHub';
import type { MasterStockGroup, ProductItem } from './components/MasterStockGroups';

export const fetchProfile = async (): Promise<UserProfile | null> => {
  const res = await apiFetch(`${API_URL}/api/v1/auth/me`);
  if (!res.ok) return null;
  return res.json();
};

export const fetchStores = async (): Promise<StoreItem[]> => {
  const res = await apiFetch(`${API_URL}/api/v1/stores`);
  if (!res.ok) return [];
  return res.json();
};

export const fetchGroupsQuery = async (): Promise<MasterStockGroup[]> => {
  const res = await apiFetch(`${API_URL}/api/stock-groups`);
  const data = await res.json();
  return data.success ? (data.groups || []) : [];
};

export const fetchProductsQuery = async (): Promise<ProductItem[]> => {
  const res = await apiFetch(`${API_URL}/api/products?page=1&page_size=1000&status=all`);
  const data = await res.json();
  return data.success ? (data.products || []) : [];
};
