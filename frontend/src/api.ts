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

function buildHeaders(init: RequestInit, token: string | null): Headers {
  const headers = new Headers(init.headers || {});
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const storeId = getActiveStoreId();
  if (storeId && !headers.has('X-Store-Id')) {
    headers.set('X-Store-Id', storeId);
  }
  return headers;
}

// access_token живёт всего 15 минут (backend/security/auth.py), а обновлять
// его раньше было некому: apiFetch просто отдавал 401 наружу без ретрая.
// На телефоне тестеры обычно открывают приложение заново (свежий токен),
// на ПК вкладка годами висит открытой — токен тухнет, и КАЖДЫЙ запрос
// (mc-status, stores, products...) начинает 401'ить молча, выглядит как
// "магазин отключился". Бэкенд `/api/v1/auth/refresh` уже читает httpOnly
// refresh_token-куку и живой уже 30 дней (REFRESH_TOKEN_EXPIRE_DAYS) — просто
// никогда не вызывался с фронта. Дедуп через inFlightRefresh, чтобы 5-10
// параллельных запросов, словивших 401 одновременно, не долбили /refresh каждый.
let inFlightRefresh: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (!inFlightRefresh) {
    inFlightRefresh = (async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/auth/refresh`, { method: 'POST' });
        if (!res.ok) return null;
        const data = await res.json();
        if (data.access_token) {
          localStorage.setItem('access_token', data.access_token);
          return data.access_token as string;
        }
        return null;
      } catch {
        return null;
      } finally {
        inFlightRefresh = null;
      }
    })();
  }
  return inFlightRefresh;
}

export async function apiFetch(input: string | URL, init: RequestInit = {}): Promise<Response> {
  const urlStr = input.toString();
  // login/register не несут токен, а /refresh сам себя ретраить не должен
  // (иначе бесконечная рекурсия при протухшем refresh_token).
  const isAuthBootstrapEndpoint =
    urlStr.includes('/api/v1/auth/login') ||
    urlStr.includes('/api/v1/auth/register') ||
    urlStr.includes('/api/v1/auth/refresh');

  const token = localStorage.getItem('access_token');
  let response: Response;
  try {
    response = await fetch(input, { ...init, headers: buildHeaders(init, token) });
  } catch (error) {
    throw error;
  }

  if (response.status === 401 && !isAuthBootstrapEndpoint) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      response = await fetch(input, { ...init, headers: buildHeaders(init, newToken) });
    }
  }

  if (response.status === 401) {
    localStorage.removeItem('access_token');
    if (unauthorizedHandler) {
      unauthorizedHandler();
    }
  }

  return response;
}
