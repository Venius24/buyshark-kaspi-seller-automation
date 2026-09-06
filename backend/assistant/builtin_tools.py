"""Инструменты помощника.

Реализованы четыре читающих инструмента — обзор, заказы, товары, демпинг. Остальные пока
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


# Состояния демпинга человеческим языком — модель не должна догадываться,
# что значит NO_CONFIG против OFF (первое «никогда не настраивали», второе
# «настроили и выключили», и продавцу это разные разговоры).
_DUMPING_STATE_RU = {
    "ON": "включён",
    "OFF": "настроен, но выключен",
    "NO_CONFIG": "не настроен",
}

_CONFIG_SOURCE_RU = {
    "group": "из Мастер-Группы",
    "master_group": "из Мастер-Группы",
    "own": "свой конфиг",
    "individual": "свой конфиг",
}


def _product_projection(product: dict) -> dict:
    """Товар -> компактная запись. Состояние демпинга берём из аннотации
    dumping_filters (эффективный конфиг: свой + развёрнутая Мастер-Группа) —
    тем же кодом, что и вкладка «Товары», и что сам движок демпинга."""
    cfg = product.get("dumping_config")
    source = product.get("dumping_config_source")
    war = product.get("price_war")

    dumping: dict = {
        "состояние": _DUMPING_STATE_RU.get(product.get("dumping_state"), product.get("dumping_state")),
    }
    if cfg:
        dumping["источник_настройки"] = _CONFIG_SOURCE_RU.get(str(source), source or "свой конфиг")
        dumping["мин_цена"] = cfg.get("min_price")
        dumping["макс_цена"] = cfg.get("max_price")
        dumping["режим"] = cfg.get("mode")
    if war:
        dumping["конкурентов"] = war.get("competitors_count")
        dumping["наша_позиция"] = war.get("our_rank")
        dumping["лучшая_цена_конкурента"] = war.get("best_competitor_price")
        dumping["данные_на"] = war.get("checked_at")
    else:
        # None и 0 — разные вещи: радар этот товар никогда не сканировал.
        dumping["конкуренты"] = "нет данных — товар не попадал в скан радара"

    return {
        "название": product.get("name"),
        "артикул": product.get("merchantSku"),
        "kaspi_sku": product.get("kaspiSku"),
        "цена": product.get("price_num"),
        "остаток": product.get("stock"),
        "статус": product.get("status"),
        "бренд": product.get("brand"),
        "демпинг": dumping,
    }


async def _annotated_catalog(store_id: str, only_active: bool) -> Optional[list]:
    """Каталог магазина с проставленным состоянием демпинга.

    Один путь для search_products и get_dumping_status: оба обязаны считать
    «включён ли демпинг» ЭФФЕКТИВНЫМ конфигом. Наивный матч по своему конфигу
    показывал товары из Мастер-Группы как «демпинг не настроен», хотя движок
    их в это время исправно демпингует — то же враньё, от которого в
    routers/products.py стоит отдельный комментарий.

    None — каталог ещё не синхронизирован.
    """
    from backend.services import dumping_filters
    from backend.state_helpers import (
        _apply_overrides,
        _load_dumping_configs,
        _load_master_stock_groups,
        _load_price_war_snapshot,
        _load_products,
    )

    products = await asyncio.to_thread(_load_products, store_id)
    if not products:
        return None

    products = await asyncio.to_thread(_apply_overrides, products, store_id)

    configs = await asyncio.to_thread(_load_dumping_configs, store_id)
    groups = await asyncio.to_thread(_load_master_stock_groups, store_id)
    war = await asyncio.to_thread(_load_price_war_snapshot, store_id)
    await asyncio.to_thread(dumping_filters.annotate_products, products, configs, groups, war)

    if only_active:
        products = [p for p in products if p.get("status") == "Активен"]
    return products


def _maybe_int(raw) -> Optional[int]:
    """Модель иногда шлёт число строкой — это не повод падать."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _stock_of(product: dict) -> Optional[int]:
    raw = product.get("stock")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _stock_filter_label(min_stock: Optional[int], max_stock: Optional[int]) -> str:
    if min_stock is None and max_stock is None:
        return "нет"
    if max_stock is None:
        return f"от {min_stock}"
    if min_stock is None:
        return f"до {max_stock}"
    return f"от {min_stock} до {max_stock}"


def _sorted_products(products: list, sort_key: str) -> list:
    """Товары без известного остатка/цены уезжают в конец при любой
    сортировке: «неизвестно» — это не «ноль», и показывать их первыми в
    ответе на «что скоро закончится» было бы враньём."""
    if sort_key == "stock_asc":
        return sorted(products, key=lambda p: (_stock_of(p) is None, _stock_of(p) or 0))
    if sort_key == "stock_desc":
        return sorted(products, key=lambda p: (_stock_of(p) is None, -(_stock_of(p) or 0)))
    if sort_key == "price_asc":
        return sorted(products, key=lambda p: (p.get("price_num") is None, p.get("price_num") or 0))
    if sort_key == "price_desc":
        return sorted(products, key=lambda p: (p.get("price_num") is None, -(p.get("price_num") or 0)))
    return products


_CATALOG_NOT_SYNCED = {
    "status": "unavailable",
    "message": "Каталог этого магазина ещё не синхронизирован. Открой вкладку «Товары» — она подтянет его из Kaspi.",
}


@tool(
    name="search_products",
    description=(
        "Поиск и подборки по каталогу: цена, остаток, статус, состояние демпинга "
        "(учитывая Мастер-Группы). Пустой query = весь каталог. "
        "НЕ перебирай этот инструмент много раз с разными угаданными словами — "
        "вместо этого оставь query пустым и возьми нужный срез фильтрами. "
        "«Что скоро закончится» = query пустой, max_stock 5, sort stock_asc. "
        "«Самые дорогие» = query пустой, sort price_desc. "
        "Фильтр dumping_state: ON — демпинг работает; OFF — настроен, но выключен; "
        "NO_CONFIG — не настраивали; OFF_WITH_RIVALS — не работает, хотя конкуренты есть. "
        "Поле «сводка_по_демпингу» считается по ВСЕМУ найденному, а не по показанному срезу: "
        "на вопрос «сколько товаров без демпинга» отвечай числом оттуда, а не пересчётом списка."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Название, артикул, SKU или бренд. Пусто = весь каталог"},
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
            "dumping_state": {
                "type": "string",
                "description": "Фильтр по состоянию демпинга",
                "enum": ["ON", "OFF", "NO_CONFIG", "OFF_WITH_RIVALS"],
            },
            "max_stock": {
                "type": "integer",
                "description": "Только товары с остатком не больше этого числа (для «скоро закончится»)",
            },
            "min_stock": {
                "type": "integer",
                "description": "Только товары с остатком не меньше этого числа",
            },
            "sort": {
                "type": "string",
                "description": "Порядок выдачи. По умолчанию — как в каталоге",
                "enum": ["stock_asc", "stock_desc", "price_asc", "price_desc"],
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    section="products",
)
async def search_products(args: dict, ctx: ToolContext) -> dict:
    from backend.services import dumping_filters

    query = str(args.get("query") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 10), _MAX_PRODUCTS))
    only_active = args.get("only_active", True) is not False
    wanted_state = str(args.get("dumping_state") or "").strip().upper()
    max_stock = _maybe_int(args.get("max_stock"))
    min_stock = _maybe_int(args.get("min_stock"))
    sort_key = str(args.get("sort") or "").strip().lower()

    # Сознательно НЕ дёргаем sync_merchant_assortment: полная синхронизация
    # каталога — минуты и запись в БД, это не то, что должен молча запускать
    # вопрос в чате.
    products = await _annotated_catalog(ctx.store_id, only_active)
    if products is None:
        return _CATALOG_NOT_SYNCED

    if query:
        fields = ("name", "sku", "merchantSku", "kaspiSku", "brand")
        products = [
            p
            for p in products
            if any(query in str(p.get(f)).lower() for f in fields if p.get(f) is not None)
        ]

    # Счётчики срезов — по всей найденной выборке, ДО фильтра по демпингу.
    # Именно это делает ответ на «сколько у меня товаров без демпинга»
    # точным: раньше модель видела только показанные 25 из 1022 и честно
    # оговаривалась, что полной картины у неё нет.
    facets = dumping_filters.facet_counts(products)

    if wanted_state:
        products = dumping_filters.apply_dumping_filters(products, dumping_state=wanted_state)

    if max_stock is not None:
        products = [p for p in products if _stock_of(p) is not None and _stock_of(p) <= max_stock]
    if min_stock is not None:
        products = [p for p in products if _stock_of(p) is not None and _stock_of(p) >= min_stock]

    products = _sorted_products(products, sort_key)

    return {
        "запрос": args.get("query") or "",
        "фильтр_демпинга": wanted_state or "нет",
        "фильтр_остатка": _stock_filter_label(min_stock, max_stock),
        "сортировка": sort_key or "как в каталоге",
        "найдено": len(products),
        "показано": min(len(products), limit),
        "сводка_по_демпингу": {
            "всего_товаров": facets.get("TOTAL"),
            "демпинг_включён": facets.get("ON"),
            "настроен_но_выключен": facets.get("OFF"),
            "не_настроен": facets.get("NO_CONFIG"),
            "не_работает_хотя_конкуренты_есть": facets.get("OFF_WITH_RIVALS"),
        },
        "товары": [_product_projection(p) for p in products[:limit]],
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
    description=(
        "Состояние демпинга по всему каталогу: сколько товаров демпингуется, где мы "
        "не лидируем и кто перебивает цену. Учитывает Мастер-Группы. "
        "Расклад конкурентов известен ТОЛЬКО по товарам, попадавшим в скан радара — "
        "для остальных это «нет данных», а не «конкурентов нет»."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": f"Сколько проблемных товаров перечислить, максимум {_MAX_PRODUCTS}",
                "default": 10,
            }
        },
        "additionalProperties": False,
    },
    section="dumping",
)
async def get_dumping_status(args: dict, ctx: ToolContext) -> dict:
    from backend import feature_flags
    from backend.services import dumping_filters

    limit = max(1, min(int(args.get("limit") or 10), _MAX_PRODUCTS))

    products = await _annotated_catalog(ctx.store_id, only_active=True)
    if products is None:
        return _CATALOG_NOT_SYNCED

    facets = dumping_filters.facet_counts(products)
    flags = await asyncio.to_thread(feature_flags.get_flags, ctx.store_id)

    # Где нас перебивают: демпинг работает, радар товар видел, а первое место
    # не наше. Именно это продавец имеет в виду под «кто перебивает цену».
    losing = [
        p
        for p in products
        if p.get("dumping_state") == dumping_filters.DUMPING_STATE_ON
        and p.get("rank_state") == dumping_filters.RANK_STATE_NOT_LEADER
    ]
    losing.sort(key=lambda p: (p.get("price_war") or {}).get("our_rank") or 0, reverse=True)

    return {
        "включён_по_магазину": bool(flags.get("dumping_enabled", True)),
        "сводка": {
            "всего_активных_товаров": facets.get("TOTAL"),
            "демпинг_включён": facets.get("ON"),
            "настроен_но_выключен": facets.get("OFF"),
            "не_настроен": facets.get("NO_CONFIG"),
            "не_работает_хотя_конкуренты_есть": facets.get("OFF_WITH_RIVALS"),
            "мы_первые": facets.get("LEADER"),
            "мы_не_первые": facets.get("NOT_LEADER"),
            "радар_не_сканировал": facets.get("NO_DATA"),
        },
        "нас_перебивают_всего": len(losing),
        "нас_перебивают": [
            {
                "название": p.get("name"),
                "артикул": p.get("merchantSku"),
                "наша_цена": p.get("price_num"),
                "наша_позиция": (p.get("price_war") or {}).get("our_rank"),
                "конкурентов": (p.get("price_war") or {}).get("competitors_count"),
                "лучшая_цена_конкурента": (p.get("price_war") or {}).get("best_competitor_price"),
                "конкурент": (p.get("price_war") or {}).get("best_competitor_name"),
                "данные_на": (p.get("price_war") or {}).get("checked_at"),
            }
            for p in losing[:limit]
        ],
        "оговорка": (
            "Позиции и конкуренты — снимок последнего скана радара, а не онлайн-истина. "
            "Товары, у которых демпинг никогда не включали, радар не сканировал вообще."
        ),
    }


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
