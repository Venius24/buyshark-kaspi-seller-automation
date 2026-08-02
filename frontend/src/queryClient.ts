import { QueryClient } from '@tanstack/react-query';
import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Переживает размонтирование вкладки (react-query по умолчанию держит
      // неактивные данные в памяти 5 мин) — этого достаточно, чтобы кэш
      // из localStorage (см. persister ниже) успел restore-иться раньше,
      // чем что-либо его вычистит.
      gcTime: 24 * 60 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// Переживает полную перезагрузку страницы/повторный вход: при заходе в
// аккаунт список магазинов/групп/товаров рисуется мгновенно из последнего
// известного состояния (stale), а не с пустого экрана в ожидании round-trip
// к бэкенду — обновление приходит следом в фоне благодаря staleTime выше.
export const queryPersister = createSyncStoragePersister({
  storage: window.localStorage,
  key: 'buyshark-query-cache',
});
