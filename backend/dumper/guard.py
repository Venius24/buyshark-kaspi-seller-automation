"""
Защита демпинга от «оттеснения» (2026-10-03).

Что происходило на живых данных Бархана (сентябрь 2026, HANDOFF §3.111):
- лесенка: мы ставим X, через ~15 минут соперник ставит X−1, наш бот уходит
  в X−3, и так по кругу — подставка для зубочисток 603 → 433 ₸ за три недели,
  X172-0009-1 481 → 135 ₸ за 1294 шага, пока третий продавец стоял на 212 ₸;
- провокаторы: напротив нас стоят продавцы с 0–24 отзывами или без рейтинга,
  зарегистрированные несколько месяцев назад, часто везут из другого города —
  покупатель их почти не выбирает, но наш бот всё равно уходит под них.

Модуль — чистые функции без БД и сети, чтобы движок (brain.py), отчёт и
тесты считали одно и то же:
- GuardConfig и разворот наследования «магазин → Мастер-Группа → товар»;
- normalize_offer: сырой оффер Kaspi offer-view → плоские поля. Именно здесь
  живут настоящие имена полей (merchantRating, merchantReviewsQuantity,
  deliveryOptions.*.interCity/deliveryDays) — старые фильтры brain.py читали
  несуществующие `rating`/`deliveryDays`/`cityCode` и не отсеивали никого;
- weak_reasons: почему соперника не учитывать;
- detect_ladder: распознавание лесенки по истории наших ходов;
- apply_guard_limits: пол под следующим нормальным конкурентом, предел
  снижения за сутки, заморозка при лесенке.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------
# Возраст продавца по номеру
# --------------------------------------------------------------------------

# merchant_id новых продавцов Kaspi выдаются подряд, но не с ровной скоростью
# (130–220 в день в разные месяцы). Опорные точки сняты со страниц продавцов
# 2026-10-03 (`created` в BACKEND.components.merchant), между ними —
# линейная интерполяция, после последней — экстраполяция с её скоростью.
_AGE_ANCHORS: List[Tuple[int, datetime]] = [
    (20186986, datetime(2024, 1, 31)),
    (20340000, datetime(2024, 10, 18)),
    (20370000, datetime(2025, 2, 11)),
    (20400000, datetime(2025, 6, 29)),
    (20420000, datetime(2025, 10, 3)),
    (20437934, datetime(2025, 12, 22)),
    (20450000, datetime(2026, 2, 28)),
    (20470000, datetime(2026, 7, 5)),
    (20481000, datetime(2026, 9, 28)),
]
_ANCHOR_IDS = [a[0] for a in _AGE_ANCHORS]
# ~5 лет вперёд при 150–220 номерах в день — дальше это уже не номер из серии.
_MAX_IDS_AHEAD = 400_000


def estimate_registered_at(merchant_id) -> Optional[datetime]:
    """Примерная дата регистрации продавца. None — номер не числовой
    (старые продавцы вроде «Astanacase») или старше первой опорной точки:
    про таких известно только, что они не новички."""
    try:
        mid = int(str(merchant_id).strip())
    except (TypeError, ValueError):
        return None
    # Вне серии: старые продавцы ниже первой опоры и номера другого формата
    # (живой пример 03.10 — «2207134711»), которые экстраполяция унесла бы
    # за пределы datetime и уронила весь цикл демпинга.
    if mid < _ANCHOR_IDS[0] or mid > _ANCHOR_IDS[-1] + _MAX_IDS_AHEAD:
        return None
    i = bisect.bisect_right(_ANCHOR_IDS, mid) - 1
    if i >= len(_AGE_ANCHORS) - 1:
        (id0, d0), (id1, d1) = _AGE_ANCHORS[-2], _AGE_ANCHORS[-1]
    else:
        (id0, d0), (id1, d1) = _AGE_ANCHORS[i], _AGE_ANCHORS[i + 1]
    seconds_per_id = (d1 - d0).total_seconds() / max(1, id1 - id0)
    return d0 + timedelta(seconds=(mid - id0) * seconds_per_id)


def merchant_age_days(merchant_id, now: Optional[datetime] = None) -> Optional[int]:
    reg = estimate_registered_at(merchant_id)
    if reg is None:
        return None
    now = now or datetime.utcnow()
    return max(0, (now - reg).days)


# --------------------------------------------------------------------------
# Оффер
# --------------------------------------------------------------------------

def parse_price(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace("₸", "").replace(" ", "").replace("\xa0", "").strip())
    except ValueError:
        return None


def _to_float(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def normalize_offer(offer: dict, city_code: Optional[str] = None) -> dict:
    """Плоские поля оффера. Отсутствующее — None, а не 0: «нет рейтинга» и
    «рейтинг 0.0» Kaspi отдаёт по-разному, и это разные сигналы."""
    opts = offer.get("deliveryOptions") or {}
    days = [_to_int(o.get("deliveryDays")) for o in opts.values() if isinstance(o, dict)]
    days = [d for d in days if d is not None]
    delivery_days = min(days) if days else _to_int(offer.get("deliveryDays") or offer.get("delivery_days"))

    intercity = None
    flags = [o.get("interCity") for o in opts.values() if isinstance(o, dict) and o.get("interCity") is not None]
    if flags:
        # Межгород, только если ВСЕ способы доставки межгородские.
        intercity = all(bool(f) for f in flags)
    else:
        offer_city = str(offer.get("cityCode") or offer.get("cityId") or offer.get("city") or "")
        if offer_city.isdigit() and city_code:
            intercity = offer_city != str(city_code)

    rating = offer.get("merchantRating")
    if rating is None:
        rating = offer.get("rating", offer.get("sellerRating"))
    reviews = offer.get("merchantReviewsQuantity")
    if reviews is None:
        reviews = offer.get("reviewsQuantity")

    return {
        "merchant_id": str(offer.get("merchantId", "")),
        "name": str(offer.get("merchantName", "")),
        "price": parse_price(offer.get("price")),
        "rating": _to_float(rating),
        "reviews": _to_int(reviews),
        "intercity": intercity,
        "delivery_days": delivery_days,
        "preorder": (_to_int(offer.get("preorder")) or 0) > 0,
    }


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------

LADDER_FLAG = "FLAG"        # только пометить в отчёте
LADDER_FREEZE = "FREEZE"    # не опускать цену, пока идёт перемирие
LADDER_RETREAT = "RETREAT"  # перестать отвечать этому сопернику на время перемирия
# Мы на 1–2 месте — раз в сутки прыгаем на max_price и обнуляем лесенку
# (решение владельца 03.10.2026): бот-соперник, привязанный к нашей цене,
# поднимается следом, и война продолжается с высокого уровня, а не с копеек.
LADDER_RESET_MAX = "RESET_MAX"
LADDER_RESPONSES = (LADDER_FLAG, LADDER_FREEZE, LADDER_RETREAT, LADDER_RESET_MAX)
LADDER_RESET_EVERY_HOURS = 24
LADDER_RESET_MAX_RANK = 2

# Продавец без рейтинга: решает менеджер, по каждому товару отдельно.
NO_RATING_ASK = "ASK"            # учитывать, но спросить менеджера
NO_RATING_IGNORE = "IGNORE"      # не учитывать никогда
NO_RATING_CONSIDER = "CONSIDER"  # учитывать молча
NO_RATING_ACTIONS = (NO_RATING_ASK, NO_RATING_IGNORE, NO_RATING_CONSIDER)

# Решение менеджера по продавцу на конкретном товаре.
DECISION_ASK = "ASK"
DECISION_IGNORE = "IGNORE"
DECISION_CONSIDER = "CONSIDER"
DECISIONS = (DECISION_ASK, DECISION_IGNORE, DECISION_CONSIDER)

# Коды действий, которые пишутся в историю ходов и служат «событиями» защиты.
ACTION_DAILY_DROP = "GUARD_DAILY_DROP"
ACTION_DROP_PAUSE = "GUARD_DROP_PAUSE"
ACTION_LADDER_RESET = "GUARD_LADDER_RESET"

ALL_IGNORED_HOLD = "HOLD"   # все соперники отсеяны — держим текущую цену
ALL_IGNORED_MAX = "MAX"     # все соперники отсеяны — как будто мы одни (ставим max_price)

_INT_FIELDS = {"min_reviews", "min_merchant_age_days", "max_extra_delivery_days"}
# Насколько дольше нашей может быть доставка у продавца, от которого ещё
# считается «пол под следующим конкурентом» (иначе пол строится от
# предзаказа на 18 дней — живой случай T114-0023-1, 03.10.2026).
FLOOR_REF_MAX_EXTRA_DAYS = 2
_NULLABLE_FIELDS = _INT_FIELDS | {"min_rating", "floor_below_next_pct", "max_daily_drop_pct"}

OVERRIDE_INHERIT = "INHERIT"
OVERRIDE_CUSTOM = "CUSTOM"
OVERRIDE_OFF = "OFF"


@dataclass
class GuardConfig:
    """Умолчания — цифры, согласованные с владельцем 03.10.2026 (HANDOFF
    §3.112). Сама защита по умолчанию выключена."""
    enabled: bool = False
    observe_only: bool = False                 # наблюдение: считать и показывать, цены не трогать
    # --- кого не учитывать ---
    min_reviews: Optional[int] = 50            # меньше N отзывов (только у продавцов с рейтингом)
    no_rating_action: str = NO_RATING_ASK      # продавец без рейтинга: спросить / игнорировать / учитывать
    min_rating: Optional[float] = None         # рейтинг ниже X
    min_merchant_age_days: Optional[int] = None  # продавец моложе N дней
    ignore_intercity: bool = False             # везёт только из другого города
    max_extra_delivery_days: Optional[int] = None  # доставка дольше нашей больше чем на N дней
    ignore_preorder: bool = False
    blacklist: List[str] = field(default_factory=list)
    whitelist: List[str] = field(default_factory=list)  # этих учитываем всегда
    all_ignored_action: str = ALL_IGNORED_HOLD
    # --- защита от лесенки ---
    floor_below_next_pct: Optional[float] = None  # не ниже чем на X% под следующим нормальным конкурентом
    max_daily_drop_pct: Optional[float] = 15.0    # упали на X% за сутки — пауза
    drop_pause_hours: int = 3                     # сколько длится пауза после такого падения
    drop_alert: bool = True                       # сообщать о паузе в Телеграм
    ladder_min_moves: int = 5                     # сколько ответов одному сопернику...
    ladder_window_hours: int = 24                 # ...за сколько часов — это лесенка
    ladder_response: str = LADDER_RESET_MAX
    ladder_cooldown_hours: int = 12               # длительность перемирия (FREEZE / RETREAT)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "GuardConfig":
        cfg = cls()
        data = dict(data or {})
        # Флаг до 03.10.2026 (на прод не выезжал, но мог остаться в песочницах).
        if "ignore_no_rating" in data and "no_rating_action" not in data:
            data["no_rating_action"] = NO_RATING_IGNORE if data["ignore_no_rating"] else NO_RATING_CONSIDER
        for f in fields(cls):
            if f.name not in (data or {}):
                continue
            v = data[f.name]
            # None у числового порога — «не задан»; у остальных полей None
            # значит «нет значения», и остаётся умолчание.
            if v is None and f.name not in _NULLABLE_FIELDS:
                continue
            if v is not None and f.name in _NULLABLE_FIELDS:
                v = (_to_int if f.name in _INT_FIELDS else _to_float)(v)
            setattr(cfg, f.name, v)
        cfg.enabled = bool(cfg.enabled)
        cfg.observe_only = bool(cfg.observe_only)
        cfg.drop_alert = bool(cfg.drop_alert)
        if cfg.no_rating_action not in NO_RATING_ACTIONS:
            cfg.no_rating_action = NO_RATING_ASK
        cfg.blacklist = [str(x).strip() for x in (cfg.blacklist or []) if str(x).strip()]
        cfg.whitelist = [str(x).strip() for x in (cfg.whitelist or []) if str(x).strip()]
        if cfg.ladder_response not in LADDER_RESPONSES:
            cfg.ladder_response = LADDER_FLAG
        if cfg.all_ignored_action not in (ALL_IGNORED_HOLD, ALL_IGNORED_MAX):
            cfg.all_ignored_action = ALL_IGNORED_HOLD
        cfg.ladder_min_moves = max(2, int(cfg.ladder_min_moves or 5))
        cfg.ladder_window_hours = max(1, int(cfg.ladder_window_hours or 24))
        cfg.ladder_cooldown_hours = max(0, int(cfg.ladder_cooldown_hours or 0))
        cfg.drop_pause_hours = max(1, int(cfg.drop_pause_hours or 3))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_guard_config(store_defaults: Optional[dict], *overrides: Optional[dict]) -> GuardConfig:
    """Действующие настройки защиты: общие настройки магазина, поверх них —
    переопределения по порядку (Мастер-Группа, потом товар).

    Переопределение — {"mode": "INHERIT"|"CUSTOM"|"OFF", ...поля}. CUSTOM
    накладывает свои поля поверх родителя (незаданные наследуются), OFF —
    защита для этого товара выключена целиком, INHERIT/None — ничего не меняет.
    """
    merged = dict(store_defaults or {})
    for ov in overrides:
        if not ov:
            continue
        mode = str(ov.get("mode") or OVERRIDE_INHERIT).upper()
        if mode == OVERRIDE_OFF:
            merged = dict(merged)
            merged["enabled"] = False
        elif mode == OVERRIDE_CUSTOM:
            merged = {**merged, **{k: v for k, v in ov.items() if k != "mode"}}
            merged.setdefault("enabled", True)
    return GuardConfig.from_dict(merged)


# --------------------------------------------------------------------------
# Кого не учитывать
# --------------------------------------------------------------------------

REASON_BLACKLIST = "blacklist"
REASON_FEW_REVIEWS = "few_reviews"
REASON_NO_RATING = "no_rating"
REASON_LOW_RATING = "low_rating"
REASON_NEWCOMER = "newcomer"
REASON_INTERCITY = "intercity"
REASON_SLOW_DELIVERY = "slow_delivery"
REASON_PREORDER = "preorder"
REASON_LADDER_RETREAT = "ladder_retreat"
REASON_MANAGER = "manager"

REASON_LABELS = {
    REASON_BLACKLIST: "в чёрном списке",
    REASON_FEW_REVIEWS: "мало отзывов",
    REASON_NO_RATING: "нет рейтинга",
    REASON_LOW_RATING: "низкий рейтинг",
    REASON_NEWCOMER: "новый продавец",
    REASON_INTERCITY: "везёт из другого города",
    REASON_SLOW_DELIVERY: "доставка дольше нашей",
    REASON_PREORDER: "предзаказ",
    REASON_LADDER_RETREAT: "перемирие после лесенки",
    REASON_MANAGER: "решение менеджера",
}


def _listed(entries: Iterable[str], off: dict) -> bool:
    keys = {off.get("merchant_id") or "", (off.get("name") or "").strip().lower()}
    for e in entries:
        if e == off.get("merchant_id") or e.strip().lower() in keys:
            return True
    return False


def has_no_rating(off: dict) -> bool:
    return off.get("rating") is None or (off.get("rating") == 0 and not off.get("reviews"))


def weak_reasons(off: dict, cfg: GuardConfig, our: Optional[dict] = None,
                 now: Optional[datetime] = None, decision: Optional[str] = None) -> List[str]:
    """Причины не учитывать соперника. Пусто — соперник нормальный.
    Неизвестное поле никогда не считается причиной (кроме «нет рейтинга»,
    которое и есть отсутствие поля).

    decision — решение менеджера по этому продавцу на этом товаре: CONSIDER
    действует как белый список, IGNORE — как чёрный."""
    if decision == DECISION_CONSIDER:
        return []
    if cfg.whitelist and _listed(cfg.whitelist, off):
        return []
    reasons = []
    if decision == DECISION_IGNORE:
        reasons.append(REASON_MANAGER)
    if cfg.blacklist and _listed(cfg.blacklist, off):
        reasons.append(REASON_BLACKLIST)
    no_rating = has_no_rating(off)
    # Порог отзывов — только для продавцов с рейтингом: судьбу продавца без
    # рейтинга решает no_rating_action (по умолчанию — менеджер).
    if cfg.min_reviews is not None and not no_rating:
        if (off.get("reviews") or 0) < cfg.min_reviews:
            reasons.append(REASON_FEW_REVIEWS)
    if no_rating and cfg.no_rating_action == NO_RATING_IGNORE:
        reasons.append(REASON_NO_RATING)
    if cfg.min_rating is not None and not no_rating and off["rating"] < cfg.min_rating:
        reasons.append(REASON_LOW_RATING)
    if cfg.min_merchant_age_days is not None:
        age = merchant_age_days(off.get("merchant_id"), now)
        if age is not None and age < cfg.min_merchant_age_days:
            reasons.append(REASON_NEWCOMER)
    if cfg.ignore_intercity and off.get("intercity") is True:
        reasons.append(REASON_INTERCITY)
    if cfg.max_extra_delivery_days is not None and off.get("delivery_days") is not None:
        ours = (our or {}).get("delivery_days")
        base = ours if ours is not None else 0
        if off["delivery_days"] > base + cfg.max_extra_delivery_days:
            reasons.append(REASON_SLOW_DELIVERY)
    if cfg.ignore_preorder and off.get("preorder"):
        reasons.append(REASON_PREORDER)
    return reasons


def needs_decision(off: dict, cfg: GuardConfig, decision: Optional[str] = None) -> bool:
    """Продавец без рейтинга, по которому менеджер ещё не решал."""
    if decision in (DECISION_IGNORE, DECISION_CONSIDER):
        return False
    if cfg.no_rating_action != NO_RATING_ASK or not has_no_rating(off):
        return False
    if (cfg.whitelist and _listed(cfg.whitelist, off)) or (cfg.blacklist and _listed(cfg.blacklist, off)):
        return False
    return True


def floor_reference_prices(considered: List[dict], our: Optional[dict] = None) -> List[float]:
    """Цены соперников, от которых считается «пол под следующим
    конкурентом»: без предзаказов, межгорода и доставки дольше нашей больше
    чем на FLOOR_REF_MAX_EXTRA_DAYS — такие продавцы не ориентир рынка, даже
    если демпинг их учитывает."""
    ours = (our or {}).get("delivery_days")
    out = []
    for o in considered:
        if o.get("preorder") or o.get("intercity") is True:
            continue
        if ours is not None and o.get("delivery_days") is not None and o["delivery_days"] > ours + FLOOR_REF_MAX_EXTRA_DAYS:
            continue
        if o.get("price") is not None:
            out.append(float(o["price"]))
    return sorted(out)


def is_suspicious(off: dict, now: Optional[datetime] = None) -> List[str]:
    """Признаки провокатора для ОТЧЁТА — независимо от настроек магазина
    (отчёт должен показывать атаку и тому, кто фильтры ещё не включил).
    Пороги фиксированные и консервативные."""
    sig = []
    reviews = off.get("reviews") or 0
    if off.get("rating") is None or reviews == 0:
        sig.append(REASON_NO_RATING)
    elif reviews < 30:
        sig.append(REASON_FEW_REVIEWS)
    age = merchant_age_days(off.get("merchant_id"), now)
    if age is not None and age < 365:
        sig.append(REASON_NEWCOMER)
    return sig


# --------------------------------------------------------------------------
# Лесенка
# --------------------------------------------------------------------------

@dataclass
class LadderSignal:
    rival_id: str
    rival_name: str
    moves: int
    first_at: datetime
    last_at: datetime
    price_from: float
    price_to: float
    active_until: Optional[datetime]  # до какого момента идёт перемирие

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("first_at", "last_at", "active_until"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def _ladder_gap_limit(step: float) -> float:
    # Бот-соперник подрезает на 1–2 ₸ (иногда на наш шаг) — «подрезка» больше
    # этого уже обычное ценовое решение, а не автомат.
    return max(3.0, step * 2 + 1)


def detect_ladder(moves: List[dict], cfg: GuardConfig, step: float,
                  now: Optional[datetime] = None) -> Optional[LadderSignal]:
    """moves — наши ходы по одному SKU: {created_at, our_old, our_new,
    rival_id, rival_name, rival_price}. Ход считается ответом на подрезку,
    если мы снижали цену, а соперник стоял ниже нашей старой цены не больше
    чем на _ladder_gap_limit. ladder_min_moves таких ответов одному сопернику
    в окне ladder_window_hours — лесенка. rival_id может быть пустым (ходы,
    восстановленные из старых логов) — такие считаются одним соперником."""
    now = now or datetime.utcnow()
    since = now - timedelta(hours=cfg.ladder_window_hours + cfg.ladder_cooldown_hours)
    gap_limit = _ladder_gap_limit(step)
    by_rival: Dict[str, List[dict]] = {}
    for m in moves or []:
        ts = m.get("created_at")
        if not ts or ts < since:
            continue
        old, new, rp = m.get("our_old"), m.get("our_new"), m.get("rival_price")
        if old is None or new is None or rp is None or new >= old:
            continue
        if not (0 < old - rp <= gap_limit):
            continue
        by_rival.setdefault(str(m.get("rival_id") or ""), []).append(m)

    best: Optional[LadderSignal] = None
    for rid, ms in by_rival.items():
        ms.sort(key=lambda m: m["created_at"])
        # Скользящее окно: есть ли ladder_min_moves ответов в пределах окна.
        window = timedelta(hours=cfg.ladder_window_hours)
        j = 0
        hit = None
        for i in range(len(ms)):
            while ms[i]["created_at"] - ms[j]["created_at"] > window:
                j += 1
            if i - j + 1 >= cfg.ladder_min_moves:
                hit = (j, i)
        if not hit:
            continue
        j, i = hit
        # Захватываем всю серию до последнего хода, а не только окно.
        series = ms[j:]
        last_at = series[-1]["created_at"]
        active_until = last_at + timedelta(hours=cfg.ladder_cooldown_hours) if cfg.ladder_cooldown_hours else None
        sig = LadderSignal(
            rival_id=rid,
            rival_name=str(series[-1].get("rival_name") or ""),
            moves=len(series),
            first_at=series[0]["created_at"],
            last_at=last_at,
            price_from=float(series[0]["our_old"]),
            price_to=float(series[-1]["our_new"]),
            active_until=active_until,
        )
        if best is None or sig.last_at > best.last_at:
            best = sig
    return best


def ladder_is_active(sig: Optional[LadderSignal], now: Optional[datetime] = None) -> bool:
    if not sig or not sig.active_until:
        return False
    return (now or datetime.utcnow()) < sig.active_until


def last_event_at(moves: List[dict], action: str) -> Optional[datetime]:
    ts = [m["created_at"] for m in moves or [] if m.get("action") == action and m.get("created_at")]
    return max(ts) if ts else None


def moves_since_reset(moves: List[dict]) -> List[dict]:
    """Ходы после последнего сброса лесенки — сброс её обнуляет."""
    reset_at = last_event_at(moves, ACTION_LADDER_RESET)
    if reset_at is None:
        return list(moves or [])
    return [m for m in moves if m["created_at"] > reset_at]


def ladder_reset_due(ladder: Optional[LadderSignal], moves: List[dict], cfg: GuardConfig,
                     card_rank: Optional[int], max_price: float,
                     now: Optional[datetime] = None) -> bool:
    """Пора ли прыгнуть на max_price и обнулить лесенку: ответ RESET_MAX,
    лесенка свежая (последний ход в окне), мы на 1–2 месте и прошлый сброс
    был не меньше суток назад."""
    if cfg.ladder_response != LADDER_RESET_MAX or not ladder or max_price <= 0:
        return False
    if card_rank is None or card_rank > LADDER_RESET_MAX_RANK:
        return False
    now = now or datetime.utcnow()
    if now - ladder.last_at > timedelta(hours=cfg.ladder_window_hours):
        return False
    last = last_event_at(moves, ACTION_LADDER_RESET)
    return last is None or now - last >= timedelta(hours=LADDER_RESET_EVERY_HOURS)


@dataclass
class DropState:
    peak: Optional[float]               # от какой цены меряется падение
    pause_until: Optional[datetime]     # до какого момента идёт пауза


def drop_state(moves: List[dict], cfg: GuardConfig, current: Optional[float] = None,
               now: Optional[datetime] = None) -> DropState:
    """Падение меряется от «якоря» — самого позднего из трёх моментов:
    - 24 часа назад (тогда от нашей цены на начало окна);
    - прошлая пауза из-за падения (от цены, на которой встали);
    - сброс лесенки (от цены ДО прыжка на максимум — иначе возврат с
      max_price после каждого сброса защита считала бы обвалом).
    После паузы бот может снова опуститься ещё на max_daily_drop_pct —
    а не стоит до конца суток."""
    now = now or datetime.utcnow()
    moves = sorted([m for m in moves or [] if m.get("created_at")], key=lambda m: m["created_at"])
    last_drop = next((m for m in reversed(moves) if m.get("action") == ACTION_DAILY_DROP), None)
    last_reset = next((m for m in reversed(moves) if m.get("action") == ACTION_LADDER_RESET), None)
    pause_until = last_drop["created_at"] + timedelta(hours=cfg.drop_pause_hours) if last_drop else None

    anchor_at = now - timedelta(hours=24)
    start_ref = None
    from_window_start = True
    for ev, ref_key in sorted(
        [(e, k) for e, k in ((last_drop, "our_new"), (last_reset, "our_old")) if e],
        key=lambda x: x[0]["created_at"],
    ):
        if ev["created_at"] >= anchor_at:
            anchor_at, start_ref, from_window_start = ev["created_at"], ev.get(ref_key), False
    after = [m for m in moves if m["created_at"] > anchor_at and m.get("action") != ACTION_LADDER_RESET]
    candidates = [start_ref]
    if from_window_start and after:
        candidates.append(after[0].get("our_old"))
    candidates += [m.get("our_new") for m in after]
    candidates = [float(c) for c in candidates if c is not None]
    if candidates and current is not None:
        candidates.append(float(current))
    return DropState(peak=max(candidates) if candidates else None, pause_until=pause_until)


# --------------------------------------------------------------------------
# Ограничения цены
# --------------------------------------------------------------------------

def apply_guard_limits(
    target: float,
    current: float,
    considered_prices: List[float],
    cfg: GuardConfig,
    min_price: float,
    max_price: float,
    peak_24h: Optional[float] = None,
    freeze=False,
) -> Tuple[float, Optional[str]]:
    """Поднимает цену, которую посчитал обычный демпинг, если она нарушает
    защиту. Никогда не опускает. Возвращает (цена, код сработавшей защиты).

    considered_prices — цены соперников-ориентиров по возрастанию (см.
    floor_reference_prices). freeze — True (лесенка) или код заморозки.
    """
    floor = None
    reason = None

    def lift(value: float, code: str):
        nonlocal floor, reason
        if value is not None and (floor is None or value > floor):
            floor, reason = value, code

    if freeze and target < current:
        lift(current, freeze if isinstance(freeze, str) else "GUARD_LADDER_FREEZE")

    if cfg.floor_below_next_pct is not None and len(considered_prices) >= 2:
        nxt = considered_prices[1]
        lift(math.ceil(nxt * (1 - cfg.floor_below_next_pct / 100.0)), "GUARD_FLOOR_NEXT")

    if cfg.max_daily_drop_pct is not None and peak_24h:
        # Предел падения только останавливает снижение: если мы уже ниже
        # (защиту включили после обвала) — стоим на месте, а не прыгаем вверх.
        drop_floor = math.ceil(peak_24h * (1 - cfg.max_daily_drop_pct / 100.0))
        if current and current > 0:
            drop_floor = min(drop_floor, current)
        lift(drop_floor, ACTION_DAILY_DROP)

    if floor is None or target >= floor:
        return target, None
    new_target = floor
    if max_price > 0:
        new_target = min(new_target, max_price)
    if min_price > 0:
        new_target = max(new_target, min_price)
    if new_target <= target:
        return target, None
    return float(new_target), reason


# --------------------------------------------------------------------------
# Проверка того, что прислал кабинет
# --------------------------------------------------------------------------

_LIMITS = {
    "min_reviews": (0, 100000),
    "min_rating": (0.0, 5.0),
    "min_merchant_age_days": (0, 3650),
    "max_extra_delivery_days": (0, 60),
    "floor_below_next_pct": (0.0, 90.0),
    "max_daily_drop_pct": (0.0, 90.0),
    "ladder_min_moves": (2, 100),
    "ladder_window_hours": (1, 168),
    "ladder_cooldown_hours": (0, 168),
    "drop_pause_hours": (1, 24),
}


class GuardValidationError(ValueError):
    pass


def clean_guard_settings(data: Optional[dict], partial: bool = False) -> dict:
    """Проверяет поля GuardConfig и возвращает чистый dict. partial=True —
    только присланные поля (для переопределения CUSTOM, где остальное
    наследуется), иначе полный набор с умолчаниями."""
    data = dict(data or {})
    known = {f.name for f in fields(GuardConfig)}
    out = {}
    for k, v in data.items():
        if k not in known:
            continue
        if k in _LIMITS and v is not None and v != "":
            lo, hi = _LIMITS[k]
            num = _to_float(v)
            if num is None or not (lo <= num <= hi):
                raise GuardValidationError(f"{k}: значение должно быть от {lo} до {hi}")
            v = int(num) if isinstance(lo, int) else num
        elif v == "":
            v = None
        out[k] = v
    if "ladder_response" in out and out["ladder_response"] not in LADDER_RESPONSES:
        raise GuardValidationError("ladder_response: " + ", ".join(LADDER_RESPONSES))
    if "no_rating_action" in out and out["no_rating_action"] not in NO_RATING_ACTIONS:
        raise GuardValidationError("no_rating_action: " + ", ".join(NO_RATING_ACTIONS))
    if "all_ignored_action" in out and out["all_ignored_action"] not in (ALL_IGNORED_HOLD, ALL_IGNORED_MAX):
        raise GuardValidationError("all_ignored_action: HOLD или MAX")
    for k in ("blacklist", "whitelist"):
        if k in out:
            out[k] = [str(x).strip() for x in (out[k] or []) if str(x).strip()][:500]
    if partial:
        return out
    return GuardConfig.from_dict(out).to_dict()


def clean_guard_override(data: Optional[dict]) -> Optional[dict]:
    """Переопределение на товаре/группе. None или INHERIT — «наследовать»
    (хранится как None)."""
    if not data:
        return None
    mode = str(data.get("mode") or OVERRIDE_INHERIT).upper()
    if mode not in (OVERRIDE_INHERIT, OVERRIDE_CUSTOM, OVERRIDE_OFF):
        raise GuardValidationError("mode: INHERIT, CUSTOM или OFF")
    if mode == OVERRIDE_INHERIT:
        return None
    if mode == OVERRIDE_OFF:
        return {"mode": OVERRIDE_OFF}
    body = clean_guard_settings({k: v for k, v in data.items() if k != "mode"}, partial=True)
    return {"mode": OVERRIDE_CUSTOM, **body}
