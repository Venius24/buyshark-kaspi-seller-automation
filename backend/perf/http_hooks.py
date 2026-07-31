"""
Учёт исходящего трафика к Kaspi (и куда угодно ещё) без правки полусотни
call-site'ов requests.get/post по всему backend/. Патчится одна точка
воронки на каждую HTTP-библиотеку:

- requests.sessions.Session.send — через него проходят все requests.get/
  post/put вызовы, включая переиспользуемые requests.Session() в
  backend/chatbot/*.
- curl_cffi.requests.Session.request — module-level curl_cffi.requests.get/
  post (backend/dumper/radar.py) внутри себя открывают `with Session(...) as
  s: return s.request(...)`, так что патч Session.request ловит и их.

Известный пробел (см. план): трафик Patchright/Playwright-браузера
(idmc.shop.kaspi.kz логин) идёт мимо этих двух библиотек и здесь не
учитывается — это отмечается отдельно в отчёте, а не выдаётся за полный охват.
"""

import logging
import time
from urllib.parse import urlparse

from backend.perf.collector import collector, is_enabled

logger = logging.getLogger(__name__)

_KNOWN_HOST_FAMILIES = {
    "kaspi.kz": "kaspi.kz",
    "mc.shop.kaspi.kz": "mc.shop.kaspi.kz",
    "idmc.shop.kaspi.kz": "idmc.shop.kaspi.kz",
    "msg-web.kaspi.kz": "msg-web.kaspi.kz",
}


def _classify_host(url: str) -> str:
    try:
        host = urlparse(url).netloc
    except Exception:
        return "unknown"
    return _KNOWN_HOST_FAMILIES.get(host, host or "unknown")


def _path_key(url: str) -> str:
    try:
        path = urlparse(url).path
    except Exception:
        return "/"
    segments = []
    for seg in path.split("/"):
        # Схлопываем числовые/длинные (id-подобные) сегменты, чтобы не
        # взрывать кардинальность ключей per-order/per-sku запросами.
        if seg.isdigit() or len(seg) > 24:
            segments.append("{id}")
        else:
            segments.append(seg)
    return "/".join(segments) or "/"


def _safe_len(data) -> int:
    if not data:
        return 0
    try:
        if isinstance(data, bytes):
            return len(data)
        if isinstance(data, str):
            return len(data.encode("utf-8", errors="ignore"))
        return len(str(data).encode("utf-8", errors="ignore"))
    except Exception:
        return 0


def install_http_hooks():
    """Идемпотентно патчит requests и curl_cffi. Вызывается один раз при
    старте, только если is_enabled()."""
    if not is_enabled():
        return
    _patch_requests()
    _patch_curl_cffi()


def _patch_requests():
    try:
        import requests
    except ImportError:
        return

    original_send = requests.sessions.Session.send
    if getattr(original_send, "_perf_patched", False):
        return

    def patched_send(self, request, **kwargs):
        start = time.perf_counter()
        response = original_send(self, request, **kwargs)
        elapsed = time.perf_counter() - start
        try:
            req_bytes = _safe_len(request.body)
            resp_bytes = _safe_len(getattr(response, "content", None))
            host_family = _classify_host(request.url)
            collector.record_outbound(host_family, _path_key(request.url), elapsed, req_bytes, resp_bytes)
        except Exception:
            logger.debug("perf http_hooks: failed to record requests call", exc_info=True)
        return response

    patched_send._perf_patched = True
    requests.sessions.Session.send = patched_send


def _patch_curl_cffi():
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return

    original_request = curl_requests.Session.request
    if getattr(original_request, "_perf_patched", False):
        return

    def patched_request(self, method, url, *args, **kwargs):
        start = time.perf_counter()
        response = original_request(self, method, url, *args, **kwargs)
        elapsed = time.perf_counter() - start
        try:
            req_bytes = _safe_len(kwargs.get("json")) or _safe_len(kwargs.get("data"))
            resp_bytes = _safe_len(getattr(response, "content", None))
            host_family = _classify_host(url)
            collector.record_outbound(host_family, _path_key(url), elapsed, req_bytes, resp_bytes)
        except Exception:
            logger.debug("perf http_hooks: failed to record curl_cffi call", exc_info=True)
        return response

    patched_request._perf_patched = True
    curl_requests.Session.request = patched_request
