import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface StoreTag {
  tag: string;
  products: number;
}

/**
 * Справочника меток нет — список собирается из того, что реально висит на
 * товарах. Поэтому после сохранения меток инвалидируем и его тоже: иначе
 * только что придуманная метка не появилась бы в подсказках.
 */
export function useStoreTags() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['product-tags', activeStoreId],
    queryFn: async (): Promise<StoreTag[]> => {
      const res = await apiFetch(`${API_URL}/api/products/tags`);
      if (!res.ok) return [];
      const json = await res.json();
      return json.tags ?? [];
    },
  });
  return data ?? [];
}

export function useSaveProductTags() {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();

  return useMutation({
    mutationFn: async ({ sku, tags }: { sku: string; tags: string[] }) => {
      const res = await apiFetch(`${API_URL}/api/products/${encodeURIComponent(sku)}/tags`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tags }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || 'Не удалось сохранить метки');
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['product-tags', activeStoreId] });
      queryClient.invalidateQueries({ queryKey: ['products'] });
    },
  });
}
