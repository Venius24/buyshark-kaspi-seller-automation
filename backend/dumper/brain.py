import logging
from typing import List, Dict, Any, Optional
from backend.dumper.config import DumpingProductConfig

logger = logging.getLogger(__name__)

class DumpingEngine:
    @staticmethod
    def filter_offers(config: DumpingProductConfig, my_merchant_id: str, sellers_offers: List[dict]) -> List[dict]:
        """
        Фильтрует список предложений продавцов с учетом настроек фильтрации:
        - Черный список продавцов (blacklisted_merchants)
        - Минимальный рейтинг продавца (min_seller_rating)
        - Максимальное время доставки в днях / предзаказ (max_delivery_days)
        - Доставка только в свой город (same_city_only)
        """
        if not sellers_offers:
            return []

        blacklist = set(config.blacklisted_merchants or [])
        cleaned = []

        for offer in sellers_offers:
            merchant_id = str(offer.get("merchantId", ""))
            merchant_name = str(offer.get("merchantName", ""))

            # 1. Проверка черного списка
            if merchant_id in blacklist or merchant_name in blacklist:
                continue

            # 2. Очистка цены
            price_val = offer.get("price")
            if price_val is None:
                continue
            try:
                price_num = float(str(price_val).replace(" ₸", "").replace(" ", "").strip())
            except ValueError:
                continue

            # 3. Фильтр по рейтингу продавца
            if config.min_seller_rating is not None:
                rating = offer.get("rating") or offer.get("sellerRating")
                if rating is not None:
                    try:
                        if float(rating) < config.min_seller_rating:
                            continue
                    except ValueError:
                        pass

            # 4. Фильтр по дням доставки (предзаказы)
            if config.max_delivery_days is not None:
                delivery_days = offer.get("deliveryDays") or offer.get("delivery_days")
                if delivery_days is not None:
                    try:
                        if int(delivery_days) > config.max_delivery_days:
                            continue
                    except ValueError:
                        pass

            # 5. Фильтр по городу
            if config.same_city_only:
                offer_city = str(offer.get("cityCode") or offer.get("cityId") or offer.get("city") or "")
                if offer_city and offer_city.isdigit() and offer_city != str(config.city_code):
                    continue

            cleaned.append({
                "merchantId": merchant_id,
                "merchantName": merchant_name,
                "price": price_num,
                "raw_offer": offer
            })

        return cleaned

    @staticmethod
    def calculate_new_price(config: DumpingProductConfig, my_merchant_id: str, sellers_offers: List[dict]) -> dict:
        """
        Главный калькулятор цены с поддержкой 5 режимов демпинга:
        - OFF: выключен
        - TOP_1: 1-е место
        - TOP_2: 2-е место
        - MATCH_FIRST: прижиматься к 1-му месту (-1 ₸ или уход в min_price)
        - TARGET_POSITION: N-е место
        """
        # Если режим OFF или конфигурация выключена
        if not config.enabled or config.mode == "OFF":
            return {
                "targetPrice": config.my_current_price,
                "currentPos": None,
                "bestCompetitorPrice": None,
                "firstPrice": config.my_current_price,
                "action": "DISABLED",
                "dumping_status": "OFF"
            }

        cleaned_offers = DumpingEngine.filter_offers(config, my_merchant_id, sellers_offers)

        # Сортировка предложений по возрастанию цены
        sorted_offers = sorted(cleaned_offers, key=lambda x: x["price"]) if cleaned_offers else []

        # Поиск нашей текущей позиции среди отфильтрованных
        my_pos = None
        for idx, offer in enumerate(sorted_offers, start=1):
            if offer.get("merchantId") == str(my_merchant_id):
                my_pos = idx
                break

        # Список чисто конкурентов (без нас)
        competitors = [o for o in sorted_offers if o.get("merchantId") != str(my_merchant_id)]

        # Случай 0: Конкурентов нет
        if not competitors:
            # Если предложения не удалось загрузить (ошибка API / пустой ответ) — сохраняем текущую цену
            if not sellers_offers:
                return {
                    "targetPrice": config.my_current_price,
                    "currentPos": 1,
                    "bestCompetitorPrice": None,
                    "firstPrice": config.my_current_price,
                    "action": "HOLD_POSITION_NO_DATA",
                    "dumping_status": "WINNING_1ST"
                }

            # Если предложения загрузились успешно, но в них действительно 0 конкурентов (мы единственные)
            target_price = config.max_price if config.max_price > 0 else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": target_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER",
                "dumping_status": "MAX_PRICE_SINGLE"
            }

        first_price = sorted_offers[0]["price"] if sorted_offers else competitors[0]["price"]
        best_competitor_price = competitors[0]["price"]
        step = config.step if config.step > 0 else 10.0

        raw_target_price = config.my_current_price
        action = "HOLD_POSITION"
        expected_pos = 1

        # -------------------------------------------------------------
        # Ветвление по режимам демпинга
        # -------------------------------------------------------------

        if config.mode == "TOP_1":
            # Борьба за 1-е место (демпинг с шагом step)
            expected_pos = 1
            raw_target_price = best_competitor_price - step
            action = "DUMP_TOP_1"

            # Умная подтяжка цены вверх (Margin Maximization), если мы уже на 1 месте
            if my_pos == 1:
                gap = best_competitor_price - config.my_current_price
                if gap > (step * 2):
                    raw_target_price = best_competitor_price - step
                    action = "PULL_UP_PRICE"

        elif config.mode == "MATCH_FIRST":
            # Прижиматься к 1-му месту (-1 ₸ или минимальный шаг)
            expected_pos = 1
            raw_target_price = best_competitor_price - 1.0
            action = "MATCH_FIRST"

            if my_pos == 1:
                gap = best_competitor_price - config.my_current_price
                if gap > 2.0:
                    raw_target_price = best_competitor_price - 1.0
                    action = "PULL_UP_MATCH_FIRST"

        elif config.mode == "TOP_2":
            # Борьба за 2-е место
            expected_pos = 2
            if len(competitors) >= 2:
                second_comp_price = competitors[1]["price"]
                # Встаем чуть ниже 2-го конкурента
                raw_target_price = second_comp_price - step
                # Но не перебиваем 1-го конкурента!
                if raw_target_price <= best_competitor_price:
                    raw_target_price = best_competitor_price + 1.0
            else:
                # Конкурент всего 1 — встаем на шаг выше него (занимаем 2-е место)
                raw_target_price = best_competitor_price + step
            action = "HOLD_TOP_2"

        elif config.mode == "TARGET_POSITION":
            # Занимать N-е место
            target_n = max(1, config.target_position or 1)
            expected_pos = target_n

            if target_n == 1:
                raw_target_price = best_competitor_price - step
                action = "TARGET_POS_1"
            elif len(competitors) >= target_n:
                target_comp_price = competitors[target_n - 1]["price"]
                raw_target_price = target_comp_price - step
                prev_comp_price = competitors[target_n - 2]["price"]
                if raw_target_price <= prev_comp_price:
                    raw_target_price = prev_comp_price + 1.0
                action = f"TARGET_POS_{target_n}"
            else:
                # На карточке меньше конкурентов, чем N
                last_comp_price = competitors[-1]["price"]
                raw_target_price = last_comp_price + step
                action = f"TARGET_POS_{target_n}_AFTER_LAST"

        # -------------------------------------------------------------
        # Защита лимитов Min Price / Max Price и формирование статуса
        # -------------------------------------------------------------

        dumping_status = "WINNING_1ST"
        if config.min_price > 0 and raw_target_price < config.min_price:
            target_price = config.min_price
            action = "REACHED_MIN_PRICE"
            dumping_status = "AT_MIN_PRICE"
        else:
            target_price = raw_target_price
            if config.max_price > 0 and target_price > config.max_price:
                target_price = config.max_price

            if expected_pos == 1:
                dumping_status = "WINNING_1ST"
            elif expected_pos == 2:
                dumping_status = "HOLDING_2ND"
            else:
                dumping_status = "TARGET_POS_REACHED"

        return {
            "targetPrice": round(target_price, 2),
            "currentPos": my_pos or expected_pos,
            "bestCompetitorPrice": best_competitor_price,
            "firstPrice": first_price,
            "action": action,
            "dumping_status": dumping_status
        }


class BrainWorker:
    """
    2. 🧠 Воркер-Мозг (Pricing Engine / Decision Worker)
    Задача: Расчет новых оптимальных цен на основе правил, лимитов min/max и маржинальности.
    Формирует очередь команд на изменение цен.
    """
    @staticmethod
    def calculate_price_decisions(
        market_snapshot: Dict[str, dict],
        configs: dict,
        merchant_id: str
    ) -> Dict[str, Any]:
        """
        Принимает снимок рынка и возвращает задачи на изменение цен и сводную аналитику.
        """
        update_queue = []
        calculation_logs = []
        total_scanned = len(market_snapshot)
        winning_count = 0
        total_saved_margin = 0.0
        # Piggyback: собираем расклад по каждому SKU из уже загруженных offers
        # (никаких лишних запросов к Kaspi) — используется для сводки
        # "война по всем товарам" и лидерборда конкурентов на дашборде.
        price_war_items = []
        competitor_encounters = []

        for sku_key, snap in market_snapshot.items():
            cfg_data = configs.get(sku_key, {})
            p = snap["product_data"]

            price_num = p.get("price_num")
            if price_num is None:
                try:
                    price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                    price_num = float(price_str)
                except Exception:
                    price_num = 0.0

            config = DumpingProductConfig(
                sku=sku_key,
                master_sku=snap.get("master_sku", ""),
                my_current_price=float(price_num),
                min_price=float(cfg_data.get("min_price", 0.0)),
                max_price=float(cfg_data.get("max_price", 0.0)),
                step=float(cfg_data.get("dump_step", 1.0)),
                enabled=bool(cfg_data.get("enabled", True)),
                mode=str(cfg_data.get("mode", "TOP_1")),
                target_position=int(cfg_data.get("target_position", 1)),
                max_delivery_days=cfg_data.get("max_delivery_days"),
                min_seller_rating=cfg_data.get("min_seller_rating"),
                same_city_only=bool(cfg_data.get("same_city_only", False)),
                blacklisted_merchants=cfg_data.get("blacklisted_merchants") or [],
                city_code=str(snap.get("city_code", "750000000"))
            )

            res = DumpingEngine.calculate_new_price(config, merchant_id, snap.get("offers", []))
            new_price = res["targetPrice"]

            # Сохраняем вычисленный статус в объекте товара
            p["dumping_status"] = res["dumping_status"]
            p["dumping_action"] = res["action"]

            if res["dumping_status"] in ("WINNING_1ST", "MAX_PRICE_SINGLE"):
                winning_count += 1

            # --- Piggyback: расклад по этому SKU для сводки войны/лидерборда ---
            raw_offers = snap.get("offers", []) or []
            rival_offers = []
            for o in raw_offers:
                if str(o.get("merchantId", "")) == str(merchant_id):
                    continue
                price_val = o.get("price")
                if price_val is None:
                    continue
                try:
                    price_num = float(str(price_val).replace(" ₸", "").replace(" ", "").strip())
                except ValueError:
                    continue
                rival_offers.append({"name": o.get("merchantName") or "Конкурент", "price": price_num})
            rival_offers.sort(key=lambda x: x["price"])
            best_rival = rival_offers[0] if rival_offers else None

            price_war_items.append({
                "sku": sku_key,
                "name": p.get("name", sku_key),
                "our_price": float(config.my_current_price),
                "our_rank": res["currentPos"],
                "competitors_count": len(rival_offers),
                "best_competitor_price": best_rival["price"] if best_rival else None,
                "best_competitor_name": best_rival["name"] if best_rival else None,
            })
            if res["dumping_status"] not in ("WINNING_1ST", "MAX_PRICE_SINGLE") and best_rival:
                competitor_encounters.append({"sku": sku_key, "competitor_name": best_rival["name"]})

            if config.min_price > 0 and new_price > config.min_price:
                total_saved_margin += (new_price - config.min_price)

            # Отправляем обновление цены только при реальном изменении (>= 1.0 ₸)
            if abs(new_price - config.my_current_price) >= 1.0:
                update_queue.append({
                    "sku": sku_key,
                    "old_price": config.my_current_price,
                    "new_price": new_price,
                    "config": config,
                    "calculation_result": res,
                    "product_ref": p
                })
                log_msg = (
                    f"🧠 [Brain] Товар '{p.get('name', sku_key)}' (SKU {sku_key}): "
                    f"расчитано изменение цены {int(config.my_current_price)} ₸ -> {int(new_price)} ₸ "
                    f"(лидер рынка: {res.get('bestCompetitorPrice') or 'Н/Д'} ₸, режим: {config.mode}, действие: {res['action']})."
                )
                calculation_logs.append(log_msg)
            else:
                diag_msg = (
                    f"ℹ️ [Brain] Товар '{p.get('name', sku_key)}' (SKU {sku_key}): "
                    f"текущая цена {int(config.my_current_price)} ₸ равна оптимальной {int(new_price)} ₸ "
                    f"(лучший конкурент: {res.get('bestCompetitorPrice') or 'Н/Д'} ₸, действие: {res['action']}, позиция: {res['currentPos']})."
                )
                logger.info(diag_msg)
                calculation_logs.append(diag_msg)

        best_price_share = round((winning_count / total_scanned * 100.0), 1) if total_scanned > 0 else 0.0

        analytics = {
            "total_scanned": total_scanned,
            "winning_count": winning_count,
            "best_price_share": best_price_share,
            "total_saved_margin": round(total_saved_margin, 2)
        }

        logger.info(f"🧠 [Brain Worker] Решений: {len(update_queue)}, доля лучшей цены: {best_price_share}%, Сохраненная маржа: {round(total_saved_margin)} ₸")

        return {
            "update_queue": update_queue,
            "analytics": analytics,
            "calculation_logs": calculation_logs,
            "price_war_items": price_war_items,
            "competitor_encounters": competitor_encounters
        }
