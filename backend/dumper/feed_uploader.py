"""
Отправка прайс-листа файлом — этап 4 гибридного демпинга.

Это единственный модуль фазы, который ПИШЕТ в Kaspi, поэтому вся его логика —
про то, как не отправить лишнего. Один принятый файл переписывает состояние
всего каталога разом: неполный файл гасит недостающее (так владелец потерял
~798 карточек), а откатить это можно только вторым полным файлом.

Замков три, и они намеренно разной природы — чтобы одна ошибка не сняла все:

  1. `XML_FEED_UPLOAD_ENABLED=1` — рубильник окружения. По умолчанию выключен,
     то есть свежий деплой физически не может ничего отправить.
  2. `confirm=<merchant_id>` — осознанное действие вызывающего. Строка, которую
     нельзя получить случайно: никакой цикл, ретрай или дефолт её не подставит.
  3. План проверяется заново прямо перед отправкой (полнота, свежесть, состав).
     Проверка на этапе 3 могла быть сделана десять минут назад — за это время
     каталог мог измениться, а мы бы отправили вчерашний снимок поверх сегодня.

Про идемпотентность. Первая отправка должна менять НОЛЬ офферов: смысл в том,
чтобы проверить транспорт, а не результат — если что-то пойдёт не так, менять
будет нечего. Поэтому `allow_changes=False` по умолчанию, и план с изменениями
отвергается.

Про квоту. Файл идёт по счётчику `FILE: 2 / 30m`, отдельному от `OFFER: 250`,
и это единственный известный способ обновить каталог целиком: JSON-путём 1110
офферов заняли бы больше двух часов.
"""
from dataclasses import dataclass
from typing import Optional
import datetime
import logging
import os
import time

logger = logging.getLogger(__name__)

BASE = "https://mc.shop.kaspi.kz/pricefeed/upload"
UPLOAD_URL = f"{BASE}/merchant/upload"
LAST_FILE_URL = f"{BASE}/merchant/files/last"
LIMIT_URL = f"{BASE}/merchant/upload/limit/settings"

# Имя поля multipart. Бандл кабинета отдаёт FormData, но имя поля из него не
# вычитано — проверяется первой живой отправкой. Ошибка тут безопасна: неверное
# имя даёт 400 и НИЧЕГО не меняет в каталоге. Переопределяется env'ом, чтобы
# подобрать его на месте, не выкатывая код.
UPLOAD_FIELD = os.getenv("XML_FEED_UPLOAD_FIELD", "file")

# Снимок старше этого отправлять нельзя: за это время владелец мог поправить
# цену руками в кабинете, и файл откатил бы правку молча.
MAX_PLAN_AGE_SECONDS = 300

# Сколько ждать, пока Kaspi дожуёт файл (статус PENDING/PROCESSING -> FLUSHED).
VERDICT_TIMEOUT_SECONDS = 180
VERDICT_POLL_SECONDS = 5

_MULTIPART_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://kaspi.kz",
    "Referer": "https://kaspi.kz/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Auth-Version": "3",
}
# Content-Type здесь НЕТ намеренно: requests обязан выставить его сам вместе с
# boundary. Захардкоженный application/json ломает multipart молча.


class FeedUploadRefused(Exception):
    """Отправка не начиналась: не сошлись замки или план. Каталог не тронут."""


class FeedUploadFailed(Exception):
    """Отправка состоялась, но результат не тот, которого ждали."""


@dataclass
class UploadVerdict:
    """Карточка файла из `files/last` — что Kaspi реально сделала с каталогом."""

    file_id: Optional[str]
    file_name: Optional[str]
    status: Optional[str]
    distinct: int = 0
    processed: int = 0
    rejected: int = 0
    not_mapped: int = 0
    not_changed: int = 0
    created: Optional[str] = None
    raw: Optional[dict] = None

    @property
    def ok(self) -> bool:
        return (
            self.status == "FLUSHED"
            and self.rejected == 0
            and self.distinct > 0
            and self.processed == self.distinct
        )

    def summary(self) -> str:
        return (
            f"файл {self.file_name} ({self.file_id}): статус {self.status}, "
            f"офферов {self.distinct}, обработано {self.processed}, "
            f"отклонено {self.rejected}, не смаплено {self.not_mapped}, "
            f"без изменений {self.not_changed}"
        )


def _verdict_from_card(card: dict) -> UploadVerdict:
    def num(key):
        try:
            return int(card.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return UploadVerdict(
        file_id=card.get("id"),
        file_name=card.get("originalFileName"),
        status=card.get("status"),
        distinct=num("distinctOffersCount"),
        processed=num("processedOffersCount"),
        rejected=num("rejectedOffersCount"),
        not_mapped=num("notMappedOffersCount"),
        not_changed=num("notChangedOffersCount"),
        created=card.get("createdDate"),
        raw=card,
    )


def fetch_last_file(store_id, merchant_uid: str) -> Optional[dict]:
    """Карточка последнего файла. Только чтение, квоту не тратит."""
    from backend.state_helpers import _mc_api_request

    resp = _mc_api_request(
        "GET", LAST_FILE_URL, store_id=store_id,
        headers=_MULTIPART_HEADERS, params={"m": merchant_uid}, timeout=20,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise FeedUploadFailed(f"files/last вернул {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError:
        return None


def file_quota_left(store_id, merchant_uid: str) -> Optional[int]:
    """
    Сколько файлов ещё можно загрузить в текущем окне.

    Ручка отдаёт потолок (`limits.FILE`), а не остаток, поэтому это верхняя
    оценка. Нужна не для точного учёта, а чтобы не долбиться в заведомо
    исчерпанное окно и не сжигать попытку.
    """
    from backend.state_helpers import _mc_api_request

    try:
        resp = _mc_api_request(
            "GET", LIMIT_URL, store_id=store_id,
            headers=_MULTIPART_HEADERS, params={"m": merchant_uid}, timeout=15,
        )
        if resp.status_code != 200:
            return None
        return int((resp.json().get("limits") or {}).get("FILE"))
    except Exception as err:
        logger.warning("[FeedUpload] Не удалось прочитать лимит FILE: %r", err)
        return None


def _post_file(store_id, merchant_uid: str, xml: bytes, filename: str):
    from backend.state_helpers import _mc_api_request

    return _mc_api_request(
        "POST", UPLOAD_URL, store_id=store_id,
        headers=_MULTIPART_HEADERS,
        params={"m": merchant_uid},
        files={UPLOAD_FIELD: (filename, xml, "text/xml")},
        timeout=120,
    )


def wait_for_verdict(
    store_id,
    merchant_uid: str,
    previous_file_id: Optional[str],
    timeout: int = VERDICT_TIMEOUT_SECONDS,
    poll: int = VERDICT_POLL_SECONDS,
) -> UploadVerdict:
    """
    Дождаться карточки НОВОГО файла в терминальном статусе.

    Сверка идёт по id, а не по времени: карточка прошлого файла лежит там же, и
    без сверки мы бы радостно приняли её счётчики за результат своей отправки.
    """
    deadline = time.monotonic() + timeout
    last_seen: Optional[UploadVerdict] = None
    while time.monotonic() < deadline:
        card = fetch_last_file(store_id, merchant_uid)
        if card:
            verdict = _verdict_from_card(card)
            if verdict.file_id != previous_file_id:
                last_seen = verdict
                if verdict.status not in ("PENDING", "PROCESSING", "UPLOADED", None):
                    return verdict
        time.sleep(poll)
    if last_seen:
        raise FeedUploadFailed(
            f"файл принят, но за {timeout}с не дошёл до терминального статуса — {last_seen.summary()}"
        )
    raise FeedUploadFailed(
        f"за {timeout}с в кабинете не появилось новой карточки файла — "
        "неизвестно, что произошло; НЕ отправлять повторно, сначала посмотреть кабинет"
    )


def send_plan(
    plan,
    store_id,
    merchant_uid: str,
    confirm: str = "",
    allow_changes: bool = False,
    filename: Optional[str] = None,
) -> UploadVerdict:
    """
    Отправить файл из готового плана (`feed_plan.build_feed_plan`).

    confirm обязан совпасть с merchant_id магазина — это не защита от злого
    умысла, а защита от случайного вызова: строку нельзя получить по умолчанию.

    allow_changes=False (этап 4): план, меняющий хоть один оффер, отвергается.

    Бросает FeedUploadRefused до отправки и FeedUploadFailed после неё.
    """
    if os.getenv("XML_FEED_UPLOAD_ENABLED", "0") != "1":
        raise FeedUploadRefused(
            "отправка фида выключена: XML_FEED_UPLOAD_ENABLED != 1"
        )

    expected_merchant = str(plan.snapshot.merchant_id)
    if str(confirm) != expected_merchant:
        raise FeedUploadRefused(
            "отправка не подтверждена: нужен confirm=<merchant_id> магазина"
        )
    if str(merchant_uid) != expected_merchant:
        raise FeedUploadRefused(
            f"план собран для мерчанта {expected_merchant}, а отправляется в {merchant_uid} — "
            "файл переписал бы чужой каталог"
        )

    # Полнота. На этапе 3 это уже проверялось, но между проверкой и отправкой
    # мог пройти код, а инвариант обязан держаться в момент записи.
    if plan.missing_from_file:
        raise FeedUploadRefused(
            f"в файле не хватает {len(plan.missing_from_file)} офферов — Kaspi снимет их с продажи"
        )
    if not plan.total:
        raise FeedUploadRefused("в файле нет офферов — такой файл гасит весь каталог")

    age = (datetime.datetime.now() - plan.snapshot.taken_at).total_seconds()
    if age > MAX_PLAN_AGE_SECONDS:
        raise FeedUploadRefused(
            f"снимку {int(age)}с (порог {MAX_PLAN_AGE_SECONDS}с) — за это время каталог мог "
            "измениться, файл откатил бы чужие правки; пересобрать план"
        )

    if not allow_changes and plan.changed_offers:
        raise FeedUploadRefused(
            f"план меняет {plan.changed_offers} офферов, а это идемпотентная отправка "
            "(этап 4). Для боевой отправки нужен allow_changes=True"
        )

    quota = file_quota_left(store_id, merchant_uid)
    if quota is not None and quota <= 0:
        raise FeedUploadRefused("окно квоты FILE исчерпано — отправка сожгла бы попытку впустую")

    before = fetch_last_file(store_id, merchant_uid)
    previous_id = (before or {}).get("id")

    name = filename or "buyshark-{}-{:%Y%m%d-%H%M%S}.xml".format(
        expected_merchant, datetime.datetime.now()
    )
    logger.warning(
        "[FeedUpload] ОТПРАВКА ФАЙЛА: %s, офферов %s, изменений %s, предыдущий файл %s",
        name, plan.total, plan.changed_offers, previous_id,
    )

    resp = _post_file(store_id, merchant_uid, plan.xml, name)
    if resp.status_code not in (200, 201, 202):
        raise FeedUploadFailed(
            f"upload вернул {resp.status_code}: {resp.text[:300]}. Каталог не тронут "
            f"(при неверном имени поля multipart ждать именно 400 — см. XML_FEED_UPLOAD_FIELD)"
        )

    verdict = wait_for_verdict(store_id, merchant_uid, previous_id)
    logger.warning("[FeedUpload] Вердикт: %s", verdict.summary())

    if not verdict.ok:
        raise FeedUploadFailed(
            "файл обработан не так, как ожидалось — " + verdict.summary()
        )
    if verdict.distinct != plan.total:
        raise FeedUploadFailed(
            f"Kaspi увидела в файле {verdict.distinct} офферов, а мы клали {plan.total} — "
            "состав разошёлся, проверить каталог немедленно"
        )
    if not allow_changes and verdict.not_changed != verdict.processed:
        # Идемпотентная отправка обязана быть пустышкой. Если Kaspi что-то
        # поменяла — наш «факт» расходился с её фактом, и это надо понять
        # ДО того, как файлу доверят настоящие изменения.
        logger.error(
            "[FeedUpload] Идемпотентная отправка изменила %s офферов из %s",
            verdict.processed - verdict.not_changed, verdict.processed,
        )
        raise FeedUploadFailed(
            f"отправка задумывалась как пустышка, но Kaspi изменила "
            f"{verdict.processed - verdict.not_changed} офферов — наш снимок расходился с фактом"
        )
    return verdict
