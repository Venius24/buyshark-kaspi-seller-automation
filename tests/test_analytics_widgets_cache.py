"""
Регрессия (2026-07-29): GET /api/analytics/widgets дёргал живой синхронный
запрос к публичному API конкурентов Kaspi (_fetch_offers_sync) на КАЖДЫЙ вызов.
Фронтенд поллит этот эндпоинт каждые 10 секунд, пока открыта вкладка Overview —
это давало до 360 запросов/час к Kaspi на один и тот же SKU и, судя по всему,
вызвало рейт-лимит/бан IP продовой VPS у Kaspi (подтверждено вживую: 429 даже
на kaspi.kz/shop/ с забаненного IP). _get_cached_offers должен отдавать
закэшированный результат в пределах TTL, чтобы не долбить Kaspi на каждый poll.
"""
from unittest.mock import patch

from backend.routers import analytics


def test_second_call_within_ttl_uses_cache():
    analytics._widget_offers_cache.clear()
    with patch("backend.routers.analytics._fetch_offers_sync", return_value=[{"price": 100}]) as mock_fetch:
        first = analytics._get_cached_offers("175942131", "750000000")
        second = analytics._get_cached_offers("175942131", "750000000")

    assert mock_fetch.call_count == 1
    assert first == [{"price": 100}]
    assert second == [{"price": 100}]


def test_call_after_ttl_expiry_refetches():
    analytics._widget_offers_cache.clear()
    with patch("backend.routers.analytics._fetch_offers_sync", return_value=[{"price": 100}]) as mock_fetch:
        analytics._get_cached_offers("175942131", "750000000")
        # Симулируем, что кэш устарел, отодвинув сохранённый timestamp в прошлое.
        cache_key = ("175942131", "750000000")
        stale_ts, offers = analytics._widget_offers_cache[cache_key]
        analytics._widget_offers_cache[cache_key] = (
            stale_ts - analytics._WIDGET_OFFERS_CACHE_TTL_SECONDS - 1,
            offers,
        )
        analytics._get_cached_offers("175942131", "750000000")

    assert mock_fetch.call_count == 2


def test_different_sku_or_city_bypasses_cache():
    analytics._widget_offers_cache.clear()
    with patch("backend.routers.analytics._fetch_offers_sync", return_value=[]) as mock_fetch:
        analytics._get_cached_offers("175942131", "750000000")
        analytics._get_cached_offers("922222222", "750000000")
        analytics._get_cached_offers("175942131", "310010000")

    assert mock_fetch.call_count == 3
