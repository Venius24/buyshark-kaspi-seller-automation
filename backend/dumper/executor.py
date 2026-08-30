import logging
from typing import List, Optional

from backend.product_name_overrides import is_placeholder_name, resolve_product_name

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

        # Если цена не передана, равна дефолтному 1000, или название — наша
        # собственная заглушка, запрашиваем реальные детали карточки через BFF.
        need_details = not new_price or new_price <= 0 or new_price == 1000 or is_placeholder_name(model_name, sku)
        if need_details:
            details = RateLimitExecutorWorker._fetch_kaspi_offer_details(merchant_uid, sku, internal_store_id=internal_store_id)
            if details.get("price"):
                new_price = int(details["price"])
            if details.get("master_sku") and not master_sku:
                master_sku = details["master_sku"]
            if details.get("title") and not is_placeholder_name(details["title"], sku):
                model_name = details["title"]

        if not new_price or new_price <= 0:
            new_price = 1000

        # Не даём в Kaspi уехать нашей заглушке или буквальному "None" —
        # см. [[project_kaspi_xml_feed_incident]] и HANDOFF §3.67.5/3.83 про
        # то, как "Товар <SKU>" однажды закрепился в самом кабинете.
        model_name = resolve_product_name(sku, model_name) or model_name or "Товар"

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
    def _priority_for(action: str, old_price: float, new_price: float, stock: int) -> int:
        """
        Во что вкладывать квоту, когда её на всех не хватает.

        Снятие с продажи — всегда вперёд всех: если не отправить ноль, Kaspi
        продолжит продавать товар, которого нет. Дальше идёт реальная потеря
        позиции (деньги утекают прямо сейчас), потом подтяжка цены вверх
        (заработок, но подождёт), потом всё остальное.
        """
        from backend.services.offer_sync import Priority

        if int(stock) <= 0:
            return Priority.WITHDRAW
        if str(action).startswith("PULL_UP") or action == "SET_MAX_PRICE_SINGLE_SELLER":
            return Priority.MARGIN
        if new_price < old_price:
            return Priority.LOSING
        return Priority.MINOR

    @staticmethod
    def execute_updates(
        update_queue: List[dict],
        save_override_func,
        rate_limit_delay: float = 0.5,
        merchant_id: Optional[str] = None,
        store_id=None,
        max_workers: int = 3,
    ) -> dict:
        """
        Разбирает очередь решений Мозга и ОБЪЯВЛЯЕТ желаемое состояние оффера
        в backend/services/offer_sync.py. Ни одного запроса к Kaspi отсюда
        больше не уходит.

        Было (до 2026-08-12): на каждый SKU — 4 запроса подряд из пула в 3
        потока, при 429 слепой sleep(60) и остановка всей очереди. На проде
        это выродилось в мёртвую петлю: каждый 5-минутный цикл ловил 429 на
        7-18-й секунде, применял 0 изменений, спал и ретраился, снова
        выбирая окно квоты, — и так сутками.

        Стало: решения складываются в очередь намерений, единый флашер раз в
        45 секунд отбрасывает то, что ничего не меняет, сортирует остальное
        по приоритету, отрезает по реальному остатку квоты и отправляет всё
        одним batch-запросом. Отсутствие кук больше не теряет решения —
        намерение переживёт восстановление сессии.

        rate_limit_delay и max_workers сохранены в сигнатуре ради обратной
        совместимости вызовов и больше ни на что не влияют.
        """
        # 1. Smart Debounce: исключаем дубликаты SKU, оставляя только самое последнее/свежее решение
        deduped_tasks = {}
        for task in update_queue:
            deduped_tasks[task["sku"]] = task
        deduped_queue = list(deduped_tasks.values())

        if len(update_queue) != len(deduped_queue):
            logger.info(f"🧹 [Smart Debounce] Очередь оптимизирована: {len(update_queue)} -> {len(deduped_queue)} запросов.")

        from backend.services import offer_sync

        log_messages = []
        price_change_events = []  # реальные события реакции на конкурента (msg + честная saved_margin)

        cookies = _load_mc_cookies(store_id=store_id)
        if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
            # Кук нет — намерения всё равно ставим (их отправит флашер, когда
            # сессия вернётся), но пользователю честно говорим, что нужен вход.
            warn_msg = "⚠️ [Executor] Внимание: Отсутствуют или недействительны mc-session / mc-sid куки. Решения демпинга поставлены в очередь и уйдут в Kaspi MC после повторного входа."
            logger.warning(warn_msg)
            log_messages.append(warn_msg)

        # Мастер-Группы остатка грузятся ОДИН раз на всю очередь, а не на каждый SKU.
        from backend.state_helpers import _load_master_stock_groups
        master_groups = _load_master_stock_groups(store_id=store_id)

        updated_count = 0

        for task in deduped_queue:
            sku_key = task["sku"]
            new_price = task["new_price"]
            config = task["config"]
            res = task["calculation_result"]
            p = task["product_ref"]

            p["price_num"] = int(new_price)
            p["price"] = f"{int(new_price)} ₸"

            min_comp = res["bestCompetitorPrice"] or new_price

            store_full_id = p.get("storeId") or f"{merchant_id}_001"
            store_suffix = store_full_id.split("_")[1] if "_" in store_full_id else "001"

            # Привязан ли SKU к Мастер-Группе остатка. На/ниже "Стоп кол-ва"
            # группы (см. stock_sync_worker.py) на Kaspi уходит 0 — иначе
            # демпинг-цикл вернул бы карточку в продажу с реальным ненулевым
            # остатком поверх Stop Qty Protection.
            stock_val = int(p.get("stock", 10))
            for mg in master_groups.values():
                if mg.get("is_active", True) and sku_key in mg.get("linked_skus", []):
                    mg_stock = int(mg.get("shared_stock", stock_val))
                    mg_stop_qty = int(mg.get("stop_qty", 5))
                    stock_val = mg_stock if mg_stock > mg_stop_qty else 0
                    break

            priority = RateLimitExecutorWorker._priority_for(
                res.get("action", ""), config.my_current_price, new_price, stock_val
            )
            if store_id is not None:
                offer_sync.enqueue(
                    store_id,
                    p.get("merchantSku") or sku_key,
                    priority=priority,
                    reason=f"dump:{res.get('action', '')}",
                    price=int(new_price),
                    stock=stock_val,
                    master_sku=config.master_sku,
                    model=resolve_product_name(p.get("merchantSku") or sku_key, p.get("name")),
                    store_suffix=store_suffix,
                    city_id=config.city_code,
                )

            msg = (
                f"🚀 [Executor] Авто-демпинг {sku_key} (город {config.city_code}): "
                f"{int(config.my_current_price)} ₸ -> {int(new_price)} ₸ "
                f"(конкурент: {int(min_comp)} ₸, действие: {res['action']}, позиция: {res['currentPos']})"
                f" (в очереди на отправку, приоритет {priority})"
            )
            logger.info(msg)

            try:
                from backend.dumper.telemetry import emit_telemetry_sync
                emit_telemetry_sync("terminal_log", {"message": msg, "sku": sku_key, "price": new_price}, store_id=store_id)
            except Exception:
                pass

            save_override_func(
                sku_key,
                int(new_price),
                p.get("stock", int(p.get("stock", 10))),
                p.get("brand"),
                p.get("status")
            )

            # Честная сохранённая маржа — та же формула, что и total_saved_margin
            # в brain.py (new_price - min_price).
            real_saved_margin = 0
            if config.min_price > 0 and new_price > config.min_price:
                real_saved_margin = int(new_price - config.min_price)

            log_messages.append(msg)
            price_change_events.append({"message": msg, "saved_margin": real_saved_margin})
            updated_count += 1

        # Сохранение логов для радара (per-store, Слайс 5)
        try:
            import datetime
            from backend.state_helpers import _append_dumper_logs

            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            formatted_new = [
                {"timestamp": now_str, "message": evt["message"], "saved_margin": evt["saved_margin"]}
                for evt in price_change_events
            ]

            _append_dumper_logs(formatted_new, store_id=store_id)
        except Exception as e:
            logger.error(f"Failed to save dumper logs: {e}")

        summary_msg = f"🚀 [Executor] Решения поставлены в очередь на отправку: {updated_count}."
        return {
            "success": True,
            "updated_count": updated_count,
            "message": summary_msg,
            "logs": log_messages
        }
