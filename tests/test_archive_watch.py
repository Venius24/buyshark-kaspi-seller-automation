"""
Сторож снятых товаров: ловит переход `status: Активен → Архив` между двумя
синхронизациями каталога и не путает настоящее снятие с исчерпанием пула
мастер-группы.

`status` — не наша догадка поверх сырых полей: `KaspiAPI.sync_merchant_assortment`
ходит двумя раздельными запросами (`a=true`/`a=false`), и каждый оффер уже
приходит из своей однозначной выборки Kaspi (kaspi_wrapper.py:1155/1244).

Проверяется не «функция вернула список», а поведение на границе: кто именно
попадает в тревогу, кто из неё вычитается, и не путает ли первый прогон
старый архив с только что случившимся.
"""
import asyncio

import pytest

from backend.dumper import archive_watch, feed_state


def _offer(sku, available=True, name=None):
    return {
        "merchantSku": sku,
        "status": "Активен" if available else "Архив",
        "name": name or sku,
    }


def _watch(store_id, products):
    """`watch_archived` асинхронна — тот же обход синхронного pytest, что и
    у `test_feed_guard._guard`: сторож ничего не делит между вызовами кроме
    файла состояния, свой event loop на вызов ему безразличен."""
    return asyncio.run(archive_watch.watch_archived(store_id, products))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Изолированный магазин: состояние пишется в tmp, а не в states/ проекта."""
    monkeypatch.setattr(
        feed_state, "_state_path",
        lambda store_id: tmp_path / "{}.json".format(store_id),
    )
    return "store-archive-watch"


def _no_groups(monkeypatch):
    monkeypatch.setattr(
        "backend.state_helpers._load_master_stock_groups", lambda store_id: {}
    )


def test_first_run_only_seeds_baseline(store, monkeypatch):
    """
    Первый прогон не имеет базы для сравнения — весь текущий архив может быть
    старым, а не только что случившимся. Уведомления быть не должно.
    """
    _no_groups(monkeypatch)
    products = [_offer("OK-0001"), _offer("H477-0009", available=False)]

    verdict = _watch(store, products)

    assert verdict.first_run
    assert not verdict.needs_attention
    assert feed_state.load(store)["archived_skus"] == ["H477-0009"]


def test_new_archival_after_baseline_is_flagged(store, monkeypatch):
    _no_groups(monkeypatch)
    _watch(store, [_offer("OK-0001"), _offer("H477-0009")])

    verdict = _watch(store, [_offer("OK-0001"), _offer("H477-0009", available=False)])

    assert verdict.new == ["H477-0009"]
    assert verdict.needs_attention


def test_already_known_archival_does_not_refire(store, monkeypatch):
    """Оффер, снятый и увиденный на прошлом проходе, — не новость."""
    _no_groups(monkeypatch)
    _watch(store, [_offer("H477-0009")])
    _watch(store, [_offer("H477-0009", available=False)])

    verdict = _watch(store, [_offer("H477-0009", available=False)])

    assert not verdict.new
    assert not verdict.needs_attention


def test_missing_status_field_is_not_a_removal(store, monkeypatch):
    """
    Только явный `status == "Архив"`. Оффер без поля вообще (сбой выкачки,
    неожиданный формат записи) не должен считаться снятым — молчание не
    равно явному сигналу от Kaspi.
    """
    _no_groups(monkeypatch)
    _watch(store, [_offer("OK-0001")])

    verdict = _watch(store, [{"merchantSku": "OK-0001", "name": "OK-0001"}])

    assert not verdict.new
    assert feed_state.load(store)["archived_skus"] == []


def test_master_group_exhaustion_is_excluded(store, monkeypatch):
    """
    Пул общего остатка кончился — все SKU группы честно уходят в архив, и
    это не тревога, а штатная работа веерной рассылки.
    """
    _watch(store, [_offer("G-1"), _offer("G-2")])
    monkeypatch.setattr(
        "backend.state_helpers._load_master_stock_groups",
        lambda store_id: {
            "grp": {"shared_stock": 0, "linked_skus": ["G-1", "G-2"]},
        },
    )

    verdict = _watch(store, [_offer("G-1", available=False), _offer("G-2", available=False)])

    assert verdict.new == []
    assert verdict.skipped_group == ["G-1", "G-2"]
    assert not verdict.needs_attention


def test_group_with_stock_left_still_alarms(store, monkeypatch):
    """
    SKU входит в группу, но пул ещё не пуст — снятие необъяснимо и должно
    дойти до владельца как есть.
    """
    _watch(store, [_offer("G-1")])
    monkeypatch.setattr(
        "backend.state_helpers._load_master_stock_groups",
        lambda store_id: {
            "grp": {"shared_stock": 40, "linked_skus": ["G-1"]},
        },
    )

    verdict = _watch(store, [_offer("G-1", available=False)])

    assert verdict.new == ["G-1"]
    assert verdict.skipped_group == []


def test_mixed_batch_splits_correctly(store, monkeypatch):
    """Одновременно снятая карточка вне группы и карточка группы — сторож
    не должен ни спрятать первую, ни разбудить по второй."""
    _watch(store, [_offer("SOLO-1"), _offer("G-1")])
    monkeypatch.setattr(
        "backend.state_helpers._load_master_stock_groups",
        lambda store_id: {"grp": {"shared_stock": 0, "linked_skus": ["G-1"]}},
    )

    verdict = _watch(
        store, [_offer("SOLO-1", available=False), _offer("G-1", available=False)]
    )

    assert verdict.new == ["SOLO-1"]
    assert verdict.skipped_group == ["G-1"]


def test_alarm_is_urgent_and_always_fires(store, monkeypatch):
    """
    По решению владельца: снятие будит ВСЕГДА, включая тихие часы —
    `urgent=True` безусловно, в отличие от большинства других алертов.
    """
    _no_groups(monkeypatch)
    _watch(store, [_offer("H477-0009")])

    calls = []

    async def fake_dispatch(store_id, group, text, **kwargs):
        calls.append({"group": group, "text": text, **kwargs})

    monkeypatch.setattr("backend.chatbot.notify_dispatch.dispatch", fake_dispatch)

    _watch(store, [_offer("H477-0009", available=False)])

    assert len(calls) == 1
    assert calls[0]["group"] == "problems"
    assert calls[0]["urgent"] is True
    assert "H477-0009" in calls[0]["text"]


def test_broken_telegram_does_not_lose_the_finding(store, monkeypatch):
    """Состояние пишется до отправки — падение канала не должно стереть факт
    снятия из базы сравнения на следующий проход."""
    _no_groups(monkeypatch)
    _watch(store, [_offer("H477-0009")])

    async def boom(*_a, **_kw):
        raise RuntimeError("телеграм лёг")

    monkeypatch.setattr("backend.chatbot.notify_dispatch.dispatch", boom)

    verdict = _watch(store, [_offer("H477-0009", available=False)])

    assert verdict.new == ["H477-0009"]
    assert feed_state.load(store)["archived_skus"] == ["H477-0009"]


def test_group_lookup_failure_does_not_hide_the_removal(store, monkeypatch):
    """Если БД мастер-групп недоступна, сторож не должен молчать про
    настоящее снятие — лучше лишняя тревога, чем спрятанная потеря."""
    _watch(store, [_offer("H477-0009")])

    def boom(store_id):
        raise RuntimeError("нет сессии БД")

    monkeypatch.setattr("backend.state_helpers._load_master_stock_groups", boom)

    verdict = _watch(store, [_offer("H477-0009", available=False)])

    assert verdict.new == ["H477-0009"]
