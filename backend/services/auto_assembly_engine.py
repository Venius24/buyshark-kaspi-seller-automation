"""Чистая логика принятия решений «Автосборки» — по образцу
backend/dumper/brain.py::DumpingEngine (конфиг+данные внутрь, вердикт
наружу, никакого I/O). Решает ОДИН вопрос: имеет ли автоматика право
тронуть этот заказ прямо сейчас, и если да — какие из 3 стадий конвейера
(accept/assemble/waybill) разрешены правилом.

Сам конвейер (какая стадия применима к заказу в его ТЕКУЩЕМ статусе Kaspi,
принят/собран/передан) остаётся на стороне вызывающего кода
(stock_sync_worker.py) — это уже про состояние заказа, не про политику
правила.
"""
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from typing import Dict, FrozenSet, List, Optional

STAGES = ("accept", "assemble", "waybill")


@dataclass
class AutoAssemblyRule:
    delivery_type: str
    enabled: bool = False
    dry_run: bool = True
    stage_accept: bool = False
    stage_assemble: bool = False
    stage_waybill: bool = False
    # {"days": [0..6], "start": "HH:MM", "end": "HH:MM"} — None/пусто = 24/7
    schedule: Optional[dict] = None
    max_order_price: Optional[float] = None
    sku_blacklist: List[str] = field(default_factory=list)
    respect_stock_groups: bool = True
    max_per_cycle: Optional[int] = None


@dataclass
class OrderSnapshot:
    code: str
    delivery_type: str
    price: float
    skus: List[str] = field(default_factory=list)


@dataclass
class AutoAssemblyDecision:
    decision: str  # 'PROCEED' | 'SKIP_NO_RULE' | 'SKIP_DISABLED' | 'SKIP_OUT_OF_WINDOW'
                    # | 'SKIP_ALREADY_EXCLUDED' | 'SKIP_PRICE_LIMIT' | 'SKIP_SKU_BLACKLIST'
                    # | 'SKIP_NO_STOCK' | 'SKIP_CYCLE_LIMIT'
    reason: str
    allowed_stages: FrozenSet[str] = frozenset()
    mark_excluded_forever: bool = False
    dry_run: bool = True


def _parse_hhmm(s: str) -> dt_time:
    h, m = s.split(":")
    return dt_time(int(h), int(m))


def is_within_schedule(schedule: Optional[dict], now: datetime) -> bool:
    """Пустое/отсутствующее расписание = круглосуточно, без ограничений."""
    if not schedule:
        return True

    days = schedule.get("days")
    if days:
        # now.weekday(): 0=Пн .. 6=Вс, тот же формат, что и в schedule.days
        if now.weekday() not in days:
            return False

    start_str = schedule.get("start")
    end_str = schedule.get("end")
    if not start_str or not end_str:
        return True

    start = _parse_hhmm(start_str)
    end = _parse_hhmm(end_str)
    now_t = now.time()

    if start <= end:
        return start <= now_t <= end
    # Окно через полночь (напр. 22:00-06:00)
    return now_t >= start or now_t <= end


def evaluate_order(
    order: OrderSnapshot,
    rule: Optional[AutoAssemblyRule],
    now: datetime,
    already_excluded: bool = False,
    stock_ok: bool = True,
    orders_processed_this_cycle: int = 0,
) -> AutoAssemblyDecision:
    """
    Решает: имеет ли право автоматика тронуть этот заказ прямо сейчас.

    already_excluded — заказ уже был замечен ВНЕ окна расписания на одном из
    прошлых тиков. По явному выбору пользователя такие заказы автоматика
    больше не трогает НИКОГДА (не "копим и обработаем в начале окна") —
    вызывающий код обязан персистентно помнить это решение (auto_assembly_log)
    и передавать сюда, иначе заказ будет молча подхвачен на следующем тике,
    как только окно откроется.

    stock_ok — предвычисленный вызывающим кодом результат проверки остатка
    по Мастер-Группе (когда rule.respect_stock_groups=True); True, если
    остаток не при делах (группа не найдена / фильтр выключен) или остаток > 0.
    """
    if rule is None or not rule.enabled:
        return AutoAssemblyDecision(
            decision="SKIP_NO_RULE" if rule is None else "SKIP_DISABLED",
            reason="Нет активного правила для этого вида доставки" if rule is None else "Правило выключено",
        )

    if already_excluded:
        return AutoAssemblyDecision(
            decision="SKIP_ALREADY_EXCLUDED",
            reason="Заказ уже был вне окна расписания на прошлом тике — автоматика больше не трогает его",
        )

    if not is_within_schedule(rule.schedule, now):
        return AutoAssemblyDecision(
            decision="SKIP_OUT_OF_WINDOW",
            reason="Заказ вне разрешённого окна расписания правила",
            mark_excluded_forever=True,
        )

    if rule.max_order_price is not None and order.price > rule.max_order_price:
        return AutoAssemblyDecision(
            decision="SKIP_PRICE_LIMIT",
            reason=f"Сумма заказа {order.price:.0f} ₸ превышает лимит {rule.max_order_price:.0f} ₸",
        )

    blacklist = set(str(s).strip().lower() for s in (rule.sku_blacklist or []))
    if blacklist and any(str(s).strip().lower() in blacklist for s in order.skus):
        return AutoAssemblyDecision(
            decision="SKIP_SKU_BLACKLIST",
            reason="В заказе есть SKU из чёрного списка правила",
        )

    if rule.respect_stock_groups and not stock_ok:
        return AutoAssemblyDecision(
            decision="SKIP_NO_STOCK",
            reason="Остаток по Мастер-Группе товара равен 0 — принятие вручную",
        )

    if rule.max_per_cycle is not None and orders_processed_this_cycle >= rule.max_per_cycle:
        return AutoAssemblyDecision(
            decision="SKIP_CYCLE_LIMIT",
            reason=f"Достигнут лимит {rule.max_per_cycle} заказов за цикл для этого правила",
        )

    allowed = frozenset(
        stage for stage, flag in (
            ("accept", rule.stage_accept),
            ("assemble", rule.stage_assemble),
            ("waybill", rule.stage_waybill),
        ) if flag
    )

    return AutoAssemblyDecision(
        decision="PROCEED",
        reason="Заказ проходит все проверки правила",
        allowed_stages=allowed,
        dry_run=rule.dry_run,
    )
