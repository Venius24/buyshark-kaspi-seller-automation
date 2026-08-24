"""
Тесты отправки прайс-листа (этап 4).

Модуль пишет в живой каталог, и цена ошибки — снятые с продажи карточки.
Поэтому большая часть тестов здесь проверяет НЕ отправку, а отказ: каждый тест
описывает способ отправить лишнее и требует, чтобы до сети дело не дошло.
Фикстура `kaspi` считает POST'ы, так что «не отправилось» проверяется фактом, а
не доверием к коду.
"""
import datetime

import pytest

from backend.dumper import feed_uploader
from backend.dumper.feed_uploader import (
    FeedUploadFailed,
    FeedUploadRefused,
    UploadVerdict,
    send_plan,
    wait_for_verdict,
)

MERCHANT = "20186986"


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Snapshot:
    def __init__(self, merchant_id=MERCHANT, age_seconds=0):
        self.merchant_id = merchant_id
        self.store_id = "store-1"
        self.taken_at = datetime.datetime.now() - datetime.timedelta(seconds=age_seconds)
        self.archived_count = 0
        self.size = 1110
        self.notes = []


class _Plan:
    def __init__(self, total=1110, changed=0, missing=None, age_seconds=0, merchant_id=MERCHANT,
                 blockers=None):
        self.snapshot = _Snapshot(merchant_id, age_seconds)
        self.xml = b"<kaspi_catalog/>"
        self.missing_from_file = missing or []
        self.blockers = blockers or []
        self.identity_source = "accepted-file-1"
        self._total = total
        self._changed = changed

    @property
    def total(self):
        return self._total

    @property
    def changed_offers(self):
        return self._changed


def _card(file_id="new-1", status="FLUSHED", distinct=1110, processed=None, rejected=0,
          not_changed=None):
    processed = distinct if processed is None else processed
    not_changed = processed if not_changed is None else not_changed
    return {
        "id": file_id,
        "originalFileName": "buyshark.xml",
        "status": status,
        "distinctOffersCount": distinct,
        "processedOffersCount": processed,
        "rejectedOffersCount": rejected,
        "notMappedOffersCount": 0,
        "notChangedOffersCount": not_changed,
        "createdDate": "2026-08-23T23:09:00",
    }


@pytest.fixture
def kaspi(monkeypatch):
    """Подменяет сеть; помнит, что и куда ушло."""
    state = {
        "posts": [],
        "limit": {"limits": {"FILE": 2, "OFFER": 250}},
        "cards": [_card("old-0"), _card("new-1")],  # до отправки / после
        "upload_status": 200,
        "_calls": 0,
    }

    def fake_request(method, url, store_id=None, headers=None, params=None,
                     files=None, timeout=None, **kw):
        if method == "POST" and url == feed_uploader.UPLOAD_URL:
            state["posts"].append({"files": files, "params": params, "headers": headers})
            return _Resp(state["upload_status"], {"status": "OK"}, "ok")
        if url == feed_uploader.LIMIT_URL:
            return _Resp(200, state["limit"])
        if url == feed_uploader.LAST_FILE_URL:
            idx = min(state["_calls"], len(state["cards"]) - 1)
            # первый вызов — «до отправки», дальше по списку
            if state["posts"]:
                state["_calls"] += 1
                idx = min(state["_calls"], len(state["cards"]) - 1)
            card = state["cards"][idx]
            return _Resp(200, card) if card else _Resp(404)
        raise AssertionError("неожиданный запрос: {} {}".format(method, url))

    monkeypatch.setattr("backend.state_helpers._mc_api_request", fake_request)
    monkeypatch.setattr(feed_uploader.time, "sleep", lambda *_: None)
    monkeypatch.setenv("XML_FEED_UPLOAD_ENABLED", "1")
    return state


# ── Замки: до сети дело доходить не должно ──────────────────────────────────

def test_disabled_by_default(kaspi, monkeypatch):
    monkeypatch.delenv("XML_FEED_UPLOAD_ENABLED", raising=False)
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert "XML_FEED_UPLOAD_ENABLED" in str(e.value)
    assert kaspi["posts"] == []


def test_requires_confirm(kaspi):
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(_Plan(), "store-1", MERCHANT)
    assert "не подтверждена" in str(e.value)
    assert kaspi["posts"] == []


def test_wrong_confirm_is_not_enough(kaspi):
    with pytest.raises(FeedUploadRefused):
        send_plan(_Plan(), "store-1", MERCHANT, confirm="да")
    assert kaspi["posts"] == []


def test_refuses_to_send_a_plan_built_for_another_merchant(kaspi):
    """Файл чужого каталога переписал бы этот каталог целиком."""
    plan = _Plan(merchant_id="99999999")
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(plan, "store-1", MERCHANT, confirm="99999999")
    assert "чужой каталог" in str(e.value)
    assert kaspi["posts"] == []


def test_refuses_incomplete_plan(kaspi):
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(_Plan(missing=["SKU-1"]), "store-1", MERCHANT, confirm=MERCHANT)
    assert "не хватает" in str(e.value)
    assert kaspi["posts"] == []


def test_refuses_empty_plan(kaspi):
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(_Plan(total=0), "store-1", MERCHANT, confirm=MERCHANT)
    assert "гасит весь каталог" in str(e.value)
    assert kaspi["posts"] == []


def test_refuses_skus_with_invisible_characters(kaspi):
    """
    Артикул с краевым табом файл адресовать не может: Kaspi обрежет край, не
    найдёт такой артикул и снимет оффер с продажи (живьём 2026-08-24 так ушли в
    архив H477-0009 и H477-0017). Замок безусловный — allow_changes его не
    снимает, потому что это не риск, а гарантированная потеря карточек.
    """
    for kwargs in ({}, {"allow_changes": True}):
        with pytest.raises(FeedUploadRefused) as e:
            send_plan(_Plan(blockers=["H477-0009	"]), "store-1", MERCHANT,
                      confirm=MERCHANT, **kwargs)
        # Сообщение обязано показывать, ГДЕ символ: в кабинете его не видно.
        assert "H477-0009<TAB>" in str(e.value)
    assert kaspi["posts"] == []


def test_unmapped_offers_are_a_failure_not_a_success(kaspi, monkeypatch):
    """
    `notMapped` — это карточки, которые Kaspi не нашла по артикулу из файла и
    потому сняла с продажи. Живая отправка 2026-08-24 отчиталась
    `FLUSHED, 1097/1097, rejected 0` при `notMapped: 2`, и прежняя проверка
    считала это успехом, хотя два товара уже лежали в архиве.
    """
    card = _card(distinct=1110, processed=1110)
    card["notMappedOffersCount"] = 2
    monkeypatch.setattr(feed_uploader, "wait_for_verdict",
                        lambda *a, **k: feed_uploader._verdict_from_card(card))
    with pytest.raises(feed_uploader.FeedUploadFailed) as e:
        send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert "не смаплено 2" in str(e.value)


def test_refuses_stale_snapshot(kaspi):
    """Старый снимок откатил бы правки, сделанные в кабинете руками."""
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(_Plan(age_seconds=900), "store-1", MERCHANT, confirm=MERCHANT)
    assert "пересобрать план" in str(e.value)
    assert kaspi["posts"] == []


def test_refuses_changes_on_idempotent_send(kaspi):
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(_Plan(changed=3), "store-1", MERCHANT, confirm=MERCHANT)
    assert "allow_changes" in str(e.value)
    assert kaspi["posts"] == []


def test_refuses_when_file_quota_exhausted(kaspi):
    kaspi["limit"] = {"limits": {"FILE": 0}}
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert "квоты FILE" in str(e.value)
    assert kaspi["posts"] == []


# ── Отправка ────────────────────────────────────────────────────────────────

def test_happy_path(kaspi):
    verdict = send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert verdict.ok
    assert verdict.file_id == "new-1"
    assert len(kaspi["posts"]) == 1


def test_sends_multipart_without_hardcoded_content_type(kaspi):
    """Захардкоженный Content-Type ломает multipart молча — boundary ставит requests."""
    send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    post = kaspi["posts"][0]
    assert feed_uploader.UPLOAD_FIELD in post["files"]
    name, payload, ctype = post["files"][feed_uploader.UPLOAD_FIELD]
    assert name.endswith(".xml") and payload == b"<kaspi_catalog/>"
    assert not any(k.lower() == "content-type" for k in post["headers"])
    # `m` тут не работает: Kaspi отвечает 400 "Required parameter 'merchantUid'
    # is not present" (проверено живой отправкой 2026-08-24).
    assert post["params"] == {"merchantUid": MERCHANT}


def test_upload_http_error_is_reported_as_untouched_catalog(kaspi):
    kaspi["upload_status"] = 400
    with pytest.raises(FeedUploadFailed) as e:
        send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert "Каталог не тронут" in str(e.value)


def test_rejected_offers_fail_the_send(kaspi):
    kaspi["cards"] = [_card("old-0"), _card("new-1", rejected=4)]
    with pytest.raises(FeedUploadFailed) as e:
        send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert "не так, как ожидалось" in str(e.value)


def test_offer_count_mismatch_fails(kaspi):
    kaspi["cards"] = [_card("old-0"), _card("new-1", distinct=900)]
    with pytest.raises(FeedUploadFailed) as e:
        send_plan(_Plan(total=1110), "store-1", MERCHANT, confirm=MERCHANT)
    assert "проверить каталог немедленно" in str(e.value)


def test_idempotent_send_that_actually_changed_something_fails(kaspi):
    """Пустышка, что-то поменявшая, означает: наш факт расходился с фактом Kaspi."""
    kaspi["cards"] = [_card("old-0"), _card("new-1", not_changed=1105)]
    with pytest.raises(FeedUploadFailed) as e:
        send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert "изменила 5 офферов" in str(e.value)


def test_real_send_allows_changes(kaspi):
    kaspi["cards"] = [_card("old-0"), _card("new-1", not_changed=1105)]
    verdict = send_plan(_Plan(changed=5), "store-1", MERCHANT, confirm=MERCHANT,
                        allow_changes=True)
    assert verdict.ok


# ── Вердикт ─────────────────────────────────────────────────────────────────

def test_verdict_waits_for_a_new_file_id(kaspi, monkeypatch):
    """Карточка прошлого файла лежит там же — принять её за свой результат легко."""
    seq = [_card("old-0"), _card("old-0"), _card("new-1", status="PROCESSING"),
           _card("new-1")]
    calls = {"i": 0}

    def fake_fetch(store_id, muid):
        card = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return card

    monkeypatch.setattr(feed_uploader, "fetch_last_file", fake_fetch)
    verdict = wait_for_verdict("store-1", MERCHANT, previous_file_id="old-0", timeout=30, poll=0)
    assert verdict.file_id == "new-1" and verdict.status == "FLUSHED"


def test_verdict_times_out_without_a_new_card(monkeypatch):
    monkeypatch.setattr(feed_uploader, "fetch_last_file", lambda *a, **k: _card("old-0"))
    monkeypatch.setattr(feed_uploader.time, "sleep", lambda *_: None)
    ticks = iter([0, 1, 2, 999])
    monkeypatch.setattr(feed_uploader.time, "monotonic", lambda: next(ticks))
    with pytest.raises(FeedUploadFailed) as e:
        wait_for_verdict("store-1", MERCHANT, previous_file_id="old-0", timeout=10, poll=0)
    assert "НЕ отправлять повторно" in str(e.value)


def test_verdict_ok_requires_everything_to_line_up():
    assert UploadVerdict("i", "f", "FLUSHED", distinct=10, processed=10, rejected=0).ok
    assert not UploadVerdict("i", "f", "FLUSHED", distinct=10, processed=9).ok
    assert not UploadVerdict("i", "f", "FLUSHED", distinct=10, processed=10, rejected=1).ok
    assert not UploadVerdict("i", "f", "ERROR", distinct=10, processed=10).ok
    assert not UploadVerdict("i", "f", "FLUSHED", distinct=0, processed=0).ok


def test_refuses_when_identity_not_verified(kaspi):
    """Без сверки с принятым файлом бренды уедут display-именами вместо слагов."""
    plan = _Plan()
    plan.identity_source = None
    with pytest.raises(FeedUploadRefused) as e:
        send_plan(plan, "store-1", MERCHANT, confirm=MERCHANT)
    assert "не сверены" in str(e.value)
    assert kaspi["posts"] == []


def test_parsing_is_not_mistaken_for_a_finished_file(monkeypatch):
    """
    Первая живая отправка 2026-08-24 упала с «обработано 0 из 0»: Kaspi отдала
    статус PARSING, а список «ещё идёт» был неполным — любой незнакомый статус
    считался финалом. Ждать надо до явного финала, а не до отсутствия в списке.
    """
    cards = iter([
        _card("new-1", status="PARSING", distinct=0, processed=0, not_changed=0),
        _card("new-1", status="PARSED", distinct=1097, processed=704, not_changed=673),
        _card("new-1", status="FLUSHED", distinct=1097, processed=1097, not_changed=1097),
    ])
    monkeypatch.setattr(feed_uploader, "fetch_last_file", lambda *a, **k: next(cards))
    monkeypatch.setattr(feed_uploader.time, "sleep", lambda *_: None)
    verdict = wait_for_verdict("store-1", MERCHANT, previous_file_id="old-0", timeout=30, poll=0)
    assert verdict.status == "FLUSHED" and verdict.processed == 1097


def test_end_date_closes_the_wait_even_on_an_unknown_status(monkeypatch):
    """Признак финала не только в статусе: проставленный endDate тоже терминален."""
    card = _card("new-1", status="СОВСЕМ_НОВЫЙ_СТАТУС", distinct=5, processed=5)
    card["endDate"] = "2026-08-24T22:40:00"
    monkeypatch.setattr(feed_uploader, "fetch_last_file", lambda *a, **k: card)
    monkeypatch.setattr(feed_uploader.time, "sleep", lambda *_: None)
    verdict = wait_for_verdict("store-1", MERCHANT, previous_file_id="old-0", timeout=30, poll=0)
    assert verdict.is_terminal and verdict.ended


# ── Архив отправленного файла (этап 5A) ─────────────────────────────────────

def test_sent_file_is_archived_before_the_verdict_is_known(kaspi, monkeypatch, tmp_path):
    """
    Файл кладётся на диск сразу после приёма, а не после вердикта: обработка
    идёт минутами, и падение процесса в это время не должно оставить нас без
    ответа на вопрос «что мы вообще объявили Kaspi».
    """
    monkeypatch.setattr(feed_uploader, "_archive_dir", lambda store_id: tmp_path)

    seen = {}

    def _spy_wait(*a, **k):
        # На момент ожидания вердикта файл уже обязан лежать.
        seen["files"] = sorted(p.name for p in tmp_path.iterdir())
        return feed_uploader._verdict_from_card(_card())

    monkeypatch.setattr(feed_uploader, "wait_for_verdict", _spy_wait)
    send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)

    xml_name = next(n for n in seen["files"] if n.endswith(".xml"))
    assert seen["files"] == [xml_name, xml_name + ".meta.json"]
    assert (tmp_path / xml_name).read_bytes() == b"<kaspi_catalog/>"


def test_archive_meta_carries_what_settlement_will_need(kaspi, monkeypatch, tmp_path):
    """
    Meta — вход для сверки после вердикта. Без snapshot_at нельзя отличить
    правку, сделанную ДО снимка (её везёт файл), от правки внутри окна полёта
    (её файл откатит, и её надо вернуть в очередь).
    """
    import json

    monkeypatch.setattr(feed_uploader, "_archive_dir", lambda store_id: tmp_path)
    monkeypatch.setattr(feed_uploader, "wait_for_verdict",
                        lambda *a, **k: feed_uploader._verdict_from_card(_card()))
    send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)

    meta = json.loads(next(tmp_path.glob("*.meta.json")).read_text(encoding="utf-8"))
    assert meta["snapshot_at"] and meta["offers"] == 1110
    assert meta["active_before"] == 1110      # детектор «погасили каталог»
    assert meta["identity_source"] == "accepted-file-1"
    assert meta["file_id"] == "new-1"         # дописано уже после вердикта
    assert meta["verdict"]["ok"] is True


def test_a_broken_archive_does_not_abort_a_good_upload(kaspi, monkeypatch, tmp_path):
    """
    Архив — наш внутренний журнал. Если диск не пишется, это повод для ошибки в
    логе, но не повод считать, что с уже принятым Kaspi файлом что-то не так.
    """
    def _boom(*a, **k):
        raise OSError("диск только для чтения")

    monkeypatch.setattr(feed_uploader, "archive_sent_file", _boom)
    monkeypatch.setattr(feed_uploader, "wait_for_verdict",
                        lambda *a, **k: feed_uploader._verdict_from_card(_card()))
    verdict = send_plan(_Plan(), "store-1", MERCHANT, confirm=MERCHANT)
    assert verdict.ok
