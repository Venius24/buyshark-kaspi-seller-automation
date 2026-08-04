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
const PERSISTED_CACHE_KEY = 'buyshark-query-cache';

// Версия формы/семантики кэша. persistOptions.buster в main.tsx сверяет это
// значение с тем, что было записано при сохранении -- при расхождении
// react-query сам выкидывает старый снимок ДО гидратации, без явного
// логина/логаута. Без этого пользователь, чья вкладка была открыта ДО
// деплоя фикса за 3.26/3.27, продолжал бы видеть отравленный кэш из
// прошлой сессии (протухший токен/пустые магазины) сколько угодно долго --
// один лишь перезаход в код фикса это не лечит для УЖЕ открытой вкладки.
// Бампать при следующем похожем инциденте.
export const CACHE_BUSTER = 'v2-2026-08-04-session-scoped';

export const queryPersister = createSyncStoragePersister({
  storage: window.localStorage,
  key: PERSISTED_CACHE_KEY,
});

// Кэш не привязан к пользователю (ключ в localStorage один на всех). При
// смене аккаунта на одном устройстве (логаут/логин другим email, или
// логаут по протухшему токену -> повторный вход) react-query иначе рисует
// экран из чужого/устаревшего снимка (напр. пустой список магазинов
// прошлого аккаунта), пока фоновый refetch его не перезапишет — на глаз
// выглядит как "магазин не подключён". Звать при каждой смене сессии.
export function clearPersistedQueryCache() {
  queryClient.clear();
  window.localStorage.removeItem(PERSISTED_CACHE_KEY);
}
