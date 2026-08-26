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

_OFFER_RE = re.compile(r'<offer sku="([^"]*)"[^>]*>(.*?)</offer>', re.S)
_PRICE_RE = re.compile(r"<cityprice[^>]*>(\d+)</cityprice>")
_STOCK_RE = re.compile(r'stockCount="([\d.]+)"')
_AVAIL_RE = re.compile(r'available="(yes|no)"')


def _unescape(text: str) -> str:
    """Значение атрибута из сырого текста файла — включая `&#9;`.

    Артикул с табуляцией уезжает символьной ссылкой; без её разбора сверка
    искала бы в каталоге строку `H477-0009&#9;` и не нашла бы — то есть
    объявила бы расхождением ровно тот оффер, ради которого всё чинилось.
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
    alarms: List[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return bool(self.alarms) or bool(self.mismatches)

    def summary(self) -> str:
        head = "файл {}: закрыто {}, оставлено в очереди {}, расхождений {}".format(
            self.file_id, self.settled, self.kept_queued, len(self.mismatches)
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


def load_meta(archive_path) -> dict:
    meta_path = archive_path.with_suffix(archive_path.suffix + ".meta.json")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def compare_with_catalog(declared: Dict[str, dict], snapshot) -> List[dict]:
    """
    Объявленное против фактического. Возвращает расхождения, а не «ок/не ок».

    Сравниваются только цена и остаток: это то, ради чего файл и отправлялся.
    Оффер, которого в свежем снимке нет вовсе, — самый тяжёлый случай (карточка
    ушла из продажи), и он тоже попадает сюда, с `actual: None`.
    """
    from backend.dumper.xml_feed import feed_sku_roundtrip

    actual = {
        feed_sku_roundtrip(str(p.get("merchantSku"))): p
        for p in snapshot.offers
    }
    mismatches: List[dict] = []
    for sku, want in declared.items():
        have = actual.get(sku)
        if have is None:
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

    if not verdict.ok:
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

    report.mismatches = compare_with_catalog(declared, snapshot)
    if report.mismatches:
        report.alarms.append(
            "{} офферов выглядят не так, как мы их объявили (напр. {})".format(
                len(report.mismatches), report.mismatches[:3]
            )
        )

    snapshot_at = datetime.datetime.fromisoformat(meta["snapshot_at"])
    report.settled, report.kept_queued = settle_intents(store_id, declared, snapshot_at)

    feed_state.mark_settled(store_id, report)
    logger.warning("[FeedSettle] %s", report.summary())
    return report
