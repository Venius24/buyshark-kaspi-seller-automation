import { getActiveStoreId } from './storeContext';

export const API_URL = import.meta.env.VITE_API_URL || '';

export function getWsUrl(path: string = '/api/ws/telemetry'): string {
  // Браузерный WebSocket API не может слать кастомные заголовки (Authorization,
  // X-Store-Id), поэтому оба передаются через query-параметры — backend читает
  // их в resolve_ws_store_id() (Phase 6 + Слайс 6), чтобы телеметрия шла
  // только своему стору, а не самому старому магазину пользователя по умолчанию.
  const token = localStorage.getItem('access_token');
  const storeId = getActiveStoreId();
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  if (storeId) params.set('store_id', storeId);
  const suffix = params.toString() ? `?${params.toString()}` : '';

  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL + suffix;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  if (!host || host.includes('localhost:5173')) {
    return 'ws://localhost:5000' + path + suffix;
  }
  return `${protocol}//${host}${path}${suffix}`;
}

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler) {
  unauthorizedHandler = handler;
}

export async function apiFetch(input: string | URL, init: RequestInit = {}): Promise<Response> {
  const token = localStorage.getItem('access_token');
  const headers = new Headers(init.headers || {});

  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const storeId = getActiveStoreId();
  if (storeId && !headers.has('X-Store-Id')) {
    headers.set('X-Store-Id', storeId);
  }

  const updatedInit: RequestInit = {
    ...init,
    headers,
  };

  try {
    const response = await fetch(input, updatedInit);

    if (response.status === 401) {
      const urlStr = input.toString();
      const isSaaSAuthEndpoint = urlStr.includes('/api/v1/auth/') || urlStr.includes('/api/verify-session');
      if (isSaaSAuthEndpoint) {
        localStorage.removeItem('access_token');
        if (unauthorizedHandler) {
          unauthorizedHandler();
        }
      }
    }

    return response;
  } catch (error) {
    throw error;
  }
}
