"""
Выгрузка настроек демпинга (min/max цена, шаг, алгоритм) из личного
кабинета конкурентного сервиса PriceFeed.kz — см. полное описание реверс-
инжиниринга в REAL_IMPORTANT/импорт_pricefeed/PRICEFEED_IMPORT.md.

Их фронт ходит в обычный JSON REST, авторизация — только по cookie сессии
браузера (без отдельного API-токена). Мы логинимся сюда headless-Chromium
через Patchright с телефоном+паролем, которые ввёл сам клиент (это его
собственный аккаунт), и после логина тянем страницы через
context.request — тот же залогиненный APIRequestContext, без переноса кук
в отдельную requests-сессию.

Пароль здесь используется ТОЛЬКО как параметр функции и нигде не логируется,
не сохраняется, не возвращается наружу.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from patchright.sync_api import sync_playwright

from backend.services.playwright_worker import _launch_browser, _is_headless

logger = logging.getLogger(__name__)

BASE = "https://pricefeed.kz/cabinet/api/v1"
LOGIN_URL = "https://pricefeed.kz/cabinet/sign-in?login=phone"

_PAGE_SIZE = 20
_DEFAULT_MAX_PAGES = 120  # хардстоп ~2400 товаров — safety net от бесконечной пагинации
_PAGE_DELAY_SECONDS = 0.2
_LOGIN_TIMEOUT_MS = 15_000


class PriceFeedError(Exception):
    """Базовая ошибка выгрузки из PriceFeed."""


class PriceFeedAuthError(PriceFeedError):
    """Не удалось войти (неверный телефон/пароль, либо сессия отвалилась)."""


class PriceFeedRateLimited(PriceFeedError):
    """PriceFeed ограничил запросы (квота ~250/30мин)."""


class PriceFeedNoMerchants(PriceFeedError):
    """У аккаунта нет ни одного магазина."""


@dataclass
class PriceFeedMerchant:
    id: str
    name: str


@dataclass
class PriceFeedFetchResult:
    merchant: Optional[PriceFeedMerchant]
    merchants: list = field(default_factory=list)  # list[PriceFeedMerchant]
    items: list = field(default_factory=list)  # сырые content[] элементы, немаппленные
    pages_fetched: int = 0
    truncated: bool = False


def _extract_merchants(payload) -> list:
    """merchants/list может прийти как голый список либо как страница
    {"content": [...]} — на живом аккаунте видели вариант с пагинацией
    у city-prices, поэтому подстраховываемся под оба варианта."""
    raw = payload.get("content", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        return []
    merchants = []
    for m in raw:
        mid = str(m.get("id") or m.get("merchantId") or "").strip()
        if not mid:
            continue
        name = m.get("name") or m.get("merchantName") or mid
        merchants.append(PriceFeedMerchant(id=mid, name=name))
    return merchants


def _login(page, context, phone: str, password: str) -> None:
    page.goto(LOGIN_URL)

    clean_phone = "".join(c for c in str(phone) if c.isdigit())
    if len(clean_phone) == 11 and clean_phone.startswith(("7", "8")):
        clean_phone = clean_phone[1:]

    phone_el = page.get_by_placeholder("Номер телефона")
    phone_el.click()
    phone_el.fill(clean_phone)

    password_el = page.get_by_placeholder("Пароль")
    password_el.click()
    password_el.fill(password)

    # exact=True — на странице входа есть ещё OAuth-кнопка "Войти через
    # аккаунт Google", чей accessible name матчился как substring без exact
    # (страйкт-мод violation, поймано вживую 2026-08-12).
    page.get_by_role("button", name="Вход", exact=True).click()

    deadline = time.monotonic() + (_LOGIN_TIMEOUT_MS / 1000)
    while time.monotonic() < deadline:
        if "sign-in" not in page.url:
            break
        page.wait_for_timeout(500)

    if "sign-in" in page.url:
        raise PriceFeedAuthError("Не удалось войти в PriceFeed. Проверьте номер телефона и пароль.")


def _get_json(context, url: str, params: dict) -> dict:
    resp = context.request.get(url, params=params)
    if resp.status == 401:
        raise PriceFeedAuthError("Сессия PriceFeed истекла в процессе выгрузки.")
    if resp.status in (403, 429):
        raise PriceFeedRateLimited(
            "PriceFeed временно ограничил запросы (квота). Попробуйте импорт позже."
        )
    if resp.status >= 400:
        raise PriceFeedError(f"PriceFeed вернул ошибку {resp.status} на {url}")
    return resp.json()


def _fetch_all_pages(fetch_page, max_pages: int = _DEFAULT_MAX_PAGES, delay_s: float = _PAGE_DELAY_SECONDS):
    """Пагинация city-prices. fetch_page(page_number) -> сырой JSON-ответ
    страницы (с ключами content/totalPages). Вынесена в чистую функцию,
    принимающую callable, — это шов для юнит-тестов без браузера/сети.

    Возвращает (items: list, pages_fetched: int, truncated: bool).
    """
    first = fetch_page(0)
    content = first.get("content") or []
    total_pages = int(first.get("totalPages") or (1 if content else 0))

    items = list(content)
    pages_fetched = 1 if total_pages > 0 else 0
    truncated = False

    page_num = 1
    while page_num < total_pages:
        if page_num >= max_pages:
            truncated = True
            break
        time.sleep(delay_s)
        data = fetch_page(page_num)
        items.extend(data.get("content") or [])
        pages_fetched += 1
        page_num += 1

    return items, pages_fetched, truncated


def fetch_pricefeed_settings(
    phone: str,
    password: str,
    merchant_id: Optional[str] = None,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> PriceFeedFetchResult:
    """Логинится в PriceFeed под клиентским аккаунтом и выгружает city-prices
    выбранного (или единственного) магазина. Если магазинов несколько и
    merchant_id не передан — возвращает результат с items=[] и заполненным
    merchants, чтобы вызывающий код переспросил у пользователя выбор."""
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(30_000)

            _login(page, context, phone, password)

            merchants_payload = _get_json(context, f"{BASE}/merchants/list", {"page": 0, "size": 20})
            merchants = _extract_merchants(merchants_payload)
            if not merchants:
                raise PriceFeedNoMerchants("В этом аккаунте PriceFeed нет ни одного магазина.")

            selected = None
            if merchant_id:
                selected = next((m for m in merchants if m.id == str(merchant_id)), None)
                if selected is None:
                    raise PriceFeedError(f"Магазин {merchant_id} не найден в этом аккаунте PriceFeed.")
            elif len(merchants) == 1:
                selected = merchants[0]

            if selected is None:
                # Несколько магазинов, выбор не передан — просим фронт уточнить,
                # не тратя квоту на выгрузку city-prices вслепую.
                return PriceFeedFetchResult(merchant=None, merchants=merchants, items=[], pages_fetched=1)

            def fetch_page(page_number: int) -> dict:
                return _get_json(
                    context,
                    f"{BASE}/merchants/{selected.id}/city-prices",
                    {"page": page_number, "filter": "ACTIVE"},
                )

            items, pages_fetched, truncated = _fetch_all_pages(fetch_page, max_pages=max_pages)

            return PriceFeedFetchResult(
                merchant=selected,
                merchants=merchants,
                items=items,
                pages_fetched=pages_fetched + 1,  # +1 за merchants/list
                truncated=truncated,
            )
        finally:
            try:
                browser.close()
            except Exception:
                pass
