"""
Тесты воркера полной сверки прайс-листом (этап 5D).

Воркер — единственное место, где файл уезжает сам, без человека. Поэтому почти
все тесты здесь про МОЛЧАНИЕ: каждый описывает причину не отправлять и требует,
чтобы отправки не случилось. Отправка проверяется одним тестом, зато с полным
набором зелёных условий.
"""
import datetime

import pytest

from backend.dumper import feed_state, feed_worker


class _Snapshot:
    def __init__(self, size=1582):
        self.merchant_id = "20186986"
        self.store_id = "s1"
        self.taken_at = datetime.datetime.now()
        self.size = size
        self.archived_count = 0


class _Plan:
    def __init__(self, total=1582, changed=300, blockers=None):
        self.snapshot = _Snapshot(total)
        self.total = total
        self.changed_offers = changed
        self.blockers = blockers or []
        self.xml = b"<kaspi_catalog/>"
        self.unchanged = total - changed
        self.missing_from_file = []
        self.orphan_intents = []
        self.warnings = []
        self.diffs = []
        self.identity_source = "accepted-file-1"
        self.identity_unknown = 0


class _Store:
    store_id = "s1"
    merchant_id = "20186986"
    token = "tok"


class _QuotaState:
    def __init__(self, used=10, limit=250):
        self.used = used
        self.limit = limit


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Магазин с включённым фидом, боевой режим, план без блокеров."""
    monkeypatch.setattr(feed_state, "_state_path", lambda store_id: tmp_path / "state.json")
    monkeypatch.setenv("XML_FEED_WORKER", "1")
    feed_state.enable("s1")

    from backend.dumper import feed_plan
    from backend.services import kaspi_quota

    from backend.services import offer_sync

    box = {"plan": _Plan(), "quota": _QuotaState(), "pending": 300, "sent": []}
    monkeypatch.setattr(feed_plan, "build_feed_plan", lambda *a, **k: box["plan"])
    monkeypatch.setattr(kaspi_quota.quota, "get", lambda *a, **k: box["quota"])
    # Очередь намерений читается из БД до всякой выкачки каталога — в тестах
    # подменяем её, иначе каждый тик упирался бы в «везти нечего».
    monkeypatch.setattr(
        offer_sync, "pending_intents", lambda store_id: {i: {} for i in range(box["pending"])}
    )
    return box


def _no_send(monkeypatch):
    from backend.dumper import feed_uploader

    calls = []
    monkeypatch.setattr(feed_uploader, "send_plan", lambda *a, **k: calls.append(a))
    return calls


# ── Причины промолчать ──────────────────────────────────────────────────────

def test_disabled_store_is_left_alone(wired, monkeypatch):
    feed_state.disable("s1", "выключен вручную")
    calls = _no_send(monkeypatch)
    assert feed_worker.run_for_store(_Store())["sent"] is False
    assert calls == []


def test_a_silent_store_does_not_touch_kaspi_at_all(wired, monkeypatch):
    """
    Ради этого проверки стоят до сборки плана. build_feed_plan выкачивает ВЕСЬ
    каталог (полтораста страниц у крупного магазина), и делать это на каждом
    тике по каждому магазину — тот самый постоянный лишний трафик, за который
    IP этого VPS уже банили. Молчащий магазин обязан не стоить ни одного
    запроса к Kaspi.
    """
    from backend.dumper import feed_plan

    builds = []
    monkeypatch.setattr(feed_plan, "build_feed_plan",
                        lambda *a, **k: builds.append(a) or _Plan())
    feed_state.disable("s1", "выключен вручную")

    feed_worker.run_for_store(_Store())
    assert builds == []


def test_small_store_never_gets_a_file(wired, monkeypatch):
    """Порог 250 = размер окна OFFER: такому магазину файл не нужен."""
    wired["plan"] = _Plan(total=249)
    calls = _no_send(monkeypatch)
    result = feed_worker.run_for_store(_Store())
    assert result["sent"] is False and "меньше порога 250" in result["reason"]
    assert calls == []


def test_broken_articles_stop_the_feed_and_disable_it(wired, monkeypatch):
    """
    Артикул с невидимыми символами файл адресовать не может, и Kaspi погасит
    карточку — ровно случай 2026-08-24. Магазин снимается с фида до починки.
    """
    wired["plan"] = _Plan(blockers=["H477-0009\t"])
    calls = _no_send(monkeypatch)
    result = feed_worker.run_for_store(_Store())
    assert result["sent"] is False and "битые артикулы" in result["reason"]
    assert feed_state.is_enabled("s1") is False
    assert calls == []


def test_nothing_to_carry_means_silence(wired, monkeypatch):
    """
    Мало намерений и свободная квота — файл не нужен: JSON и дешевле, и
    быстрее, а объявлять весь каталог ради 20 правок — лишний риск.
    """
    wired["pending"] = 20
    calls = _no_send(monkeypatch)
    result = feed_worker.run_for_store(_Store())
    assert "везти нечего" in result["reason"]
    assert calls == []


def test_quota_pressure_alone_justifies_a_file(wired, monkeypatch):
    """Даже при малом числе намерений: если окно уже занято, JSON начнёт откладывать."""
    wired["pending"] = 20
    wired["quota"] = _QuotaState(used=200, limit=250)   # 80%
    assert feed_worker.why_not_now("s1", 1582, 20, 0.8) is None


def test_too_soon_after_the_previous_file(wired):
    feed_state.save("s1", settled_at=datetime.datetime.now().isoformat())
    assert "с прошлой отправки" in feed_worker.why_not_now("s1", 1582, 500, 0.1)


def test_a_file_in_flight_blocks_the_next(wired):
    feed_state.mark_sent("s1", "buyshark-1.xml", datetime.datetime.now())
    assert "в полёте" in feed_worker.why_not_now("s1", 1582, 500, 0.1)


# ── Теневой режим ───────────────────────────────────────────────────────────

def test_shadow_mode_is_the_default(wired, monkeypatch):
    """
    Без явного XML_FEED_WORKER=1 воркер только рассказывает, что отправил бы.
    Свежий деплой не может начать слать файлы сам.
    """
    monkeypatch.delenv("XML_FEED_WORKER", raising=False)
    calls = _no_send(monkeypatch)
    result = feed_worker.run_for_store(_Store())
    assert result["reason"] == "теневой режим" and result["sent"] is False
    assert calls == []


# ── Отправка ────────────────────────────────────────────────────────────────

def test_sends_when_everything_is_green(wired, monkeypatch, tmp_path):
    from backend.dumper import feed_settle, feed_uploader

    archive = tmp_path / "f.xml"
    archive.write_bytes(b"<kaspi_catalog/>")
    verdict = feed_uploader.UploadVerdict(
        file_id="f1", file_name="f.xml", status="FLUSHED",
        distinct=1582, processed=1582, not_changed=1582,
        created="2026-08-25T12:00:00", ended="2026-08-25T12:18:00",
        archive_path=str(archive),
    )
    monkeypatch.setattr(feed_uploader, "send_plan", lambda *a, **k: verdict)
    reports = []
    monkeypatch.setattr(feed_settle, "settle_file",
                        lambda *a, **k: reports.append(a) or "REPORT")

    result = feed_worker.run_for_store(_Store())

    assert result["sent"] is True and result["report"] == "REPORT"
    # Время обработки запомнено: им считается следующий такт, а не константой.
    assert feed_state.load("s1")["last_processing_seconds"] == 18 * 60


def test_the_default_pause_is_an_hour(wired):
    """
    Час, а не полчаса. Пока файл жуётся (18-20 минут), он объявляет цены,
    снятые до отправки; отправлять следующий сразу по вердикту — держать
    каталог в этом состоянии непрерывно. Между файлами должно оставаться
    время, когда магазин живёт по JSON.
    """
    assert feed_worker.MIN_PAUSE_SECONDS == 60 * 60
    feed_state.save("s1", settled_at=(
        datetime.datetime.now() - datetime.timedelta(minutes=45)
    ).isoformat())
    assert "с прошлой отправки" in feed_worker.why_not_now("s1", 1582, 500, 0.1)
def test_shadow_run_also_starts_the_pause(wired):
    """
    В тени файл не отправляется, `settled_at` не проставляется никогда — и
    воркер выкачивал ВЕСЬ каталог каждые пять минут. Замерено на Бархане
    2026-08-26: две полные выкачки за шесть минут (15:15 и 15:21). Дорогая
    здесь сборка плана, а не отправка, поэтому пауза считается и от неё.
    """
    feed_state.save("s1", last_plan_at=(
        datetime.datetime.now() - datetime.timedelta(minutes=45)
    ).isoformat())
    assert "с прошлой отправки" in feed_worker.why_not_now("s1", 1582, 500, 0.1)


def test_the_later_of_the_two_marks_wins(wired):
    """Старая отметка не должна перебивать свежую — иначе пауза обнуляется."""
    now = datetime.datetime.now()
    feed_state.save(
        "s1",
        settled_at=(now - datetime.timedelta(hours=5)).isoformat(),
        last_plan_at=(now - datetime.timedelta(minutes=10)).isoformat(),
    )
    assert "с прошлой отправки" in feed_worker.why_not_now("s1", 1582, 500, 0.1)


def test_next_pause_grows_with_real_processing_time(wired):
    """
    18 минут — замер Бархана на 1097 офферах. У каталога втрое больше обработка
    втрое дольше, и константа врала бы в опасную сторону.
    """
    assert feed_worker.next_pause_seconds("s1") == feed_worker.MIN_PAUSE_SECONDS
    feed_state.save("s1", last_processing_seconds=60 * 60)
    assert feed_worker.next_pause_seconds("s1") == 90 * 60


def test_a_failed_send_does_not_lock_the_store_forever(wired, monkeypatch):
    """
    `in_flight` блокирует следующую отправку. Если его не снять при ошибке,
    один неудачный запуск запер бы магазин навсегда.
    """
    from backend.dumper import feed_uploader

    def _boom(*a, **k):
        raise RuntimeError("Kaspi отдала 500")

    monkeypatch.setattr(feed_uploader, "send_plan", _boom)
    result = feed_worker.run_for_store(_Store())

    assert result["sent"] is False and "отправка не удалась" in result["reason"]
    assert feed_state.load("s1")["in_flight"] is None
    assert feed_state.is_enabled("s1") is False


def test_unarchived_file_disables_the_feed(wired, monkeypatch):
    """
    Файл уехал, но на диск не лёг — сверить исход невозможно. Это не повод
    считать, что всё хорошо: мы только что объявили весь каталог.
    """
    from backend.dumper import feed_uploader

    verdict = feed_uploader.UploadVerdict(
        file_id="f1", file_name="f.xml", status="FLUSHED",
        distinct=1582, processed=1582, not_changed=1582, archive_path=None,
    )
    monkeypatch.setattr(feed_uploader, "send_plan", lambda *a, **k: verdict)
    feed_worker.run_for_store(_Store())
    assert feed_state.is_enabled("s1") is False
