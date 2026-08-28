"""
Сторож каталога: битый артикул останавливает работу через файл автоматически.

Проверяется не «функция вернула список», а поведение на границе: кто именно
дёргает рубильник, кого будят, и — главное — не разошёлся ли сторож с планом.
Разошедшийся сторож хуже отсутствующего: он говорит «чисто» про каталог,
который планом чист не будет.
"""
import pytest

from backend.dumper import feed_guard, feed_state


def _offer(sku, active=True):
    return {"merchantSku": sku, "available": active, "price": 100, "stock": 5}


def _catalogue(*skus, size=None):
    """Каталог заведомо выше порога фида, если не сказано иначе."""
    offers = [_offer(s) for s in skus]
    filler = (size if size is not None else feed_state.MIN_OFFERS_FOR_FEED + 10) - len(offers)
    offers += [_offer("CLEAN-{:05d}".format(i)) for i in range(max(0, filler))]
    return offers


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Изолированный магазин: состояние пишется в tmp, а не в states/ проекта."""
    monkeypatch.setattr(
        feed_state, "_state_path",
        lambda store_id: tmp_path / "{}.json".format(store_id),
    )
    sid = "store-guard"
    feed_state.enable(sid)
    return sid


def test_finds_tab_and_edge_space(store):
    verdict = feed_guard.check_catalogue(
        store, _catalogue("H477-0009\t", " S517-0005 ", "OK-0001")
    )
    assert verdict.blockers == [" S517-0005 ", "H477-0009\t"]
    assert not verdict.clean


def test_archived_offers_are_not_blockers(store):
    """
    Битый артикул в архиве безвреден: в файл идут только активные офферы.
    Иначе сторож будил бы человека из-за карточки, которую тот уже снял.
    """
    products = _catalogue("OK-0001") + [_offer("H477-0009\t", active=False)]
    assert feed_guard.check_catalogue(store, products).clean


def test_agrees_with_the_plan(store):
    """
    Сторож и план обязаны называть битым одно и то же множество. Здесь это
    проверяется не сверкой строк, а тем, что оба считают одной функцией.
    """
    from backend.dumper.feed_plan import feed_sku_roundtrip as plan_rule
    from backend.dumper.feed_guard import feed_sku_roundtrip as guard_rule

    assert plan_rule is guard_rule


def test_live_mode_pulls_the_switch(store, monkeypatch):
    monkeypatch.setenv("XML_FEED_WORKER", "1")
    verdict = feed_guard.check_catalogue(store, _catalogue("H477-0009\t"))

    assert verdict.disabled
    state = feed_state.load(store)
    assert state["enabled"] is False
    assert "битые артикулы" in state["blocked_reason"]


def test_shadow_mode_leaves_the_switch_alone(store, monkeypatch):
    """
    В тени рубильник не трогаем: `eligible()` начал бы отбивать проход ДО
    сборки плана, и теневые наблюдения перестали бы собираться вовсе —
    замерено 2026-08-27, 12 часов без единого тика.
    """
    monkeypatch.delenv("XML_FEED_WORKER", raising=False)
    verdict = feed_guard.check_catalogue(store, _catalogue("H477-0009\t"))

    assert verdict.blockers and not verdict.disabled
    assert feed_state.load(store)["enabled"] is True
    assert feed_state.load(store)["last_blockers"] == ["H477-0009\t"]


def test_small_store_is_only_recorded(store, monkeypatch):
    """Магазину ниже 250 офферов файл не положен — выключать нечего."""
    monkeypatch.setenv("XML_FEED_WORKER", "1")
    verdict = feed_guard.check_catalogue(store, _catalogue("H477-0009\t", size=50))

    assert verdict.blockers and not verdict.disabled
    assert feed_state.load(store)["enabled"] is True


def test_clean_catalogue_clears_the_mark_but_not_the_switch(store, monkeypatch):
    """
    Артикул починили — метка снимается, чтобы следующий был виден как новый.
    Но рубильник поднимает человек: `disable()` несимметричен намеренно.
    """
    monkeypatch.setenv("XML_FEED_WORKER", "1")
    feed_guard.check_catalogue(store, _catalogue("H477-0009\t"))
    assert feed_state.load(store)["enabled"] is False

    verdict = feed_guard.check_catalogue(store, _catalogue("OK-0001"))

    assert verdict.clean
    assert feed_state.load(store)["last_blockers"] == []
    assert feed_state.load(store)["enabled"] is False


@pytest.mark.asyncio
async def test_alarm_fires_once_per_set(store, monkeypatch):
    """
    Тревога — на СМЕНУ набора, а не на его наличие. Артикул чинят днями, а
    сторож ходит каждые полчаса: ежечасное «всё ещё сломано» приучает не читать
    тревоги — ровно тот исход, против которого канал и заводился.
    """
    sent = []
    monkeypatch.delenv("XML_FEED_WORKER", raising=False)

    async def fake_notify(store_id, verdict):
        sent.append(list(verdict.blockers))

    monkeypatch.setattr(feed_guard, "_notify", fake_notify)

    await feed_guard.guard_catalogue(store, _catalogue("H477-0009\t"))
    await feed_guard.guard_catalogue(store, _catalogue("H477-0009\t"))
    assert sent == [["H477-0009\t"]]

    # Новый артикул рядом со старым — это новая находка, будим снова.
    await feed_guard.guard_catalogue(store, _catalogue("H477-0009\t", " S517-0005 "))
    assert len(sent) == 2
    assert sent[1] == [" S517-0005 ", "H477-0009\t"]


@pytest.mark.asyncio
async def test_disabled_store_is_not_alarmed(store, monkeypatch):
    """
    Фид магазину не работает — останавливать нечего, будить не о чем. Находка
    лежит в состоянии и всплывёт при включении.
    """
    sent = []
    monkeypatch.delenv("XML_FEED_WORKER", raising=False)

    async def fake_notify(store_id, verdict):
        sent.append(verdict)

    monkeypatch.setattr(feed_guard, "_notify", fake_notify)
    feed_state.disable(store, "выключен вручную")

    verdict = await feed_guard.guard_catalogue(store, _catalogue("H477-0009\t"))

    assert verdict.blockers and not sent
    assert feed_state.load(store)["last_blockers"] == ["H477-0009\t"]


@pytest.mark.asyncio
async def test_broken_telegram_does_not_lose_the_finding(store, monkeypatch):
    """
    Состояние — то, по чему потом объясняются; сообщение можно и переслать.
    Поэтому запись идёт до отправки и падение канала её не отменяет.
    """
    monkeypatch.delenv("XML_FEED_WORKER", raising=False)

    async def boom(*_a, **_kw):
        raise RuntimeError("телеграм лёг")

    monkeypatch.setattr("backend.chatbot.notify_dispatch.dispatch", boom)

    verdict = await feed_guard.guard_catalogue(store, _catalogue("H477-0009\t"))

    assert verdict.blockers == ["H477-0009\t"]
    assert feed_state.load(store)["last_blockers"] == ["H477-0009\t"]
