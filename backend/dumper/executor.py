import logging
import json
import os
import requests
import time
from typing import List

logger = logging.getLogger(__name__)

def _load_mc_cookies() -> dict:
    if not os.path.exists("state.json"):
        return {}
    try:
        with open("state.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies = {}
        for c in data.get("cookies", []):
            if c.get("name") in ["mc-session", "mc-sid"]:
                cookies[c["name"]] = c["value"]
        return cookies
    except Exception:
        return {}

class RateLimitExecutorWorker:
    """
    3. 🚀 Воркер-Исполнитель (API Updater / Rate-Limit Worker)
    Задача: Безопасное обновление цен через приватный 3-шаговый MC API, соблюдение задержек, регистрация оверрайдов.
    """
    @staticmethod
    def _update_kaspi_price_sync(
        merchant_uid: str,
        cookies: dict,
        sku: str,
        master_sku: str,
        model_name: str,
        new_price: int,
        stock: int = 10,
        store_id_suffix: str = "001",
        city_id: str = "750000000"
    ) -> tuple:
        """
        Синхронный запуск трехшагового пайплайна Kaspi MC.
        Возвращает (success: bool, tx_id_or_error_msg: str)
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://kaspi.kz",
            "Referer": "https://kaspi.kz/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "X-Auth-Version": "3"
        }

        # Шаг 1: Discount Check
        discount_url = "https://mc.shop.kaspi.kz/price/trends/api/v1/mc/discount"
        discount_payload = {
            "merchantUID": str(merchant_uid),
            "merchantSKU": str(sku),
            "entries": [{"city": str(city_id), "price": int(new_price)}]
        }
        try:
            r1 = requests.post(discount_url, headers=headers, cookies=cookies, json=discount_payload, timeout=10)
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
            r2 = requests.post(val_url, headers=headers, cookies=cookies, json=val_payload, timeout=10)
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
            r3 = requests.post(process_url, headers=headers, cookies=cookies, json=process_payload, timeout=10)
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
        merchant_id: str = "20186986"
    ) -> dict:
        """
        Разбирает очередь задач с удержанием паузы между обновлениями и шлет запросы на Kaspi MC API.
        """
        updated_count = 0
        log_messages = []

        cookies = _load_mc_cookies()
        if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
            warn_msg = "⚠️ [Executor] Внимание: Отсутствуют или недействительны mc-session / mc-sid куки. Обновление цен в Kaspi MC пропущено (запись только в локальный фид). Требуется SMS-вход."
            logger.warning(warn_msg)
            log_messages.append(warn_msg)
            has_mc_auth = False
        else:
            has_mc_auth = True

        for task in update_queue:
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
                
                success, val = RateLimitExecutorWorker._update_kaspi_price_sync(
                    merchant_uid=merchant_id,
                    cookies=cookies,
                    sku=sku_key,
                    master_sku=config.master_sku,
                    model_name=p.get("name", "Товар"),
                    new_price=int(new_price),
                    stock=int(p.get("stock", 75)),
                    store_id_suffix=store_suffix,
                    city_id=config.city_code
                )
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
            updated_count += 1

            save_override_func(
                sku_key,
                int(new_price),
                p.get("stock", int(p.get("stock", 10))),
                p.get("brand"),
                p.get("status")
            )
            
            if rate_limit_delay > 0:
                time.sleep(rate_limit_delay)

        summary_msg = f"🚀 [Executor] Исполнение очереди завершено. Применено изменений: {updated_count}."
        return {
            "success": True,
            "updated_count": updated_count,
            "message": summary_msg,
            "logs": log_messages
        }
