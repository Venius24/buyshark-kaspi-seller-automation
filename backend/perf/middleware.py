"""
Тайминг входящих запросов. Регистрируется в backend/main.py рядом с
CORSMiddleware, только если PERF_PROFILING=1.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.perf.collector import collector


def _route_key(request: Request) -> str:
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        # Шаблон роута ("/api/products/{sku}"), а не сырой URL — иначе
        # кардинальность ключей растёт на каждый уникальный SKU/order id.
        return f"{request.method} {route.path}"
    return f"{request.method} {request.url.path}"


class PerfTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        resp_bytes = 0
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                resp_bytes = int(content_length)
            except ValueError:
                resp_bytes = 0

        collector.record_inbound(_route_key(request), elapsed, resp_bytes)
        return response
