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
// никогда не вызывался с фронта.
//
// Дедуп через inFlightRefresh покрывает только запросы, 401'нувшие в один
// синхронный тик. На практике (проверено вживую на app.buyshark.kz) при
// загрузке страницы с пустым токеном ~15 react-query хуков стартуют fetch
// одновременно, но их 401-ответы приходят не одномоментно — каждый находит
// inFlightRefresh уже сброшенным в null и бьёт /refresh заново (15 лишних
// запросов на один заход). REFRESH_COOLDOWN_MS держит только что полученный
// токен ещё немного "свежим": 401, пришедший вскоре после чужого успешного
// refresh, просто перечитывает localStorage вместо нового похода в БД.
let inFlightRefresh: Promise<string | null> | null = null;
let lastRefreshResolvedAt = 0;
const REFRESH_COOLDOWN_MS = 3000;

async function refreshAccessToken(): Promise<string | null> {
  if (inFlightRefresh) return inFlightRefresh;

  if (Date.now() - lastRefreshResolvedAt < REFRESH_COOLDOWN_MS) {
    return localStorage.getItem('access_token');
  }

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
      lastRefreshResolvedAt = Date.now();
    }
  })();
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
      // Токен обновился успешно -- значит сессия живая. Если конкретный
      // эндпоинт всё равно 401'ит и со свежим токеном (напр.
      // get_store_context в backend/security/store_context.py отдаёт 401
      // "Требуется вход в аккаунт с подключённым магазином" просто когда
      // магазин ещё не выбран/не подключён -- это НЕ протухший токен), это
      // 401 не про авторизацию, и разлогинивать пользователя из-за него
      // нельзя. Регрессия из 3.24: раньше любой такой бизнес-401 молча
      // проглатывался, теперь же после ретрая он бы разлогинивал ПОСЛЕ
      // КАЖДОГО действия. Возвращаем ответ как есть, не трогая сессию.
      return await fetch(input, { ...init, headers: buildHeaders(init, newToken) });
    }
    // Сам /auth/refresh не смог обновить токен (refresh_token тоже мёртв
    // или отсутствует) -- вот это уже реально означает разлогин.
    localStorage.removeItem('access_token');
    if (unauthorizedHandler) {
      unauthorizedHandler();
    }
  }

  return response;
}
