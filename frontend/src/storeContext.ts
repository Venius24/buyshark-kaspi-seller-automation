import { useSyncExternalStore } from 'react';

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
