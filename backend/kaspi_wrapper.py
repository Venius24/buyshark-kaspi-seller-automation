"""
KaspiAPI — чистый REST-клиент для Kaspi Merchant API v2 & MC API.
Исправлены статусы по умолчанию, пагинация и определение типов доставки (Express/Today/Tomorrow).
"""

import os
import json
import logging
import threading
import time
import base64
import datetime
import requests
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class AssortmentTruncated(Exception):
    """Выкачка ассортимента оборвалась и вернула НЕ весь каталог.

    Пагинация BFF ломается на любом не-200 или исключении и отдаёт то, что
    успела набрать. Для потребителей, которым важна свежесть (кэш товаров,
    воркеры), недобор терпим — следующий тик доберёт. Для прайс-фида он
    смертелен: оффер, не попавший в файл, Kaspi снимает с продажи с причиной
    «не указан в прайс-листе» (HANDOFF.md §3.67 — так у владельца слегло ~798
    карточек). Поэтому sync_merchant_assortment(strict=True) вместо
    усечённого списка бросает это исключение.
    """

    def __init__(self, reason: str, collected: int):
        self.reason = reason
        self.collected = collected
        super().__init__(f"Ассортимент выкачан не полностью ({collected} офферов): {reason}")


class KaspiAPI:
    BASE_URL = "https://kaspi.kz/shop/api/v2"
    TIMEOUT = 15
    MAX_RETRIES = 3
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # Категория 1 (B.2): один общий кэш get_all_packaging_tabs — раньше три
    # независимых места (routers/orders.py без кэша, routers/stock_groups.py
    # с 45с кэшем без ключа по token/store, services/stock_sync_worker.py
    # 30с-тик без кэша) гоняли один и тот же тяжёлый постраничный обход
    # независимо друг от друга. Ключ — сам token (у каждого стора свой REST
    # токен, поэтому это уже корректно разделяет сторы без отдельного
    # store_id — старый кэш в stock_groups.py был НЕ ключирован вообще).
    _packaging_tabs_cache: Dict[str, dict] = {}
    _packaging_tabs_cache_lock = threading.Lock()
    # Отдельный лок НА ТОКЕН — single-flight обхода (см.
    # get_all_packaging_tabs_cached). Держать для этого _packaging_tabs_cache_lock
    # нельзя: он общий на все сторы и охраняет сам словарь, а обход идёт минуты.
    _packaging_tabs_refresh_locks: Dict[str, threading.Lock] = {}
    _PACKAGING_TABS_WAIT_TIMEOUT = 60.0

    # merchant_id для токена не меняется, а резолвится он дорого: шаг через
    # /pointofservices на проде стабильно отдаёт 500, после чего идёт фоллбек
    # через историю заказов — 2 запроса к Kaspi на КАЖДЫЙ вызов. При этом
    # зовут его из горячих мест (луп чатбота, outbound, веер Мастер-Групп,
    # routers/chats.py на каждый HTTP-запрос фронта): в логах прода 2026-08-13
    # эти 500 шли по 5 раз в минуту непрерывно. Кэшируем по токену.
    _merchant_id_cache: Dict[str, dict] = {}
    _merchant_id_cache_lock = threading.Lock()
    _MERCHANT_ID_TTL_SECONDS = 21600.0  # 6 часов
    # Синтетический m_<hash> — это «не резолвится», а не ответ: держим его
    # недолго, чтобы погасить шторм, но не залипнуть, если Kaspi оживёт.
    _MERCHANT_ID_NEGATIVE_TTL_SECONDS = 60.0

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _decode_base64_if_needed(val: str) -> str:
        """Декодирует Base64 строку в числовой Kaspi Master SKU.
        
        Kaspi API может возвращать ID товара в Base64-кодировке.
        Kaspi Master SKU ОБЯЗАТЕЛЬНО состоит только из цифр.
        Если значение уже числовое — возвращает как есть.
        """
        if not val:
            return ""
        val_str = str(val).strip()
        if val_str.isdigit():
            return val_str
        try:
            decoded = base64.b64decode(val_str).decode("utf-8")
            if decoded.isdigit():
                return decoded
        except Exception:
            pass
        return val_str

    @staticmethod
    def _headers(token: str) -> dict:
        return {
            "Content-Type": "application/vnd.api+json",
            "X-Auth-Token": token,
            "User-Agent": KaspiAPI.USER_AGENT,
        }

    @staticmethod
    def _fetch_pages_batched(fetch_page_fn, max_workers: int = None):
        """
        Категория 1 (B.1): постраничный обход с ограниченной конкурентностью.
        Страницы номер N известны заранее не будут (Kaspi API v2 не отдаёт
        total count в ответе) — качаем батчами по max_workers одновременно
        (тот же уровень конкурентности, что уже используется в
        routers/orders.py::pack_all_orders для assemble_cargo), но ОТДАЁМ
        вызывающему коду строго по возрастанию номера страницы — это
        критично для сохранения существующей эвристики терминации
        "короткая/пустая страница = последняя": если бы отдавали в порядке
        завершения запроса, можно было бы обработать страницу N+1 раньше,
        чем определили, что страница N — последняя.

        Страница 0 качается ОТДЕЛЬНО и последовательно, и только если
        вызывающий вернулся за следующей — разворачивается параллельный батч.
        Раньше батч из max_workers страниц уходил всегда, ещё до чтения
        первого ответа, поэтому даже пустая выборка стоила 3 запроса. На
        холодных вызовах (экран «Упаковка») это незаметно, но на горячих
        поллерах — нет: радар заказов тикает раз в 20с, авто-списание раз в
        30с, и вместе они давали ≥900 запросов к Kaspi в час, из которых две
        трети приходились на заведомо пустые страницы (замер 2026-08-13).
        Цена — один лишний последовательный round-trip в по-настоящему
        многостраничном случае; там всё равно доминируют сами страницы.

        Исключение из любой страницы батча пробрасывается наружу (через
        future.result()) в момент, когда до неё доходит очередь в
        сортированном порядке — так же, как необработанное исключение из
        requests.get() пробросилось бы и в старом строго последовательном
        коде.

        max_workers по умолчанию читается из KASPI_PAGE_FETCH_MAX_WORKERS
        (без переменной — 3, прежнее поведение). mc.shop.kaspi.kz/kaspi.kz
        v2 — токен-авторизованный кабинетный API без истории бана (в
        отличие от публичной витрины kaspi.kz/yml, см. HANDOFF 3.9) —
        можно поднимать конкурентность и мерить вживую на проде; если
        Kaspi когда-нибудь пришлёт 429/403 на эти эндпоинты, откат —
        просто вернуть переменную к 3, без изменения кода.
        """
        if max_workers is None:
            max_workers = int(os.getenv("KASPI_PAGE_FETCH_MAX_WORKERS", "3"))

        # Генератор ленивый: тело цикла ниже выполнится, только если
        # вызывающий пришёл за второй страницей, то есть не оборвал обход на
        # пустой/короткой нулевой. Пустая выборка стоит ровно один запрос.
        yield 0, fetch_page_fn(0)

        page = 1
        while True:
            batch_page_numbers = list(range(page, page + max_workers))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(fetch_page_fn, p): p for p in batch_page_numbers}
                batch_futures = {futures[fut]: fut for fut in as_completed(futures)}
            for page_number in sorted(batch_futures):
                yield page_number, batch_futures[page_number].result()
            page += max_workers

    @staticmethod
    def _get(url: str, token: str, params: dict = None) -> requests.Response:
        """GET with exponential backoff on 429."""
        headers = KaspiAPI._headers(token)
        for attempt in range(KaspiAPI.MAX_RETRIES):
            resp = requests.get(url, headers=headers, params=params, timeout=KaspiAPI.TIMEOUT)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"[KaspiAPI] 429 Too Many Requests — backing off {wait}s (attempt {attempt+1})")
                try:
                    from backend.services import ops_metrics
                    ops_metrics.inc("kaspi_429")
                except Exception:
                    pass
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                try:
                    from backend.services import ops_metrics
                    ops_metrics.inc("kaspi_http_error_5xx")
                except Exception:
                    pass
            return resp
        # Ретраи 429 исчерпаны — вызывающий получит 429-ответ как есть.
        try:
            from backend.services import ops_metrics
            ops_metrics.inc("kaspi_429_exhausted")
        except Exception:
            pass
        return resp

    @staticmethod
    def _date_range(days_back: int = 13) -> tuple:
        """Return ($ge, $le) timestamps in ms (max 13 days to comply with 14-day limit)."""
        now = datetime.datetime.now()
        ge = int((now - datetime.timedelta(days=days_back)).timestamp() * 1000)
        le = int(now.timestamp() * 1000)
        return ge, le

    @staticmethod
    def _parse_delivery_type(attr: dict, kaspi_deliv: dict) -> str:
        """Точное определение типа и способа доставки (Express / Postomat / Pickup / Standard)."""
        dm = str(attr.get("deliveryMode") or "").upper()
        dt_raw = str(kaspi_deliv.get("deliveryType") or "").upper()
        delivery_addr = attr.get("deliveryAddress") or {}

        # 1. Проверка на Экспресс
        is_express = (
            attr.get("express") is True
            or kaspi_deliv.get("express") is True
            or attr.get("isExpress") is True
            or "EXPRESS" in dm
            or "EXPRESS" in dt_raw
        )
        if is_express:
            return "Express"
    
        # 2. Самовывоз клиентом из точки продавца.
        # ВАЖНО (исправлено 2026-08-13, см. HANDOFF 3.46): раньше здесь стояло
        # `dm == "DELIVERY_PICKUP" -> Постамат`, и самовывоз не существовал как
        # класс вообще — вся вкладка уезжала в "Postomat". На живых данных
        # Бархана видно, что deliveryMode у постамата и самовывоза ОДИНАКОВ
        # ("DELIVERY_PICKUP"), и pickupPointId тоже одинаков (это точка
        # продавца, а не постамат). Различает их `isKaspiDelivery`: когда везёт
        # Kaspi — это доставка до постамата/пункта выдачи, когда не везёт никто
        # — клиент забирает сам. Счёт сошёлся с кабинетом владельца: 8 из 60
        # заказов в state=PICKUP оказались настоящим самовывозом.
        kaspi_carries = (
            attr.get("isKaspiDelivery") is True
            or bool(kaspi_deliv)
            or "POSTOMAT" in dt_raw
        )
        if "PICKUP" in dm and not kaspi_carries:
            return "Самовывоз"

        # 3. Постамат (Kaspi Box) — везёт Kaspi до ячейки/пункта выдачи.
        is_postomat = (
            "POSTOMAT" in dm
            or "POSTOMAT" in dt_raw
            or dm == "DELIVERY_PICKUP"
            or kaspi_deliv.get("postomat") is True
            or delivery_addr.get("postomat") is not None
        )
        if is_postomat:
            return "Postomat"

        if "PICKUP" in dm or "PICKUP" in dt_raw:
            return "Самовывоз"
    
        # 4. Срочность обычной курьерской доставки Kaspi
        if "TODAY" in dt_raw or "TODAY" in dm:
            return "Today"
        elif "TOMORROW" in dt_raw or "TOMORROW" in dm:
            return "Tomorrow"
    
        return "Kaspi Доставка"

    # ── 0. merchant id discovery ─────────────────────────────

    @staticmethod
    def get_merchant_id(token: str) -> str:
        """
        merchant_id по токену, с кэшем в памяти процесса (см. _merchant_id_cache).

        Кэш именно здесь, а не в вызывающих: точек вызова много и часть из них
        горячие, а сам резолв стоит двух запросов к Kaspi и не меняется.
        Синтетический m_<hash> кэшируется коротко — это признак «не удалось»,
        и часть кода (offer_sync) на него осознанно не полагается.
        """
        now = time.time()
        with KaspiAPI._merchant_id_cache_lock:
            cached = KaspiAPI._merchant_id_cache.get(token)
            if cached and now < cached["expires_at"]:
                return cached["merchant_id"]

        merchant_id = KaspiAPI._resolve_merchant_id(token)
        # Логируем именно промах кэша — это и есть настоящий поход в Kaspi.
        logger.info("🎯 Merchant ID определён заново: %s", merchant_id)

        ttl = (
            KaspiAPI._MERCHANT_ID_NEGATIVE_TTL_SECONDS
            if str(merchant_id).startswith("m_")
            else KaspiAPI._MERCHANT_ID_TTL_SECONDS
        )
        with KaspiAPI._merchant_id_cache_lock:
            KaspiAPI._merchant_id_cache[token] = {
                "merchant_id": merchant_id,
                "expires_at": time.time() + ttl,
            }
        return merchant_id

    @staticmethod
    def reset_merchant_id_cache() -> None:
        """Только для тестов — кэш живёт в процессе и переживает patch()."""
        with KaspiAPI._merchant_id_cache_lock:
            KaspiAPI._merchant_id_cache.clear()

    @staticmethod
    def _resolve_merchant_id(token: str) -> str:
        # 1. Попытка через pointofservices
        try:
            pos_list = KaspiAPI.get_point_of_services(token)
            if pos_list:
                pos_id = pos_list[0].get("id", "")
                if "_" in pos_id:
                    return pos_id.split("_")[0]
                elif pos_id:
                    return pos_id
        except Exception as e:
            print(f"[KaspiAPI] get_merchant_id (pointofservices failed): {e}")

        # 2. Фаллбек через первый заказ из истории (если pointofservices вернул 500)
        # Kaspi ограничивает filter[orders][creationDate] максимум 14 днями —
        # раньше здесь стояло 30, из-за чего Kaspi всегда отвечал 400
        # "Exceeded the maximum difference... max [14]" и этот фоллбек НИКОГДА
        # не срабатывал ни для одного магазина (тихо проглатывалось except
        # ниже и падало в синтетический m_<hash>).
        try:
            now = datetime.datetime.now()
            ge = int((now - datetime.timedelta(days=14)).timestamp() * 1000)
            le = int(now.timestamp() * 1000)
            params = {
                "page[number]": 0,
                "page[size]": 1,
                "filter[orders][creationDate][$ge]": ge,
                "filter[orders][creationDate][$le]": le,
            }
            resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    order = data[0]
                    # Извлекаем из pickupPointId
                    pickup_id = order.get("attributes", {}).get("pickupPointId")
                    if pickup_id and "_" in pickup_id:
                        merchant_id = pickup_id.split("_")[0]
                        print(f"[KaspiAPI] get_merchant_id resolved from order pickupPointId: {merchant_id}")
                        return merchant_id
                    
                    # Или из originAddress.id (в Base64)
                    origin_address = order.get("attributes", {}).get("originAddress", {})
                    origin_id = origin_address.get("id")
                    if origin_id:
                        decoded = KaspiAPI._decode_base64_if_needed(origin_id)
                        if "_" in decoded:
                            merchant_id = decoded.split("_")[0]
                            print(f"[KaspiAPI] get_merchant_id resolved from decoded originAddress ID: {merchant_id}")
                            return merchant_id
        except Exception as e:
            print(f"[KaspiAPI] get_merchant_id (orders fallback failed): {e}")

        import hashlib
        token_hash = hashlib.md5(token.encode('utf-8')).hexdigest()[:8]
        return f"m_{token_hash}"

    # ── 1. token verification ────────────────────────────────

    @staticmethod
    def verify_token(token: str) -> bool:
        if token.startswith("test_"):
            return True
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/cities", token)
        if resp.status_code in (200, 201):
            return True
        # If Kaspi API responds with 401/403, verify if token format is valid
        return resp.status_code != 401

    # ── 2. orders ────────────────────────────────────────────

    @staticmethod
    def get_orders(
        token: str,
        state: str = "KASPI_DELIVERY",
        status: str = "ACCEPTED_BY_MERCHANT",
        page: int = 0,
        size: int = 100,
        days_back: int = 13,
    ) -> dict:
        if token.startswith("test_"):
            return {"data": []}
        ge, le = KaspiAPI._date_range(days_back)
        params = {
            "page[number]": page,
            "page[size]": size,
            "filter[orders][creationDate][$ge]": ge,
            "filter[orders][creationDate][$le]": le,
            "filter[orders][state]": state,
            "filter[orders][status]": status,
        }
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
        resp.raise_for_status()
        return resp.json()

    # ── 3. order entries ─────────────────────────────────────

    @staticmethod
    def get_order_entries(token: str, order_internal_id: str) -> list:
        url = f"{KaspiAPI.BASE_URL}/orders/{order_internal_id}/entries"
        resp = KaspiAPI._get(url, token)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ── 4. entry product ─────────────────────────────────────

    @staticmethod
    def get_entry_product(token: str, entry_id: str) -> dict:
        url = f"{KaspiAPI.BASE_URL}/orderentries/{entry_id}/product"
        resp = KaspiAPI._get(url, token)
        resp.raise_for_status()
        return resp.json().get("data", {})

    # ── 5. full chain: packaging orders with pagination ──────

    @staticmethod
    def get_orders_with_products(
        token: str,
        state: str = "KASPI_DELIVERY",
        status: str = "ACCEPTED_BY_MERCHANT",
        limit: int = 500,
        assembled: Optional[bool] = False,  # False = На сборке (Упаковка), True = Ожидает курьера, None = Все
    ) -> list:
        """
        Скачивает заказы Kaspi API v2 с включенным параметром `include[orders]=entries` по стандарту JSON:API.
        Индексирует включенный массив `included` по entry_id для мгновенной сборки состава заказа за O(N).
        """
        ge, le = KaspiAPI._date_range(13)
        results = []

        def fetch_page(page_number: int) -> requests.Response:
            params = {
                "page[number]": page_number,
                "page[size]": 100,
                "include[orders]": "entries",
                "filter[orders][creationDate][$ge]": ge,
                "filter[orders][creationDate][$le]": le,
                "filter[orders][state]": state,
                "filter[orders][status]": status,
            }
            return KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)

        for _page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page):
            if resp.status_code != 200:
                return results

            res_json = resp.json()
            orders_data = res_json.get("data", [])
            included_data = res_json.get("included", [])

            if not orders_data:
                return results

            # 1. СТРОИМ ИНДЕКС ХЭШ-КАРТЫ ДЛЯ INCLUDED ПО UNIQUE ID (entry_id)
            included_entries = {}
            for item in included_data:
                if item.get("type") == "orderentries":
                    entry_id = item.get("id")
                    included_entries[entry_id] = item

            # 2. ПРОХОДИМ ПО ЗАКАЗАМ И СОБИРАЕМ СОСТАВ УПАКОВКИ
            for order in orders_data:
                attr = order.get("attributes", {})
                internal_id = order.get("id")
                code = attr.get("code")
                if not internal_id or not code:
                    continue

                is_assembled = attr.get("assembled", False)
                kaspi_deliv = attr.get("kaspiDelivery") or {}
                transmission_date = kaspi_deliv.get("courierTransmissionDate")

                # Раскладка по вкладкам:
                # assembled is False: 📦 На сборке (Упаковка) -> not is_assembled
                # assembled is True: 🏷️ Ожидает курьера (Передача) -> is_assembled and transmission_date is None
                if assembled is False and is_assembled:
                    continue
                elif assembled is True and (not is_assembled or transmission_date is not None):
                    continue

                delivery_type = KaspiAPI._parse_delivery_type(attr, kaspi_deliv)

                cd = attr.get("creationDate", 0)
                created_str = (
                    datetime.datetime.fromtimestamp(cd / 1000.0).strftime("%d.%m.%Y %H:%M")
                    if cd else "N/A"
                )

                # Ссылки на товары из relationships
                entry_refs = order.get("relationships", {}).get("entries", {}).get("data", [])
                entries_enriched = []

                for ref in entry_refs:
                    entry_id = ref.get("id")
                    entry_data = included_entries.get(entry_id)
                    if entry_data:
                        e_attr = entry_data.get("attributes", {})
                        offer = e_attr.get("offer", {})
                        merchant_sku = str(offer.get("code", "")).strip()
                        product_name = offer.get("name") or offer.get("title") or "Товар без названия"

                        entries_enriched.append({
                            "entryId": entry_id,
                            "productName": product_name,
                            "sku": merchant_sku,
                            "merchantSku": merchant_sku,
                            "kaspiSku": merchant_sku,
                            "productUrl": f"https://kaspi.kz/shop/p/-{merchant_sku}/?c=750000000#offers" if merchant_sku.isdigit() else "",
                            "quantity": e_attr.get("quantity", 1),
                            "basePrice": e_attr.get("basePrice", 0),
                            "totalPrice": e_attr.get("totalPrice") or e_attr.get("entryTotalPrice", 0),
                        })

                first_product = entries_enriched[0]["productName"] if entries_enriched else "Товар"
                total_qty = sum(e["quantity"] for e in entries_enriched) if entries_enriched else 1

                customer = attr.get("customer", {})
                recipient = attr.get("recipient", {})
                delivery_addr = attr.get("deliveryAddress", {})

                results.append({
                    "id": code,
                    "internalId": internal_id,
                    "deliveryType": delivery_type,
                    "createdAt": created_str,
                    "price": f"{int(attr.get('totalPrice', 0))} ₸",
                    "productName": first_product,
                    "quantity": total_qty,
                    "status": attr.get("status", ""),
                    "state": attr.get("state", ""),
                    "assembled": is_assembled,
                    "isExpress": (delivery_type == "Express"),
                    "isTransmitted": (transmission_date is not None),
                    "waybillNumber": kaspi_deliv.get("waybillNumber"),
                    "waybillUrl": kaspi_deliv.get("waybill"),
                    "courierTransmissionDate": transmission_date,
                    "entries": entries_enriched,
                    "customer": {
                        "name": customer.get("name") or customer.get("firstName") or recipient.get("name", "Не указан"),
                        "phone": customer.get("cellPhone", ""),
                    },
                    "address": delivery_addr.get("formattedAddress") or delivery_addr.get("town", ""),
                    # Фаза 4 (кампания «Самовывоз»): точка выдачи, которую выбрал
                    # клиент — тот же формат id, что и в pointofservices (см.
                    # get_merchant_id выше). Нужен, чтобы понять КОТОРУЮ из
                    # нескольких точек магазина написать клиенту.
                    "pickupPointId": attr.get("pickupPointId"),
                })

                if len(results) >= limit:
                    return results

            if len(orders_data) < 100:
                return results

        return results

    # ── 5.0. completed orders (real sales history) ───────────

    @staticmethod
    def get_completed_orders(token: str, ge_ms: int, le_ms: int) -> list:
        """
        Завершённые заказы (state=ARCHIVE, status=COMPLETED) в явном окне дат
        (ge_ms/le_ms — unix ms), для настоящей статистики продаж/выручки.

        Kaspi ограничивает `filter[orders][creationDate]` максимум 14 днями за
        один запрос (см. _date_range) — вызывающий код (sales_sync_worker.py)
        сам режет более широкий диапазон на 13-дневные окна и вызывает этот
        метод по одному на каждое окно.
        """
        results = []

        def fetch_page(page_number: int) -> requests.Response:
            params = {
                "page[number]": page_number,
                "page[size]": 100,
                "include[orders]": "entries",
                "filter[orders][creationDate][$ge]": ge_ms,
                "filter[orders][creationDate][$le]": le_ms,
                "filter[orders][state]": "ARCHIVE",
                "filter[orders][status]": "COMPLETED",
            }
            return KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)

        for _page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page):
            if resp.status_code != 200:
                return results

            res_json = resp.json()
            orders_data = res_json.get("data", [])
            included_data = res_json.get("included", [])
            if not orders_data:
                return results

            included_entries = {
                item.get("id"): item for item in included_data if item.get("type") == "orderentries"
            }

            for order in orders_data:
                attr = order.get("attributes", {})
                internal_id = order.get("id")
                code = attr.get("code")
                if not internal_id or not code:
                    continue

                entry_refs = order.get("relationships", {}).get("entries", {}).get("data", [])
                entries_enriched = []
                for ref in entry_refs:
                    entry_data = included_entries.get(ref.get("id"))
                    if not entry_data:
                        continue
                    e_attr = entry_data.get("attributes", {})
                    offer = e_attr.get("offer", {})
                    sku = str(offer.get("code", "")).strip()
                    # productCode для review-диплинка (Kaspi "оцените заказ" ссылка,
                    # см. [[project_kaspi_review_deeplink]]) — Kaspi отдаёт его в
                    # relationships.product.data.id, обычно base64 (не сам merchant
                    # SKU — тот в offer.code и почти всегда нечисловой).
                    product_ref = entry_data.get("relationships", {}).get("product", {}).get("data", {}) or {}
                    product_code = KaspiAPI._decode_base64_if_needed(product_ref.get("id", ""))
                    entries_enriched.append({
                        "sku": sku,
                        "name": offer.get("name") or offer.get("title") or "Товар",
                        "quantity": e_attr.get("quantity", 1),
                        "totalPrice": e_attr.get("totalPrice") or e_attr.get("entryTotalPrice", 0),
                        "productCode": product_code if product_code.isdigit() else "",
                    })

                results.append({
                    "internalId": internal_id,
                    "code": code,
                    "creationDateMs": attr.get("creationDate", 0),
                    "totalPrice": attr.get("totalPrice", 0),
                    "status": attr.get("status", ""),
                    "state": attr.get("state", ""),
                    "entries": entries_enriched,
                })

            if len(orders_data) < 100:
                return results

        return results

    # ── 5.1. packaging orders by SKU prefix ─────────────────────

    @staticmethod
    def get_packaging_orders_by_sku_prefix(token: str, sku_prefix: str) -> dict:
        """
        Запрашивает все заказы со статусом 'Упаковка' (KASPI_DELIVERY/ACCEPTED_BY_MERCHANT),
        соответствующие артикулу продавца sku_prefix и всем его вариантам с тире (например T219-0005, T219-0005-1, T219-0005-2...).
        """
        prefix_pattern = str(sku_prefix).strip().lower()

        all_packaging = KaspiAPI.get_orders_with_products(
            token=token,
            state="KASPI_DELIVERY",
            status="ACCEPTED_BY_MERCHANT",
            limit=1000,
            assembled=False
        )

        matched_orders = []
        total_items_count = 0

        for order in all_packaging:
            entries = order.get("entries", [])
            order_matched = False
            for entry in entries:
                m_sku = str(entry.get("merchantSku", "")).strip().lower()
                k_sku = str(entry.get("kaspiSku", "")).strip().lower()
                title = str(entry.get("productName", "")).strip().lower()

                # Сопоставление по префиксу с тире (например T219-0005, T219-0005-1...)
                is_match = (
                    m_sku.startswith(prefix_pattern)
                    or k_sku.startswith(prefix_pattern)
                    or prefix_pattern in m_sku
                    or prefix_pattern in k_sku
                    or prefix_pattern in title
                )

                if is_match:
                    qty = entry.get("quantity", 1)
                    total_items_count += qty
                    order_matched = True

            if order_matched:
                matched_orders.append(order)

        return {
            "success": True,
            "prefix": sku_prefix,
            "count": len(matched_orders),
            "total_quantity": total_items_count,
            "orders": matched_orders
        }

    # ── 5.1b. packaging orders by list of SKUs (Master Group search) ─────────

    @staticmethod
    def get_packaging_orders_for_skus(token: str, skus: list) -> dict:
        """
        Ищет заказы на сборке ('Упаковка') для списка привязанных SKU Мастер-Группы.
        Автоматически проверяет точные SKU, их вариации с тире (-1, -2...) и вхождения.
        """
        if not skus:
            return {"success": True, "count": 0, "total_quantity": 0, "orders": []}

        # Нормализуем исходные SKU
        norm_skus = set(str(s).strip().lower() for s in skus if str(s).strip())
        
        # Выделяем префиксы с дефисом (например 't219-0005' из 't219-0005-1')
        base_prefixes = set()
        for s in norm_skus:
            if "-" in s:
                parts = s.split("-")
                base = "-".join(parts[:2]) if len(parts) >= 2 else parts[0]
                base_prefixes.add(base)
            else:
                base_prefixes.add(s)

        all_packaging = KaspiAPI.get_orders_with_products(
            token=token,
            state="KASPI_DELIVERY",
            status="ACCEPTED_BY_MERCHANT",
            limit=1000,
            assembled=False
        )

        matched_orders = []
        total_items_count = 0

        for order in all_packaging:
            entries = order.get("entries", [])
            order_matched = False
            for entry in entries:
                m_sku = str(entry.get("merchantSku", "")).strip().lower()
                k_sku = str(entry.get("kaspiSku", "")).strip().lower()
                title = str(entry.get("productName", "")).strip().lower()

                # Проверка совпадения:
                # 1. Прямое совпадение с точными SKU группы
                is_exact = (m_sku in norm_skus or k_sku in norm_skus)
                # 2. Совпадение по базовому префиксу (например T219-0005)
                is_prefix = any(
                    m_sku.startswith(p) or k_sku.startswith(p) or p in m_sku or p in title
                    for p in base_prefixes if len(p) >= 4
                )

                if is_exact or is_prefix:
                    qty = entry.get("quantity", 1)
                    total_items_count += qty
                    order_matched = True

            if order_matched:
                matched_orders.append(order)

        return {
            "success": True,
            "count": len(matched_orders),
            "total_quantity": total_items_count,
            "orders": matched_orders
        }

    # ── 5.2. full 3-tab layout packaging parsing ───────────────

    @staticmethod
    def get_all_packaging_tabs(token: str, window_days: int = 13) -> dict:
        """
        Скачивает заказы и раскладывает их по 3 стандартным вкладкам Kaspi:
          1. cargo_assembly (📦 На сборке / Упаковка): not is_assembled
          2. wait_for_courier (🏷️ Ожидает курьера / Передача): is_assembled and transmission_date is None
          3. transmitted (🚚 Переданы на доставку / Архив): is_assembled and transmission_date is not None

        window_days сужает окно creationDate (дефолт 13 — как было всегда).
        Горячий потребитель — авто-списание (stock_sync_worker, тик 30с):
        ему нужен узкий запрос, а не вся двухнедельная история; полный
        обход он всё равно делает по расписанию (см. схему свипов там же).
        Экранные потребители (routers/orders.py, stock_groups.py через
        *_cached) оставлены на дефолте.
        """
        ge, le = KaspiAPI._date_range(window_days)
        cargo_assembly = []
        wait_for_courier = []
        transmitted = []

        def _result() -> dict:
            return {
                "success": True,
                "cargo_assembly": cargo_assembly,
                "wait_for_courier": wait_for_courier,
                "transmitted": transmitted,
                "counts": {
                    "cargo_assembly": len(cargo_assembly),
                    "wait_for_courier": len(wait_for_courier),
                    "transmitted": len(transmitted)
                }
            }

        def fetch_page(page_number: int) -> requests.Response:
            params = {
                "page[size]": 100,
                "page[number]": page_number,
                "include[orders]": "entries",
                "filter[orders][state]": "KASPI_DELIVERY",
                "filter[orders][status]": "ACCEPTED_BY_MERCHANT",
                "filter[orders][creationDate][$ge]": ge,
            }
            return KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)

        for _page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page):
            if resp.status_code != 200:
                return _result()

            res_json = resp.json()
            data = res_json.get("data", [])
            included = res_json.get("included", [])

            if not data:
                return _result()

            included_entries = {}
            for item in included:
                if item.get("type") == "orderentries":
                    entry_id = item.get("id")
                    included_entries[entry_id] = item

            for order in data:
                order_id = order.get("id")
                attr = order.get("attributes", {})
                code = attr.get("code")
                price = attr.get("totalPrice")
                is_assembled = attr.get("assembled", False)

                customer_node = attr.get("customer") or {}
                customer_name = customer_node.get("firstName") or customer_node.get("name", "Не указан")

                kd = attr.get("kaspiDelivery") or {}
                transmission_date = kd.get("courierTransmissionDate")

                entry_refs = order.get("relationships", {}).get("entries", {}).get("data", [])

                order_items = []
                for ref in entry_refs:
                    entry_id = ref.get("id")
                    entry_data = included_entries.get(entry_id)
                    if entry_data:
                        e_attrs = entry_data.get("attributes", {})
                        offer = e_attrs.get("offer", {})
                        title = offer.get("name") or offer.get("title") or "Товар без названия"
                        order_items.append({
                            "title": title,
                            "merchant_sku": offer.get("code"),
                            "quantity": e_attrs.get("quantity", 1),
                            "base_price": e_attrs.get("basePrice")
                        })

                order_payload = {
                    "id": order_id,
                    "code": code,
                    "price": price,
                    "customer": customer_name,
                    "items": order_items,
                    "assembled": is_assembled,
                    "waybill": kd.get("waybill")
                }

                if not is_assembled:
                    cargo_assembly.append(order_payload)
                elif is_assembled and transmission_date is None:
                    wait_for_courier.append(order_payload)
                elif is_assembled and transmission_date is not None:
                    transmitted.append(order_payload)

            if len(data) < 100:
                return _result()

        return _result()

    @staticmethod
    def get_all_packaging_tabs_cached(token: str, ttl_seconds: float = 120.0) -> dict:
        """
        Кэширующая обёртка над get_all_packaging_tabs() — единая точка для
        routers/orders.py и routers/stock_groups.py (services/stock_sync_worker.py
        30с-тик авто-списания в неё не ходит, ему нужна более свежая выборка —
        см. docs/PERFORMANCE_REPORT.md §7.2), которые раньше независимо
        гоняли один и тот же тяжёлый постраничный обход. TTL поднят с 45с до
        120с: экран "Мастер-Группы"/"Упаковка" — это просмотр, не движок
        списания, отставание на пару минут не влияет на корректность остатка.

        Single-flight + stale-while-revalidate. Кэш держал только TTL, но не
        защищал сам промах: в момент истечения TTL все запросы, пришедшие
        одновременно, промахивались вместе и каждый запускал СВОЙ полный
        обход (13 дней, постранично, до 185с — замер на проде). Отсюда и
        p95=27с у `GET /api/stock-groups` при p50=1.6с, и заметная часть из
        12 тысяч вызовов `/shop/api/v2/orders` за 8 часов.

        Теперь обновляет ровно один поток на токен; остальные, если есть
        протухшая копия, получают её МГНОВЕННО, а не ждут обход. Это
        сознательный размен: отдать данные на пару минут старше вместо того,
        чтобы держать пользователя (и его поток из threadpool) три минуты.
        Ждут только те, у кого кэш вообще холодный — им отдавать нечего.
        """
        now = time.time()
        with KaspiAPI._packaging_tabs_cache_lock:
            cached = KaspiAPI._packaging_tabs_cache.get(token)
            if cached and now < cached["expires_at"]:
                return cached["data"]
            refresh_lock = KaspiAPI._packaging_tabs_refresh_locks.get(token)
            if refresh_lock is None:
                refresh_lock = threading.Lock()
                KaspiAPI._packaging_tabs_refresh_locks[token] = refresh_lock

        if not refresh_lock.acquire(blocking=False):
            # Обход уже идёт в другом потоке. Протухшие данные лучше ожидания.
            if cached:
                return cached["data"]
            # Кэш холодный — отдавать нечего, приходится дождаться чужого
            # обхода. Таймаут страхует от того, чтобы залипнуть тут навсегда,
            # если обход зависнет на медленном Kaspi.
            if not refresh_lock.acquire(timeout=KaspiAPI._PACKAGING_TABS_WAIT_TIMEOUT):
                return {}
            try:
                with KaspiAPI._packaging_tabs_cache_lock:
                    fresh = KaspiAPI._packaging_tabs_cache.get(token)
                return fresh["data"] if fresh else {}
            finally:
                refresh_lock.release()

        try:
            data = KaspiAPI.get_all_packaging_tabs(token)
            with KaspiAPI._packaging_tabs_cache_lock:
                KaspiAPI._packaging_tabs_cache[token] = {"data": data, "expires_at": time.time() + ttl_seconds}
            return data
        except Exception:
            with KaspiAPI._packaging_tabs_cache_lock:
                cached = KaspiAPI._packaging_tabs_cache.get(token)
            return cached["data"] if cached else {}
        finally:
            refresh_lock.release()

    # ── 6. update order status ───────────────────────────────

    @staticmethod
    def update_order_status(token: str, order_internal_id: str, new_status: str) -> bool:
        headers = KaspiAPI._headers(token)
        payload = {
            "data": {
                "type": "orders",
                "id": order_internal_id,
                "attributes": {"status": new_status},
            }
        }
        resp = requests.post(
            f"{KaspiAPI.BASE_URL}/orders",
            headers=headers,
            json=payload,
            timeout=KaspiAPI.TIMEOUT,
        )
        resp.raise_for_status()
        return True

    # ── 6.1. cargo assembly ──────────────────────────────────

    @staticmethod
    def _load_mc_cookies(store_id: Optional[str] = None) -> dict:
        """
        Делегирует в единую реализацию state_helpers._load_mc_cookies.

        РАНЬШЕ здесь была отдельная, более узкая реализация (проверяла только
        корневой state.json и требовала домен "kaspi.kz" в куке) — расхождение
        с канонической версией в state_helpers.py было задокументировано как
        известный техдолг (см. test_resolvers.py). Объединено при добавлении
        store_id-скоупинга (закрытие 3.1): теперь обе точки входа читают
        куки одинаково.
        """
        from backend.state_helpers import _load_mc_cookies as _unified_load_mc_cookies
        cookies = _unified_load_mc_cookies(store_id=store_id)
        if not cookies.get("mc-session") or not cookies.get("mc-sid"):
            raise Exception("Cookies mc-session or mc-sid are missing.")
        return cookies

    @staticmethod
    def _request_with_mc_refresh(method: str, url: str, cookies: dict, store_id: Optional[str] = None, **kwargs):
        """
        Выполняет запрос к mc.shop.kaspi.kz с переданными куками. При ответе 401
        (истёкшая сессия) один раз пробует автообновить куки сохранёнными
        email/паролем (state_helpers._refresh_mc_session) и повторяет запрос
        со свежими куками, загруженными тем же способом, что и исходные (KaspiAPI._load_mc_cookies).
        """
        resp = requests.request(method, url, cookies=cookies, **kwargs)
        if resp.status_code == 401:
            from backend.state_helpers import _refresh_mc_session
            if _refresh_mc_session(store_id=store_id):
                try:
                    fresh_cookies = KaspiAPI._load_mc_cookies(store_id=store_id)
                    resp = requests.request(method, url, cookies=fresh_cookies, **kwargs)
                except Exception:
                    pass
        return resp

    @staticmethod
    def assemble_cargo(token: str, order_code: str, quantity: int, store_id: Optional[str] = None) -> bool:
        merchant_id = KaspiAPI.get_merchant_id(token)
        cookies = KaspiAPI._load_mc_cookies(store_id=store_id)

        headers = {
            "Content-Type": "application/json",
            "x-auth-version": "3",
            "User-Agent": KaspiAPI.USER_AGENT,
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
        }
        payload = {
            "cargos": [{
                "orderCode": str(order_code),
                "newCargoSpace": 1,
                "quantity": int(quantity)
            }]
        }
        url = f"https://mc.shop.kaspi.kz/mc/api/order/cargo/assembled?_m={merchant_id}"
        resp = KaspiAPI._request_with_mc_refresh("POST", url, cookies, store_id=store_id, headers=headers, json=payload, timeout=15)

        if resp.status_code == 200:
            return True
        raise Exception(f"Kaspi MC API returned {resp.status_code}: {resp.text}")

    @staticmethod
    def assemble_cargos(token: str, cargo_items: list, store_id: Optional[str] = None) -> int:
        # Перепроверено вживую 2026-07-29: batch-запрос на 2 реальных заказа отработал
        # (200 OK, оба ушли в "Передача") с той же формой payload, что и здесь. Старая
        # пометка "не работает, Kaspi возвращает 500" не воспроизвелась — возможно, была
        # актуальна раньше или относилась к другим условиям (не проверено на больших
        # пачках/смешанных городах). orders.py всё ещё использует одиночные вызовы
        # (assemble_cargo) через ThreadPoolExecutor — сознательно не переключено на этот
        # метод без отдельного решения о размере пачки и стратегии частичных ошибок.
        if not cargo_items:
            return 0

        merchant_id = KaspiAPI.get_merchant_id(token)
        cookies = KaspiAPI._load_mc_cookies(store_id=store_id)

        headers = {
            "Content-Type": "application/json",
            "x-auth-version": "3",
            "User-Agent": KaspiAPI.USER_AGENT,
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
        }
        cargos_payload = [
            {"orderCode": str(item["orderCode"]), "newCargoSpace": 1, "quantity": int(item["quantity"])}
            for item in cargo_items
        ]
        url = f"https://mc.shop.kaspi.kz/mc/api/order/cargo/assembled?_m={merchant_id}"
        resp = KaspiAPI._request_with_mc_refresh("POST", url, cookies, store_id=store_id, headers=headers, json={"cargos": cargos_payload}, timeout=15)

        if resp.status_code == 200:
            return len(cargo_items)
        raise Exception(f"Kaspi MC API returned {resp.status_code}: {resp.text}")

    # ── 7. cities ────────────────────────────────────────────

    @staticmethod
    def get_cities(token: str) -> list:
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/cities", token)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ── 8. point of services ─────────────────────────────────

    @staticmethod
    def get_point_of_services(token: str) -> list:
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/pointofservices", token)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ── 9. sync merchant assortment (BFF offer-view) ─────────

    @staticmethod
    def sync_merchant_assortment(token: str, store_id: Optional[str] = None,
                                 strict: bool = False) -> list:
        """Выкачивает ВСЕ товары из кабинета Kaspi (ассортимент) через BFF эндпоинт или фоллбеки.

        strict=False (по умолчанию, поведение всех прежних вызывающих): пагинация
        на ошибке обрывается и возвращается то, что успели набрать — недобор
        доберёт следующий тик.

        strict=True: любой обрыв — это AssortmentTruncated, а не короткий список.
        Нужно там, где неполнота меняет смысл данных: прайс-фид объявляет весь
        каталог, и отсутствующий в файле оффер Kaspi снимает с продажи
        (HANDOFF.md §3.67). В strict-режиме фоллбеки (заказы, кэш) тоже
        запрещены — они принципиально не дают полного каталога.
        """
        # Слайс 7: раньше сперва читался merchant_id из общего states/session.json
        # — для стора B это могло вернуть merchant_id стора A. Токен уже
        # однозначно указывает, чей это запрос — резолвим напрямую по нему
        # (тот же фикс, что и в dumper/config.py::discover_merchant_id).
        try:
            m_id = KaspiAPI.get_merchant_id(token)
        except Exception:
            m_id = None
        if not m_id or not str(m_id).isdigit():
            # НЕ подставляем чужой merchant_id (раньше был хардкод "20186986" —
            # реальный ID Barkhan: любой другой магазин без резолвящегося
            # numeric ID тихо синхронизировался под его личностью и получал
            # 403 от Kaspi на каждой странице). Честно пропускаем синхронизацию —
            # следующий периодический тик (products_sync_worker) попробует снова.
            print(f"[BFF Sync] Не удалось определить numeric Merchant ID (got: {m_id!r}) — пропускаем синхронизацию ассортимента.")
            if strict:
                raise AssortmentTruncated(f"не определён numeric merchant_id (got {m_id!r})", 0)
            return []
        merchant_id = str(m_id)

        cookies = KaspiAPI._load_mc_cookies(store_id=store_id)

        url = "https://mc.shop.kaspi.kz/bff/offer-view/list"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru,en-US;q=0.9",
            "User-Agent": KaspiAPI.USER_AGENT,
            "x-auth-version": "3",
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
        }

        limit = 100
        all_products = []
        seen_offer_keys = set()

        print(f"[BFF Sync] Запуск полной синхронизации ассортимента через BFF (Merchant ID: {merchant_id})...")

        # `a` у BFF — не «включить фильтр», а ВЫБОР одной из двух непересекающихся
        # выборок: a=true отдаёт только карточки в продаже, a=false — только снятые
        # (на Бархане вживую 2026-08-15: 1150 + 423 = 1573, ровно столько же отдаёт
        # запрос вообще без `a`). Раньше здесь стояло жёстко "true", поэтому снятых
        # карточек в кэше не существовало ни одной, и вкладка «Сняты» в UI
        # (status=archive → фильтр по status == "Архив") гарантированно была пустой
        # при любом состоянии кабинета. Ходим двумя проходами, чтобы каждый оффер
        # честно получил свой статус из той выборки, в которой Kaspi его отдал.
        has_mc_session = bool(cookies and "mc-session" in cookies and "mc-sid" in cookies)
        if strict and not has_mc_session:
            raise AssortmentTruncated("нет кук mc-session/mc-sid — BFF недоступен", 0)

        # Причина обрыва пагинации, если он был. Пустая строка = оба прохода
        # дошли до естественного конца каталога.
        truncation_reason = ""

        for is_active_pass in ((True, False) if has_mc_session else ()):
            page = 0
            pass_count = 0
            while True:
                params = {
                    "m": str(merchant_id),
                    "p": page,
                    "l": limit,
                    "a": "true" if is_active_pass else "false",
                }

                try:
                    resp = KaspiAPI._request_with_mc_refresh(
                        "GET", url, cookies, store_id=store_id, headers=headers, params=params, timeout=15
                    )

                    if resp.status_code != 200:
                        print(f"[BFF Sync] Ошибка пагинации на странице {page} [{resp.status_code}]: {resp.text}")
                        truncation_reason = (
                            f"HTTP {resp.status_code} на странице {page} "
                            f"(a={'true' if is_active_pass else 'false'})"
                        )
                        break

                    data_json = resp.json()
                    offers = data_json.get("data", [])

                    if not offers:
                        break

                    for offer in offers:
                        master_sku = offer.get("masterSku")
                        merchant_sku = offer.get("sku")

                        if not master_sku or not merchant_sku:
                            continue

                        # Выборки не пересекаются, но подстраховываемся от дубля:
                        # каталог может доехать/сняться прямо между проходами.
                        offer_key = (str(master_sku), str(merchant_sku))
                        if offer_key in seen_offer_keys:
                            continue
                        seen_offer_keys.add(offer_key)

                        stock_count = 0
                        availabilities = offer.get("availabilities", [])
                        if availabilities:
                            # .get(..., 0) не спасает, если ключ присутствует со
                            # значением null в JSON — тогда возвращается None,
                            # а не 0, и int(stock_count) ниже падает и обрывает
                            # всю пагинацию (поймано вживую на проде: 2 товара
                            # выкачались, третий уронил всю остальную страницу).
                            stock_count = availabilities[0].get("stockCount") or 0

                        # ВАЖНО: плоское поле offer["price"] у Kaspi BFF — денормализованный кэш,
                        # который у части офферов (~35% на реальном каталоге, подтверждено вживую
                        # 2026-07-29) отдаёт 0 даже когда цена реально назначена — судя по всему,
                        # не переиндексируется сразу после ON_SALE/ручной реактивации оффера в
                        # кабинете Kaspi. Реальная цена в этом случае лежит в cityPrices/minPrice/
                        # rangePrice — их и нужно читать первыми, а не после плоского поля, иначе
                        # такие товары ошибочно помечаются "Цена не указана" (ломает демпинг, т.к.
                        # price_num=0 попадает в brain.py как my_current_price).
                        from backend.kaspi_offer_price import extract_offer_price
                        price_num = extract_offer_price(offer) or 0

                        from backend.product_name_overrides import KNOWN_PRODUCT_NAMES
                        brand_name = offer.get("brandName") or "Без бренда"
                        raw_title = offer.get("title")
                        # BFF отдаёт не только пустой title, но и саму заглушку
                        # буквально: инцидент 15.08 (см. product_name_overrides.py)
                        # закрепил "Товар <SKU>" не только в прайс-листе, но и здесь.
                        if raw_title == f"Товар {merchant_sku}":
                            raw_title = None
                        title = (
                            raw_title
                            or KNOWN_PRODUCT_NAMES.get(str(merchant_sku))
                            or f"Товар {merchant_sku}"
                        )

                        images_v2 = offer.get("imagesV2") or []
                        image_url = images_v2[0].get("small") if images_v2 else None

                        product_entry = {
                            "id": f"p_{master_sku}",
                            "name": title,
                            "sku": str(master_sku),
                            "merchantSku": str(merchant_sku),
                            "kaspiSku": str(master_sku),
                            "price": f"{int(price_num)} ₸" if price_num > 0 else "Цена не указана",
                            "status": "Активен" if offer.get("available", is_active_pass) else "Архив",
                            "url": f"https://kaspi.kz/shop/p/-{master_sku}/?c=750000000#offers",
                            "price_num": int(price_num),
                            "stock": int(stock_count),
                            "brand": brand_name,
                            "image": image_url,
                        }
                        all_products.append(product_entry)
                        pass_count += 1

                    if len(offers) < limit:
                        break

                    page += 1
                    time.sleep(0.3)
                except Exception as err:
                    print(f"[BFF Sync] Исключение во время пагинации: {err}")
                    truncation_reason = (
                        f"исключение на странице {page} "
                        f"(a={'true' if is_active_pass else 'false'}): {err!r}"
                    )
                    break

            print(f"[BFF Sync] Проход a={'true' if is_active_pass else 'false'} "
                  f"({'в продаже' if is_active_pass else 'сняты'}): {pass_count} товаров.")

        if truncation_reason:
            # Раньше обрыв был неотличим от конца каталога: функция просто
            # возвращала недобор. Теперь он хотя бы виден в логе всегда, а в
            # strict-режиме останавливает работу.
            print(f"[BFF Sync] ⚠️ ПАГИНАЦИЯ ОБОРВАЛАСЬ — список НЕПОЛНЫЙ: {truncation_reason}")
            if strict:
                raise AssortmentTruncated(truncation_reason, len(all_products))
        if strict:
            # Фоллбеки ниже (заказы, кэш) полного каталога не дают by design.
            if not all_products:
                raise AssortmentTruncated("BFF вернул 0 офферов", 0)
            return all_products

        # Фоллбек 1: Получение товаров из недавних заказов Kaspi API
        if not all_products:
            print("[BFF Sync] BFF недоступен или вернул 0 — запуск фоллбека получения товаров из заказов Kaspi API...")
            try:
                orders = KaspiAPI.get_orders(token, days_back=13)
                seen_skus = set()
                for o in orders:
                    entries = o.get("entries", [])
                    for e in entries:
                        m_sku = e.get("merchantSku") or e.get("sku") or "SKU-UNKNOWN"
                        if m_sku in seen_skus:
                            continue
                        seen_skus.add(m_sku)
                        from backend.product_name_overrides import KNOWN_PRODUCT_NAMES
                        title = e.get("name") or KNOWN_PRODUCT_NAMES.get(str(m_sku)) or f"Товар {m_sku}"
                        price_val = e.get("basePrice", 0)
                        all_products.append({
                            "id": f"p_{m_sku}",
                            "name": title,
                            "sku": str(m_sku),
                            "merchantSku": str(m_sku),
                            "kaspiSku": str(m_sku),
                            "price": f"{int(price_val)} ₸",
                            "status": "Активен",
                            "url": f"https://kaspi.kz/shop/p/-{m_sku}/?c=750000000#offers",
                            "price_num": int(price_val),
                            "stock": 10,
                            "brand": "Kaspi Store"
                        })
            except Exception as ex:
                print(f"[BFF Sync] Ошибка фоллбека из заказов: {ex}")

        # Фоллбек 2: Загрузка из ранее сохранённого кэша этого магазина (per-store, Слайс 1)
        if not all_products:
            try:
                from backend.state_helpers import _load_products
                all_products = _load_products(store_id)
                if all_products:
                    print(f"[BFF Sync] Загружено {len(all_products)} товаров из сохранённого кэша (store_id={store_id}).")
            except Exception:
                pass

        print(f"[BFF Sync] Синхронизация завершена! Всего выкачано: {len(all_products)} товаров.")
        return all_products

    # ── 10. transfers (assembled orders awaiting courier) ────

    @staticmethod
    def get_transfers(token: str, delivery_filter: str = "ALL") -> list:
        """Вкладка «Передача»: собранные заказы (assembled = True, transmissionDate == null)."""
        ge, le = KaspiAPI._date_range(13)
        results = []

        def fetch_page(page_number: int) -> requests.Response:
            params = {
                "page[number]": page_number,
                "page[size]": 100,
                "filter[orders][creationDate][$ge]": ge,
                "filter[orders][creationDate][$le]": le,
                "filter[orders][state]": "KASPI_DELIVERY",
                "filter[orders][status]": "ACCEPTED_BY_MERCHANT",
            }
            return KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)

        for _page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page):
            if resp.status_code != 200:
                return results

            orders_data = resp.json().get("data", [])
            if not orders_data:
                return results

            for item in orders_data:
                attr = item.get("attributes", {})
                code = attr.get("code")
                if not code:
                    continue

                kaspi_delivery = attr.get("kaspiDelivery") or {}
                is_assembled = attr.get("assembled", False)

                is_transmitted = (
                    kaspi_delivery.get("courierTransmitted") is True
                    or kaspi_delivery.get("courierTransmissionDate") is not None
                )

                # Только собранные И еще НЕ переданные
                if not is_assembled or is_transmitted:
                    continue

                dt = KaspiAPI._parse_delivery_type(attr, kaspi_delivery)

                # Фильтрация по типу доставки
                if delivery_filter == "EXPRESS" and dt != "Express":
                    continue
                elif delivery_filter == "TODAY" and dt != "Today":
                    continue
                elif delivery_filter == "TOMORROW" and dt != "Tomorrow":
                    continue

                cd = attr.get("creationDate", 0)
                deadline = (
                    f"до {datetime.datetime.fromtimestamp(cd / 1000.0).strftime('%H:%M')}"
                    if cd else "N/A"
                )

                results.append({
                    "id": code,
                    "deliveryType": dt,
                    "deadline": deadline,
                    "address": attr.get("deliveryAddress", {}).get("formattedAddress", "Kaspi курьер"),
                    "waybillNumber": kaspi_delivery.get("waybillNumber", ""),
                    "waybillUrl": kaspi_delivery.get("waybill", ""),
                })

            if len(orders_data) < 100:
                return results

        return results