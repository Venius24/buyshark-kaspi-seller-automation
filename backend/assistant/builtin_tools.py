"""Инструменты помощника.

Реализованы три читающих инструмента — обзор, заказы, товары. Остальные пока
объявлены заглушками: модель их видит, зовёт и получает честное
`status=not_implemented`, а не выдуманные числа.

Правила при реализации:

* **store_id и token — ТОЛЬКО из ctx**, никогда из arguments. Иначе модель
  сможет попросить чужой магазин.
* **Инструмент наследует права раздела** (`section=`), см. tools.py.
* **Ответ инструмента уезжает в контекст модели**, поэтому payload режем:
  отдаём проекцию из нескольких полей и жёсткий лимит строк, а не сырой ответ
  Kaspi. Один разворот `GET /orders` — это сотни килобайт JSON.
* **Всё, что ходит в Kaspi/БД, синхронное** (KaspiAPI и state_helpers написаны
  на requests и sync-сессии), поэтому вызывается через `asyncio.to_thread` —
  тот же приём, что в services/stock_sync_worker.py.
"""

import asyncio
import logging
from typing import Optional

from backend.assistant.tools import ToolContext, tool

logger = logging.getLogger(__name__)

_TODO = "Инструмент объявлен в каркасе, реализация ещё не подключена."

# Потолки на размер ответа. Модель всё равно не пересказывает 500 заказов, а
# в контекст они влезают только за счёт вытеснения истории диалога.
_MAX_ORDERS = 30
_MAX_PRODUCTS = 25


def _stub(where: str, **echo) -> dict:
    return {"status": "not_implemented", "message": _TODO, "wire_to": where, **echo}


def _no_token() -> dict:
    return {
        "status": "unavailable",
        "message": "У магазина нет рабочего токена Kaspi API — данные заказов недоступны.",
    }


# ─────────────────────────── Обзор магазина ───────────────────────────


@tool(
    name="get_store_overview",
    description=(
        "Сводка по магазину прямо сейчас: сколько заказов на сборке, сколько ждёт курьера, "
        "сколько уже передано, размер каталога и состояние демпинга. "
        "Зови это первым, когда вопрос общий («как дела», «что по магазину», «что сейчас в работе»). "
        "Самовывозы сюда НЕ входят — для них зови get_orders со state=PICKUP."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    section="overview",
)
async def get_store_overview(args: dict, ctx: ToolContext) -> dict:
    from backend import feature_flags
    from backend.kaspi_wrapper import KaspiAPI
    from backend.state_helpers import _load_dumping_configs, _load_products

    result: dict = {"store_id": ctx.store_id}

    # Заказы. Кэш на 120с общий с вкладкой «Упаковка» (kaspi_wrapper.py) —
    # помощник специально ходит через него, а не запускает свой обход: полный
    # разворот 13 дней занимает на проде до 185с.
    if ctx.token:
        try:
            tabs = await asyncio.to_thread(KaspiAPI.get_all_packaging_tabs_cached, ctx.token)
            counts = (tabs or {}).get("counts") or {}
            result["orders"] = {
                "на_сборке": counts.get("cargo_assembly", 0),
                "ожидают_курьера": counts.get("wait_for_courier", 0),
                "переданы_на_доставку": counts.get("transmitted", 0),
                "окно": "последние 13 дней",
            }
        except Exception as e:
            logger.warning(f"Ассистент: обзор заказов не получен для {ctx.store_id}: {e}")
            result["orders"] = {"error": f"Kaspi не ответил: {e}"}
    else:
        result["orders"] = _no_token()

    # Каталог и демпинг — из нашей БД, дёшево.
    products = await asyncio.to_thread(_load_products, ctx.store_id)
    active = sum(1 for p in products if p.get("status") == "Активен")
    result["catalog"] = {
        "всего_товаров": len(products),
        "активных": active,
        "в_архиве": len(products) - active,
        "синхронизирован": bool(products),
    }
    if not products:
        result["catalog"]["hint"] = "Каталог ещё ни разу не синхронизирован — открой вкладку «Товары»."

    configs = await asyncio.to_thread(_load_dumping_configs, ctx.store_id)
    flags = await asyncio.to_thread(feature_flags.get_flags, ctx.store_id)
    result["dumping"] = {
        "включён_по_магазину": bool(flags.get("dumping_enabled", True)),
        "товаров_с_настройкой": len(configs),
        "из_них_включено": sum(1 for c in configs.values() if c.get("enabled")),
    }

    return result


# ─────────────────────────────── Заказы ───────────────────────────────

# Человеческое состояние -> (state, status, assembled) для Kaspi Order API.
# Разложено здесь, а не отдано модели: комбинации неочевидны (например
# «ожидает курьера» — это НЕ отдельный state, а assembled=True при
# ещё пустом courierTransmissionDate), и угадывать их модель не должна.
_ORDER_STATES: dict[str, tuple[str, str, Optional[bool]]] = {
    "NEW": ("NEW", "APPROVED_BY_BANK", None),
    "ASSEMBLY": ("KASPI_DELIVERY", "ACCEPTED_BY_MERCHANT", False),
    "WAIT_COURIER": ("KASPI_DELIVERY", "ACCEPTED_BY_MERCHANT", True),
    "PICKUP": ("PICKUP", "APPROVED_BY_BANK", None),
}

# Для этих состояний точный счёт уже посчитан кэшем вкладки «Упаковка» —
# берём его оттуда, а не из длины собственной выборки.
_TAB_FOR_STATE = {"ASSEMBLY": "cargo_assembly", "WAIT_COURIER": "wait_for_courier"}

# Потолок собственного обхода для состояний без кэша (NEW/PICKUP). Не «сколько
# показать», а «сколько вытащить, чтобы посчитать»: get_orders_with_products
# обрывает пагинацию по limit, поэтому len(результата) на потолке — это НЕ
# общее число заказов, и выдавать его за «всего» нельзя.
_COUNT_FETCH_CAP = 300


def _order_projection(order: dict) -> dict:
    """Заказ Kaspi -> компактная запись для модели.

    Персональные данные покупателя (имя, телефон, адрес) сознательно НЕ
    попадают: они уезжают во внешний LLM-API, а для ответов на вопросы
    продавца про сборку и сроки не нужны."""
    return {
        "код": order.get("id"),
        "создан": order.get("createdAt"),
        "сумма": order.get("price"),
        "товар": order.get("productName"),
        "количество": order.get("quantity"),
        "доставка": order.get("deliveryType"),
        "собран": order.get("assembled"),
        "накладная": order.get("waybillNumber"),
    }


@tool(
    name="get_orders",
    description=(
        "Заказы магазина в конкретном состоянии. Состояния: "
        "NEW — новые, ещё не приняты; "
        "ASSEMBLY — на сборке (приняты, но не упакованы); "
        "WAIT_COURIER — упакованы и ждут курьера; "
        "PICKUP — самовывоз клиентом из точки продавца. "
        "Возвращает количество и список заказов. Поле «всего» — это всё количество "
        "заказов в этом состоянии, а не длина показанного списка; если «счёт_точный» "
        "равно false, называй число как нижнюю границу («не менее N»). "
        "Данные покупателей (имя, телефон, адрес) инструмент не отдаёт."
    ),
    parameters={
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "Состояние заказов",
                "enum": ["NEW", "ASSEMBLY", "WAIT_COURIER", "PICKUP"],
            },
            "limit": {
                "type": "integer",
                "description": f"Сколько заказов вернуть списком, максимум {_MAX_ORDERS}",
                "default": 10,
            },
        },
        "required": ["state"],
        "additionalProperties": False,
    },
    section="packaging",
)
async def get_orders(args: dict, ctx: ToolContext) -> dict:
    from backend.kaspi_wrapper import KaspiAPI

    if not ctx.token:
        return _no_token()

    state_key = str(args.get("state") or "").upper()
    mapping = _ORDER_STATES.get(state_key)
    if not mapping:
        return {
            "status": "bad_argument",
            "message": f"Неизвестное состояние '{state_key}'. Допустимые: {', '.join(_ORDER_STATES)}.",
        }

    kaspi_state, kaspi_status, assembled = mapping
    limit = max(1, min(int(args.get("limit") or 10), _MAX_ORDERS))

    # Точный счёт. Для «на сборке»/«ждут курьера» он уже есть в кэше вкладки
    # «Упаковка» (TTL 120с) — берём оттуда: это и бесплатно, и гарантирует,
    # что помощник и кабинет называют пользователю ОДНО И ТО ЖЕ число.
    exact_total: Optional[int] = None
    tab = _TAB_FOR_STATE.get(state_key)
    if tab:
        try:
            tabs = await asyncio.to_thread(KaspiAPI.get_all_packaging_tabs_cached, ctx.token)
            exact_total = ((tabs or {}).get("counts") or {}).get(tab)
        except Exception as e:
            logger.warning(f"Ассистент: счётчик вкладок недоступен для {ctx.store_id}: {e}")

    # Сколько тянуть из Kaspi. Если точный счёт уже есть — ровно столько,
    # сколько показываем (одна страница вместо обхода на сотни заказов).
    fetch_limit = limit if exact_total is not None else _COUNT_FETCH_CAP

    try:
        orders = await asyncio.to_thread(
            KaspiAPI.get_orders_with_products,
            ctx.token,
            state=kaspi_state,
            status=kaspi_status,
            limit=fetch_limit,
            assembled=assembled,
        )
    except Exception as e:
        logger.warning(f"Ассистент: заказы {state_key} не получены для {ctx.store_id}: {e}")
        return {"status": "error", "message": f"Kaspi не ответил: {e}"}

    orders = orders or []

    capped = exact_total is None and len(orders) >= _COUNT_FETCH_CAP
    total = exact_total if exact_total is not None else len(orders)

    result = {
        "state": state_key,
        "окно": "последние 13 дней",
        "всего": total,
        # Честность про счёт важнее красоты ответа: модель обязана знать,
        # что число — нижняя граница, иначе она уверенно назовёт потолок
        # обхода реальным количеством заказов.
        "счёт_точный": not capped,
        "показано": min(len(orders), limit),
        "заказы": [_order_projection(o) for o in orders[:limit]],
    }
    if capped:
        result["примечание"] = (
            f"Заказов больше {_COUNT_FETCH_CAP} — точное количество не считали. "
            f"Говори «не менее {_COUNT_FETCH_CAP}», а не «ровно»."
        )
    return result


# ─────────────────────────────── Товары ───────────────────────────────


def _product_projection(product: dict, config: Optional[dict]) -> dict:
    return {
        "название": product.get("name"),
        "артикул": product.get("merchantSku"),
        "kaspi_sku": product.get("kaspiSku"),
        "цена": product.get("price_num"),
        "остаток": product.get("stock"),
        "статус": product.get("status"),
        "бренд": product.get("brand"),
        "демпинг": (
            {
                "включён": bool(config.get("enabled")),
                "мин_цена": config.get("min_price"),
                "макс_цена": config.get("max_price"),
                "режим": config.get("mode"),
            }
            if config
            else "не настроен"
        ),
    }


@tool(
    name="search_products",
    description=(
        "Поиск товаров магазина по названию, артикулу продавца, Kaspi SKU или бренду. "
        "Возвращает цену, остаток, статус (Активен/Архив) и настройки демпинга. "
        "Пустой query вернёт первые товары каталога."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Название, артикул, SKU или бренд"},
            "limit": {
                "type": "integer",
                "description": f"Сколько вернуть, максимум {_MAX_PRODUCTS}",
                "default": 10,
            },
            "only_active": {
                "type": "boolean",
                "description": "Только товары в продаже (по умолчанию да)",
                "default": True,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    section="products",
)
async def search_products(args: dict, ctx: ToolContext) -> dict:
    from backend.state_helpers import (
        _apply_overrides,
        _load_dumping_configs,
        _load_products,
        dumping_config_for_product,
    )

    query = str(args.get("query") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 10), _MAX_PRODUCTS))
    only_active = args.get("only_active", True) is not False

    products = await asyncio.to_thread(_load_products, ctx.store_id)
    if not products:
        # Сознательно НЕ дёргаем sync_merchant_assortment: полная
        # синхронизация каталога — минуты и запись в БД, это не то, что
        # должен молча запускать вопрос в чате.
        return {
            "status": "unavailable",
            "message": "Каталог этого магазина ещё не синхронизирован. Открой вкладку «Товары» — она подтянет его из Kaspi.",
        }

    products = await asyncio.to_thread(_apply_overrides, products, ctx.store_id)

    if only_active:
        products = [p for p in products if p.get("status") == "Активен"]

    if query:
        fields = ("name", "sku", "merchantSku", "kaspiSku", "brand")
        products = [
            p
            for p in products
            if any(query in str(p.get(f)).lower() for f in fields if p.get(f) is not None)
        ]

    configs = await asyncio.to_thread(_load_dumping_configs, ctx.store_id)

    return {
        "запрос": args.get("query") or "",
        "найдено": len(products),
        "показано": min(len(products), limit),
        "товары": [
            # Матч конфига через dumping_config_for_product, а не по одному
            # ключу: конфиги пишутся по merchantSku, а часть кода знает товар
            # по Kaspi sku. Наивный матч по sku уже стоил проду 374 из 377
            # «невидимых» конфигов (см. routers/products.py).
            _product_projection(p, dumping_config_for_product(p, configs))
            for p in products[:limit]
        ],
    }


# ──────────────────────── Ещё не реализованы ─────────────────────────


@tool(
    name="get_sales_stats",
    description="Продажи и выручка за период: сумма, количество заказов, топ товаров.",
    parameters={
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "description": "YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": ["date_from", "date_to"],
        "additionalProperties": False,
    },
    section="overview",
)
def get_sales_stats(args: dict, ctx: ToolContext) -> dict:
    # Подключать к backend/routers/analytics.py + SalesOrderDB
    return _stub("backend/routers/analytics.py", store_id=ctx.store_id, period=[args.get("date_from"), args.get("date_to")])


@tool(
    name="get_dumping_status",
    description="Подробности демпинга: кто из конкурентов перебивает цену и по каким товарам.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    section="dumping",
)
def get_dumping_status(args: dict, ctx: ToolContext) -> dict:
    # Подключать к backend/routers/dumping.py + _load_price_war_snapshot
    return _stub("backend/routers/dumping.py", store_id=ctx.store_id)


@tool(
    name="set_dumping_enabled",
    description=(
        "ПРИМЕР ИЗМЕНЯЮЩЕГО ИНСТРУМЕНТА. Включить или выключить демпинг магазина. "
        "Требует подтверждения пользователя и роли не ниже MANAGER."
    ),
    parameters={
        "type": "object",
        "properties": {"enabled": {"type": "boolean"}},
        "required": ["enabled"],
        "additionalProperties": False,
    },
    read_only=False,
    min_role="MANAGER",
    section="dumping",
)
def set_dumping_enabled(args: dict, ctx: ToolContext) -> dict:
    # Подключать к backend/feature_flags.py::set_flags. Пока не реализовано
    # намеренно: сначала нужен UI-флоу подтверждения (см. tools.py, п.2).
    return _stub("backend/feature_flags.py::set_flags", store_id=ctx.store_id, enabled=args.get("enabled"))
