"""Каталог тарифов — намеренно Python-константа, а не таблица в БД.

Каталог структурен: движок ветвится на is_free (DEV — никогда не списывается,
не блокируется, не лимитируется) и max_products is None (безлимит). Строка в
БД, разошедшаяся с этой логикой, была бы молчаливым багом в биллинге, который
не поймает ни один тип-чекер. Единственное, что реально должно быть
изменяемым в рантайме — договорная цена ГигаПредприятие, и это
store_tariffs.custom_monthly_price_minor (колонка нужна в любом случае).
Каждое списание снапшотит фактическую сумму в billing_transactions, поэтому
история не зависит от неизменности каталога — цены можно менять деплоем кода
без порчи прошлых начислений.

Деньги — целые числа в тиын (1/100 ₸), никакого float. Кредиты — целые числа
в милли-кредитах (×1000): 50/день = 50_000, сообщение при множителе 1.2 =
1200. Дробных величин во float нигде нет.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    max_products: Optional[int]  # None = безлимит
    monthly_price_minor: Optional[int]  # None = договорная (ГигаПредприятие)
    badge: Optional[str] = None
    is_free: bool = False
    is_public: bool = True  # показывать ли в публичном прайс-листе кабинета


PLANS: dict[str, Plan] = {
    "START": Plan(
        code="START", name="Бодрый старт",
        max_products=50, monthly_price_minor=640_000,
    ),
    "HUNTER": Plan(
        code="HUNTER", name="Охотник",
        max_products=200, monthly_price_minor=1_130_000,
    ),
    "PREDATOR": Plan(
        code="PREDATOR", name="Хищник",
        max_products=1000, monthly_price_minor=1_790_000,
        badge="ХИТ ПРОДАЖ",
    ),
    "PREDATOR_PLUS": Plan(
        code="PREDATOR_PLUS", name="Хищник+",
        max_products=2000, monthly_price_minor=2_980_000,
    ),
    "GIGA": Plan(
        code="GIGA", name="ГигаПредприятие",
        max_products=None, monthly_price_minor=None,
    ),
    "DEV": Plan(
        code="DEV", name="DEV",
        max_products=None, monthly_price_minor=0,
        is_free=True, is_public=False,
    ),
}

DEFAULT_PLAN_CODE = "DEV"

# (daily_credit_limit_milli, monthly_price_minor). Ступени ЗАМЕНЯЮТ друг
# друга — одна колонка store_tariffs.credit_addon_code структурно не даёт
# им суммироваться (владелец: "это замена, у магазина должны быть ежедневно
# ответы как кредиты").
CREDIT_TIERS: dict[str, tuple[int, int]] = {
    "NONE": (50_000, 0),
    "C70": (70_000, 200_000),
    "C100": (100_000, 400_000),
}
DEFAULT_CREDIT_ADDON = "NONE"


@dataclass(frozen=True)
class PromptBand:
    code: str
    max_chars: float  # верхняя граница включительно; последняя — inf
    monthly_price_minor: int
    multiplier_milli: int  # ×1000: 1.2 = 1200


# Границы: [0,500] -> band0, (500,2000] -> band1, (2000,5000] -> band2, >5000 -> band3.
# Каждая ступень даёт И месячную доплату, И множитель кредита за сообщение.
PROMPT_BANDS: list[PromptBand] = [
    PromptBand(code="band0", max_chars=500, monthly_price_minor=0, multiplier_milli=1000),
    PromptBand(code="band1", max_chars=2000, monthly_price_minor=100_000, multiplier_milli=1200),
    PromptBand(code="band2", max_chars=5000, monthly_price_minor=250_000, multiplier_milli=1500),
    PromptBand(code="band3", max_chars=float("inf"), monthly_price_minor=500_000, multiplier_milli=2000),
]
PROMPT_BANDS_BY_CODE: dict[str, PromptBand] = {b.code: b for b in PROMPT_BANDS}
DEFAULT_PROMPT_BAND = "band0"

# Блокировка баланса — при достижении -3000 ₸ включительно.
BALANCE_BLOCK_THRESHOLD_MINOR = -300_000

# Часовой пояс, по которому считаются "дни" для дневных кредитов ответов
# (Алматы, UTC+5 — иначе кредиты обнулялись бы в 05:00 по местному времени,
# что клиенты бы заметили и посчитали багом).
BILLING_TZ_OFFSET_HOURS = 5


def get_plan(plan_code: str) -> Plan:
    return PLANS.get(plan_code, PLANS[DEFAULT_PLAN_CODE])


def is_free_plan(plan_code: str) -> bool:
    return get_plan(plan_code).is_free


def resolve_prompt_band(chars: int) -> PromptBand:
    for band in PROMPT_BANDS:
        if chars <= band.max_chars:
            return band
    return PROMPT_BANDS[-1]


def multiplier_milli(band_code: str) -> int:
    band = PROMPT_BANDS_BY_CODE.get(band_code)
    return band.multiplier_milli if band else 1000


def effective_daily_credits(credit_addon_code: str) -> int:
    tier = CREDIT_TIERS.get(credit_addon_code, CREDIT_TIERS[DEFAULT_CREDIT_ADDON])
    return tier[0]


def credit_addon_price_minor(credit_addon_code: str) -> int:
    tier = CREDIT_TIERS.get(credit_addon_code, CREDIT_TIERS[DEFAULT_CREDIT_ADDON])
    return tier[1]


def plan_price_minor(plan_code: str, custom_monthly_price_minor: Optional[int]) -> int:
    """Цена базовой подписки. custom_monthly_price_minor (ГигаПредприятие —
    договорная цена) перекрывает цену из каталога, если задана."""
    if custom_monthly_price_minor is not None:
        return custom_monthly_price_minor
    plan = get_plan(plan_code)
    return plan.monthly_price_minor or 0


def public_plans() -> list[Plan]:
    return [p for p in PLANS.values() if p.is_public]
