"""
Сверка каталога после того, как Kaspi дожевала наш прайс-лист.

Зачем это отдельный шаг, а не «получили FLUSHED и разошлись».

Файл свежий на входе и устаревший на выходе. Снимок берётся в T0, отправка
уходит через секунды, а применяет Kaspi файл ~18 минут (замер на 1097 офферах
2026-08-24). Всё это время магазин живёт: приходят заказы, срабатывает
автосписание, демпер двигает цены. Значения в файле — от T0, и к моменту, когда
Kaspi доходит до конца, часть из них уже неверна. Учащением фетча это не
лечится в принципе: сколько ни обновляй данные перед отправкой, обработка всё
равно займёт свои минуты.

Отсюда два обязательных действия по вердикту:

1. Закрыть намерения, которые файл ДЕЙСТВИТЕЛЬНО довёз, — и только их. Признак:
   намерение существовало уже на момент снимка (`desired_at <= snapshot_at`) и
   его значение совпадает с тем, что реально лежало в файле. Всё остальное
   остаётся в очереди и уезжает обычным JSON-флашем. Так окно полёта
   закрывается само, без отдельного механизма компенсации.

2. Посмотреть, что в каталоге на самом деле. Вердикт Kaspi — это отчёт о
   разборе файла, а не о состоянии магазина: первая живая отправка отчиталась
   `FLUSHED, 1097/1097, rejected 0` и при этом сняла с продажи две карточки
   (`notMapped: 2`). Поэтому после вердикта берётся свежий снимок и сравнивается
   с тем, что мы объявляли.

Чего эта сверка НЕ обнаружит, и это свойство самого транспорта, а не недоделка:
правку, сделанную человеком в кабинете внутри окна полёта. Файл вернёт значение
на момент T0, Kaspi покажет ровно то, что мы объявляли, — расхождения не будет,
хотя чужая правка потеряна. Единственная защита здесь — не держать файл в
полёте дольше необходимого.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import datetime
import json
import logging
import re

from backend.dumper import feed_state
from backend.dumper.xml_feed import unescape_attr

logger = logging.getLogger(__name__)

# Насколько может просесть число активных офферов, прежде чем это перестанет
# быть распродажей и станет похоже на «файл погасил каталог». Порог тот же, что
# у снимка (feed_snapshot.MAX_SHRINK_RATIO): 10% каталога Бархана — это ~110
# карточек, и настоящая убыль такого размера за один цикл — повод разбудить
# человека раньше, чем он сам заметит.
MAX_ACTIVE_DROP_RATIO = 0.10

# Сколько максимум держать JSON-писателя от записи по офферу, который прямо
# сейчас везёт файл. Нормальный полёт кончается раньше: `mark_settled` снимает
# `in_flight` по вердикту (замеры — 3м22с на 1099 офферах 28.08 и ~18 минут на
# 1097 24.08). Это страховка на случай, когда сверка не доехала вовсе — упал
# процесс, оборвалось ожидание вердикта. Держать намерение вечно нельзя:
# незаписанная цена дороже дублирующей записи, поэтому по истечении срока
# писатель работает как раньше.
MAX_HOLD_SECONDS = 30 * 60

# Разбор летящего файла — 480 КБ и ~1100 офферов; флашер ходит раз в 45с, и
# перечитывать его каждый раз незачем: пока файл тот же, объявленное не меняется.
_inflight_cache: dict = {}

_OFFER_RE = re.compile(r'<offer sku="([^"]*)"[^>]*>(.*?)</offer>', re.S)
_PRICE_RE = re.compile(r"<cityprice[^>]*>(\d+)</cityprice>")
_STOCK_RE = re.compile(r'stockCount="([\d.]+)"')
_AVAIL_RE = re.compile(r'available="(yes|no)"')


def _unescape(text: str) -> str:
    """Значение атрибута из сырого текста файла — включая `&#9;`.

    Сами мы символьные ссылки больше не пишем (не работают, см.
    `xml_feed._escape_attr`), но на диске лежат файлы от 2026-08-26, и сверка
    по ним должна читаться: без разбора `&#9;` она искала бы в каталоге
    строку `H477-0009&#9;` и объявила бы расхождением живой оффер.
    """
    return unescape_attr(text)


@dataclass
class SettleReport:
    """Что файл на самом деле сделал с каталогом."""

    store_id: str
    file_id: Optional[str] = None
    ok: bool = False
    settled: int = 0          # намерений закрыто файлом
    kept_queued: int = 0      # намерений оставлено: появились после снимка
    mismatches: List[dict] = field(default_factory=list)
    active_before: Optional[int] = None
    active_after: Optional[int] = None
    # Офферы, снятые Kaspi без нашего ведома — см. `_unexpected_archived`.
    unexpected_archived: List[str] = field(default_factory=list)
    # Два класса «не смапленных» — см. `classify_unmapped`.
    unmapped_expected: List[str] = field(default_factory=list)
    unmapped_unexplained: List[str] = field(default_factory=list)
    # Сколько «не смапленных» не объяснила ни одна из двух категорий. Живой
    # случай 2026-08-29: Kaspi назвала 2, обе категории пусты. Отчёт кабинета
    # показал H477-0009 и H477-0017 с ошибкой «Товар не связан» — при том что
    # обе карточки активны, с ценой и остатком, и лежат в собственной выгрузке
    # Kaspi. То есть у notMapped есть причина, которую по каталогу не видно:
    # не прицепляется строка файла, а не пропадает карточка. Вреда нет, но
    # молчать об этом нельзя — иначе «не смаплено 2 / объяснено 0» читается
    # как «всё разобрано».
    unmapped_unclassified: int = 0
    # Расхождения, объяснённые движением мира после снимка — см.
    # `_moved_after_snapshot`. В отчёте они есть, тревогой не будят.
    mismatches_explained: List[dict] = field(default_factory=list)
    alarms: List[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return bool(self.alarms) or bool(self.mismatches)

    def summary(self) -> str:
        head = "файл {}: закрыто {}, оставлено в очереди {}, расхождений {}".format(
            self.file_id, self.settled, self.kept_queued, len(self.mismatches)
        )
        if self.mismatches_explained:
            head += " (+{} объяснены правками после снимка)".format(
                len(self.mismatches_explained)
            )
        if self.unmapped_expected:
            head += ", не смаплено без остатка {}".format(len(self.unmapped_expected))
        if self.unmapped_unclassified:
            head += ", не смаплено без объяснения по каталогу {}".format(
                self.unmapped_unclassified
            )
        if self.active_before is not None and self.active_after is not None:
            head += ", активных {} -> {}".format(self.active_before, self.active_after)
        if self.alarms:
            head += ". ТРЕВОГА: " + "; ".join(self.alarms)
        return head


def parse_declared(xml_bytes: bytes) -> Dict[str, dict]:
    """
    Что мы объявили — по каждому офферу, из САМОГО отправленного файла.

    Читаем файл, а не план в памяти: между планом и отправкой лежит
    сериализация, и сверять надо с тем, что реально уехало. Ключ — артикул как
    его увидит Kaspi (`feed_sku_roundtrip`), иначе сверка разошлась бы с
    каталогом ровно на тех артикулах, из-за которых всё и затевалось.
    """
    from backend.dumper.xml_feed import feed_sku_roundtrip

    declared: Dict[str, dict] = {}
    for raw_sku, body in _OFFER_RE.findall(xml_bytes.decode("utf-8")):
        price = _PRICE_RE.search(body)
        stock = _STOCK_RE.search(body)
        avail = _AVAIL_RE.search(body)
        declared[feed_sku_roundtrip(_unescape(raw_sku))] = {
            "price": int(price.group(1)) if price else None,
            "stock": float(stock.group(1)) if stock else None,
            "available": avail.group(1) == "yes" if avail else None,
        }
    return declared


def _has_stock(declared_offer: dict) -> bool:
    """Объявляли ли мы этот оффер торгующим. None и 0.0 — одинаково «нет»."""
    return bool(declared_offer.get("stock"))


def classify_unmapped(declared: Dict[str, dict], snapshot) -> tuple:
    """
    Разделить объявленное, не попавшее в активный каталог, на два класса.

    Возвращает (необъяснённые, ожидаемые).

    Счётчик `notMappedOffersCount` говорит СКОЛЬКО, но не КАКИЕ, а списка
    артикулов Kaspi по API не отдаёт (перебраны ~12 вариантов пути, все 404;
    в кабинете такой отчёт есть). Поэтому класс восстанавливается по нашим же
    данным: что мы объявили против того, что лежит в свежем снимке.

    Ожидаемый класс — оффер, объявленный с НУЛЕВЫМ остатком. Прайс-лист живёт
    в измерении «товар торгует», а не «карточка существует»: замерено 28.08,
    `X144-0001-2` со стоком 0 отсутствовал в ACTIVE-выгрузке самой Kaspi ещё
    до нашей отправки. Такому офферу не к чему привязаться, и это не потеря —
    продавать всё равно нечего.

    Необъяснённый класс — оффер, объявленный с остатком, которого в активном
    каталоге после обработки нет. Вот это потеря, и её видно поимённо.

    Чего этот классификатор НЕ ловит: подмену артикула невидимыми символами
    (случай 26.08). После нормализации файл и каталог выглядят одинаково, и
    разность множеств молчит. Ловится это раньше и надёжнее — замком
    `blockers` в `send_plan`, который такой файл вообще не выпускает.
    """
    from backend.dumper.xml_feed import feed_sku_roundtrip

    active = {
        feed_sku_roundtrip(str(p.get("merchantSku")))
        for p in getattr(snapshot, "offers", []) or []
    }
    expected: List[str] = []
    unexplained: List[str] = []
    for sku, want in declared.items():
        if sku in active:
            continue
        (expected if not _has_stock(want) else unexplained).append(sku)
    return sorted(unexplained), sorted(expected)


def _moved_after_snapshot(store_id, snapshot_at) -> set:
    """
    Артикулы, по которым мир уехал вперёд уже ПОСЛЕ снимка файла.

    Нужны, чтобы отличить «файл не сработал» от «мир уехал вперёд». Файл несёт
    значения на момент T0 и применяется минутами; всё, что демпер или связка
    остатков решили за это время, в нём физически быть не могло. Каталог тогда
    честно показывает НЕ то, что мы объявили, — и это не сбой, а работа
    транспорта.

    Признаков ДВА, и второй не менее важен первого:

      * `desired_at > snapshot_at` — намерение родилось после снимка и всё ещё
        стоит в очереди. Уедет обычным флашем.
      * `pushed_at > snapshot_at` — намерение родилось после снимка и уже
        УЕХАЛО JSON-ом, пока файл летел. Строка при этом погашена
        (`desired_at IS NULL`), и по первому признаку она не видна вовсе.

    Второй признак стоил разбора живого прогона. 2026-08-29 файл летел с
    11:25:01 по 11:43:28; в 11:38:41 демпер решил `K471-0003-1 1087 -> 1084` и
    `X171-0016-2 1277 -> 1273`, в 11:39:37 флашер написал 41 запись — всё
    внутри полёта. К моменту сверки эти намерения были уже погашены, из
    очереди исчезли, и отчёт сказал «расхождений 54, объяснённых 0». Вывод
    «цену поставили не мы, и записи об этом у нас нет» (HANDOFF §3.82) был
    ошибкой этого запроса, а не находкой: писатель — наш собственный флашер,
    и `pushed_at` его называет поимённо (проверено по проду: 41 строка с
    `pushed_at` внутри окна полёта).

    Граница у первого признака та же, по которой `settle_intents` решает, что
    гасить (`desired_at <= snapshot_at`), и это не совпадение: закрываем и не
    будим ровно по одной и той же черте.
    """
    from sqlalchemy import or_, select

    from backend.db.models import KaspiOfferSyncDB
    from backend.services.offer_sync import _session

    session = _session()
    if not session:
        # Без БД лучше показать лишнее, чем спрятать настоящее.
        return set()
    try:
        rows = session.execute(
            select(KaspiOfferSyncDB.sku).where(
                KaspiOfferSyncDB.store_id == store_id,
                # NULL в сравнении даёт NULL и отсеивается сам — отдельная
                # проверка на пустоту тут была бы шумом.
                or_(
                    KaspiOfferSyncDB.desired_at > snapshot_at,
                    KaspiOfferSyncDB.pushed_at > snapshot_at,
                ),
            )
        ).all()
        return {str(r[0]) for r in rows}
    except Exception as err:
        logger.warning("[FeedSettle] Очередь для объяснения расхождений не прочиталась: %r", err)
        return set()
    finally:
        session.close()


def in_flight_declaration(store_id) -> tuple:
    """
    Что везёт файл, который прямо сейчас в полёте. `({}, None)` — если файла нет.

    Возвращает (объявленное по артикулам, момент снимка). Момент снимка нужен
    вызывающему не меньше самих значений: намерение, появившееся ПОСЛЕ снимка,
    файл нести не мог, и придерживать его нельзя ни при каком совпадении
    значений — иначе правка, сделанная в кабинете внутри окна полёта, не уедет
    вообще.

    Зачем это существует. Замер 28.08, окно 20:05:24 → 20:08:46: JSON-писатель
    записал в нём 81 оффер, и все 81 — теми же ценами, что вёз файл, при том
    что в том же логе стояло «квота 232/250, откладываем 45 записей». Работа
    была сделана дважды, и сожжена ровно та квота, ради экономии которой файл
    и затевался. План допускал, что экономия получится сама («фид применил
    цену → BFF её показывает → JSON молча пропустит запись»), и это верно
    только при мгновенном применении файла. На минутах полёта — нет: BFF ещё
    показывает старое, и писатель честно пишет.
    """
    state = feed_state.load(store_id)
    name = state.get("in_flight")
    if not name:
        return {}, None

    raw_snapshot = state.get("snapshot_at")
    if not raw_snapshot:
        return {}, None
    try:
        snapshot_at = datetime.datetime.fromisoformat(raw_snapshot)
    except (TypeError, ValueError):
        return {}, None

    # Страховка от зависшего `in_flight`: см. MAX_HOLD_SECONDS.
    if (datetime.datetime.now() - snapshot_at).total_seconds() > MAX_HOLD_SECONDS:
        logger.warning(
            "[FeedSettle] Файл %s числится в полёте дольше %s мин — придержка снята, "
            "писатель работает как обычно",
            name, MAX_HOLD_SECONDS // 60,
        )
        return {}, None

    cached = _inflight_cache.get(str(store_id))
    if cached and cached[0] == name:
        return cached[1], snapshot_at

    from backend.dumper.feed_uploader import _archive_dir

    try:
        declared = parse_declared((_archive_dir(store_id) / name).read_bytes())
    except (OSError, ValueError) as err:
        # Без файла придержать нечего. Это не повод ломать флаш: писатель
        # отработает как раньше, максимум продублировав работу файла.
        logger.warning("[FeedSettle] Летящий файл %s не прочитан (%r)", name, err)
        return {}, None

    _inflight_cache[str(store_id)] = (name, declared)
    return declared, snapshot_at


def load_meta(archive_path) -> dict:
    meta_path = archive_path.with_suffix(archive_path.suffix + ".meta.json")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def compare_with_catalog(declared: Dict[str, dict], snapshot, ignore=None) -> List[dict]:
    """
    Объявленное против фактического. Возвращает расхождения, а не «ок/не ок».

    Сравниваются только цена и остаток: это то, ради чего файл и отправлялся.
    Оффер, которого в свежем снимке нет вовсе, — самый тяжёлый случай (карточка
    ушла из продажи), и он тоже попадает сюда, с `actual: None`.

    `ignore` — артикулы, отсутствие которых уже объяснено выше по потоку
    (`classify_unmapped`: объявлены с нулевым остатком, торговать нечем). Без
    этого исключения каждый распроданный товар давал бы расхождение, тревога
    уходила бы после каждой отправки и канал перестали бы читать — ровно тот
    исход, против которого он заводился.
    """
    from backend.dumper.xml_feed import feed_sku_roundtrip

    skip = set(ignore or ())
    actual = {
        feed_sku_roundtrip(str(p.get("merchantSku"))): p
        for p in snapshot.offers
    }
    mismatches: List[dict] = []
    for sku, want in declared.items():
        have = actual.get(sku)
        if have is None:
            if sku in skip:
                continue
            mismatches.append({"sku": sku, "field": "offer", "declared": "active", "actual": None})
            continue
        if want["price"] is not None and int(have.get("price_num") or 0) != want["price"]:
            mismatches.append({
                "sku": sku, "field": "price",
                "declared": want["price"], "actual": have.get("price_num"),
            })
        if want["stock"] is not None:
            try:
                have_stock = float(have.get("stock"))
            except (TypeError, ValueError):
                have_stock = None
            if have_stock != want["stock"]:
                mismatches.append({
                    "sku": sku, "field": "stock",
                    "declared": want["stock"], "actual": have.get("stock"),
                })
    return mismatches


def settle_intents(store_id, declared: Dict[str, dict], snapshot_at) -> tuple:
    """
    Погасить намерения, которые файл действительно довёз. Возвращает (закрыто, оставлено).

    Ключевое условие — `desired_at <= snapshot_at`. Намерение, появившееся после
    снимка, файл нести не мог: в нём лежат значения на момент T0. Если такое
    намерение погасить, правка, сделанная внутри окна полёта, будет откачена
    файлом и потеряна навсегда — а так она просто остаётся в очереди и уезжает
    ближайшим JSON-флашем.

    Второе условие — совпадение значений. Намерение могло измениться по тому же
    SKU уже после снимка; тогда `desired_at` обновился, но проверить стоит и
    само значение: гасим только то, что файл довёз дословно.

    `kaspi_*` обнуляется намеренно, ровно как в JSON-пути: вердикт означает
    «Kaspi разобрала файл», а не «карточка выглядит так». Пусть следующая
    бесплатная сверка перечитает факт, вместо того чтобы мы записали в него
    собственное предположение.
    """
    import datetime

    from backend.services.offer_sync import Priority, _session
    from backend.db.models import KaspiOfferSyncDB
    from sqlalchemy import select

    now = datetime.datetime.now()
    session = _session()
    if not session:
        logger.error("[FeedSettle] Нет сессии БД — намерения не закрыты")
        return 0, 0
    try:
        rows = session.scalars(
            select(KaspiOfferSyncDB).where(
                KaspiOfferSyncDB.store_id == store_id,
                KaspiOfferSyncDB.desired_at.isnot(None),
            )
        ).all()

        settled = kept = 0
        for row in rows:
            want = declared.get(str(row.sku))
            if want is None or row.desired_at is None or row.desired_at > snapshot_at:
                kept += 1
                continue
            if row.desired_price is not None and row.desired_price != want["price"]:
                kept += 1
                continue
            if row.desired_stock is not None and float(row.desired_stock) != want["stock"]:
                kept += 1
                continue

            row.pushed_price = want["price"]
            row.pushed_stock = int(want["stock"]) if want["stock"] is not None else None
            # Момент подтверждения, а не момент снимка: pushed_at читают как
            # «когда мы в последний раз что-то писали по этому офферу», и
            # возрастом от него меряют, пора ли перепроверять факт.
            row.pushed_at = now
            row.kaspi_price = None
            row.kaspi_stock = None
            row.kaspi_seen_at = None
            row.desired_price = None
            row.desired_stock = None
            row.desired_available = None
            row.desired_at = None
            row.reason = None
            row.priority = Priority.MINOR
            row.last_error = None
            settled += 1

        session.commit()
        return settled, kept
    except Exception as err:
        session.rollback()
        logger.exception("[FeedSettle] Закрытие намерений упало: %s", err)
        return 0, 0
    finally:
        session.close()


async def notify_settle_report(store_id, report: SettleReport) -> None:
    """
    Разбудить человека. Лог для этого не годится — его никто не читает.

    Шлём только когда есть что сказать: тревога или расхождение. Уведомление о
    каждой успешной отправке приучило бы игнорировать канал ровно к тому
    моменту, когда придёт настоящая беда.
    """
    if not report.needs_attention:
        return
    from backend.chatbot import notify_dispatch

    lines = ["🚨 *ПРАЙС-ЛИСТ: ТРЕБУЕТСЯ ВНИМАНИЕ*", ""]
    for alarm in report.alarms:
        lines.append("⚠️ {}".format(alarm))
    if report.active_before is not None and report.active_after is not None:
        lines.append("")
        lines.append("📦 Активных офферов: {} → {}".format(report.active_before, report.active_after))
    for m in report.mismatches[:5]:
        lines.append("• `{}` {}: объявили {}, в каталоге {}".format(
            m["sku"], m["field"], m["declared"], m["actual"]))
    if len(report.mismatches) > 5:
        lines.append("… и ещё {}".format(len(report.mismatches) - 5))

    try:
        await notify_dispatch.dispatch(
            str(store_id), "problems", "\n".join(lines),
            urgent=True, dedup_key="feed-settle-{}".format(report.file_id),
        )
    except Exception as err:
        logger.error("[FeedSettle] Тревога не ушла в Телеграм: %r", err)


def _unexpected_archived(meta: dict, declared: Dict[str, dict], snapshot) -> List[str]:
    """
    Офферы, уехавшие в архив за время полёта, которых мы туда не отправляли.

    Считается разностью множеств, а не счётчиком: `archived_before` пишется в
    meta перед отправкой, `archived_skus` приходит со свежим снимком. Из
    новичков в архиве вычитаются те, кого мы сами объявили снятыми
    (`available="no"`) — их гашение и есть выполненное намерение.

    Пустой `archived_before` (файл отправлен старой версией, до появления
    поля) означает «сравнить не с чем» — тогда молчим. Ложная тревога на
    каждом старом файле стоила бы дороже, чем пропуск одного цикла.

    Из новичков вычитаются ДВА множества, и второе стоит объяснить отдельно.
    Оффер с нулевым остатком уезжает в архив не от нашего файла, а оттого, что
    торговать нечем: `X144-0001-2` был обнулён связкой остатков в 15:31, а файл
    ушёл в 20:05 — архив был предрешён до отправки. Считать это потерей значит
    выключать фид каждый раз, когда у магазина кончается товар.

    ЧЕГО Я НЕ ПРОВЕРИЛ: возвращается ли такая карточка в продажу сама, когда
    остаток снова станет положительным. Косвенно похоже, что да (`S517-0044`
    после записи нуля 26.08 сейчас снова активен с остатком 1), но прямого
    замера «архив → приход товара → снова активна» у меня нет. Если окажется,
    что не возвращается, это дефект связки остатков, а не фида, — и ловить его
    надо там, где пишется ноль.
    """
    before = meta.get("archived_before")
    if not before:
        return []
    now = set(getattr(snapshot, "archived_skus", []) or [])
    if not now:
        return []
    withdrawn = {sku for sku, d in declared.items() if d.get("available") is False}
    out_of_stock = {sku for sku, d in declared.items() if not _has_stock(d)}
    return sorted((now - set(before)) - withdrawn - out_of_stock)


def settle_file(store_id, archive_path, verdict, snapshot=None, token=None) -> SettleReport:
    """
    Полная сверка по вердикту: что файл довёз, что откатил, что сломал.

    snapshot — свежий снимок каталога ПОСЛЕ обработки. Передаётся явно, чтобы
    тесты не ходили в сеть; в проде собирается здесь же по token.

    При плохом вердикте не пишется ничего: намерения остаются в очереди и
    уедут JSON-ом. Тихо проглотить плохой вердикт значило бы объявить
    доставленным то, что Kaspi не приняла.
    """
    import datetime

    meta = load_meta(archive_path)
    report = SettleReport(
        store_id=str(store_id),
        file_id=verdict.file_id,
        ok=bool(verdict.ok),
        active_before=meta.get("active_before"),
    )

    if not verdict.accepted:
        # Файл не разобран целиком — сверять не с чем, каталог в неизвестном
        # состоянии. Это по-прежнему безусловное выключение.
        reason = "вердикт не годен ({}) — намерения оставлены в очереди".format(verdict.summary())
        report.alarms.append(reason)
        feed_state.disable(store_id, reason)
        return report

    declared = parse_declared(archive_path.read_bytes())

    if snapshot is None:
        from backend.dumper.feed_snapshot import build_catalog_snapshot

        snapshot = build_catalog_snapshot(token, str(store_id))
    report.active_after = snapshot.size

    # Детектор «погасили каталог». Смотрим ДО закрытия намерений: если карточки
    # исчезли, гасить намерения нельзя тем более.
    before = report.active_before
    if before:
        lost = before - snapshot.size
        if lost > max(1, int(before * MAX_ACTIVE_DROP_RATIO)):
            reason = (
                "активных офферов было {}, стало {} — потеряно {}. Фид для магазина "
                "выключен, проверить каталог немедленно".format(before, snapshot.size, lost)
            )
            report.alarms.append(reason)
            feed_state.disable(store_id, reason)
            return report

    # Детектор «сняли то, чего мы не снимали». Отдельно от счётчика выше и
    # БЕЗ порога, потому что ловит он другое.
    #
    # Счётчик видит массовую убыль. Этот видит одну карточку — ту, что появилась
    # в кабинете, пока файл был в полёте, не попала в файл (её там физически не
    # могло быть) и потому была снята как «не указана в прайс-листе». В нетто
    # она невидима: создание даёт +1, гашение −1, размер каталога не двигается.
    #
    # Порога нет намеренно. Одна карточка, снятая без нашего ведома, — это тот
    # же механизм, который однажды снял 798, просто пойманный в начале. Цена
    # ложной тревоги — полчаса без фида; цена пропущенной — товар, который не
    # продаётся, и никто не знает почему.
    unexpected = _unexpected_archived(meta, declared, snapshot)
    if unexpected:
        reason = (
            "Kaspi сняла с продажи {} офферов, которых мы не снимали: {}. Похоже, "
            "карточки появились в кабинете, пока файл был в полёте, и не попали "
            "в него. Фид выключен, карточки нужно вернуть руками".format(
                len(unexpected), unexpected[:10]
            )
        )
        report.alarms.append(reason)
        report.unexpected_archived = unexpected
        feed_state.disable(store_id, reason)
        return report

    # Не смапленное. Смотрим ПОСЛЕ детекторов пропажи — они уже сказали бы,
    # что карточки потеряны, и до этой ветки дело бы не дошло. Здесь решается
    # только одно: объяснимо ли то, что осталось.
    #
    # Раньше на этом месте решения не было вовсе: `send_plan` бросал исключение
    # по `not_mapped != 0` ещё до сверки, и 28.08 фид выключился, не потеряв
    # ни одной карточки. Прокси заменён прямым измерением по каталогу.
    unexplained, expected_unmapped = classify_unmapped(declared, snapshot)
    report.unmapped_expected = expected_unmapped
    report.unmapped_unexplained = unexplained
    report.unmapped_unclassified = max(
        0, int(verdict.not_mapped or 0) - len(expected_unmapped) - len(unexplained)
    )
    if verdict.not_mapped:
        logger.warning(
            "[FeedSettle] Не смаплено %s: без остатка (ожидаемо) %s, необъяснённых %s%s",
            verdict.not_mapped, len(expected_unmapped), len(unexplained),
            " — " + str(unexplained[:10]) if unexplained else "",
        )
    if report.unmapped_unclassified:
        # Оффер объявлен, карточка активна и в прайс-листе — а строка не
        # прицепилась. По каталогу это не видно вообще; артикулы называет
        # только отчёт в кабинете («Результат проверки прайс-листа» →
        # фильтр «Нераспознанные товары»). Фид НЕ выключаем: замерено
        # 2026-08-28 и 29 — обе даты по два оффера, карточки целы.
        logger.warning(
            "[FeedSettle] Из них %s не объясняются каталогом: карточки на месте, "
            "но Kaspi не связала с ними строку файла. Артикулы — в отчёте кабинета "
            "по файлу %s",
            report.unmapped_unclassified, verdict.file_id,
        )
    if unexplained:
        reason = (
            "{} объявленных офферов с остатком не найдены в активном каталоге после "
            "обработки файла: {}. Фид выключен, карточки проверить руками".format(
                len(unexplained), unexplained[:10]
            )
        )
        report.alarms.append(reason)
        feed_state.disable(store_id, reason)
        return report

    snapshot_at = datetime.datetime.fromisoformat(meta["snapshot_at"])

    # Расхождения делятся на объяснённые и настоящие. Объяснённое — то, по чему
    # мир уехал вперёд уже после снимка: файл нёс значение на T0, а демпер или
    # связка остатков решили иначе, пока он летел. Такое намерение либо ещё
    # стоит в очереди, либо уже уехало JSON-ом внутри полёта — будить человека
    # тут не о чем ни в том, ни в другом случае.
    #
    # Прячется при этом НИЧЕГО: объяснённые лежат в отчёте отдельным полем.
    # Тревога — только по тем, где мир не двигался, а каталог всё равно
    # показывает не то, что мы объявили. Вот это и есть «запись не применилась».
    found = compare_with_catalog(declared, snapshot, ignore=expected_unmapped)
    moved = _moved_after_snapshot(store_id, snapshot_at)
    report.mismatches = [m for m in found if m["sku"] not in moved]
    report.mismatches_explained = [m for m in found if m["sku"] in moved]
    if report.mismatches:
        report.alarms.append(
            "{} офферов выглядят не так, как мы их объявили (напр. {})".format(
                len(report.mismatches), report.mismatches[:3]
            )
        )

    report.settled, report.kept_queued = settle_intents(store_id, declared, snapshot_at)

    feed_state.mark_settled(store_id, report)
    logger.warning("[FeedSettle] %s", report.summary())
    return report
