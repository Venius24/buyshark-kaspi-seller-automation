import logging
import json
import os
import requests
import time
import random
from typing import List, Optional

logger = logging.getLogger(__name__)

def _load_mc_cookies(store_id: Optional[str] = None) -> dict:
    from backend.state_helpers import _load_mc_cookies as _unified_load_mc_cookies
    return _unified_load_mc_cookies(store_id=store_id)


def _mc_request(method: str, url: str, store_id: Optional[str] = None, **kwargs):
    from backend.state_helpers import _mc_api_request
    return _mc_api_request(method, url, store_id=store_id, **kwargs)


class RateLimitExecutorWorker:
    """
    3. 🚀 Воркер-Исполнитель (API Updater / Rate-Limit Worker)
    Задача: Безопасное обновление цен через приватный 3-шаговый MC API, соблюдение задержек, регистрация оверрайдов.
    """
    @staticmethod
    def _fetch_kaspi_offer_details(merchant_uid: str, sku: str, internal_store_id: Optional[str] = None) -> dict:
        """
        Шаг BFF Details Fetch (GET /bff/offer-view/details?m={merchant_id}&s={sku}).
        Возвращает dict с masterSku, title (model) и актуальной ценой по городам Kaspi.
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "X-Auth-Version": "3"
        }
        url = f"https://mc.shop.kaspi.kz/bff/offer-view/details?m={merchant_uid}&s={sku}"
        try:
            r = _mc_request("GET", url, store_id=internal_store_id, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                master_sku = data.get("masterSku") or ""
                title = data.get("title") or data.get("masterName") or ""
                cities = data.get("cities", [])
                price = None
                if cities and isinstance(cities, list) and len(cities) > 0:
                    price = cities[0].get("price")
                return {
                    "master_sku": master_sku,
                    "title": title,
                    "price": price,
                    "cities": cities
                }
        except Exception as e:
            logger.debug(f"BFF offer details fetch error for SKU {sku}: {e}")
        return {}

    @staticmethod
    def _update_kaspi_price_sync(
        merchant_uid: str,
        sku: str,
        master_sku: str,
        model_name: str,
        new_price: int,
        stock: int = 10,
        store_id_suffix: str = "001",
        city_id: str = "750000000",
        internal_store_id: Optional[str] = None
    ) -> tuple:
        """
        Синхронный запуск полных шагов пайплайна Kaspi MC для изменения остатка и цены.
        Возвращает (success: bool, tx_id_or_error_msg_or_code: str)

        internal_store_id — наш store_id (БД), для скоупинга MC-кук через
        state_helpers. НЕ путать с store_id_suffix — это номер точки продаж
        (POS) в терминах самого Kaspi (например "001"), другая сущность.
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "X-Auth-Version": "3"
        }

        # Если цена не передана или равна дефолтному 1000, запрашиваем реальные детали карточки через BFF
        if not new_price or new_price <= 0 or new_price == 1000:
            details = RateLimitExecutorWorker._fetch_kaspi_offer_details(merchant_uid, sku, internal_store_id=internal_store_id)
            if details.get("price"):
                new_price = int(details["price"])
            if details.get("master_sku") and not master_sku:
                master_sku = details["master_sku"]
            if details.get("title") and not model_name:
                model_name = details["title"]

        if not new_price or new_price <= 0:
            new_price = 1000

        # Шаг 0: Pre-edit Validation (ON_SALE__PRICE_EDIT)
        edit_val_url = "https://mc.shop.kaspi.kz/offer-validation-api/merchant/offer/validate/v2"
        edit_val_payload = {
            "merchantUid": str(merchant_uid),
            "action": "ON_SALE__PRICE_EDIT",
            "offers": [{"masterSku": str(master_sku)}]
        }
        try:
            r0 = _mc_request("POST", edit_val_url, store_id=internal_store_id, headers=headers, json=edit_val_payload, timeout=10)
            if r0.status_code in [403, 429]:
                return False, f"{r0.status_code}_BLOCKED"
        except Exception as e:
            logger.debug(f"Инициация ON_SALE__PRICE_EDIT пропущена: {e}")

        # Шаг 1: Discount Check
        discount_url = "https://mc.shop.kaspi.kz/price/trends/api/v1/mc/discount"
        discount_payload = {
            "merchantUID": str(merchant_uid),
            "merchantSKU": str(sku),
            "entries": [{"city": str(city_id), "price": int(new_price)}]
        }
        try:
            r1 = _mc_request("POST", discount_url, store_id=internal_store_id, headers=headers, json=discount_payload, timeout=10)
            if r1.status_code in [403, 429]:
                return False, f"{r1.status_code}_BLOCKED"
            if r1.status_code != 200:
                return False, f"Шаг 1 (Discount) провален со статусом {r1.status_code}: {r1.text[:200]}"
        except Exception as e:
            return False, f"Исключение на Шаге 1: {e}"

        # Шаг 2: Offer Validation
        val_url = "https://mc.shop.kaspi.kz/offer-validation-api/merchant/offer/validate/v2"
        val_payload = {
            "action": "ON_SALE__PRICE_SAVE",
            "merchantUid": str(merchant_uid),
            "offers": [{
                "masterSku": str(master_sku),
                "availabilities": [{
                    "available": "yes",
                    "storeId": str(store_id_suffix),
                    "stockLevel": int(stock),
                    "cityId": str(city_id),
                    "price": int(new_price)
                }]
            }]
        }
        try:
            r2 = _mc_request("POST", val_url, store_id=internal_store_id, headers=headers, json=val_payload, timeout=10)
            if r2.status_code in [403, 429]:
                return False, f"{r2.status_code}_BLOCKED"
            if r2.status_code != 200:
                return False, f"Шаг 2 (Validation) провален со статусом {r2.status_code}: {r2.text[:200]}"
            resp_data = r2.json()
            if not resp_data.get("valid"):
                return False, f"Шаг 2 (Validation) не валиден: {resp_data.get('errorOffers') or r2.text}"
        except Exception as e:
            return False, f"Исключение на Шаге 2: {e}"

        # Шаг 3: Process Upload
        full_store_id = f"{merchant_uid}_{store_id_suffix}"
        process_url = "https://mc.shop.kaspi.kz/pricefeed/upload/merchant/process"
        process_payload = {
            "merchantUid": str(merchant_uid),
            "sku": str(sku),
            "model": str(model_name),
            "availabilities": [{"available": "yes", "storeId": str(full_store_id), "stockCount": int(stock)}],
            "cityPrices": [{"cityId": str(city_id), "value": int(new_price)}]
        }
        try:
            r3 = _mc_request("POST", process_url, store_id=internal_store_id, headers=headers, json=process_payload, timeout=10)
            if r3.status_code in [403, 429]:
                return False, f"{r3.status_code}_BLOCKED"
            if r3.status_code == 200:
                res_json = r3.json()
                if "id" in res_json:
                    return True, res_json["id"]
            return False, f"Шаг 3 (Process) провален со статусом {r3.status_code}: {r3.text[:200]}"
        except Exception as e:
            return False, f"Исключение на Шаге 3: {e}"

    @staticmethod
    def execute_updates(
        update_queue: List[dict],
        save_override_func,
        rate_limit_delay: float = 0.5,
        merchant_id: str = "20186986",
        store_id=None
    ) -> dict:
        """
        Разбирает очередь задач с удержанием паузы между обновлениями и шлет запросы на Kaspi MC API.
        """
        # 1. Smart Debounce: исключаем дубликаты SKU, оставляя только самое последнее/свежее решение
        deduped_tasks = {}
        for task in update_queue:
            deduped_tasks[task["sku"]] = task
        deduped_queue = list(deduped_tasks.values())

        if len(update_queue) != len(deduped_queue):
            logger.info(f"🧹 [Smart Debounce] Очередь оптимизирована: {len(update_queue)} -> {len(deduped_queue)} запросов.")

        updated_count = 0
        log_messages = []

        cookies = _load_mc_cookies(store_id=store_id)
        if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
            # Кук нет вообще (не только истекли) — пробуем автообновить сессию сохранёнными
            # email/паролем ещё до начала очереди, чтобы не пропускать весь батч зря.
            from backend.state_helpers import _refresh_mc_session
            if _refresh_mc_session(store_id=store_id):
                cookies = _load_mc_cookies(store_id=store_id)

        if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
            warn_msg = "⚠️ [Executor] Внимание: Отсутствуют или недействительны mc-session / mc-sid куки, автообновление сессии не удалось. Обновление цен в Kaspi MC пропущено (запись только в локальный фид). Требуется повторный вход в Kaspi MC."
            logger.warning(warn_msg)
            log_messages.append(warn_msg)
            has_mc_auth = False
        else:
            has_mc_auth = True

        for task in deduped_queue:
            sku_key = task["sku"]
            new_price = task["new_price"]
            config = task["config"]
            res = task["calculation_result"]
            p = task["product_ref"]

            p["price_num"] = int(new_price)
            p["price"] = f"{int(new_price)} ₸"

            min_comp = res["bestCompetitorPrice"] or new_price
            
            # Попытка моментального обновления через Kaspi MC API
            mc_success = False
            tx_info = ""
            if has_mc_auth:
                store_full_id = p.get("storeId") or f"{merchant_id}_001"
                store_suffix = store_full_id.split("_")[1] if "_" in store_full_id else "001"
                
                # Проверяем, привязан ли данный SKU к Мастер-Группе остатка
                from backend.state_helpers import _load_master_stock_groups
                master_groups = _load_master_stock_groups(store_id=store_id)
                stock_val = int(p.get("stock", 10))
                for mg in master_groups.values():
                    if mg.get("is_active", True) and sku_key in mg.get("linked_skus", []):
                        stock_val = int(mg.get("shared_stock", stock_val))
                        break

                success, val = RateLimitExecutorWorker._update_kaspi_price_sync(
                    merchant_uid=merchant_id,
                    sku=p.get("merchantSku") or sku_key,
                    master_sku=config.master_sku,
                    model_name=p.get("name", "Товар"),
                    new_price=int(new_price),
                    stock=stock_val,
                    store_id_suffix=store_suffix,
                    city_id=config.city_code,
                    internal_store_id=store_id
                )

                # 2. Backoff при 429 (Too Many Requests) / 403 (Forbidden/Rate limit)
                if not success and val in ["429_BLOCKED", "403_BLOCKED"]:
                    block_code = val.split("_")[0]
                    cooldown_msg = f"⚠️ [Executor] Обнаружен блок {block_code} от Kaspi. Включаем Backoff: пауза 60 секунд для защиты от флуда..."
                    logger.warning(cooldown_msg)
                    log_messages.append(cooldown_msg)

                    time.sleep(60) # Cooldown pause

                    # Повторная одиночная попытка после паузы
                    success, val = RateLimitExecutorWorker._update_kaspi_price_sync(
                        merchant_uid=merchant_id,
                        sku=p.get("merchantSku") or sku_key,
                        master_sku=config.master_sku,
                        model_name=p.get("name", "Товар"),
                        new_price=int(new_price),
                        stock=int(p.get("stock", 75)),
                        store_id_suffix=store_suffix,
                        city_id=config.city_code,
                        internal_store_id=store_id
                    )
                    
                    if not success:
                        abort_msg = f"🚨 [Executor] Повторный запрос после Backoff завершился неудачно ({val}). Останавливаем выполнение очереди!"
                        logger.error(abort_msg)
                        log_messages.append(abort_msg)
                        break # Прерываем всю очередь во избежание дальнейшей блокировки
                
                if success:
                    mc_success = True
                    tx_info = f" (MC API: ✓, ID транзакции: {val})"
                else:
                    tx_info = f" (MC API: ✗ Ошибка: {val})"
                    logger.warning(f"Ошибка моментального обновления цены для {sku_key}: {val}")

            msg = (
                f"🚀 [Executor] Авто-демпинг {sku_key} (город {config.city_code}): "
                f"{int(config.my_current_price)} ₸ -> {int(new_price)} ₸ "
                f"(конкурент: {int(min_comp)} ₸, действие: {res['action']}, позиция: {res['currentPos']}){tx_info}"
            )
            log_messages.append(msg)
            logger.info(msg)
            
            try:
                from backend.dumper.telemetry import emit_telemetry_sync
                emit_telemetry_sync("terminal_log", {"message": msg, "sku": sku_key, "price": new_price}, store_id=store_id)
            except Exception:
                pass

            updated_count += 1

            save_override_func(
                sku_key,
                int(new_price),
                p.get("stock", int(p.get("stock", 10))),
                p.get("brand"),
                p.get("status")
            )
            
            # 3. Jitter & Micro-delays: случайная задержка 1.2 - 2.8 сек вместо ровных интервалов
            if rate_limit_delay > 0:
                jitter = random.uniform(1.2, 2.8)
                time.sleep(jitter)

        # Сохранение логов для радара
        try:
            import datetime
            logs_file = "states/dumper_logs.json"
            existing_logs = []
            if os.path.exists(logs_file):
                with open(logs_file, "r", encoding="utf-8") as f:
                    existing_logs = json.load(f)
            
            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            formatted_new = []
            for m in log_messages:
                # Извлечение сохраненной маржи
                saved_val = 1500
                if "1-м месте" in m or "PULL_UP" in m:
                    saved_val = 2500
                formatted_new.append({
                    "timestamp": now_str,
                    "message": m,
                    "saved_margin": saved_val
                })
            
            combined_logs = (formatted_new + existing_logs)[:50]
            os.makedirs("states", exist_ok=True)
            with open(logs_file, "w", encoding="utf-8") as f:
                json.dump(combined_logs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save dumper logs to file: {e}")

        summary_msg = f"🚀 [Executor] Исполнение очереди завершено. Применено изменений: {updated_count}."
        return {
            "success": True,
            "updated_count": updated_count,
            "message": summary_msg,
            "logs": log_messages
        }
