"""Тесты каркаса ИИ-помощника. Сети и ключей не требуют: провайдер
подставной, движок о нём знает только через интерфейс LLMProvider.
"""

import asyncio
import json

import pytest

from backend.assistant import builtin_tools  # noqa: F401 — регистрирует инструменты
from backend.assistant import tools as tool_registry
from backend.assistant.config import AssistantConfig
from backend.assistant.engine import run_turn
from backend.assistant.prompt import StoreBrief, build_system_prompt
from backend.assistant.provider import (
    ROLE_TOOL,
    ROLE_USER,
    LLMProvider,
    LLMResult,
    Message,
    StubProvider,
    ToolCall,
    get_provider,
)
from backend.assistant.tools import ToolContext
from backend.kaspi_wrapper import KaspiAPI
from backend import feature_flags, state_helpers


def _cfg(**over) -> AssistantConfig:
    base = dict(
        enabled=True,
        provider="stub",
        model="test-model",
        api_key="",
        base_url="",
        max_tool_iterations=2,
        history_limit=10,
        request_timeout=5.0,
        max_output_tokens=100,
        allow_write_tools=False,
    )
    base.update(over)
    return AssistantConfig(**base)


class ScriptedProvider(LLMProvider):
    """Отдаёт заранее записанные ответы по одному на вызов."""

    name = "scripted"

    def __init__(self, script: list[LLMResult]):
        self.script = list(script)
        self.calls: list[list[Message]] = []

    async def complete(self, messages, tools=None):
        self.calls.append(list(messages))
        return self.script.pop(0) if self.script else LLMResult(text="конец")


def _ctx(role="OWNER", confirmed=False, token="") -> ToolContext:
    return ToolContext(store_id="store-1", user_id="user-1", role=role, confirmed=confirmed, token=token)


def _brief() -> StoreBrief:
    return StoreBrief(store_id="store-1", store_name="Тест", merchant_id="123", user_role="OWNER")


# --- Провайдер ---------------------------------------------------------

def test_get_provider_without_key_falls_back_to_stub():
    provider = get_provider(_cfg(provider="openai", api_key=""))
    assert isinstance(provider, StubProvider)


def test_stub_provider_answers_without_network():
    result = asyncio.run(StubProvider().complete([Message(role=ROLE_USER, content="сколько заказов?")]))
    assert "сколько заказов?" in result.text
    assert result.error is None


def test_message_to_wire_shapes():
    tool_msg = Message(role=ROLE_TOOL, content='{"ok": true}', tool_call_id="c1", name="get_store_overview").to_wire()
    assert tool_msg == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}

    assistant_msg = Message(
        role="assistant", content="", tool_calls=[ToolCall(id="c1", name="get_store_overview", arguments={"a": 1})]
    ).to_wire()
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_store_overview"
    assert json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"]) == {"a": 1}


# --- Реестр инструментов ----------------------------------------------

def test_builtin_tools_registered():
    names = {s.name for s in tool_registry.all_specs()}
    assert {"get_store_overview", "search_products", "get_orders"} <= names


def test_write_tools_hidden_when_disabled():
    visible = {s.name for s in tool_registry.available_specs(_ctx(), allow_write=False)}
    assert "set_dumping_enabled" not in visible
    visible_write = {s.name for s in tool_registry.available_specs(_ctx(), allow_write=True)}
    assert "set_dumping_enabled" in visible_write


def test_viewer_does_not_see_manager_tool():
    visible = {s.name for s in tool_registry.available_specs(_ctx(role="VIEWER"), allow_write=True)}
    assert "set_dumping_enabled" not in visible
    assert "get_store_overview" in visible


def test_write_tool_requires_confirmation():
    res = asyncio.run(tool_registry.execute("set_dumping_enabled", {"enabled": True}, _ctx(), allow_write=True))
    assert res["ok"] is False
    assert "подтверждения" in res["error"]


def test_unknown_tool_returns_error_not_raise():
    res = asyncio.run(tool_registry.execute("no_such_tool", {}, _ctx()))
    assert res["ok"] is False


def test_read_tool_returns_not_implemented_stub():
    """Ещё не реализованный инструмент честно говорит об этом, а не выдумывает."""
    res = asyncio.run(tool_registry.execute("get_sales_stats", {"date_from": "2026-09-01", "date_to": "2026-09-06"}, _ctx()))
    assert res["ok"] is True
    assert res["result"]["status"] == "not_implemented"
    # store_id всегда из контекста, не из аргументов модели
    assert res["result"]["store_id"] == "store-1"


def test_tool_ignores_store_id_from_model_arguments(monkeypatch):
    """Главная гарантия мультиарендности: какой store_id ни назови модель,
    в слой данных уходит store_id из контекста запроса."""
    seen = []

    def fake_load_products(store_id):
        seen.append(store_id)
        return []

    monkeypatch.setattr(state_helpers, "_load_products", fake_load_products)

    asyncio.run(tool_registry.execute("search_products", {"query": "чехол", "store_id": "чужой"}, _ctx()))
    assert seen == ["store-1"]


# --- Права по разделам кабинета ---------------------------------------

def test_tool_hidden_when_section_not_allowed():
    """Сборщику не выдали раздел «Товары» — помощник не должен становиться
    обходным путём к ценам."""
    packer = ToolContext(store_id="store-1", role="MANAGER", sections=frozenset({"overview", "packaging"}))
    visible = {s.name for s in tool_registry.available_specs(packer, allow_write=False)}
    assert "get_orders" in visible
    assert "search_products" not in visible
    assert "get_dumping_status" not in visible


def test_section_enforced_on_execute_not_only_on_listing(monkeypatch):
    """Список инструментов — подсказка модели, а не граница прав: модель может
    позвать имя, которого ей не показывали."""
    monkeypatch.setattr(state_helpers, "_load_products", lambda store_id: [{"name": "секрет", "status": "Активен"}])
    packer = ToolContext(store_id="store-1", role="MANAGER", sections=frozenset({"overview", "packaging"}))

    res = asyncio.run(tool_registry.execute("search_products", {"query": ""}, packer))
    assert res["ok"] is False
    assert "products" in res["error"]


def test_sections_none_means_unrestricted():
    """None — это «ограничений нет» (владелец, строка до миграции 040), а не
    «ничего нельзя». Пустое множество — наоборот."""
    unrestricted = {s.name for s in tool_registry.available_specs(_ctx(), allow_write=False)}
    assert "search_products" in unrestricted

    locked = ToolContext(store_id="store-1", role="MANAGER", sections=frozenset())
    assert "search_products" not in {s.name for s in tool_registry.available_specs(locked, allow_write=False)}


# --- Читающие инструменты ---------------------------------------------

_FAKE_ORDER = {
    "id": "T219-0005",
    "internalId": "999",
    "createdAt": "01.09.2026 10:00",
    "price": "12000 ₸",
    "productName": "Чехол",
    "quantity": 2,
    "deliveryType": "Самовывоз",
    "assembled": False,
    "waybillNumber": None,
    "customer": {"firstName": "Иван", "cellPhone": "+7700"},
    "deliveryAddress": {"formattedAddress": "ул. Абая 1"},
}


def test_get_orders_requires_known_state():
    res = asyncio.run(tool_registry.execute("get_orders", {"state": "ВЫДУМАННОЕ"}, _ctx(token="t")))
    assert res["ok"] is True
    assert res["result"]["status"] == "bad_argument"


def test_get_orders_without_token_says_so():
    res = asyncio.run(tool_registry.execute("get_orders", {"state": "NEW"}, _ctx()))
    assert res["result"]["status"] == "unavailable"


def test_get_orders_counts_from_tab_cache_not_from_slice(monkeypatch):
    """На вопрос «сколько заказов на сборке» ответ — точный счёт вкладки
    «Упаковка», а не длина укороченного списка.

    Регрессия с прода 2026-09-06: счёт брался как len(выборки), а выборка
    обрывается по limit внутри get_orders_with_products. Обзор говорил 462,
    get_orders — «всего 200» (ровно потолок), и модель уверенно назвала
    пользователю потолок обхода реальным количеством заказов."""
    monkeypatch.setattr(
        KaspiAPI,
        "get_all_packaging_tabs_cached",
        staticmethod(lambda token, **kw: {"counts": {"cargo_assembly": 462, "wait_for_courier": 1}}),
    )
    monkeypatch.setattr(
        KaspiAPI, "get_orders_with_products", staticmethod(lambda *a, **kw: [dict(_FAKE_ORDER)] * 2)
    )
    res = asyncio.run(tool_registry.execute("get_orders", {"state": "ASSEMBLY", "limit": 2}, _ctx(token="t")))
    result = res["result"]
    assert result["всего"] == 462
    assert result["счёт_точный"] is True
    assert result["показано"] == 2
    assert len(result["заказы"]) == 2


def test_get_orders_asks_kaspi_only_for_the_slice_when_count_is_cached(monkeypatch):
    """Раз точный счёт уже есть в кэше, тянуть из Kaspi сотни заказов ради
    показа десяти незачем — обход платный (до 185с на проде)."""
    seen = {}

    monkeypatch.setattr(
        KaspiAPI,
        "get_all_packaging_tabs_cached",
        staticmethod(lambda token, **kw: {"counts": {"cargo_assembly": 462}}),
    )

    def fake(token, state=None, status=None, limit=None, assembled=None):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(KaspiAPI, "get_orders_with_products", staticmethod(fake))
    asyncio.run(tool_registry.execute("get_orders", {"state": "ASSEMBLY", "limit": 3}, _ctx(token="t")))
    assert seen["limit"] == 3


def test_get_orders_marks_count_inexact_when_capped(monkeypatch):
    """У NEW/PICKUP кэша счётчиков нет: считаем сами, но упёршись в потолок —
    честно говорим, что число неточное."""
    monkeypatch.setattr(
        KaspiAPI, "get_orders_with_products", staticmethod(lambda *a, **kw: [dict(_FAKE_ORDER)] * 300)
    )
    res = asyncio.run(tool_registry.execute("get_orders", {"state": "NEW", "limit": 5}, _ctx(token="t")))
    result = res["result"]
    assert result["всего"] == 300
    assert result["счёт_точный"] is False
    assert "не менее" in result["примечание"]


def test_get_orders_count_is_exact_when_below_cap(monkeypatch):
    monkeypatch.setattr(
        KaspiAPI, "get_orders_with_products", staticmethod(lambda *a, **kw: [dict(_FAKE_ORDER)] * 7)
    )
    res = asyncio.run(tool_registry.execute("get_orders", {"state": "PICKUP", "limit": 2}, _ctx(token="t")))
    result = res["result"]
    assert result["всего"] == 7
    assert result["счёт_точный"] is True
    assert result["показано"] == 2


def test_get_orders_survives_tab_cache_outage(monkeypatch):
    """Кэш вкладок недоступен — инструмент обязан ответить хоть чем-то, а не
    упасть: считаем сами и помечаем счёт как возможно неточный."""
    def boom(token, **kw):
        raise RuntimeError("Kaspi 503")

    monkeypatch.setattr(KaspiAPI, "get_all_packaging_tabs_cached", staticmethod(boom))
    monkeypatch.setattr(
        KaspiAPI, "get_orders_with_products", staticmethod(lambda *a, **kw: [dict(_FAKE_ORDER)] * 4)
    )
    res = asyncio.run(tool_registry.execute("get_orders", {"state": "ASSEMBLY", "limit": 10}, _ctx(token="t")))
    assert res["ok"] is True
    assert res["result"]["всего"] == 4


def test_get_orders_does_not_leak_customer_data(monkeypatch):
    """Ответ инструмента уезжает во внешний LLM-API — персональных данных
    покупателя там быть не должно."""
    monkeypatch.setattr(KaspiAPI, "get_orders_with_products", staticmethod(lambda *a, **kw: [dict(_FAKE_ORDER)]))
    monkeypatch.setattr(KaspiAPI, "get_all_packaging_tabs_cached", staticmethod(lambda token, **kw: {"counts": {}}))
    res = asyncio.run(tool_registry.execute("get_orders", {"state": "PICKUP"}, _ctx(token="t")))
    dumped = tool_registry.serialize_result(res)
    assert "Иван" not in dumped
    assert "+7700" not in dumped
    assert "Абая" not in dumped
    assert "Чехол" in dumped


def test_get_orders_pickup_maps_to_kaspi_state(monkeypatch):
    """Самовывоз живёт в state=PICKUP (см. CLAUDE.md): постамат и самовывоз
    неразличимы по deliveryMode, и модель не должна угадывать эту раскладку."""
    seen = {}

    def fake(token, state=None, status=None, limit=None, assembled=None):
        seen.update(state=state, status=status, assembled=assembled)
        return []

    monkeypatch.setattr(KaspiAPI, "get_orders_with_products", staticmethod(fake))
    monkeypatch.setattr(
        KaspiAPI,
        "get_all_packaging_tabs_cached",
        staticmethod(lambda token, **kw: {"counts": {"wait_for_courier": 1}}),
    )
    asyncio.run(tool_registry.execute("get_orders", {"state": "PICKUP"}, _ctx(token="t")))
    assert seen["state"] == "PICKUP"

    asyncio.run(tool_registry.execute("get_orders", {"state": "WAIT_COURIER"}, _ctx(token="t")))
    assert seen["state"] == "KASPI_DELIVERY" and seen["assembled"] is True


def test_search_products_reports_unsynced_catalog(monkeypatch):
    monkeypatch.setattr(state_helpers, "_load_products", lambda store_id: [])
    res = asyncio.run(tool_registry.execute("search_products", {"query": "чехол"}, _ctx()))
    assert res["result"]["status"] == "unavailable"


def _catalog():
    return [
        {"name": "Чехол синий", "sku": "111", "merchantSku": "ART-1", "kaspiSku": "111",
         "status": "Активен", "price_num": 5000, "stock": 3, "brand": "NoName"},
        {"name": "Чехол групповой", "sku": "222", "merchantSku": "ART-2", "kaspiSku": "222",
         "status": "Активен", "price_num": 900, "stock": 7, "brand": "NoName"},
        {"name": "Чехол ничей", "sku": "333", "merchantSku": "ART-3", "kaspiSku": "333",
         "status": "Активен", "price_num": 700, "stock": 1, "brand": "NoName"},
        {"name": "Кабель", "sku": "444", "merchantSku": "ART-4", "kaspiSku": "444",
         "status": "Активен", "price_num": 300, "stock": 0, "brand": "NoName"},
        {"name": "Чехол архивный", "sku": "555", "merchantSku": "ART-5", "kaspiSku": "555",
         "status": "Архив", "price_num": 100, "stock": 0, "brand": "NoName"},
    ]


def _wire_catalog(monkeypatch, *, configs=None, groups=None, war=None):
    """Подставляем слой данных, но dumping_filters.annotate_products оставляем
    настоящим — именно он и проверяется."""
    monkeypatch.setattr(state_helpers, "_load_products", lambda store_id: _catalog())
    monkeypatch.setattr(state_helpers, "_apply_overrides", lambda items, store_id: items)
    monkeypatch.setattr(state_helpers, "_load_dumping_configs", lambda store_id: configs or {})
    monkeypatch.setattr(state_helpers, "_load_master_stock_groups", lambda store_id: groups or {})
    monkeypatch.setattr(state_helpers, "_load_price_war_snapshot", lambda store_id: war or [])


_OWN_CONFIG = {"ART-1": {"sku": "ART-1", "enabled": True, "min_price": 4000.0,
                         "max_price": 6000.0, "mode": "TOP_1"}}

# Мастер-Группа, которая демпингует ART-2 — своего конфига у товара нет.
_GROUP = {
    "g1": {
        "is_active": True,
        "linked_skus": ["ART-2"],
        "dumping_config": {"enabled": True, "min_price": 500.0, "max_price": 1500.0, "mode": "TOP_1"},
    }
}


def test_search_products_sees_dumping_from_master_group(monkeypatch):
    """Регрессия с прода 2026-09-06: помощник считал «включён ли демпинг» по
    одному лишь индивидуальному конфигу и называл товары из Мастер-Группы
    «демпинг не настроен», хотя движок их в это время демпингует."""
    _wire_catalog(monkeypatch, configs=_OWN_CONFIG, groups=_GROUP)

    res = asyncio.run(tool_registry.execute("search_products", {"query": "чехол", "limit": 25}, _ctx()))
    by_sku = {p["артикул"]: p for p in res["result"]["товары"]}

    assert by_sku["ART-1"]["демпинг"]["состояние"] == "включён"
    assert by_sku["ART-1"]["демпинг"]["источник_настройки"] == "свой конфиг"

    assert by_sku["ART-2"]["демпинг"]["состояние"] == "включён"
    assert by_sku["ART-2"]["демпинг"]["источник_настройки"] == "из Мастер-Группы"

    assert by_sku["ART-3"]["демпинг"]["состояние"] == "не настроен"


def test_search_products_summary_counts_whole_catalog_not_the_slice(monkeypatch):
    """«Сколько у меня товаров без демпинга» должно отвечаться числом по всему
    каталогу. На проде модель видела только первые 25 из 1022 и честно
    оговаривалась, что полной картины у неё нет."""
    _wire_catalog(monkeypatch, configs=_OWN_CONFIG, groups=_GROUP)

    res = asyncio.run(tool_registry.execute("search_products", {"query": "", "limit": 1}, _ctx()))
    result = res["result"]

    assert result["показано"] == 1  # показали один
    summary = result["сводка_по_демпингу"]
    assert summary["всего_товаров"] == 4  # архивный отфильтрован
    assert summary["демпинг_включён"] == 2  # свой конфиг + Мастер-Группа
    assert summary["не_настроен"] == 2  # ART-3 и кабель


def test_search_products_can_filter_by_dumping_state(monkeypatch):
    _wire_catalog(monkeypatch, configs=_OWN_CONFIG, groups=_GROUP)

    res = asyncio.run(
        tool_registry.execute("search_products", {"query": "", "dumping_state": "NO_CONFIG", "limit": 25}, _ctx())
    )
    result = res["result"]
    assert result["найдено"] == 2
    assert {p["артикул"] for p in result["товары"]} == {"ART-3", "ART-4"}
    # Сводка считается ДО фильтра — иначе она бы схлопнулась под сам фильтр.
    assert result["сводка_по_демпингу"]["всего_товаров"] == 4


def test_search_products_archived_excluded_and_query_matches(monkeypatch):
    _wire_catalog(monkeypatch, configs=_OWN_CONFIG, groups=_GROUP)
    res = asyncio.run(tool_registry.execute("search_products", {"query": "кабель", "limit": 25}, _ctx()))
    result = res["result"]
    assert result["найдено"] == 1
    assert result["товары"][0]["артикул"] == "ART-4"


def test_search_products_distinguishes_no_radar_data_from_zero_rivals(monkeypatch):
    """None конкурентов и 0 конкурентов — разные вещи, и путать их нельзя."""
    _wire_catalog(
        monkeypatch,
        configs=_OWN_CONFIG,
        groups=_GROUP,
        war=[{"sku": "ART-1", "our_rank": 3, "competitors_count": 5,
              "best_competitor_price": 4800, "best_competitor_name": "Конкурент", "updated_at": "2026-09-06"}],
    )
    res = asyncio.run(tool_registry.execute("search_products", {"query": "чехол", "limit": 25}, _ctx()))
    by_sku = {p["артикул"]: p for p in res["result"]["товары"]}

    assert by_sku["ART-1"]["демпинг"]["конкурентов"] == 5
    assert by_sku["ART-1"]["демпинг"]["наша_позиция"] == 3
    assert "нет данных" in by_sku["ART-3"]["демпинг"]["конкуренты"]


def test_get_dumping_status_reports_who_outbids_us(monkeypatch):
    _wire_catalog(
        monkeypatch,
        configs=_OWN_CONFIG,
        groups=_GROUP,
        war=[
            {"sku": "ART-1", "our_rank": 4, "competitors_count": 6,
             "best_competitor_price": 4800, "best_competitor_name": "Магазин А", "updated_at": "2026-09-06"},
            {"sku": "ART-2", "our_rank": 1, "competitors_count": 2,
             "best_competitor_price": 950, "best_competitor_name": "Магазин Б", "updated_at": "2026-09-06"},
        ],
    )
    monkeypatch.setattr(feature_flags, "get_flags", lambda store_id: {"dumping_enabled": True})

    res = asyncio.run(tool_registry.execute("get_dumping_status", {}, _ctx()))
    result = res["result"]

    assert result["включён_по_магазину"] is True
    assert result["сводка"]["демпинг_включён"] == 2
    assert result["сводка"]["мы_первые"] == 1
    # Перебивают только там, где демпинг включён и позиция не первая.
    assert result["нас_перебивают_всего"] == 1
    loser = result["нас_перебивают"][0]
    assert loser["артикул"] == "ART-1"
    assert loser["конкурент"] == "Магазин А"
    assert loser["наша_позиция"] == 4


def test_get_dumping_status_needs_synced_catalog(monkeypatch):
    monkeypatch.setattr(state_helpers, "_load_products", lambda store_id: [])
    res = asyncio.run(tool_registry.execute("get_dumping_status", {}, _ctx()))
    assert res["result"]["status"] == "unavailable"


def test_store_overview_aggregates_orders_catalog_and_dumping(monkeypatch):
    monkeypatch.setattr(
        KaspiAPI,
        "get_all_packaging_tabs_cached",
        staticmethod(lambda token, **kw: {"counts": {"cargo_assembly": 82, "wait_for_courier": 34, "transmitted": 5}}),
    )
    monkeypatch.setattr(
        state_helpers,
        "_load_products",
        lambda store_id: [{"status": "Активен"}, {"status": "Активен"}, {"status": "Архив"}],
    )
    monkeypatch.setattr(state_helpers, "_load_dumping_configs", lambda store_id: {"a": {"enabled": True}, "b": {"enabled": False}})
    monkeypatch.setattr(feature_flags, "get_flags", lambda store_id: {"dumping_enabled": True})

    res = asyncio.run(tool_registry.execute("get_store_overview", {}, _ctx(token="t")))
    result = res["result"]
    assert result["orders"]["на_сборке"] == 82
    assert result["catalog"] == {"всего_товаров": 3, "активных": 2, "в_архиве": 1, "синхронизирован": True}
    assert result["dumping"]["товаров_с_настройкой"] == 2
    assert result["dumping"]["из_них_включено"] == 1


def test_store_overview_survives_kaspi_outage(monkeypatch):
    """Kaspi лежит — помощник обязан ответить остальным, а не упасть целиком."""
    def boom(token, **kw):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(KaspiAPI, "get_all_packaging_tabs_cached", staticmethod(boom))
    monkeypatch.setattr(state_helpers, "_load_products", lambda store_id: [])
    monkeypatch.setattr(state_helpers, "_load_dumping_configs", lambda store_id: {})
    monkeypatch.setattr(feature_flags, "get_flags", lambda store_id: {"dumping_enabled": False})

    res = asyncio.run(tool_registry.execute("get_store_overview", {}, _ctx(token="t")))
    assert res["ok"] is True
    assert "503" in res["result"]["orders"]["error"]
    assert res["result"]["catalog"]["синхронизирован"] is False


# --- Движок ------------------------------------------------------------

def test_engine_plain_answer():
    provider = ScriptedProvider([LLMResult(text="Готово", usage={"model": "m", "prompt_tokens": 5, "completion_tokens": 2})])
    turn = asyncio.run(run_turn("привет", [], _brief(), _ctx(), cfg=_cfg(), provider=provider))
    assert turn.reply == "Готово"
    assert turn.tool_trace == []
    assert turn.usage["prompt_tokens"] == 5
    # Систем-промпт всегда первым сообщением
    assert provider.calls[0][0].role == "system"


def test_engine_executes_tool_then_answers():
    provider = ScriptedProvider([
        LLMResult(tool_calls=[ToolCall(id="c1", name="get_store_overview", arguments={})]),
        LLMResult(text="Данных пока нет"),
    ])
    turn = asyncio.run(run_turn("что с магазином?", [], _brief(), _ctx(), cfg=_cfg(), provider=provider))
    assert turn.reply == "Данных пока нет"
    assert len(turn.tool_trace) == 1
    assert turn.tool_trace[0].name == "get_store_overview"
    # Второй запрос к модели содержит результат инструмента
    assert any(m.role == ROLE_TOOL for m in provider.calls[1])


def test_engine_stops_at_iteration_limit():
    loop_forever = [LLMResult(tool_calls=[ToolCall(id=f"c{i}", name="get_store_overview", arguments={})]) for i in range(10)]
    provider = ScriptedProvider(loop_forever)
    turn = asyncio.run(run_turn("зациклись", [], _brief(), _ctx(), cfg=_cfg(max_tool_iterations=2), provider=provider))
    assert turn.truncated is True
    assert len(turn.tool_trace) == 2


def test_engine_propagates_provider_error():
    provider = ScriptedProvider([LLMResult(error="HTTP 500")])
    turn = asyncio.run(run_turn("вопрос", [], _brief(), _ctx(), cfg=_cfg(), provider=provider))
    assert turn.error == "HTTP 500"
    assert turn.reply == ""


# --- Промпт ------------------------------------------------------------

def test_system_prompt_carries_store_and_delivery_rule():
    prompt = build_system_prompt(_brief())
    assert "store-1" in prompt
    assert "isKaspiDelivery" in prompt  # см. CLAUDE.md — постамат ≠ самовывоз
