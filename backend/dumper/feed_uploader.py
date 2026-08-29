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
import json
import logging
import os
import pathlib
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

# Статусы, после которых счётчики карточки уже не изменятся. Всё, чего тут нет
# (включая незнакомые новые), означает «ещё обрабатывается» — см. подробности в
# UploadVerdict.is_terminal.
TERMINAL_STATUSES = frozenset({"FLUSHED", "ERROR", "FAILED", "REJECTED", "CANCELLED"})

# Сколько ждать, пока Kaspi дожуёт файл. Живой замер 2026-08-24 на 1097 офферах:
# разбор идёт рывками с долгими паузами, за 12 минут дошло только до 748 — так что
# прежние 180с не хватало даже близко; целиком файл занял ~18 минут.
#
# Запас взят кратный, а не впритык, потому что цена ошибки несимметрична.
# Досрочно сдаться — худший из исходов: файл-то Kaspi всё равно дожуёт и
# применит, но мы об этом не узнаем, выключим фид и останемся с каталогом в
# неизвестном состоянии. Лишнее ожидание не стоит ничего: опрос идёт в
# отдельном потоке и никого не держит. Время разбора растёт с размером
# каталога, а замер у нас ровно один — на ~1100 офферах.
VERDICT_TIMEOUT_SECONDS = 90 * 60
VERDICT_POLL_SECONDS = 15

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
    ended: Optional[str] = None
    raw: Optional[dict] = None
    # Куда лёг отправленный файл. Нужен сверке после вердикта: чтобы понять,
    # что Kaspi сделала с каталогом, надо знать, что мы объявили по каждому
    # офферу. Заполняется только в send_plan — карточка файла про это не знает.
    archive_path: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        """
        Обработка завершена — дальше счётчики не изменятся.

        Раньше терминальным считался любой статус, которого нет в списке «ещё
        идёт», и незнакомый `PARSING` был принят за готовый результат: первая
        живая отправка 2026-08-24 упала с «обработано 0 из 0», хотя файл в этот
        момент только начали разбирать. Поэтому список перевёрнут: выходим
        только по явно известному финалу, всё остальное — ждать.

        `endDate` — самостоятельный признак финала: Kaspi проставляет его,
        когда дожевала файл, и на него можно опереться при новом статусе.
        """
        return bool(self.ended) or self.status in TERMINAL_STATUSES

    @property
    def accepted(self) -> bool:
        """
        Файл принят и разобран целиком. НЕ означает «ничего не потеряно».

        Это условие продолжения работы, а не признак успеха: разбор состоялся,
        значит есть что сверять с каталогом. Всё, что Kaspi сделала с
        карточками, выясняется дальше — в `feed_settle`, по свежему снимку.
        """
        return (
            self.status == "FLUSHED"
            and self.rejected == 0
            and self.distinct > 0
            and self.processed == self.distinct
        )

    @property
    def ok(self) -> bool:
        """
        Идеальный исход: файл принят и все объявленные офферы нашли карточку.

        `not_mapped` тут появился дорого. Первая живая отправка 2026-08-24
        отчиталась `FLUSHED, 1097/1097, rejected 0` — по этой проверке успех, —
        но в той же карточке стояло `notMappedOffersCount: 2`, и это были два
        реально снятых с продажи товара.

        ОДНАКО рубильником это свойство больше не служит, и вот почему.
        `notMapped` смешивает два разных случая:

          (а) артикул не найден в каталоге — настоящая потеря карточки; ровно
              это случилось 26.08 с `H477-0009<TAB>`;
          (б) карточка есть, но не участвует в прайс-листе, потому что у неё
              нулевой остаток. Замерено 28.08: `X144-0001-2` со стоком 0
              отсутствовал в ACTIVE-выгрузке Kaspi ЕЩЁ ДО нашей отправки.

        Случай (б) у живого магазина будет всегда, и гашение фида по нему
        означало бы выключение после каждой отправки. Поэтому решение принимает
        `feed_settle`, глядя на КАТАЛОГ, а не на счётчик: пропажа карточек
        ловится напрямую (`_unexpected_archived`, просадка активных), и это
        строго сильнее прокси. Случай (а) он поймал бы и 26.08.

        Само значение по-прежнему пишется в meta и в отчёт — как факт, который
        объясняют, а не как приговор.
        """
        return self.accepted and self.not_mapped == 0

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
        ended=card.get("endDate"),
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
        # Именно `merchantUid`, а не `m`: соседние ручки фида разошлись в
        # неймингах (`files/last?m=`, но `upload/configuration?merchantUid=`).
        # Живая отправка 2026-08-24 вернула 400 "Required parameter
        # 'merchantUid' is not present" — контракт подтверждён самим Kaspi.
        params={"merchantUid": merchant_uid},
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
                if verdict.is_terminal:
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


def _archive_dir(store_id) -> pathlib.Path:
    base = pathlib.Path(__file__).resolve().parents[2] / "states" / "feeds" / str(store_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def archive_sent_file(store_id, name: str, xml: bytes, plan) -> pathlib.Path:
    """
    Положить отправленный файл на диск ДО того, как станет известен вердикт.

    Это не журнал «на всякий случай», а источник истины для сверки после
    вердикта: чтобы понять, что именно Kaspi применила, надо знать, что именно
    мы объявили — по каждому офферу, а не суммой. Держать эти значения в БД
    нельзя по размеру (у крупного магазина это десятки тысяч строк на каждую
    отправку), а перекачивать файл обратно у Kaspi — лишняя зависимость от неё
    ровно в тот момент, когда мы разбираемся, не сломала ли она нам каталог.

    Сохраняем именно ДО ожидания вердикта: обработка идёт минутами, и если
    процесс за это время умрёт, файл должен остаться. Имя файла — наше
    (`name`), потому что `file_id` Kaspi выдаёт только вместе с карточкой,
    то есть позже; связь с ним пишет `archive_verdict`.
    """
    path = _archive_dir(store_id) / name
    path.write_bytes(xml)
    meta = {
        "name": name,
        "store_id": str(store_id),
        "merchant_id": str(plan.snapshot.merchant_id),
        "snapshot_at": plan.snapshot.taken_at.isoformat(),
        "offers": plan.total,
        "changed_offers": plan.changed_offers,
        "identity_source": getattr(plan, "identity_source", None),
        "active_before": plan.snapshot.size,
        # Архив ДО отправки, поимённо. Без него сверка может сказать только
        # «активных стало меньше на N», а этого мало: карточка, созданная в
        # кабинете внутри окна полёта и погашенная нашим файлом как «не указана
        # в прайс-листе», в счётчике не видна вовсе.
        "archived_before": list(getattr(plan.snapshot, "archived_skus", []) or []),
    }
    path.with_suffix(path.suffix + ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def archive_verdict(path, verdict: UploadVerdict) -> None:
    """Дописать в meta итог обработки — включая `file_id`, известный лишь теперь."""
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    meta["file_id"] = verdict.file_id
    meta["verdict"] = {
        "status": verdict.status,
        "distinct": verdict.distinct,
        "processed": verdict.processed,
        "rejected": verdict.rejected,
        "not_mapped": verdict.not_mapped,
        "not_changed": verdict.not_changed,
        "ended": verdict.ended,
        "ok": verdict.ok,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def make_file_name(merchant_uid: str) -> str:
    """
    Имя отправляемого файла. Живёт отдельной функцией, потому что его обязан
    знать не только отправитель.

    Пока имя рождалось внутри `send_plan`, воркер отмечал полёт литералом
    "pending" — и всё, что ищет летящий файл ПО ИМЕНИ, промахивалось: ротация
    копий не узнавала его и сжимала прямо во время полёта (сверка после
    вердикта падала на FileNotFoundError), а придержка JSON не находила
    объявленного и молча не придерживала ничего. Замерено на Бархане
    2026-08-29. Поэтому имя генерируется ДО отправки и передаётся вниз.
    """
    return "buyshark-{}-{:%Y%m%d-%H%M%S}.xml".format(
        merchant_uid, datetime.datetime.now()
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

    # Артикулы с краевыми пробелами/табами. Файл их не адресует: Kaspi обрежет
    # края, не найдёт такой артикул в каталоге и снимет оффер с продажи как
    # отсутствующий в прайс-листе (живой случай 2026-08-24, `notMapped: 2`).
    # Замок безусловный — его не снимают ни allow_changes, ни любой другой флаг:
    # это не «рискованная правка, которую владелец подтвердил», а гарантированная
    # потеря карточек. Пока такой артикул активен, магазин работает по JSON-пути,
    # где артикул едет в теле запроса и не нормализуется.
    blockers = getattr(plan, "blockers", None)
    if blockers:
        from backend.dumper.xml_feed import describe_sku_defect

        raise FeedUploadRefused(
            "{} артикулов содержат невидимые символы по краям и не переживут приём "
            "файла — Kaspi снимет эти офферы с продажи: {}. Пока они активны, фид "
            "по этому магазину отправлять нельзя.".format(
                len(blockers), [describe_sku_defect(s) for s in blockers[:10]]
            )
        )

    age = (datetime.datetime.now() - plan.snapshot.taken_at).total_seconds()
    if age > MAX_PLAN_AGE_SECONDS:
        raise FeedUploadRefused(
            f"снимку {int(age)}с (порог {MAX_PLAN_AGE_SECONDS}с) — за это время каталог мог "
            "измениться, файл откатил бы чужие правки; пересобрать план"
        )

    # Поля идентичности. Если они не взяты из принятого Kaspi файла, значит они
    # пришли из BFF — а там бренд лежит display-именем («Волат») вместо слага
    # («volat»), и файл переписал бы бренды сотням карточек заодно с ценой.
    if not getattr(plan, "identity_source", None):
        raise FeedUploadRefused(
            "названия и бренды не сверены с принятым файлом Kaspi — отправка переписала бы "
            "поля идентичности; пересобрать план с use_accepted_identity"
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

    name = filename or make_file_name(expected_merchant)
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

    # Файл принят — кладём его на диск ПРЕЖДЕ, чем уйдём ждать вердикт: ждать
    # приходится минутами, и падение процесса в этот момент не должно оставить
    # нас без ответа на вопрос «что мы вообще объявили».
    try:
        archived = archive_sent_file(store_id, name, plan.xml, plan)
    except OSError as err:
        archived = None
        logger.error("[FeedUpload] Не удалось сохранить отправленный файл: %r", err)

    verdict = wait_for_verdict(store_id, merchant_uid, previous_id)
    logger.warning("[FeedUpload] Вердикт: %s", verdict.summary())
    if archived is not None:
        verdict.archive_path = str(archived)
        try:
            archive_verdict(archived, verdict)
        except OSError as err:
            logger.error("[FeedUpload] Не удалось дописать вердикт в meta: %r", err)

    if not verdict.accepted:
        raise FeedUploadFailed(
            "файл обработан не так, как ожидалось — " + verdict.summary()
        )
    if verdict.not_mapped:
        # Не приговор и не успех: часть объявленного не нашла карточку. Какая
        # именно и страшно ли это — видно только по каталогу, а он читается в
        # `feed_settle`. Раньше здесь летело исключение, и до осмотра каталога
        # дело не доходило вовсе: 28.08 фид выключился, хотя не потерял ничего.
        logger.warning(
            "[FeedUpload] Не смаплено %s офферов из %s — разбирается сверкой по каталогу",
            verdict.not_mapped, verdict.distinct,
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
