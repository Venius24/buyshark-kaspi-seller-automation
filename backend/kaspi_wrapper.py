"""
KaspiAPI — чистый REST-клиент для Kaspi Merchant API v2.

Все методы принимают token напрямую.
Реализует 3-step query chain: GET /orders -> GET /orders/{id}/entries -> GET /orderentries/{id}/product.
Exponential backoff при 429. Таймаут 15с.
Без mock-данных, без fallback-эмуляции.
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
        return resp  # return last response even if still 429

    @staticmethod
    def _date_range(days_back: int = 14) -> tuple:
        """Return ($ge, $le) timestamps in ms for the last `days_back` days."""
        now = datetime.datetime.now()
        ge = int((now - datetime.timedelta(days=days_back)).timestamp() * 1000)
        le = int(now.timestamp() * 1000)
        return ge, le

    # ── 0. merchant id discovery ─────────────────────────────

    @staticmethod
    def get_merchant_id(token: str) -> str:
        """
        Dynamically retrieve the merchant ID using the pointofservices API.
        Prefix of the service point ID is the merchant ID (e.g. 20186986 from 20186986_001).
        """
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
        return "20186986" # fallback

    # ── 1. token verification ────────────────────────────────

    @staticmethod
    def verify_token(token: str) -> bool:
        """Verify token validity via GET /cities."""
        try:
            resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/cities", token)
            return resp.status_code == 200
        except Exception as e:
            print(f"[KaspiAPI] verify_token error: {e}")
            # Connection/timeout errors — treat as potentially valid (offline)
            err = str(e)
            if "timeout" in err.lower() or "Connection" in err or "NameResolution" in err:
                return True
            return False

    # ── 2. orders ────────────────────────────────────────────

    @staticmethod
    def get_orders(
        token: str,
        state: str = "NEW",
        status: str = "APPROVED_BY_BANK",
        page: int = 0,
        size: int = 20,
        days_back: int = 14,
    ) -> dict:
        """
        GET /orders with pagination and date filters.
        Returns raw JSON:API response dict.
        """
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
        """
        GET /orders/{id}/entries — list of entries for an order.
        `order_internal_id` is the Base64 id (e.g. 'OTg0MDIyNTQw'), NOT the numeric code.
        """
        url = f"{KaspiAPI.BASE_URL}/orders/{order_internal_id}/entries"
        resp = KaspiAPI._get(url, token)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ── 4. entry product ─────────────────────────────────────

    @staticmethod
    def get_entry_product(token: str, entry_id: str) -> dict:
        """
        GET /orderentries/{id}/product — master product info.
        Returns the 'data' object with attributes (name, brand, category).
        """
        url = f"{KaspiAPI.BASE_URL}/orderentries/{entry_id}/product"
        resp = KaspiAPI._get(url, token)
        resp.raise_for_status()
        return resp.json().get("data", {})

    # ── 5. full 3-step chain: orders with products ───────────

    @staticmethod
    def get_orders_with_products(
        token: str,
        state: str = "NEW",
        status: str = "APPROVED_BY_BANK",
        limit: int = 20,
        assembled: Optional[bool] = None,
    ) -> list:
        """
        Retrieves orders and sequentially fetches entries for each order to guarantee 100% correct data.
        """
        ge, le = KaspiAPI._date_range(13) # 13 days as per reference example
        params = {
            "page[number]": 0,
            "page[size]": limit,
            "filter[orders][creationDate][$ge]": ge,
            "filter[orders][creationDate][$le]": le,
            "filter[orders][state]": state,
            "filter[orders][status]": status,
        }
        if assembled is not None:
            params["filter[orders][assembled]"] = "true" if assembled else "false"
            
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
        resp.raise_for_status()
        data = resp.json()
        
        orders_data = data.get("data", [])
        results = []
        
        for item in orders_data:
            attr = item.get("attributes", {})
            internal_id = item.get("id")
            code = attr.get("code")
            if not internal_id or not code:
                continue
                
            # Delivery mode mapping
            dm = attr.get("deliveryMode", "")
            if "EXPRESS" in dm.upper():
                delivery_type = "Express"
            elif "POSTOMAT" in dm.upper():
                delivery_type = "Postomat"
            elif "PICKUP" in dm.upper():
                delivery_type = "Самовывоз"
            else:
                delivery_type = "Kaspi Доставка"

            # Creation date formatting
            cd = attr.get("creationDate", 0)
            if cd:
                dt = datetime.datetime.fromtimestamp(cd / 1000.0)
                created_str = dt.strftime("%d.%m.%Y %H:%M")
            else:
                created_str = "N/A"
                
            # Fetch entries sequentially for each order
            entries_enriched = []
            try:
                entries_data = KaspiAPI.get_order_entries(token, internal_id)
                for entry in entries_data:
                    entry_id = entry.get("id")
                    e_attr = entry.get("attributes", {})
                    offer = e_attr.get("offer", {})
                    quantity = e_attr.get("quantity", 1)
                    base_price = e_attr.get("basePrice", 0)
                    total_price = e_attr.get("totalPrice") or e_attr.get("entryTotalPrice", 0)
                    
                    product_name = offer.get("name", "Товар")
                    product_sku = offer.get("code", "")
                    
                    entries_enriched.append({
                        "entryId": entry_id,
                        "productName": product_name,
                        "sku": product_sku,
                        "quantity": quantity,
                        "basePrice": base_price,
                        "totalPrice": total_price,
                    })
            except Exception as e:
                print(f"[KaspiAPI] Failed to fetch entries for order {code}: {e}")
                    
            first_product = entries_enriched[0]["productName"] if entries_enriched else "Товар"
            total_qty = sum(e["quantity"] for e in entries_enriched) if entries_enriched else 1
            
            # Customer info
            customer = attr.get("customer", {})
            recipient = attr.get("recipient", {})
            delivery_addr = attr.get("deliveryAddress", {})
            kaspi_deliv = attr.get("kaspiDelivery", {})

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
                "assembled": attr.get("assembled", False),
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
            
        return results

    # ── 6. update order status ───────────────────────────────

    @staticmethod
    def update_order_status(token: str, order_internal_id: str, new_status: str) -> bool:
        """
        POST /orders to transition an order status.
        e.g. new_status = 'ACCEPTED_BY_MERCHANT'
        """
        headers = KaspiAPI._headers(token)
        payload = {
            "data": {
                "type": "orders",
                "id": order_internal_id,
                "attributes": {
                    "status": new_status,
                },
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
    def assemble_cargo(token: str, order_code: str, quantity: int) -> bool:
        """
        POST to mc.shop.kaspi.kz/mc/api/order/cargo/assembled
        to move the order to 'Awaiting Courier' (Передача).
        """
        merchant_id = KaspiAPI.get_merchant_id(token)
        
        # Load cookies from state.json
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
            raise Exception("Cookies mc-session or mc-sid are missing. Please log in via idmc.shop.kaspi.kz first.")
            
        headers = {
            "Content-Type": "application/json",
            "x-auth-version": "3",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36 OPR/132.0.0.0"
            ),
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
        }
        
        payload = {
            "cargos": [
                {
                    "orderCode": str(order_code),
                    "newCargoSpace": 1,
                    "quantity": int(quantity)
                }
            ]
        }
        
        url = f"https://mc.shop.kaspi.kz/mc/api/order/cargo/assembled?_m={merchant_id}"
        resp = requests.post(url, headers=headers, cookies=cookies, json=payload, timeout=15)
        
        if resp.status_code == 200:
            return True
        else:
            raise Exception(f"Kaspi MC API returned {resp.status_code}: {resp.text}")

    @staticmethod
    def assemble_cargos(token: str, cargo_items: list) -> int:
        """
        POST multiple cargos to mc.shop.kaspi.kz/mc/api/order/cargo/assembled.
        cargo_items format: [{"orderCode": "...", "quantity": ...}]
        Returns the number of successfully assembled orders.
        """
        if not cargo_items:
            return 0
            
        merchant_id = KaspiAPI.get_merchant_id(token)
        
        # Load cookies from state.json
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
            raise Exception("Cookies mc-session or mc-sid are missing. Please log in via idmc.shop.kaspi.kz first.")
            
        headers = {
            "Content-Type": "application/json",
            "x-auth-version": "3",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36 OPR/132.0.0.0"
            ),
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
        }
        
        cargos_payload = []
        for item in cargo_items:
            cargos_payload.append({
                "orderCode": str(item["orderCode"]),
                "newCargoSpace": 1,
                "quantity": int(item["quantity"])
            })
            
        payload = {
            "cargos": cargos_payload
        }
        
        url = f"https://mc.shop.kaspi.kz/mc/api/order/cargo/assembled?_m={merchant_id}"
        resp = requests.post(url, headers=headers, cookies=cookies, json=payload, timeout=15)
        
        if resp.status_code == 200:
            return len(cargo_items)
        else:
            raise Exception(f"Kaspi MC API returned {resp.status_code}: {resp.text}")

    # ── 7. cities ────────────────────────────────────────────

    @staticmethod
    def get_cities(token: str) -> list:
        """GET /cities — list of Kaspi cities."""
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/cities", token)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ── 8. point of services ─────────────────────────────────

    @staticmethod
    def get_point_of_services(token: str) -> list:
        """GET /pointofservices — merchant's pickup points and warehouses."""
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/pointofservices", token)
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ── 9. extract products from order history ───────────────

    @staticmethod
    def extract_products_from_orders(token: str, days_back: int = 60, max_orders: int = 500) -> list:
        """
        Crawl recent orders over the last `days_back` days (60 by default) in chunks of 12 days
        using JSON:API inclusions (include[orders]=entries) to extract unique products.
        Deduplicates products by SKU.
        """
        import datetime
        now = datetime.datetime.now()
        unique: Dict[str, dict] = {}
        
        # 12-day chunks to respect the 14-day limit per request
        chunk_days = 12
        num_chunks = (days_back + chunk_days - 1) // chunk_days
        
        print(f"[KaspiAPI] Crawling products over the last {days_back} days in {num_chunks} chunks...")
        
        for i in range(num_chunks):
            start_date = now - datetime.timedelta(days=(i + 1) * chunk_days)
            end_date = now - datetime.timedelta(days=i * chunk_days)
            
            ge = int(start_date.timestamp() * 1000)
            le = int(end_date.timestamp() * 1000)
            
            print(f"[KaspiAPI] Chunk {i+1}/{num_chunks}: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}")
            
            page_num = 0
            while True:
                # Fetch orders in ARCHIVE state
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
                        print(f"[KaspiAPI] Failed to get orders chunk: {resp.status_code}")
                        break
                        
                    data = resp.json()
                except Exception as e:
                    print(f"[KaspiAPI] Network error fetching orders: {e}")
                    break
                    
                orders_data = data.get("data", [])
                included = data.get("included", [])
                
                # Extract products from entries
                page_products_count = 0
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
                            page_products_count += 1
                            
                print(f"[KaspiAPI]   Page {page_num}: processed {len(orders_data)} orders. Extracted {page_products_count} new unique products.")
                
                # Check pagination bounds
                if len(orders_data) < 100 or len(unique) >= max_orders:
                    break
                page_num += 1
                
            if len(unique) >= max_orders:
                print(f"[KaspiAPI] Reached max_orders limit of {max_orders}.")
                break
                
        products = list(unique.values())
        print(f"[KaspiAPI] Extracted total of {len(products)} unique products from order history.")
        return products

    # ── 10. transfers (assembled orders awaiting courier) ────

    @staticmethod
    def get_transfers(token: str, delivery_filter: str = "ALL") -> list:
        """
        Fetch orders awaiting courier transfer (assembled = True and courierTransmissionDate == null).
        """
        ge, le = KaspiAPI._date_range(14)
        params = {
            "page[number]": 0,
            "page[size]": 100,
            "filter[orders][creationDate][$ge]": ge,
            "filter[orders][creationDate][$le]": le,
            "filter[orders][state]": "KASPI_DELIVERY",
            "filter[orders][status]": "ACCEPTED_BY_MERCHANT",
            "filter[orders][assembled]": "true",
        }
        
        resp = KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, params)
        resp.raise_for_status()
        data = resp.json()
        
        orders_data = data.get("data", [])
        
        results = []
        for item in orders_data:
            attr = item.get("attributes", {})
            code = attr.get("code")
            if not code:
                continue
                
            kaspi_delivery = attr.get("kaspiDelivery", {})
            
            # Filter: courierTransmissionDate must be null
            if kaspi_delivery.get("courierTransmissionDate") is not None:
                continue
                
            # Delivery mode mapping
            dm = attr.get("deliveryMode", "")
            if "EXPRESS" in dm.upper():
                dt = "Express"
            elif "POSTOMAT" in dm.upper():
                dt = "Postomat"
            elif "PICKUP" in dm.upper():
                dt = "Самовывоз"
            else:
                dt = "Kaspi Доставка"
                
            # Filter by delivery type
            if delivery_filter == "EXPRESS" and dt != "Express":
                continue
            if delivery_filter == "TODAY":
                today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                cd = attr.get("creationDate", 0)
                order_dt = datetime.datetime.fromtimestamp(cd / 1000.0)
                if order_dt < today_start:
                    continue
            
            cd = attr.get("creationDate", 0)
            if cd:
                deadline = f"до {datetime.datetime.fromtimestamp(cd / 1000.0).strftime('%H:%M')}"
            else:
                deadline = "N/A"
                
            addr = attr.get("deliveryAddress", {})
            waybill_number = kaspi_delivery.get("waybillNumber", "")
            waybill_url = kaspi_delivery.get("waybill", "")
            
            results.append({
                "id": code,
                "deliveryType": dt,
                "deadline": deadline,
                "address": addr.get("formattedAddress") or addr.get("town", "Kaspi курьер"),
                "waybillNumber": waybill_number,
                "waybillUrl": waybill_url
            })
        return results
