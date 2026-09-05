import { useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchStores } from './queries';

// Слайс 6: единственный источник правды "какой магазин сейчас выбран" —
// раньше это был локальный useState внутри StoreHub.tsx, который терялся
// при уходе с вкладки и никогда не доходил до apiFetch (backend всегда
// резолвил самый старый магазин пользователя, что бы ни было выбрано в UI).
// Персистится в localStorage (переживает перезагрузку страницы), читается
// синхронно из apiFetch/getWsUrl (обычные функции вне React-дерева) и через
// useActiveStoreId() в компонентах (подписка на изменения).

const STORAGE_KEY = 'active_store_id';

type Listener = () => void;
let listeners: Listener[] = [];

export function getActiveStoreId(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

export function setActiveStoreId(storeId: string | null): void {
  if (getActiveStoreId() === storeId) return;
  if (storeId) {
    localStorage.setItem(STORAGE_KEY, storeId);
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: Listener): () => void {
  listeners.push(listener);
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

export function useActiveStoreId(): string | null {
  return useSyncExternalStore(subscribe, getActiveStoreId);
}

// Роль текущего пользователя в активном магазине (OWNER/MANAGER/VIEWER).
// GET /api/v1/stores уже отдаёт role на каждый магазин (StoreHub.tsx грузит
// его через тот же ['stores'] queryKey) — react-query дедуплицирует запрос,
// доп. сетевого похода на роль не требуется.
export function useActiveStoreRole(): string | null {
  const activeStoreId = useActiveStoreId();
  const { data: stores = [] } = useQuery({ queryKey: ['stores'], queryFn: fetchStores });
  const store = stores.find((s) => s.id === activeStoreId);
  return store?.role ?? null;
}

export function useCanEdit(): boolean {
  return useActiveStoreRole() !== 'VIEWER';
}

// Разделы кабинета, доступные в активном магазине (вторая ось прав, см.
// backend/security/sections.py). Источник — тот же ['stores'] queryKey, что и
// у роли: справочника разделов на клиенте нет, показываем ровно то, что
// разрешил бэкенд. null = ещё не загрузилось или магазин не отдал список.
export function useActiveStoreSections(): Set<string> | null {
  const activeStoreId = useActiveStoreId();
  const { data: stores = [] } = useQuery({ queryKey: ['stores'], queryFn: fetchStores });
  const store = stores.find((s) => s.id === activeStoreId) ?? (stores.length === 1 ? stores[0] : undefined);
  if (!store || !store.sections) return null;
  return new Set(store.sections);
}

// Fail-open, пока список не загружен: настоящая проверка всё равно на
// бэкенде (403 из store_section_guard), здесь только UX. Иначе на каждой
// перезагрузке страницы сайдбар на долю секунды схлопывался бы в пустоту.
export function useHasSection(): (section: string) => boolean {
  const sections = useActiveStoreSections();
  return (section: string) => sections === null || sections.has(section);
}

export function useIsStoreOwner(): boolean {
  // Fail-open, пока роль ещё не загружена (null) — реальная защита всё
  // равно на бэкенде (403), здесь только UX. Без этого OWNER на долю
  // секунды терял бы вкладку "Тариф и Баланс" при каждой загрузке страницы.
  const role = useActiveStoreRole();
  return role === null || role === 'OWNER';
}
