"""
KaspiAPI — чистый REST-клиент для Kaspi Merchant API v2 & MC API.
Исправлены статусы по умолчанию, пагинация и определение типов доставки (Express/Today/Tomorrow).
"""

import os
import json
import time
import base64
import datetime
import requests
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed


class KaspiAPI:
    BASE_URL = "https://kaspi.kz/shop/api/v2"
    TIMEOUT = 15
    MAX_RETRIES = 3
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

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
    def _get(url: str, token: str, params: dict = None) -> requests.Response:
        """GET with exponential backoff on 429."""
        headers = KaspiAPI._headers(token)
        for attempt in range(KaspiAPI.MAX_RETRIES):
            resp = requests.get(url, headers=headers, params=params, timeout=KaspiAPI.TIMEOUT)
            if resp.status_code == 429:
                wait = 2 ** attempt
                print(f"[KaspiAPI] 429 Too Many Requests — backing off {wait}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            return resp
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
    
        # 2. Проверка на Постамат (Kaspi Box)
        is_postomat = (
            "POSTOMAT" in dm
            or "POSTOMAT" in dt_raw
            or kaspi_deliv.get("postomat") is True
            or delivery_addr.get("postomat") is not None
        )
        if is_postomat:
            return "Postomat"
    
        # 3. Самовывоз из точки продавца
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
        try:
            now = datetime.datetime.now()
            ge = int((now - datetime.timedelta(days=30)).timestamp() * 1000)
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
        page = 0
        results = []

        while True:
            params = {
                "page[number]": page,
                "page[size]": 100,
                "include[orders]": "entries",
                "filter[orders][creationDate][$ge]": ge,
                "filter[orders][creationDate][$le]": le,
                "filter[orders][state]": state,
                "filter[orders][status]": status,
            }
            
            resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
            if resp.status_code != 200:
                break
                
            res_json = resp.json()
            orders_data = res_json.get("data", [])
            included_data = res_json.get("included", [])
            
            if not orders_data:
                break

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
                })

                if len(results) >= limit:
                    return results

            if len(orders_data) < 100:
                break
            page += 1

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
    def get_all_packaging_tabs(token: str) -> dict:
        """
        Скачивает заказы и раскладывает их по 3 стандартным вкладкам Kaspi:
          1. cargo_assembly (📦 На сборке / Упаковка): not is_assembled
          2. wait_for_courier (🏷️ Ожидает курьера / Передача): is_assembled and transmission_date is None
          3. transmitted (🚚 Переданы на доставку / Архив): is_assembled and transmission_date is not None
        """
        ge, le = KaspiAPI._date_range(13)
        page = 0
        cargo_assembly = []
        wait_for_courier = []
        transmitted = []

        while True:
            params = {
                "page[size]": 100,
                "page[number]": page,
                "include[orders]": "entries",
                "filter[orders][state]": "KASPI_DELIVERY",
                "filter[orders][status]": "ACCEPTED_BY_MERCHANT",
                "filter[orders][creationDate][$ge]": ge,
            }

            resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
            if resp.status_code != 200:
                break

            res_json = resp.json()
            data = res_json.get("data", [])
            included = res_json.get("included", [])

            if not data:
                break

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
                break
            page += 1

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
    def _load_mc_cookies() -> dict:
        cookies = {}
        if os.path.exists("state.json"):
            try:
                with open("state.json", "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                    for c in state_data.get("cookies", []):
                        if c.get("name") in ["mc-session", "mc-sid"] and "kaspi.kz" in c.get("domain", ""):
                            cookies[c["name"]] = c["value"]
            except Exception as e:
                print(f"[KaspiAPI] Error parsing state.json: {e}")

        if not cookies.get("mc-session") or not cookies.get("mc-sid"):
            raise Exception("Cookies mc-session or mc-sid are missing in state.json.")
        return cookies

    @staticmethod
    def _request_with_mc_refresh(method: str, url: str, cookies: dict, **kwargs):
        """
        Выполняет запрос к mc.shop.kaspi.kz с переданными куками. При ответе 401
        (истёкшая сессия) один раз пробует автообновить куки сохранёнными
        email/паролем (state_helpers._refresh_mc_session) и повторяет запрос
        со свежими куками, загруженными тем же способом, что и исходные (KaspiAPI._load_mc_cookies).
        """
        resp = requests.request(method, url, cookies=cookies, **kwargs)
        if resp.status_code == 401:
            from backend.state_helpers import _refresh_mc_session
            if _refresh_mc_session():
                try:
                    fresh_cookies = KaspiAPI._load_mc_cookies()
                    resp = requests.request(method, url, cookies=fresh_cookies, **kwargs)
                except Exception:
                    pass
        return resp

    @staticmethod
    def assemble_cargo(token: str, order_code: str, quantity: int) -> bool:
        merchant_id = KaspiAPI.get_merchant_id(token)
        cookies = KaspiAPI._load_mc_cookies()

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
        resp = KaspiAPI._request_with_mc_refresh("POST", url, cookies, headers=headers, json=payload, timeout=15)

        if resp.status_code == 200:
            return True
        raise Exception(f"Kaspi MC API returned {resp.status_code}: {resp.text}")

    @staticmethod
    def assemble_cargos(token: str, cargo_items: list) -> int:
        # ВНИМАНИЕ: Метод пакетной отправки не работает (Kaspi API возвращает 500).
        # Не использовать в продакшене. Оставлен для совместимости/документации.
        if not cargo_items:
            return 0

        merchant_id = KaspiAPI.get_merchant_id(token)
        cookies = KaspiAPI._load_mc_cookies()

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
        resp = KaspiAPI._request_with_mc_refresh("POST", url, cookies, headers=headers, json={"cargos": cargos_payload}, timeout=15)

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
    def sync_merchant_assortment(token: str) -> list:
        """Выкачивает ВСЕ товары из кабинета Kaspi (ассортимент) через BFF эндпоинт или фоллбеки."""
        from backend.state_helpers import _load_session
        session = _load_session()
        m_id = session.get("merchant_id")
        if not m_id or not str(m_id).isdigit():
            try:
                m_id = KaspiAPI.get_merchant_id(token)
            except Exception:
                m_id = "20186986"
            if not str(m_id).isdigit():
                m_id = "20186986"
        merchant_id = str(m_id)

        cookies = KaspiAPI._load_mc_cookies()

        url = "https://mc.shop.kaspi.kz/bff/offer-view/list"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru,en-US;q=0.9",
            "User-Agent": KaspiAPI.USER_AGENT,
            "x-auth-version": "3",
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
        }

        page = 0
        limit = 100
        all_products = []

        print(f"[BFF Sync] Запуск полной синхронизации ассортимента через BFF (Merchant ID: {merchant_id})...")

        if cookies and "mc-session" in cookies and "mc-sid" in cookies:
            while True:
                params = {
                    "m": str(merchant_id),
                    "p": page,
                    "l": limit,
                    "a": "true",  # Только активные (в продаже)
                }

                try:
                    resp = KaspiAPI._request_with_mc_refresh(
                        "GET", url, cookies, headers=headers, params=params, timeout=15
                    )

                    if resp.status_code != 200:
                        print(f"[BFF Sync] Ошибка пагинации на странице {page} [{resp.status_code}]: {resp.text}")
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

                        stock_count = 0
                        availabilities = offer.get("availabilities", [])
                        if availabilities:
                            stock_count = availabilities[0].get("stockCount", 0)

                        price_val = offer.get("price")
                        price_num = 0
                        if isinstance(price_val, (int, float)) and price_val > 0:
                            price_num = int(price_val)
                        elif isinstance(price_val, dict):
                            val = price_val.get("value") or price_val.get("amount") or price_val.get("price")
                            if isinstance(val, (int, float)) and val > 0:
                                price_num = int(val)

                        if not price_num:
                            for field in ["basePrice", "currentPrice", "priceValue"]:
                                val = offer.get(field)
                                if isinstance(val, (int, float)) and val > 0:
                                    price_num = int(val)
                                    break

                        if not price_num and availabilities:
                            val = availabilities[0].get("price") or availabilities[0].get("value")
                            if isinstance(val, (int, float)) and val > 0:
                                price_num = int(val)

                        brand_name = offer.get("brandName") or "Без бренда"
                        title = offer.get("title") or f"Товар {merchant_sku}"

                        product_entry = {
                            "id": f"p_{master_sku}",
                            "name": title,
                            "sku": str(master_sku),
                            "merchantSku": str(merchant_sku),
                            "kaspiSku": str(master_sku),
                            "price": f"{int(price_num)} ₸" if price_num > 0 else "Цена не указана",
                            "status": "Активен" if offer.get("available", True) else "Архив",
                            "url": f"https://kaspi.kz/shop/p/-{master_sku}/?c=750000000#offers",
                            "price_num": int(price_num),
                            "stock": int(stock_count),
                            "brand": brand_name,
                        }
                        all_products.append(product_entry)

                    if len(offers) < limit:
                        break

                    page += 1
                    time.sleep(0.3)
                except Exception as err:
                    print(f"[BFF Sync] Исключение во время пагинации: {err}")
                    break

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
                        title = e.get("name") or f"Товар {m_sku}"
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

        # Фоллбек 2: Загрузка из ранее сохраненного файла states/products.json
        if not all_products and os.path.exists("states/products.json"):
            try:
                with open("states/products.json", "r", encoding="utf-8") as f:
                    all_products = json.load(f)
                print(f"[BFF Sync] Загружено {len(all_products)} товаров из сохраненного файла кэша.")
            except Exception:
                pass

        print(f"[BFF Sync] Синхронизация завершена! Всего выкачано: {len(all_products)} товаров.")
        return all_products

    # ── 10. transfers (assembled orders awaiting courier) ────

    @staticmethod
    def get_transfers(token: str, delivery_filter: str = "ALL") -> list:
        """Вкладка «Передача»: собранные заказы (assembled = True, transmissionDate == null)."""
        ge, le = KaspiAPI._date_range(13)
        page = 0
        results = []

        while True:
            params = {
                "page[number]": page,
                "page[size]": 100,
                "filter[orders][creationDate][$ge]": ge,
                "filter[orders][creationDate][$le]": le,
                "filter[orders][state]": "KASPI_DELIVERY",
                "filter[orders][status]": "ACCEPTED_BY_MERCHANT",
            }

            resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
            if resp.status_code != 200:
                break

            orders_data = resp.json().get("data", [])
            if not orders_data:
                break

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
                break
            page += 1

        return results