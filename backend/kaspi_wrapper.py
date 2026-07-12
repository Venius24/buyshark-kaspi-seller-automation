"""
KaspiAPI — чистый REST-клиент для Kaspi Merchant API v2 & MC API.
Исправлены статусы по умолчанию, пагинация и определение типов доставки (Express/Today/Tomorrow).
"""

import os
import json
import time
import datetime
import requests
from typing import Dict, Any, List, Optional


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
        try:
            pos_list = KaspiAPI.get_point_of_services(token)
            if pos_list:
                pos_id = pos_list[0].get("id", "")
                if "_" in pos_id:
                    return pos_id.split("_")[0]
                elif pos_id:
                    return pos_id
        except Exception as e:
            print(f"[KaspiAPI] get_merchant_id error: {e}")
        return "20186986"

    # ── 1. token verification ────────────────────────────────

    @staticmethod
    def verify_token(token: str) -> bool:
        try:
            resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/cities", token)
            return resp.status_code == 200
        except Exception as e:
            err = str(e)
            if "timeout" in err.lower() or "Connection" in err or "NameResolution" in err:
                return True
            return False

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
        assembled: Optional[bool] = False,  # False = Упаковка (На сборке)
    ) -> list:
        """
        Скачивает заказы со всеми страницами (пагинацией) и локально фильтрует по собранности,
        так как Kaspi игнорирует query-параметр assembled.
        """
        ge, le = KaspiAPI._date_range(13)
        page = 0
        results = []

        while True:
            params = {
                "page[number]": page,
                "page[size]": 100,
                "filter[orders][creationDate][$ge]": ge,
                "filter[orders][creationDate][$le]": le,
                "filter[orders][state]": state,
                "filter[orders][status]": status,
            }
            
            resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
            if resp.status_code != 200:
                break
                
            data = resp.json()
            orders_data = data.get("data", [])
            if not orders_data:
                break

            for item in orders_data:
                attr = item.get("attributes", {})
                internal_id = item.get("id")
                code = attr.get("code")
                if not internal_id or not code:
                    continue

                kaspi_deliv = attr.get("kaspiDelivery") or {}
                is_assembled = attr.get("assembled", False)
                is_transmitted = (
                    kaspi_deliv.get("courierTransmitted") is True
                    or kaspi_deliv.get("courierTransmissionDate") is not None
                )

                # Локальный фильтр по assembled и передаче (Kaspi не фильтрует это на бэке)
                if assembled is False and (is_assembled or is_transmitted):
                    continue
                elif assembled is True and not is_assembled:
                    continue

                delivery_type = KaspiAPI._parse_delivery_type(attr, kaspi_deliv)

                cd = attr.get("creationDate", 0)
                created_str = (
                    datetime.datetime.fromtimestamp(cd / 1000.0).strftime("%d.%m.%Y %H:%M")
                    if cd else "N/A"
                )

                # Fetch entries
                entries_enriched = []
                try:
                    entries_data = KaspiAPI.get_order_entries(token, internal_id)
                    for entry in entries_data:
                        e_attr = entry.get("attributes", {})
                        offer = e_attr.get("offer", {})
                        entries_enriched.append({
                            "entryId": entry.get("id"),
                            "productName": offer.get("name", "Товар"),
                            "sku": offer.get("code", ""),
                            "quantity": e_attr.get("quantity", 1),
                            "basePrice": e_attr.get("basePrice", 0),
                            "totalPrice": e_attr.get("totalPrice") or e_attr.get("entryTotalPrice", 0),
                        })
                except Exception as e:
                    print(f"[KaspiAPI] Failed to fetch entries for order {code}: {e}")

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
                    "isTransmitted": is_transmitted,
                    "waybillNumber": kaspi_deliv.get("waybillNumber"),
                    "waybillUrl": kaspi_deliv.get("waybill"),
                    "courierTransmissionDate": kaspi_deliv.get("courierTransmissionDate"),
                    "entries": entries_enriched,
                    "customer": {
                        "name": customer.get("name") or recipient.get("name", ""),
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
                        if c.get("name") in ["mc-session", "mc-sid"] and "mc.shop.kaspi.kz" in c.get("domain", ""):
                            cookies[c["name"]] = c["value"]
            except Exception as e:
                print(f"[KaspiAPI] Error parsing state.json: {e}")

        if not cookies.get("mc-session") or not cookies.get("mc-sid"):
            raise Exception("Cookies mc-session or mc-sid are missing in state.json.")
        return cookies

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
        resp = requests.post(url, headers=headers, cookies=cookies, json=payload, timeout=15)

        if resp.status_code == 200:
            return True
        raise Exception(f"Kaspi MC API returned {resp.status_code}: {resp.text}")

    @staticmethod
    def assemble_cargos(token: str, cargo_items: list) -> int:
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
        resp = requests.post(url, headers=headers, cookies=cookies, json={"cargos": cargos_payload}, timeout=15)

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

    # ── 9. extract products from order history ───────────────

    @staticmethod
    def extract_products_from_orders(token: str, days_back: int = 60, max_orders: int = 500) -> list:
        now = datetime.datetime.now()
        unique: Dict[str, dict] = {}
        chunk_days = 12
        num_chunks = (days_back + chunk_days - 1) // chunk_days

        for i in range(num_chunks):
            start_date = now - datetime.timedelta(days=(i + 1) * chunk_days)
            end_date = now - datetime.timedelta(days=i * chunk_days)
            ge = int(start_date.timestamp() * 1000)
            le = int(end_date.timestamp() * 1000)

            page_num = 0
            while True:
                params = {
                    "page[number]": page_num,
                    "page[size]": 100,
                    "filter[orders][creationDate][$ge]": ge,
                    "filter[orders][creationDate][$le]": le,
                    "filter[orders][state]": "ARCHIVE",
                    "include[orders]": "entries"
                }
                try:
                    resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                except Exception:
                    break

                orders_data = data.get("data", [])
                included = data.get("included", [])

                for item in included:
                    if item.get("type") == "orderentries":
                        attr = item.get("attributes", {})
                        offer = attr.get("offer", {})
                        sku = offer.get("code")
                        name = offer.get("name", "Товар")
                        price = attr.get("basePrice", 0)

                        if sku and sku not in unique:
                            unique[sku] = {
                                "id": f"p_{sku}",
                                "name": name,
                                "sku": sku,
                                "price": f"{int(price)} ₸" if price else "0 ₸",
                                "status": "Активен",
                                "url": f"https://kaspi.kz/shop/p/-{sku}/",
                            }

                if len(orders_data) < 100 or len(unique) >= max_orders:
                    break
                page_num += 1

            if len(unique) >= max_orders:
                break

        return list(unique.values())

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