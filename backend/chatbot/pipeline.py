"""Решения автоответчика до и после вызова модели.

Раньше эта последовательность жила только внутри worker._run_chatbot_pass, и
раннер воспроизведения (scripts/autoresponder_replay.py) повторял её копией.
Теперь воркер, раннер и evals/autoresponder вызывают одни и те же функции:
что измерено в eval, то и работает на проде.

Функции чистые: без сети, БД и Kaspi. Всё, что им нужно, приходит аргументами.
"""
from dataclasses import dataclass
from typing import Optional

from backend.chatbot.kaspi import check_logistics_question
from backend.chatbot.prompt import (
    STOCK_TRIGGERS, find_escalation_reason, find_human_triggers, promises_manager, says_message_empty,
)

# Голосовые бот не эскалирует: на них отвечает модель по правилу 8 промпта
# (решение владельца 03.10.2026). Фото и видео — сразу менеджеру.
MEDIA_HANDLED_BY_MODEL = {"голосовое"}


@dataclass
class Route:
    """action: "escalate" — сразу менеджеру, "template" — готовый ответ reply,
    "model" — отвечает модель. detail уходит в журнал (chat_response_logs.detail)."""
    action: str
    detail: Optional[str] = None
    reply: Optional[str] = None


def route_before_model(client_text: str, profile: dict, facts, pending_media: list) -> Route:
    """Детерминированные проверки до платного вызова модели, в порядке приоритета."""
    triggers = find_human_triggers(client_text, profile.get("human_triggers"))
    # Вопрос только про наличие, а остаток товара известен из каталога —
    # отвечает бот, менеджер не нужен.
    if triggers and set(triggers) <= STOCK_TRIGGERS and facts.stock_known:
        triggers = []
    if triggers:
        return Route("escalate", "триггеры: " + ", ".join(triggers))

    reason = find_escalation_reason(client_text)
    if reason:
        return Route("escalate", f"жалоба/просьба: {reason}")

    media = [k for k in pending_media if k not in MEDIA_HANDLED_BY_MODEL]
    if not client_text.strip() and not pending_media:
        media = ["пустое сообщение"]  # история не загрузилась или в ней нечего прочесть
    if media:
        return Route("escalate", "клиент прислал: " + ", ".join(sorted(set(media))))

    logistics_reply = check_logistics_question(client_text, profile)
    # Шаблон говорит «мы передали заказ, дальше везёт Kaspi». Если по данным
    # заказа это неправда (ещё на сборке, самовывоз, заказа нет) — отвечает
    # модель, у которой есть статус заказа. Данные не получили (сбой Kaspi) —
    # шаблон, как раньше.
    if logistics_reply and (facts.order or not facts.order_code) and not facts.handed_to_courier:
        logistics_reply = None
    if logistics_reply:
        return Route("template", "шаблон «логистика»", logistics_reply)
    return Route("model")


def route_after_model(reply: str, is_human_override: bool) -> Optional[str]:
    """None — ответ модели можно отправлять клиенту. Иначе — detail эскалации."""
    if is_human_override:
        return "модель: " + (reply or "")[:180]
    if says_message_empty(reply):
        return "модель не увидела сообщение: " + (reply or "")[:150]
    promise = promises_manager(reply)
    if promise:
        return f"модель обещала человека без метки ({promise}): " + (reply or "")[:150]
    return None
