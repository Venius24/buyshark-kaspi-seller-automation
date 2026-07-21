import logging
from typing import List, Dict, Any
from backend.dumper.config import DumpingProductConfig

logger = logging.getLogger(__name__)

class DumpingEngine:
    @staticmethod
    def calculate_new_price(config: DumpingProductConfig, my_merchant_id: str, sellers_offers: List[dict]) -> dict:
        """
        Рассчитывает новую цену с учетом конкурентов, лимитов min/max и шага демпинга.
        Реализует защиту от самодемпинга (Self-Dumping Prevention) и случай отсутствия на карточке.
        """
        if not sellers_offers:
            target_price = config.max_price if config.max_price else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": target_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER"
            }

        # Очистка и валидация цен предложений
        cleaned_offers = []
        for offer in sellers_offers:
            merchant_id = offer.get("merchantId")
            price_val = offer.get("price")
            if price_val is not None:
                try:
                    price_num = float(str(price_val).replace(" ₸", "").replace(" ", "").strip())
                    cleaned_offers.append({
                        "merchantId": str(merchant_id),
                        "price": price_num
                    })
                except ValueError:
                    continue

        if not cleaned_offers:
            target_price = config.max_price if config.max_price else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": target_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER"
            }

        # Сортировка предложений по возрастанию цены
        sorted_offers = sorted(cleaned_offers, key=lambda x: x["price"])
        first_price = sorted_offers[0]["price"]

        # Определение нашей позиции
        my_pos = None
        for idx, offer in enumerate(sorted_offers, start=1):
            if offer.get("merchantId") == str(my_merchant_id):
                my_pos = idx
                break

        # Отфильтровываем конкурентов
        competitors = [o for o in sorted_offers if o.get("merchantId") != str(my_merchant_id)]

        # Если конкурентов нет вообще (мы одни на карточке)
        if not competitors:
            target_price = config.max_price if config.max_price else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": first_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER"
            }

        best_competitor_price = competitors[0]["price"]

        # Кейс 1: Нас нет на карточке (встаем в демпинг)
        if my_pos is None:
            calculated_target = best_competitor_price - config.step
            if calculated_target < config.min_price:
                target_price = config.min_price
                action = "REACHED_MIN_PRICE_ENTER_CARD"
            else:
                target_price = calculated_target
                action = "ENTER_CARD"
            
            return {
                "targetPrice": target_price,
                "currentPos": len(sorted_offers) + 1,
                "bestCompetitorPrice": best_competitor_price,
                "firstPrice": first_price,
                "action": action
            }

        # Кейс 2: Мы на 1-м месте
        if my_pos == 1:
            gap = best_competitor_price - config.my_current_price
            if gap > (config.step * 2):
                # Разрыв слишком большой — приподнимаем цену (защита от самодемпинга)
                calculated_target = best_competitor_price - config.step
                if config.max_price:
                    calculated_target = min(calculated_target, config.max_price)
                
                if calculated_target < config.min_price:
                    target_price = config.min_price
                    action = "REACHED_MIN_PRICE_PULL_UP"
                else:
                    target_price = calculated_target
                    action = "PULL_UP_PRICE"
            else:
                # Держим текущую позицию
                target_price = config.my_current_price
                action = "HOLD_FIRST_POS"

            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": best_competitor_price,
                "firstPrice": first_price,
                "action": action
            }

        # Кейс 3: Мы на карточке, но не на 1-м месте
        calculated_target = best_competitor_price - config.step
        if calculated_target < config.min_price:
            target_price = config.min_price
            action = "REACHED_MIN_PRICE"
        else:
            target_price = calculated_target
            action = "PRICE_UPDATED"

        return {
            "targetPrice": target_price,
            "currentPos": my_pos,
            "bestCompetitorPrice": best_competitor_price,
            "firstPrice": first_price,
            "action": action
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
        total_scanned = len(market_snapshot)
        winning_count = 0
        total_saved_margin = 0.0

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
                master_sku=snap["master_sku"],
                my_current_price=float(price_num),
                min_price=float(cfg_data.get("min_price", 0.0)),
                max_price=float(cfg_data.get("max_price", 0.0)),
                step=float(cfg_data.get("dump_step", 10.0)),
                enabled=True,
                city_code=snap["city_code"]
            )

            res = DumpingEngine.calculate_new_price(config, merchant_id, snap["offers"])
            new_price = res["targetPrice"]

            if res["currentPos"] == 1:
                winning_count += 1

            if config.min_price > 0 and new_price > config.min_price:
                total_saved_margin += (new_price - config.min_price)

            if abs(new_price - config.my_current_price) > 0.01:
                update_queue.append({
                    "sku": sku_key,
                    "old_price": config.my_current_price,
                    "new_price": new_price,
                    "config": config,
                    "calculation_result": res,
                    "product_ref": p
                })

        buy_box_share = round((winning_count / total_scanned * 100.0), 1) if total_scanned > 0 else 0.0

        analytics = {
            "total_scanned": total_scanned,
            "winning_count": winning_count,
            "buy_box_share": buy_box_share,
            "total_saved_margin": round(total_saved_margin, 2)
        }

        logger.info(f"🧠 [Brain Worker] Решений: {len(update_queue)}, Buy Box Share: {buy_box_share}%, Сохраненная маржа: {round(total_saved_margin)} ₸")
        
        return {
            "update_queue": update_queue,
            "analytics": analytics
        }
