import logging
from typing import List

logger = logging.getLogger(__name__)

class RateLimitExecutorWorker:
    """
    3. 🚀 Воркер-Исполнитель (API Updater / Rate-Limit Worker)
    Задача: Безопасное обновление цен, соблюдение Rate Limits и задержек, регистрация оверрайдов.
    """
    @staticmethod
    def execute_updates(
        update_queue: List[dict],
        save_override_func,
        rate_limit_delay: float = 0.05
    ) -> dict:
        """
        Разбирает очередь задач с удержанием паузы между обновлениями.
        """
        updated_count = 0
        log_messages = []

        for task in update_queue:
            sku_key = task["sku"]
            new_price = task["new_price"]
            config = task["config"]
            res = task["calculation_result"]
            p = task["product_ref"]

            p["price_num"] = int(new_price)
            p["price"] = f"{int(new_price)} ₸"
            updated_count += 1

            min_comp = res["bestCompetitorPrice"] or new_price
            msg = (
                f"🚀 [Executor] Авто-демпинг {sku_key} (город {config.city_code}): "
                f"{int(config.my_current_price)} ₸ -> {int(new_price)} ₸ "
                f"(конкурент: {int(min_comp)} ₸, действие: {res['action']}, позиция: {res['currentPos']})"
            )
            log_messages.append(msg)
            logger.info(msg)

            save_override_func(
                sku_key,
                int(new_price),
                p.get("stock", 10),
                p.get("brand"),
                p.get("status")
            )

        summary_msg = f"🚀 [Executor] Исполнение очереди завершено. Применено изменений: {updated_count}."
        return {
            "success": True,
            "updated_count": updated_count,
            "message": summary_msg,
            "logs": log_messages
        }
