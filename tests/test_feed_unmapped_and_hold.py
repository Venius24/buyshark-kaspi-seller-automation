"""
Два дефекта, вскрытые первой боевой отправкой 2026-08-28, и их лечение.

ПЕРВЫЙ. `notMapped` использовался как рубильник (`verdict.ok` требовал ноль), а
он смешивает два несовместимых случая: потерянную карточку и товар, которого
просто нет на складе. Файл 28.08 не потерял ничего, но выключил фид. Здесь
проверяется, что решение принимает КАТАЛОГ, а не счётчик, — и что настоящая
потеря по-прежнему выключает.

ВТОРОЙ. Пока файл летит, JSON-писатель пишет то же самое. Замер по окну
20:05:24 → 20:08:46: 81 запись, все 81 — с ценами из файла, при «квота 232/250,
откладываем 45 записей». Здесь проверяется, что такие записи придерживаются —
и, что важнее, что правка, сделанная ВНУТРИ окна, не придерживается никогда.
"""
import datetime
import json

import pytest

from backend.dumper import feed_settle, feed_state
from backend.dumper.feed_settle import classify_unmapped, parse_declared, settle_file
from backend.dumper.feed_uploader import UploadVerdict

SNAP_AT = datetime.datetime(2026, 8, 28, 20, 5, 23)


def _offer(sku, price=1890, stock=19.0):
    return {
        "merchantSku": sku,
        "sku": "143461639",
        "name": "Товар {}".format(sku),
        "brand": "barkhan",
        "price_num": price,
        "stock": stock,
        "available": True,
        "status": "Активен",
    }


def _xml(offers):
    from backend.dumper.xml_feed import generate_xml_feed

    return generate_xml_feed("20186986", offers)


class _Snapshot:
    def __init__(self, offers, archived=()):
        self.offers = offers
        self.size = len(offers)
        self.archived_skus = list(archived)


class _Verdict:
    def __init__(self, not_mapped=0, accepted=True, file_id="file-1"):
        self.not_mapped = not_mapped
        self.accepted = accepted
        self.ok = accepted and not_mapped == 0
        self.file_id = file_id

    def summary(self):
        return "статус FLUSHED, не смаплено {}".format(self.not_mapped)


@pytest.fixture
def archive(tmp_path):
    def _make(offers, active_before=None, archived_before=()):
        path = tmp_path / "buyshark-20186986-20260828-200524.xml"
        path.write_bytes(_xml(offers))
        path.with_suffix(path.suffix + ".meta.json").write_text(
            json.dumps({
                "snapshot_at": SNAP_AT.isoformat(),
                "offers": len(offers),
                "active_before": active_before if active_before is not None else len(offers),
                "archived_before": list(archived_before),
            }),
            encoding="utf-8",
        )
        return path

    return _make


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        feed_state, "_state_path", lambda sid: tmp_path / "{}.json".format(sid)
    )
    monkeypatch.setattr(feed_settle, "_inflight_cache", {})
    sid = "store-unmapped"
    feed_state.enable(sid)
    return sid


# ── Вердикт: принят ≠ идеален ───────────────────────────────────────────────

def test_not_mapped_no_longer_means_rejected():
    """
    Файл разобран целиком — работа продолжается, даже если что-то не смаплено.
    Ровно это 28.08 и произошло: 1099/1099, отклонено 0, не смаплено 2.
    """
    v = UploadVerdict(
        file_id="f", file_name="n", status="FLUSHED",
        distinct=1099, processed=1099, rejected=0, not_mapped=2,
    )
    assert v.accepted
    assert not v.ok


def test_a_rejected_file_is_not_accepted():
    v = UploadVerdict(
        file_id="f", file_name="n", status="FLUSHED",
        distinct=1099, processed=1099, rejected=1, not_mapped=0,
    )
    assert not v.accepted


def test_a_half_processed_file_is_not_accepted():
    """Обработано меньше объявленного — сверять не с чем."""
    v = UploadVerdict(
        file_id="f", file_name="n", status="FLUSHED",
        distinct=1099, processed=748, rejected=0,
    )
    assert not v.accepted


# ── Классификация не смапленных ─────────────────────────────────────────────

def test_zero_stock_offer_is_expected_to_be_unmapped(archive):
    """
    `X144-0001-2` со стоком 0 отсутствовал в ACTIVE-выгрузке Kaspi ещё ДО
    отправки: прайс-лист живёт в измерении «товар торгует».
    """
    declared = parse_declared(
        archive([_offer("A"), _offer("X144-0001-2", stock=0.0)]).read_bytes()
    )
    unexplained, expected = classify_unmapped(declared, _Snapshot([_offer("A")]))

    assert expected == ["X144-0001-2"]
    assert unexplained == []


def test_an_offer_with_stock_that_vanished_is_unexplained(archive):
    """А вот пропажа торгующего товара — это потеря, и она называется поимённо."""
    declared = parse_declared(archive([_offer("A"), _offer("B", stock=19.0)]).read_bytes())
    unexplained, expected = classify_unmapped(declared, _Snapshot([_offer("A")]))

    assert unexplained == ["B"]
    assert expected == []


def test_everything_in_place_classifies_to_nothing(archive):
    declared = parse_declared(archive([_offer("A"), _offer("B")]).read_bytes())
    assert classify_unmapped(declared, _Snapshot([_offer("A"), _offer("B")])) == ([], [])


# ── Решение принимает каталог, а не счётчик ─────────────────────────────────

def test_benign_unmapped_keeps_the_feed_running(archive, store, monkeypatch):
    """
    Сценарий 28.08 дословно: вердикт с `notMapped`, но каталог цел. Фид обязан
    остаться включённым, а намерения — закрыться. Раньше здесь выключалось.
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (7, 1))
    path = archive([_offer("A"), _offer("X144-0001-2", stock=0.0)], active_before=2)

    report = settle_file(store, path, _Verdict(not_mapped=2), snapshot=_Snapshot([_offer("A")]))

    assert feed_state.is_enabled(store)
    assert report.settled == 7
    assert report.unmapped_expected == ["X144-0001-2"]
    assert report.unmapped_unexplained == []


def test_benign_unmapped_does_not_raise_a_false_mismatch(archive, store, monkeypatch):
    """
    Распроданный товар не должен всплывать расхождением: иначе тревога уходит
    после каждой отправки и канал перестают читать.
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("A"), _offer("SOLD-OUT", stock=0.0)], active_before=2)

    report = settle_file(store, path, _Verdict(not_mapped=1), snapshot=_Snapshot([_offer("A")]))

    assert report.mismatches == []
    assert not report.needs_attention


def test_a_real_loss_still_pulls_the_switch(archive, store, monkeypatch):
    """
    Ослабление порога не имеет права ослабить защиту: торгующий товар пропал —
    фид выключается, намерения не гасятся.
    """
    called = []
    monkeypatch.setattr(
        feed_settle, "settle_intents", lambda *a: called.append(a) or (0, 0)
    )
    path = archive([_offer("A"), _offer("H477-0009", stock=19.0)], active_before=2)

    report = settle_file(store, path, _Verdict(not_mapped=1), snapshot=_Snapshot([_offer("A")]))

    assert not feed_state.is_enabled(store)
    assert report.unmapped_unexplained == ["H477-0009"]
    assert called == []


def test_an_unaccepted_verdict_still_pulls_the_switch(archive, store, monkeypatch):
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("A")])

    settle_file(store, path, _Verdict(accepted=False), snapshot=_Snapshot([_offer("A")]))

    assert not feed_state.is_enabled(store)


def test_zero_stock_archived_is_not_an_unexpected_archive(archive, store, monkeypatch):
    """
    Товар кончился → карточка уезжает в архив. Это следствие пустого склада, а
    не работы файла, и выключать из-за него фид значило бы выключать его каждый
    раз, когда что-то распродалось.
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("A"), _offer("GONE", stock=0.0)], active_before=2, archived_before=["OLD"])

    report = settle_file(
        store, path, _Verdict(not_mapped=1),
        snapshot=_Snapshot([_offer("A")], archived=["OLD", "GONE"]),
    )

    assert report.unexpected_archived == []
    assert feed_state.is_enabled(store)


def test_a_card_archived_with_stock_is_still_an_alarm(archive, store, monkeypatch):
    """Зеркало предыдущего: с остатком — тот же механизм, что снял 798 карточек."""
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("A"), _offer("LOST", stock=19.0)], active_before=2, archived_before=["OLD"])

    report = settle_file(
        store, path, _Verdict(not_mapped=1),
        snapshot=_Snapshot([_offer("A")], archived=["OLD", "LOST"]),
    )

    assert report.unexpected_archived == ["LOST"]
    assert not feed_state.is_enabled(store)


# ── Придержка JSON на время полёта ──────────────────────────────────────────

class _Row:
    def __init__(self, sku, desired_at, store_id="store-hold"):
        self.store_id = store_id
        self.sku = sku
        self.desired_at = desired_at


class _Dec:
    def __init__(self, sku, price, stock):
        self.sku = sku
        self.price = price
        self.stock = stock


@pytest.fixture
def in_flight(tmp_path, monkeypatch):
    """Магазин с файлом в полёте, как его оставляет mark_sent."""
    monkeypatch.setattr(
        feed_state, "_state_path", lambda sid: tmp_path / "{}.json".format(sid)
    )
    monkeypatch.setattr(feed_settle, "_inflight_cache", {})
    monkeypatch.setattr(
        "backend.dumper.feed_uploader._archive_dir", lambda sid: tmp_path
    )
    sid = "store-hold"

    def _fly(offers, snapshot_at=None):
        name = "buyshark-20186986-20260828-200524.xml"
        (tmp_path / name).write_bytes(_xml(offers))
        feed_state.enable(sid)
        feed_state.mark_sent(sid, name, snapshot_at or datetime.datetime.now())
        return sid

    return _fly


def _held(store_id, decisions):
    from backend.services.offer_sync import _carried_by_feed_in_flight

    return _carried_by_feed_in_flight(store_id, decisions)


def test_nothing_is_held_when_no_file_is_flying(tmp_path, monkeypatch):
    monkeypatch.setattr(
        feed_state, "_state_path", lambda sid: tmp_path / "{}.json".format(sid)
    )
    feed_state.enable("store-hold")
    row = _Row("A", datetime.datetime.now())

    assert _held("store-hold", [(row, _Dec("A", 1890, 19))]) == []


def test_an_intent_the_file_carries_is_held(in_flight):
    """Ровно те 81 запись, что 28.08 ушли дважды."""
    now = datetime.datetime.now()
    sid = in_flight([_offer("A", price=1890, stock=19.0)], snapshot_at=now)
    row = _Row("A", now - datetime.timedelta(seconds=30))

    assert _held(sid, [(row, _Dec("A", 1890, 19))])


def test_an_edit_made_inside_the_flight_is_never_held(in_flight):
    """
    Самое важное свойство. Правка, появившаяся ПОСЛЕ снимка, файлом не везётся
    (в нём значения на момент T0) — придержать её значило бы дать файлу молча
    её откатить и не переписать обратно.
    """
    now = datetime.datetime.now()
    sid = in_flight([_offer("A", price=1890, stock=19.0)], snapshot_at=now)
    row = _Row("A", now + datetime.timedelta(seconds=30))

    assert _held(sid, [(row, _Dec("A", 1890, 19))]) == []


def test_a_different_price_is_not_held(in_flight):
    """Демпер сдвинул цену после снимка: файл везёт устаревшее, писать надо."""
    now = datetime.datetime.now()
    sid = in_flight([_offer("A", price=1890, stock=19.0)], snapshot_at=now)
    row = _Row("A", now - datetime.timedelta(seconds=30))

    assert _held(sid, [(row, _Dec("A", 1887, 19))]) == []


def test_a_different_stock_is_not_held(in_flight):
    now = datetime.datetime.now()
    sid = in_flight([_offer("A", price=1890, stock=19.0)], snapshot_at=now)
    row = _Row("A", now - datetime.timedelta(seconds=30))

    assert _held(sid, [(row, _Dec("A", 1890, 3))]) == []


def test_an_offer_absent_from_the_file_is_not_held(in_flight):
    now = datetime.datetime.now()
    sid = in_flight([_offer("A")], snapshot_at=now)
    row = _Row("B", now - datetime.timedelta(seconds=30))

    assert _held(sid, [(row, _Dec("B", 1890, 19))]) == []


def test_a_stuck_flight_releases_the_hold(in_flight):
    """
    Сверка могла не доехать вовсе — упал процесс, оборвалось ожидание. Держать
    цену вечно хуже, чем записать её второй раз.
    """
    old = datetime.datetime.now() - datetime.timedelta(
        seconds=feed_settle.MAX_HOLD_SECONDS + 60
    )
    sid = in_flight([_offer("A", price=1890, stock=19.0)], snapshot_at=old)
    row = _Row("A", old - datetime.timedelta(seconds=30))

    assert _held(sid, [(row, _Dec("A", 1890, 19))]) == []


def test_the_flying_file_is_parsed_once(in_flight, monkeypatch):
    """Флашер ходит раз в 45с — перечитывать 480 КБ каждый раз незачем."""
    now = datetime.datetime.now()
    sid = in_flight([_offer("A", price=1890, stock=19.0)], snapshot_at=now)
    reads = []
    real = feed_settle.parse_declared
    monkeypatch.setattr(
        feed_settle, "parse_declared", lambda b: reads.append(1) or real(b)
    )
    row = _Row("A", now - datetime.timedelta(seconds=30))

    for _ in range(3):
        _held(sid, [(row, _Dec("A", 1890, 19))])

    assert len(reads) == 1


def test_settling_the_file_releases_the_hold(in_flight):
    """`mark_settled` снимает in_flight — и придержка обязана исчезнуть с ним."""
    now = datetime.datetime.now()
    sid = in_flight([_offer("A", price=1890, stock=19.0)], snapshot_at=now)
    row = _Row("A", now - datetime.timedelta(seconds=30))
    assert _held(sid, [(row, _Dec("A", 1890, 19))])

    feed_state.mark_settled(sid, type("R", (), {"file_id": "f", "summary": lambda s: "ok"})())

    assert _held(sid, [(row, _Dec("A", 1890, 19))]) == []


def test_a_broken_hold_never_blocks_writing(in_flight, monkeypatch):
    """
    Придержка — экономия, а не корректность. Её падение не имеет права
    остановить запись цен.
    """
    now = datetime.datetime.now()
    sid = in_flight([_offer("A")], snapshot_at=now)

    def boom(*_a, **_kw):
        raise RuntimeError("состояние не прочиталось")

    monkeypatch.setattr(feed_settle, "in_flight_declaration", boom)
    row = _Row("A", now - datetime.timedelta(seconds=30))

    assert _held(sid, [(row, _Dec("A", 1890, 19))]) == []


# ── Расхождения: «файл не сработал» против «мир уехал вперёд» ───────────────

def test_a_price_the_damper_moved_after_the_snapshot_is_not_an_alarm(
    archive, store, monkeypatch
):
    """
    Файл несёт значения на момент T0 и применяется минутами. Всё, что демпер
    решил внутри этого окна, в файле физически быть не могло — каталог честно
    покажет НЕ то, что мы объявили, и это работа транспорта, а не сбой.

    Замер, ради которого правило появилось: прогон файла 28.08 дал 117
    расхождений (79 цена, 38 остаток) при полностью целом каталоге.
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    monkeypatch.setattr(feed_settle, "_intents_newer_than", lambda *a: {"A"})
    path = archive([_offer("A", price=1890)], active_before=1)

    report = settle_file(
        store, path, _Verdict(), snapshot=_Snapshot([_offer("A", price=1887)])
    )

    assert report.mismatches == []
    assert len(report.mismatches_explained) == 1
    assert not report.needs_attention
    assert feed_state.is_enabled(store)


def test_a_price_that_did_not_take_is_still_an_alarm(archive, store, monkeypatch):
    """
    Зеркало: намерение по этому SKU не менялось, а Kaspi показывает своё —
    значит запись не применилась. Ровно ради этого сверка и существует.
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    monkeypatch.setattr(feed_settle, "_intents_newer_than", lambda *a: set())
    path = archive([_offer("A", price=1890)], active_before=1)

    report = settle_file(
        store, path, _Verdict(), snapshot=_Snapshot([_offer("A", price=1887)])
    )

    assert len(report.mismatches) == 1
    assert report.mismatches[0]["field"] == "price"
    assert report.needs_attention


def test_explained_mismatches_are_never_swallowed(archive, store, monkeypatch):
    """
    Объяснённое остаётся в отчёте отдельным полем и попадает в summary. Тихо
    выброшенное расхождение потом невозможно ни объяснить, ни пересчитать.
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    monkeypatch.setattr(feed_settle, "_intents_newer_than", lambda *a: {"A"})
    path = archive([_offer("A", price=1890)], active_before=1)

    report = settle_file(
        store, path, _Verdict(), snapshot=_Snapshot([_offer("A", price=1887)])
    )

    assert "объяснены правками после снимка" in report.summary()


def test_enabling_after_a_settle_halt_actually_unblocks_the_feed(archive, store, monkeypatch):
    """
    Сверка, выключая фид, не доходит до `mark_settled` — полёт остаётся
    незакрытым. Если `enable` его не снимет, включение окажется молча
    бесполезным: `eligible()` будет вечно отвечать «файл ещё в полёте».
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    feed_state.mark_sent(store, "buyshark-20186986-20260828-200524.xml", SNAP_AT)
    path = archive([_offer("A"), _offer("LOST", stock=19.0)], active_before=2)

    settle_file(store, path, _Verdict(not_mapped=1), snapshot=_Snapshot([_offer("A")]))
    assert not feed_state.is_enabled(store)
    assert feed_state.load(store)["in_flight"]

    feed_state.enable(store)

    assert feed_state.eligible(store, 1000) is None


# ── Замок наружу: не молчит ли файл о том, что Kaspi продаёт ────────────────

def _kaspi_export(skus):
    from backend.dumper.xml_feed import generate_xml_feed

    return generate_xml_feed("20186986", [_offer(s) for s in skus])


def test_a_card_kaspi_sells_and_we_forgot_blocks_the_send(monkeypatch):
    """
    Механизм, погасивший ~798 карточек. Все внутренние инварианты при этом
    целы: файл в точности равен снимку — просто снимок сам неполон.
    """
    from backend.dumper import feed_plan, feed_export

    monkeypatch.setattr(
        feed_export, "load_cached", lambda sid, available="ACTIVE": (_kaspi_export(["A", "B"]), None)
    )
    snap = _Snapshot([_offer("A")], archived=[])

    missing, note = feed_plan.missing_from_price_list("s", snap, {"A"})

    assert missing == ["B"]
    assert note


def test_a_card_archived_since_the_export_is_not_missing(monkeypatch):
    """
    Выгрузка Kaspi генерируется не в момент сборки файла. Карточку могли снять
    уже после — и тогда её отсутствие в файле правда, а не пропажа. Признак
    один: наш снимок ЗНАЕТ, что она в архиве.
    """
    from backend.dumper import feed_plan, feed_export

    monkeypatch.setattr(
        feed_export, "load_cached", lambda sid, available="ACTIVE": (_kaspi_export(["A", "B"]), None)
    )
    snap = _Snapshot([_offer("A")], archived=["B"])

    missing, note = feed_plan.missing_from_price_list("s", snap, {"A"})

    assert missing == []
    assert note is None


def test_a_complete_file_passes_clean(monkeypatch):
    from backend.dumper import feed_plan, feed_export

    monkeypatch.setattr(
        feed_export, "load_cached", lambda sid, available="ACTIVE": (_kaspi_export(["A", "B"]), None)
    )

    assert feed_plan.missing_from_price_list("s", _Snapshot([]), {"A", "B"}) == ([], None)


def test_an_unavailable_export_says_so_instead_of_passing_silently(monkeypatch):
    """
    Не сумев проверить, проверка обязана сказать это вслух. Молчаливое «ок» на
    недоступной выгрузке — худший исход: замок выглядит взведённым, а его нет.
    """
    from backend.dumper import feed_plan, feed_export

    monkeypatch.setattr(feed_export, "load_cached", lambda sid, available="ACTIVE": (None, None))

    missing, note = feed_plan.missing_from_price_list("s", _Snapshot([]), {"A"})

    assert missing == []
    assert "НЕ сверен" in note


def test_the_comparison_survives_invisible_characters(monkeypatch):
    """
    Выгрузка Kaspi теряет табуляцию в sku ровно так же, как наш файл. Сравнивать
    надо после одинаковой нормализации, иначе живой оффер выглядел бы пропажей.
    """
    from backend.dumper import feed_plan, feed_export

    monkeypatch.setattr(
        feed_export, "load_cached", lambda sid, available="ACTIVE": (_kaspi_export(["H477-0009"]), None)
    )

    assert feed_plan.missing_from_price_list("s", _Snapshot([]), {"H477-0009"}) == ([], None)


# ─────────────────────────────────────────────────────────────────────────────
# ТРЕТИЙ дефект, вскрытый боевой отправкой 2026-08-29. Воркер отмечал полёт
# литералом "pending" вместо имени файла, а имя рождалось внутри send_plan.
# Всё, что ищет летящий файл ПО ИМЕНИ, промахивалось: ротация копий сжала его
# прямо во время полёта (сверка упала на FileNotFoundError и заперла магазин
# непустым in_flight), а придержка JSON не находила объявленного и молча не
# придерживала ничего — то есть ВТОРОЙ дефект не был вылечен в бою ни разу.
# ─────────────────────────────────────────────────────────────────────────────
def test_worker_marks_the_flight_with_the_real_file_name():
    """В состоянии полёта обязано лежать имя файла, а не заглушка."""
    import inspect

    from backend.dumper import feed_worker

    src = inspect.getsource(feed_worker.run_for_store)
    assert 'mark_sent(store.store_id, "pending"' not in src, (
        "полёт снова отмечается литералом — ротация и придержка ослепнут"
    )
    assert "mark_sent(store.store_id, file_name" in src
    assert "filename=file_name" in src


def test_name_generator_is_shared_with_the_uploader():
    """Отправитель и воркер зовут один генератор, а не два разных формата."""
    from backend.dumper.feed_uploader import make_file_name

    name = make_file_name("20186986")
    assert name.startswith("buyshark-20186986-")
    assert name.endswith(".xml")


def test_rotate_keeps_a_file_whose_name_is_in_flight(tmp_path, monkeypatch):
    """Ротация не трогает летящий файл — по нему пойдёт сверка после вердикта."""
    from backend.dumper import feed_backup

    sid = "s-rotate"
    store_dir = tmp_path / sid
    (store_dir / "backups").mkdir(parents=True)
    monkeypatch.setattr(feed_state, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(feed_backup, "_backup_dir", lambda s: tmp_path / str(s) / "backups")

    flying = store_dir / "buyshark-1-20260829-085730.xml"
    flying.write_bytes(b"<offers/>")
    feed_state.save(sid, in_flight=flying.name)

    feed_backup.rotate(sid)

    assert flying.exists(), "летящий файл сжали — сверке нечего будет читать"


def test_a_crashed_settle_always_closes_the_flight():
    """Упавшая сверка не смеет оставить in_flight — иначе магазин заперт навсегда."""
    import inspect

    from backend.dumper import feed_worker

    src = inspect.getsource(feed_worker.run_for_store)
    tail = src[src.index("settle_file("):]
    assert "except Exception" in tail, "падение сверки не перехвачено"
    assert "in_flight=None" in tail, "полёт не закрывается при падении сверки"


# ─────────────────────────────────────────────────────────────────────────────
# Третья причина notMapped, опознанная 2026-08-29 по отчёту кабинета: оффер
# объявлен, карточка активна и лежит в выгрузке Kaspi, а строка файла к ней не
# прицепилась («Товар не связан» — H477-0009 и H477-0017). По каталогу это не
# видно: обе категории классификатора пусты. Молчать нельзя — «не смаплено 2 /
# объяснено 0» читается как «всё разобрано».
# ─────────────────────────────────────────────────────────────────────────────
def test_unmapped_that_the_catalogue_cannot_explain_is_counted(archive, store, monkeypatch):
    """Живые карточки, которые Kaspi не смапила, попадают в отчёт числом."""
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("H477-0009"), _offer("H477-0017")], active_before=2)
    snapshot = _Snapshot([_offer("H477-0009"), _offer("H477-0017")])

    report = settle_file(store, path, _Verdict(not_mapped=2), snapshot=snapshot)

    assert report.unmapped_expected == []
    assert report.unmapped_unexplained == []
    assert report.unmapped_unclassified == 2, (
        "необъяснимое не сосчитано — отчёт молча выглядит разобранным"
    )
    assert "без объяснения по каталогу 2" in report.summary()


def test_unexplainable_unmapped_does_not_switch_the_feed_off(archive, store, monkeypatch):
    """Карточки целы — выключать фид не за что. Замерено 28.08 и 29.08."""
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("H477-0009"), _offer("H477-0017")], active_before=2)
    snapshot = _Snapshot([_offer("H477-0009"), _offer("H477-0017")])

    report = settle_file(store, path, _Verdict(not_mapped=2), snapshot=snapshot)

    assert report.alarms == []
    assert feed_state.is_enabled(store)


def test_a_fully_explained_verdict_reports_nothing_unclassified(archive, store, monkeypatch):
    """Когда каталог объясняет всё, счётчик молчит."""
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("A"), _offer("SOLD-OUT", stock=0.0)], active_before=2)

    report = settle_file(store, path, _Verdict(not_mapped=1), snapshot=_Snapshot([_offer("A")]))

    assert report.unmapped_expected == ["SOLD-OUT"]
    assert report.unmapped_unclassified == 0
    assert "без объяснения по каталогу" not in report.summary()
