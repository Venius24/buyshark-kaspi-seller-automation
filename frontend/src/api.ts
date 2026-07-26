export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000';

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

  const updatedInit: RequestInit = {
    ...init,
    headers,
  };

  try {
    const response = await fetch(input, updatedInit);

    if (response.status === 401) {
      localStorage.removeItem('access_token');
      if (unauthorizedHandler) {
        unauthorizedHandler();
      }
    }

    return response;
  } catch (error) {
    throw error;
  }
}
