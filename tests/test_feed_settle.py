"""
Тесты сверки каталога после вердикта (этап 5B).

Главное свойство, ради которого модуль существует: намерение закрывается ТОЛЬКО
если файл его действительно довёз. Файл несёт значения на момент снимка, а
Kaspi применяет его ~18 минут — всё, что появилось внутри этого окна, файл
откатит, и погасить такое намерение значило бы потерять правку навсегда.
Поэтому здесь два зеркальных теста: «довезённое закрываем» и «появившееся
внутри окна — не трогаем».
"""
import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pytest

from backend.dumper import feed_settle
from backend.dumper.feed_settle import (
    compare_with_catalog,
    parse_declared,
    settle_file,
)

SNAP_AT = datetime.datetime(2026, 8, 25, 12, 0, 0)


def _xml(offers):
    """Файл того же вида, что собирает generate_xml_feed."""
    from backend.dumper.xml_feed import generate_xml_feed

    return generate_xml_feed("20186986", offers)


def _offer(sku, price=590, stock=64.0):
    return {
        "merchantSku": sku,
        "sku": "147776238",
        "name": "Товар {}".format(sku),
        "brand": "barkhan",
        "price_num": price,
        "stock": stock,
        "available": True,
        "status": "Активен",
    }


class _Snapshot:
    def __init__(self, offers):
        self.offers = offers
        self.size = len(offers)


class _Verdict:
    def __init__(self, ok=True, file_id="file-1"):
        self.ok = ok
        self.file_id = file_id

    def summary(self):
        return "статус FLUSHED" if self.ok else "статус ERROR, не смаплено 2"


@pytest.fixture
def archive(tmp_path):
    """Готовый архив отправленного файла + meta, как их кладёт send_plan."""

    def _make(offers, active_before=None, snapshot_at=SNAP_AT):
        path = tmp_path / "buyshark-20186986-20260825-120000.xml"
        path.write_bytes(_xml(offers))
        path.with_suffix(path.suffix + ".meta.json").write_text(
            json.dumps({
                "snapshot_at": snapshot_at.isoformat(),
                "offers": len(offers),
                "active_before": active_before if active_before is not None else len(offers),
            }),
            encoding="utf-8",
        )
        return path

    return _make


# ── Разбор отправленного файла ──────────────────────────────────────────────

def test_declared_is_read_back_from_the_file_itself(archive):
    """Сверять надо с тем, что реально уехало, а не с планом в памяти."""
    path = archive([_offer("D844-0005", price=1290, stock=7.0)])
    declared = parse_declared(path.read_bytes())
    assert declared == {"D844-0005": {"price": 1290, "stock": 7.0, "available": True}}


def test_declared_keys_match_how_kaspi_will_see_them(archive):
    """
    Ключ — артикул после приёма Kaspi. Иначе сверка разошлась бы с каталогом
    ровно на тех артикулах, из-за которых всё и затевалось.
    """
    declared = parse_declared(archive([_offer("H477-0009\t")]).read_bytes())
    assert list(declared) == ["H477-0009"]


# ── Сравнение с фактом ──────────────────────────────────────────────────────

def test_a_vanished_offer_is_the_heaviest_mismatch(archive):
    """Оффера нет в свежем снимке — карточка ушла из продажи. Худший случай."""
    declared = parse_declared(archive([_offer("A"), _offer("B")]).read_bytes())
    m = compare_with_catalog(declared, _Snapshot([_offer("A")]))
    assert m == [{"sku": "B", "field": "offer", "declared": "active", "actual": None}]


def test_price_that_did_not_take_is_reported(archive):
    declared = parse_declared(archive([_offer("A", price=500)]).read_bytes())
    m = compare_with_catalog(declared, _Snapshot([_offer("A", price=590)]))
    assert m == [{"sku": "A", "field": "price", "declared": 500, "actual": 590}]


def test_matching_catalog_produces_no_noise(archive):
    declared = parse_declared(archive([_offer("A"), _offer("B")]).read_bytes())
    assert compare_with_catalog(declared, _Snapshot([_offer("A"), _offer("B")])) == []


# ── Поведение по вердикту ───────────────────────────────────────────────────

def test_bad_verdict_settles_nothing(archive, monkeypatch):
    """
    Тихо проглотить плохой вердикт = объявить доставленным то, что Kaspi не
    приняла. Намерения обязаны остаться в очереди.
    """
    called = []
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: called.append(a) or (0, 0))
    report = settle_file("store-1", archive([_offer("A")]), _Verdict(ok=False))
    assert called == []
    assert not report.ok and report.alarms


def test_catalog_wipe_is_caught_before_anything_is_settled(archive, monkeypatch):
    """
    Прямой детектор сценария «файл погасил каталог»: активных было 1000, стало
    880. Гасить намерения в такой ситуации нельзя тем более — они понадобятся
    для восстановления.
    """
    called = []
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: called.append(a) or (0, 0))
    path = archive([_offer("A")], active_before=1000)
    report = settle_file("store-1", path, _Verdict(), snapshot=_Snapshot([_offer("A")] * 880))

    assert called == []
    assert report.active_before == 1000 and report.active_after == 880
    assert any("потеряно 120" in a for a in report.alarms)


def test_ordinary_shrink_is_not_an_alarm(archive, monkeypatch):
    """Обычная распродажа не должна будить человека: 1000 -> 950 это 5%."""
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (0, 0))
    path = archive([_offer("A")], active_before=1000)
    report = settle_file("store-1", path, _Verdict(), snapshot=_Snapshot([_offer("A")] * 950))
    assert report.alarms == []


def test_mismatch_raises_an_alarm_but_still_settles(archive, monkeypatch):
    """
    Расхождение — повод разбудить человека, но не повод бросить очередь в
    неопределённом состоянии: то, что файл довёз, закрыть всё равно надо.
    """
    monkeypatch.setattr(feed_settle, "settle_intents", lambda *a: (7, 2))
    path = archive([_offer("A", price=500)])
    report = settle_file("store-1", path, _Verdict(), snapshot=_Snapshot([_offer("A", price=590)]))
    assert report.settled == 7 and report.kept_queued == 2
    assert report.mismatches and report.alarms
    assert report.needs_attention


# ── Закрытие намерений: главное свойство модуля ─────────────────────────────

class SettleIntentsTestCase(unittest.TestCase):
    """
    Проверяется зеркальная пара. Намерение, существовавшее на момент снимка,
    файл довёз — его гасим. Намерение, появившееся ПОСЛЕ снимка, файл нести не
    мог и вдобавок откатит — его обязаны оставить в очереди, иначе правка,
    сделанная внутри 18-минутного окна обработки, пропадёт навсегда.
    """

    def setUp(self):
        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        self._tmp = tempfile.mkdtemp()
        path = os.path.join(self._tmp, "settle.db").replace(os.sep, "/")
        self._engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self._patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _enqueue(self, sku, price, stock, desired_at, store="s1"):
        from backend.db.models import KaspiOfferSyncDB
        from sqlalchemy.orm import Session

        with Session(self._engine) as s:
            s.add(KaspiOfferSyncDB(
                store_id=store, sku=sku, master_sku="M1", model="Товар",
                store_suffix="001", city_id="750000000",
                desired_price=price, desired_stock=stock, desired_at=desired_at,
                priority=0, reason="test",
            ))
            s.commit()

    def _row(self, sku, store="s1"):
        from backend.db.models import KaspiOfferSyncDB
        from sqlalchemy.orm import Session

        with Session(self._engine) as s:
            return s.get(KaspiOfferSyncDB, (store, sku))

    def test_intent_the_file_carried_is_closed(self):
        self._enqueue("A", 500, 7, SNAP_AT - datetime.timedelta(minutes=5))
        declared = {"A": {"price": 500, "stock": 7.0, "available": True}}

        settled, kept = feed_settle.settle_intents("s1", declared, SNAP_AT)

        self.assertEqual((settled, kept), (1, 0))
        row = self._row("A")
        self.assertIsNone(row.desired_at)
        self.assertEqual(row.pushed_price, 500)
        # kaspi_* обнуляется намеренно: вердикт говорит "файл разобран", а не
        # "карточка выглядит так" — пусть следующая сверка перечитает факт.
        self.assertIsNone(row.kaspi_price)

    def test_intent_born_inside_the_flight_window_survives(self):
        """
        Самый дорогой случай. Снимок в 12:00, файл доехал в 12:18, а в 12:05
        автосписание выставило остаток 0. Файл вернёт 7 штук — и если погасить
        это намерение, мы будем продавать то, чего нет, до следующего цикла.
        """
        self._enqueue("A", 500, 0, SNAP_AT + datetime.timedelta(minutes=5))
        declared = {"A": {"price": 500, "stock": 7.0, "available": True}}

        settled, kept = feed_settle.settle_intents("s1", declared, SNAP_AT)

        self.assertEqual((settled, kept), (0, 1))
        row = self._row("A")
        self.assertIsNotNone(row.desired_at)   # осталось в очереди
        self.assertEqual(row.desired_stock, 0)

    def test_value_the_file_did_not_carry_is_not_closed(self):
        """Намерение старое, но значение разошлось — гасим только дословно довезённое."""
        self._enqueue("A", 500, 7, SNAP_AT - datetime.timedelta(minutes=5))
        declared = {"A": {"price": 999, "stock": 7.0, "available": True}}

        settled, kept = feed_settle.settle_intents("s1", declared, SNAP_AT)

        self.assertEqual((settled, kept), (0, 1))
        self.assertIsNotNone(self._row("A").desired_at)

    def test_offer_absent_from_the_file_is_not_closed(self):
        self._enqueue("A", 500, 7, SNAP_AT - datetime.timedelta(minutes=5))
        settled, kept = feed_settle.settle_intents("s1", {}, SNAP_AT)
        self.assertEqual((settled, kept), (0, 1))

    def test_other_store_is_untouched(self):
        self._enqueue("A", 500, 7, SNAP_AT - datetime.timedelta(minutes=5), store="s2")
        settled, kept = feed_settle.settle_intents("s1", {"A": {"price": 500, "stock": 7.0, "available": True}}, SNAP_AT)
        self.assertEqual((settled, kept), (0, 0))
        self.assertIsNotNone(self._row("A", store="s2").desired_at)
