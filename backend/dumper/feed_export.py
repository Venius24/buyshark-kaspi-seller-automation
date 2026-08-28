"""
Выгрузка каталога из кабинета Kaspi — каталог её собственными глазами.

Это НЕ файл, который отправили мы. Наш живёт отдельно, по
`/pricefeed/upload/merchant/files/{id}/download` (см. `feed_identity`), и
отвечает на вопрос «что мы объявили». Этот отвечает на другой — «что Kaspi
считает правдой», и покрывает в том числе SKU, которых мы не отправляли
никогда.

Цепочка отреверсена на живом кабинете Бархана 2026-08-25, подробности и
образцы — в `REAL_IMPORTANT/выгрузка_каталога/CATALOG_EXPORT.md`:

  1. GET .../offer/export/types    — какие форматы доступны
  2. GET .../offer/export/trigger  — ЗАПУСТИТЬ генерацию; ловит 429
  3. GET .../offer/export/status   — {"fileName": ..., "status": "FINISHED"}
  4. GET /image/processor/.../{fileName} — сам XML

Ключевое свойство, ради которого этот модуль вообще возможен: **шаги 3 и 4
работают независимо от 2**. Готовый файл лежит на бакете от прошлой генерации,
и скачать его можно, даже когда `trigger` отвечает 429 — так делает и сам
фронт кабинета. Поэтому по умолчанию мы `trigger` НЕ зовём: качаем что лежит и
смотрим на дату внутри файла. Дёргать генерацию — отдельное осознанное
действие (`refresh=True`), а не побочный эффект чтения.

Чем эта выгрузка НЕ является
----------------------------
Эталоном артикулов. Kaspi, выгружая собственный каталог, теряет табуляцию в
`sku` ровно так же, как теряем её мы: XML 1.0 §3.3.3 нормализует `\\t` в
пробел, а `&#9;` они не пишут. Замерено 25.08 — `H477-0009<TAB>` приехал в
выгрузке как `H477-0009<ПРОБЕЛ>`. Значит по ней нельзя решать, битый артикул
или нет; это решается по BFF (`feed_guard`). Цены, остатки, бренды и состав
каталога — можно.
"""
from typing import Dict, Optional, Tuple
import datetime
import logging
import pathlib
import re

logger = logging.getLogger(__name__)

BASE = "https://mc.shop.kaspi.kz"
STATUS_URL = BASE + "/offers/api/v1/offer/export/status"
TRIGGER_URL = BASE + "/offers/api/v1/offer/export/trigger"
# Префикс — не выдумка: фронт склеивает window.env.FRONT_MS_OFFERS_S3_BUCKET
# ("cnt/m/o") с fileName из статуса и вешает на <a download>.
FILE_URL = BASE + "/image/processor/merchant/img/cnt/m/o/{file_name}"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://kaspi.kz",
    "Referer": "https://kaspi.kz/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Auth-Version": "3",
}

# Дата генерации лежит в самом файле, в корневом теге.
_DATE_RE = re.compile(rb'<kaspi_catalog[^>]*\sdate="([^"]+)"')
_SKU_RE = re.compile(rb'<offer sku="([^"]*)"')

# Насколько старая выгрузка ещё годится как источник идентичности. Названия и
# бренды меняются несравнимо реже цен, поэтому сутки тут — запас, а не риск:
# устаревшее название хуже свежего, но неизмеримо лучше пустого.
MAX_IDENTITY_AGE_HOURS = 24


class ExportUnavailable(Exception):
    """Выгрузку взять не удалось. Не повод падать — повод не считать её источником."""


def _cache_path(store_id, available: str) -> pathlib.Path:
    base = pathlib.Path(__file__).resolve().parents[2] / "states" / "feeds" / str(store_id)
    base.mkdir(parents=True, exist_ok=True)
    return base / "kaspi_export_{}.xml".format(available.lower())


def parse_generated_at(xml_bytes: bytes) -> Optional[datetime.datetime]:
    """Момент генерации выгрузки — из атрибута `date` корневого тега."""
    m = _DATE_RE.search(xml_bytes or b"")
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1).decode("utf-8"), "%Y-%m-%d %H:%M")
    except (ValueError, UnicodeDecodeError):
        return None


def skus(xml_bytes: bytes) -> set:
    """
    Множество SKU в выгрузке, как их видит Kaspi.

    Для сравнения составов этого достаточно и это честно: артикулы, которые
    выгрузка искажает (табуляция), она искажает одинаково в обоих снимках, и
    разность множеств от этого не врёт.
    """
    return {m.group(1).decode("utf-8") for m in _SKU_RE.finditer(xml_bytes or b"")}


def fetch_export(
    store_id, merchant_uid: str, available: str = "ACTIVE", *, refresh: bool = False,
) -> Tuple[bytes, Optional[datetime.datetime]]:
    """
    Скачать лежащую на бакете выгрузку. Возвращает (байты, момент генерации).

    refresh=True дополнительно дёргает `trigger`. По умолчанию False: генерация
    ограничена по частоте (429), а свежесть файла и так видна по его дате —
    решать, нужна ли новая, должен вызывающий, а не этот модуль.

    Бросает ExportUnavailable, если файла нет или он не скачался.
    """
    from backend.state_helpers import _mc_api_request

    params = {"m": str(merchant_uid), "available": available}

    if refresh:
        try:
            resp = _mc_api_request(
                "GET", TRIGGER_URL, store_id=store_id, headers=_HEADERS,
                params=dict(params, fileType="XML"), timeout=20,
            )
            # 429 — не ошибка: значит генерацию уже запускали недавно, и на
            # бакете лежит свежий файл. Ровно так же ведёт себя фронт кабинета.
            if resp.status_code not in (200, 429):
                logger.warning("[FeedExport] trigger -> %s", resp.status_code)
        except Exception as err:
            logger.warning("[FeedExport] trigger не прошёл: %r", err)

    try:
        resp = _mc_api_request(
            "GET", STATUS_URL, store_id=store_id, headers=_HEADERS,
            params=params, timeout=20,
        )
    except Exception as err:
        raise ExportUnavailable("статус выгрузки не прочитан: {!r}".format(err)) from err

    if resp.status_code != 200:
        raise ExportUnavailable("статус выгрузки -> {}".format(resp.status_code))

    card = resp.json() or {}
    if card.get("status") != "FINISHED":
        raise ExportUnavailable(
            "выгрузка не готова: {} {}".format(card.get("status"), card.get("errorMessage") or "")
        )
    file_name = card.get("fileName")
    if not file_name:
        raise ExportUnavailable("в статусе нет fileName")

    try:
        resp = _mc_api_request(
            "GET", FILE_URL.format(file_name=file_name), store_id=store_id,
            headers=_HEADERS, timeout=120,
        )
    except Exception as err:
        raise ExportUnavailable("файл выгрузки не скачался: {!r}".format(err)) from err

    if resp.status_code != 200 or not resp.content:
        raise ExportUnavailable("файл выгрузки -> {}".format(resp.status_code))

    body = resp.content
    generated_at = parse_generated_at(body)
    try:
        _cache_path(store_id, available).write_bytes(body)
    except OSError as err:
        logger.warning("[FeedExport] выгрузка не сохранилась на диск: %r", err)

    # Точка восстановления. Штамп берётся из даты ГЕНЕРАЦИИ, а не из момента
    # скачивания: файл на бакете живёт между генерациями, и качать его мы можем
    # хоть каждый час — копия при этом должна остаться одна.
    if available == "ACTIVE":
        from backend.dumper import feed_backup

        feed_backup.save(store_id, body, "kaspi-export", generated_at)

    logger.info(
        "[FeedExport] Выгрузка %s: %s офферов, сгенерирована %s",
        available, len(skus(body)), generated_at,
    )
    return body, generated_at


def load_cached(store_id, available: str = "ACTIVE") -> Tuple[Optional[bytes], Optional[datetime.datetime]]:
    """Последняя скачанная выгрузка с диска. (None, None) — если её нет."""
    path = _cache_path(store_id, available)
    try:
        body = path.read_bytes()
    except OSError:
        return None, None
    return body, parse_generated_at(body)


def identity_from_export(
    store_id, merchant_uid: str, *, max_age_hours: int = MAX_IDENTITY_AGE_HOURS,
) -> Tuple[Optional[str], Dict[str, dict]]:
    """
    Поля идентичности из выгрузки Kaspi. (источник, карта) либо (None, {}).

    Источник лучше принятого файла по двум причинам, обе замерены 25.08:
    выгрузка покрывает SKU, которых мы никогда не отправляли (19 штук на
    Бархане), и несёт бренд СЛАГОМ (`barkhan`), а слаг из BFF не выводится —
    там лежит display-имя («Бархан»).

    Пустые `<model/>`/`<brand/>` в выгрузке — не пробел в данных, а правда: в
    выгрузке Бархана 28.08 их 115 и 333 при нормальных названиях карточек в
    кабинете. Значит `<model>` прайс-листа — не название карточки, и пустое
    значение её не переименовывает.
    """
    from backend.dumper.feed_identity import parse_identity

    body, generated_at = load_cached(store_id)
    stale = (
        generated_at is None
        or (datetime.datetime.now() - generated_at).total_seconds() > max_age_hours * 3600
    )
    if body is None or stale:
        try:
            body, generated_at = fetch_export(store_id, merchant_uid)
        except ExportUnavailable as err:
            logger.warning("[FeedExport] Выгрузка недоступна: %s", err)
            if body is None:
                return None, {}
            # Протухшая выгрузка лучше отсутствующей: названия меняются реже,
            # чем цены, а альтернатива — пустые поля идентичности.
            logger.warning("[FeedExport] Работаем на старой выгрузке от %s", generated_at)

    identity = parse_identity(body)
    if not identity:
        return None, {}
    source = "выгрузка Kaspi от {}".format(
        generated_at.strftime("%Y-%m-%d %H:%M") if generated_at else "неизвестной даты"
    )
    return source, identity
