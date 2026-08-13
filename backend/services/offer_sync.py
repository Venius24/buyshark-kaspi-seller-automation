"""
Единый писатель цен и остатков в Kaspi MC.

До этого модуля цену писал демпер (backend/dumper/executor.py), а остаток —
веерная рассылка Мастер-Групп (backend/services/stock_sync_worker.py), каждый
своим циклом, вслепую и немедленно. На проде это давало ~250 записей за 30
минут при том, что реально менялось на порядок меньше, и упиралось в лимит
Kaspi (250 офферов / 30 мин), после чего вся очередь вставала.

Здесь оба источника лишь ОБЪЯВЛЯЮТ желаемое состояние оффера (enqueue), а
единственный флашер раз в FLUSH_INTERVAL секунд:

  1. отбрасывает намерения, которые ничего не меняют (главный источник
     потерь: Kaspi сам помечал такие наши записи operationType=NO_CHANGE);
  2. сортирует остаток по приоритету (снятие с продажи важнее косметики);
  3. отрезает по реальному остатку квоты, прочитанному у самого Kaspi;
  4. отправляет всё одним запросом на batch-эндпоинт.

Про batch-эндпоинт: /pricefeed/upload/merchant/process/process/batch принимает
КОРНЕВОЙ JSON-массив тех же payload'ов, что и одиночный /merchant/process, и
отвечает массивом id. Найден в бандле кабинета (processMultipleOffers) и
проверен живьём: пачка из 8 офферов — 0.5 секунды против ~40 по одному.
Потолок он не поднимает (квота считает офферы, а не запросы), но убирает N
отдельных TLS-сессий, N джиттер-пауз и пул потоков.
"""
import datetime
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, List

from sqlalchemy import select

from backend.services.kaspi_quota import quota

logger = logging.getLogger(__name__)

BATCH_URL = "https://mc.shop.kaspi.kz/pricefeed/upload/merchant/process/process/batch"
# l=50, а не 1: t= — это ПОИСК ПО ПОДСТРОКЕ, а не выборка по ключу. Запрос по
# "H422-0002" находит ещё и H422-0002-3, -4, -5, -7..., и при l=1 в ответ
# попадала случайная из них, точного совпадения в выдаче не оказывалось, и
# сверка вечно возвращала None: намерение по такому SKU висело в очереди
# бесконечно (замечено на проде 2026-08-12 — H422-0002 не мог сверитьcя
# десятки флашей подряд). Запрос всё так же один и квоту не тратит.
OFFER_LIST_URL = "https://mc.shop.kaspi.kz/bff/offer-view/list?m={muid}&p=0&l=50&t={sku}"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://kaspi.kz",
    "Referer": "https://kaspi.kz/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Auth-Version": "3",
}


# ── Приоритеты расхода квоты ────────────────────────────────────────────────
class Priority:
    """Меньше — важнее. Совпадает с сортировкой по столбцу priority."""

    WITHDRAW = 0   # снятие с продажи / стоп кол-во — иначе продадим то, чего нет
    LOSING = 1     # потеряли первое место, деньги утекают прямо сейчас
    MARGIN = 2     # подтяжка цены вверх, заработок но не срочно
    MINOR = 3      # всё остальное


# ── Лестница шагов остатка (решение пользователя, пункт 9) ──────────────────
# Чем больше остаток, тем реже имеет смысл его пересылать: разница в 8 штук
# при остатке 1006 не стоит единицы квоты, а при остатке 12 — стоит.
# (порог, шаг): остаток >= порога -> шлём только при накопленной разнице >= шаг.
STOCK_LADDER = ((500, 100), (150, 50), (30, 10), (20, 5))
STOCK_LADDER_FLOOR_STEP = 1  # ниже последнего порога — каждая штука

# Порог изменения цены (решение пользователя: 2 ₸). Раньше писали при разнице
# >= 1 ₸, а режим MATCH_FIRST ставит ровно "конкурент минус 1 ₸" — два бота
# могли пилить друг друга по рублю бесконечно, сжигая всё окно квоты.
PRICE_EPS = 2

# Анти-качели: если SKU за окно успел сменить направление цены столько раз —
# замораживаем его до конца заморозки. P0 заморозка не касается никогда.
FLAP_WINDOW_SECONDS = 3600.0
FLAP_REVERSALS = 3
FLAP_FREEZE_SECONDS = 1800.0

FLUSH_INTERVAL_SECONDS = 45.0
# Сколько офферов кладём в один HTTP-батч. Kaspi верхнюю границу не
# документирует; 50 заведомо проходит и держит тело запроса вменяемым.
BATCH_CHUNK = 50
# Максимум бесплатных сверок с Kaspi за один флаш (offer-view/list квоту не
# тратит, но это всё равно сетевые запросы — не устраиваем шторм).
MAX_VERIFY_PER_FLUSH = 40
# Насколько долго доверяем ранее увиденному состоянию оффера на Kaspi.
KASPI_STATE_TTL_SECONDS = 1800.0
# Через сколько после НАШЕЙ записи (не подтверждённой чтением) перепроверяем
# оффер, если движок снова просит ровно ту же цену.
#
# Ручка batch асинхронная: 200 значит "принято", а не "применено". На проде
# 2026-08-13 батч из 105 офферов ушёл в 19:10 с 200 и без ошибок, но часть
# строк Kaspi так и не применил (X471-0018-6: у нас записано 471 ₸, на
# карточке 720 ₸). Мы же после 200 помечали состояние достигнутым — и каждое
# следующее одинаковое намерение гасилось как "нечего менять". Позиция
# залипала навсегда: демпер решает, флашер гасит, цена стоит старая.
#
# Значение = один цикл демпинга: раньше перепроверять бессмысленно (Kaspi
# ещё обрабатывает), позже — деньги подтекают дольше нужного.
UNCONFIRMED_RECHECK_SECONDS = 900.0

# Раньше quota.get() вызывался ТОЛЬКО когда в этом флаше была хотя бы одна
# запись на отправку — на тихом сторе (демпинг выключен по всем SKU, группы
# не шевелятся) кэш quota.peek() никогда не наполнялся, и чип "Лимит Kaspi"
# на фронте не появлялся вообще (жалоба пользователя 2026-08-12: "нету").
# Теперь раз в этот интервал обновляем кэш независимо от того, есть ли что
# писать — запрос лёгкий и квоту OFFER не тратит (см. kaspi_quota.py), но
# всё равно не хотим ходить в Kaspi на каждом 45-секундном тике впустую.
QUOTA_HEARTBEAT_SECONDS = 120.0


def stock_step(value: int) -> int:
    """Шаг лестницы для данного остатка."""
    for threshold, step in STOCK_LADDER:
        if value >= threshold:
            return step
    return STOCK_LADDER_FLOOR_STEP


def should_push_stock(last: Optional[int], new: int, stop_qty: int = 0) -> bool:
    """
    Пора ли отправлять новый остаток на Kaspi.

    Шаг считается по МЕНЬШЕМУ из (последнего отправленного, нового). Иначе
    ловим дыру: отправили 160, остаток упал до 115 — разница 45, а шаг «по
    160» равен 50, и мы бы промолчали, оставив на витрине 160 при реальных
    115. По 115 шаг равен 10, и запись уходит.

    Переходы через ноль и через стоп кол-во лестницу игнорируют — это P0.
    """
    if last is None:
        return True
    if new == last:
        return False
    # Появление/пропадание товара на витрине — всегда, вне лестницы.
    if (last > stop_qty) != (new > stop_qty):
        return True
    if (last > 0) != (new > 0):
        return True
    return abs(new - last) >= stock_step(min(last, new))


# ── Анти-качели (в памяти процесса: uvicorn --workers 1, в БД не тащим) ─────
@dataclass
class _FlapRecord:
    events: List[tuple] = field(default_factory=list)  # [(timestamp, direction)]
    frozen_until: float = 0.0


_flap_registry: dict = {}
_flap_lock = threading.Lock()


def _flap_frozen(store_id, sku: str, now_ts: float) -> bool:
    with _flap_lock:
        rec = _flap_registry.get((store_id, sku))
        return bool(rec and now_ts < rec.frozen_until)


def _flap_note_price_write(store_id, sku: str, old_price: Optional[int], new_price: int, now_ts: float) -> None:
    if old_price is None or old_price == new_price:
        return
    direction = 1 if new_price > old_price else -1
    with _flap_lock:
        rec = _flap_registry.setdefault((store_id, sku), _FlapRecord())
        rec.events = [(t, d) for t, d in rec.events if now_ts - t < FLAP_WINDOW_SECONDS]
        rec.events.append((now_ts, direction))
        reversals = sum(
            1 for i in range(1, len(rec.events)) if rec.events[i][1] != rec.events[i - 1][1]
        )
        if reversals >= FLAP_REVERSALS:
            rec.frozen_until = now_ts + FLAP_FREEZE_SECONDS
            rec.events.clear()
            logger.warning(
                "🧊 [OfferSync] SKU %s заморожен на %d мин: цена качалась туда-сюда %d раз за час.",
                sku, int(FLAP_FREEZE_SECONDS // 60), reversals,
            )


def reset_flap_state() -> None:
    """Только для тестов — сбросить накопленные качели."""
    with _flap_lock:
        _flap_registry.clear()


# ── Запись намерений ────────────────────────────────────────────────────────
def _session():
    from backend.state_helpers import _get_sync_db_session

    return _get_sync_db_session()


def last_known_stock(store_id, sku: str) -> Optional[int]:
    """
    Остаток, который Kaspi уже знает про этот SKU: ещё не отправленное
    намерение, иначе последняя отправка, иначе фактическое состояние.

    Нужен веерной рассылке Мастер-Групп, чтобы применить лестницу шагов ДО
    постановки в очередь (лестница по решению пользователя действует только
    на группы, поэтому и живёт на их стороне, а не внутри флашера).
    """
    session = _session()
    if not session:
        return None
    try:
        from backend.db.models import KaspiOfferSyncDB

        row = session.get(KaspiOfferSyncDB, (store_id, sku))
        if not row:
            return None
        if row.desired_stock is not None and row.desired_at is not None:
            return row.desired_stock
        if row.pushed_stock is not None:
            return row.pushed_stock
        return row.kaspi_stock
    finally:
        session.close()


def queue_depth(store_id) -> int:
    """Сколько намерений ждёт отправки. Для индикатора на вкладке демпинга."""
    from sqlalchemy import func

    session = _session()
    if not session:
        return 0
    try:
        from backend.db.models import KaspiOfferSyncDB

        return int(session.scalar(
            select(func.count())
            .select_from(KaspiOfferSyncDB)
            .where(
                KaspiOfferSyncDB.store_id == store_id,
                KaspiOfferSyncDB.desired_at.isnot(None),
            )
        ) or 0)
    except Exception as e:
        logger.debug("Не удалось посчитать глубину очереди: %s", e)
        return 0
    finally:
        session.close()


def enqueue(
    store_id,
    sku: str,
    *,
    priority: int = Priority.MINOR,
    reason: str = "",
    price: Optional[int] = None,
    stock: Optional[int] = None,
    available: Optional[bool] = None,
    master_sku: Optional[str] = None,
    model: Optional[str] = None,
    store_suffix: Optional[str] = None,
    city_id: Optional[str] = None,
) -> None:
    """
    Объявить желаемое состояние оффера. Ничего не отправляет — только пишет
    намерение, которое подхватит флашер.

    Повторный enqueue по тому же SKU до флаша ПЕРЕЗАПИСЫВАЕТ намерение (это и
    есть схлопывание веера: пять заказов подряд по одной группе оставляют одну
    запись с финальным остатком, а не пять записей подряд), и поднимает
    приоритет, если новый важнее.
    """
    if price is None and stock is None and available is None:
        return
    session = _session()
    if not session:
        return
    try:
        from backend.db.models import KaspiOfferSyncDB

        now = datetime.datetime.utcnow()
        row = session.get(KaspiOfferSyncDB, (store_id, sku))
        if not row:
            row = KaspiOfferSyncDB(store_id=store_id, sku=sku)
            session.add(row)

        if price is not None:
            row.desired_price = int(price)
        if stock is not None:
            row.desired_stock = int(stock)
        if available is not None:
            row.desired_available = bool(available)
        if master_sku:
            row.master_sku = str(master_sku)
        if model:
            row.model = str(model)[:500]
        if store_suffix:
            row.store_suffix = str(store_suffix)
        if city_id:
            row.city_id = str(city_id)

        # priority: меньше — важнее, поэтому берём минимум из старого и нового.
        row.priority = min(row.priority if row.priority is not None else Priority.MINOR, priority)
        row.reason = (reason or row.reason or "")[:255]
        if row.desired_at is None:
            # Возраст намерения считаем от ПЕРВОГО объявления, чтобы
            # схлопывание не отбрасывало SKU в конец очереди бесконечно.
            row.desired_at = now
        session.commit()
    except Exception as e:
        logger.error("Не удалось поставить намерение по SKU %s: %s", sku, e)
        session.rollback()
    finally:
        session.close()


# ── Сверка с фактическим состоянием на Kaspi ────────────────────────────────
def _fetch_kaspi_state(store_id, merchant_uid: str, sku: str) -> Optional[dict]:
    """
    Фактические цена/остаток оффера. Квоту НЕ тратит (проверено: отвечает 200
    даже когда process уже отдаёт 429). Параметр t= — текстовый поиск кабинета,
    единственный из перебранных (s/sku/search/text/q/f), который реально
    фильтрует выдачу.
    """
    from backend.state_helpers import _mc_api_request
    from urllib.parse import quote

    try:
        resp = _mc_api_request(
            "GET",
            OFFER_LIST_URL.format(muid=merchant_uid, sku=quote(str(sku))),
            store_id=store_id,
            headers=_HEADERS,
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        rows = (resp.json() or {}).get("data") or []
        match = next((r for r in rows if str(r.get("sku")) == str(sku)), None)
        if not match:
            return None
        avail = (match.get("availabilities") or [{}])[0]
        # Цену берём через extract_offer_price, а не из плоского match["price"]:
        # то поле — денормализованный кэш Kaspi, у ~35% офферов отдающий 0 при
        # реально назначенной цене (см. backend/kaspi_offer_price.py). Наивное
        # чтение стоило двух карточек, снятых Kaspi с продажи по причине
        # "не указан в прайс-листе", — мы записали им цену 0.
        from backend.kaspi_offer_price import extract_offer_price

        return {
            "price": extract_offer_price(match),
            "stock": int(avail["stockCount"]) if avail.get("stockCount") is not None else None,
            "available": avail.get("available") == "yes",
            "master_sku": match.get("masterSku"),
            "model": match.get("title") or match.get("model"),
            "store_id_full": avail.get("storeId"),
        }
    except Exception as e:
        logger.debug("Сверка состояния SKU %s не удалась: %s", sku, e)
        return None


@dataclass
class _Decision:
    sku: str
    priority: int
    reason: str
    price: int
    stock: int
    available: bool
    base_price: Optional[int]
    base_stock: Optional[int]
    model: str
    store_suffix: str
    city_id: str


def _baseline(row) -> tuple:
    """
    Что, по нашим сведениям, сейчас стоит на Kaspi: свежее фактическое
    состояние, иначе последняя отправка.
    """
    now = datetime.datetime.utcnow()
    fresh = (
        row.kaspi_seen_at is not None
        and (now - row.kaspi_seen_at).total_seconds() < KASPI_STATE_TTL_SECONDS
    )
    if fresh:
        return _usable_price(row.kaspi_price), row.kaspi_stock, (row.kaspi_stock or 0) > 0, True
    return _usable_price(row.pushed_price), row.pushed_stock, (row.pushed_stock or 0) > 0, False


def _usable_price(price) -> Optional[int]:
    """
    Неположительная цена — это не цена, а "не знаем": возвращаем None, чтобы
    сработала сверка (need_verify гейтится именно на None). Иначе ноль,
    однажды записанный в базу — а на проде такие строки уже есть, — залипал бы
    навсегда: писать его нельзя, а перечитать было некому.
    """
    if price is None or int(price) <= 0:
        return None
    return int(price)


#: Возвращается вместо решения, когда для сборки payload не хватает данных.
#: Принципиально отличается от "ничего не меняет": такое намерение НЕЛЬЗЯ
#: гасить, его надо отложить до следующего флаша, когда бюджет сверок
#: восстановится. Иначе намерение только по остатку (веер Мастер-Группы),
#: не попавшее в MAX_VERIFY_PER_FLUSH, пропадало бы молча.
INCOMPLETE = "incomplete"


def _looks_like_unconfirmed_noop(row, base_price, base_stock, was_fresh: bool) -> bool:
    """
    Похоже ли, что намерение сейчас погасится вхолостую об НЕподтверждённую
    запись — то есть об наше же "мы это уже отправили", которое никто не
    перечитывал у Kaspi.

    Только такие строки и стоит сверять дополнительно: если движок просит
    новую цену, она уйдёт и без сверки; а вот повтор ровно той же цены — это
    либо честное "всё уже так" (сверка подтвердит и погасит), либо залипшая
    позиция, чью запись Kaspi молча не применил (см.
    UNCONFIRMED_RECHECK_SECONDS). Отличить их можно только чтением.

    was_fresh=True означает, что база взята из недавней СВЕРКИ — там врать
    нечему, повторно не ходим.
    """
    if was_fresh or row.pushed_at is None:
        return False
    age = (datetime.datetime.utcnow() - row.pushed_at).total_seconds()
    if age < UNCONFIRMED_RECHECK_SECONDS:
        return False
    price_same = row.desired_price is None or (
        base_price is not None and abs(int(row.desired_price) - int(base_price)) < PRICE_EPS
    )
    stock_same = row.desired_stock is None or (
        base_stock is not None and int(row.desired_stock) == int(base_stock)
    )
    return price_same and stock_same


def _decide(row, base_price, base_stock, base_available, now_ts: float):
    """
    Нужна ли запись по этому SKU.

    None      — намерение ничего не меняет, гасим (не тратя квоту).
    INCOMPLETE — не хватает данных для payload, откладываем без гашения.
    _Decision — писать.
    """
    want_price = row.desired_price if row.desired_price is not None else base_price
    want_stock = row.desired_stock if row.desired_stock is not None else base_stock
    if want_price is None or want_stock is None:
        # Payload Kaspi всегда несёт и цену, и остаток — отправить половину
        # нельзя, затрём чужое значение своим "не знаю".
        return INCOMPLETE
    if int(want_price) <= 0:
        # Жёсткий инвариант: цену 0 на Kaspi не отправляем НИКОГДА. Оффер с
        # нулевой ценой выпадает из прайс-листа, и карточка снимается с
        # продажи с причиной "не указан в прайс-листе" — ровно это и
        # случилось 2026-08-12 с H422-0002-7 и H422-0002-9, когда база цены
        # пришла нулём из денормализованного кэша Kaspi. Откладываем: на
        # следующем флаше сверка перечитает цену (base_price is None ->
        # need_verify), и намерение уйдёт уже с настоящей ценой.
        logger.warning(
            "[OfferSync] SKU %s: расчётная цена %r — отправлять нельзя, откладываем.",
            row.sku, want_price,
        )
        return INCOMPLETE

    want_available = row.desired_available
    if want_available is None:
        want_available = want_stock > 0

    price_changed = (
        row.desired_price is not None
        and base_price is not None
        and abs(int(row.desired_price) - int(base_price)) >= PRICE_EPS
    ) or (row.desired_price is not None and base_price is None)

    stock_changed = (
        row.desired_stock is not None
        and (base_stock is None or int(row.desired_stock) != int(base_stock))
    )
    availability_changed = bool(base_available != want_available)

    if not (price_changed or stock_changed or availability_changed):
        return None

    # Анти-качели: замороженный SKU не двигаем по цене, но снятие с продажи и
    # изменение остатка пропускаем — это не качели, это товародвижение.
    if price_changed and row.priority > Priority.WITHDRAW and _flap_frozen(row.store_id, row.sku, now_ts):
        if not (stock_changed or availability_changed):
            return None
        want_price = base_price if base_price is not None else want_price

    return _Decision(
        sku=row.sku,
        priority=row.priority if row.priority is not None else Priority.MINOR,
        reason=row.reason or "",
        price=int(want_price),
        stock=int(want_stock),
        available=bool(want_available),
        base_price=base_price,
        base_stock=base_stock,
        model=row.model or "Товар",
        store_suffix=row.store_suffix or "001",
        city_id=row.city_id or "750000000",
    )


def _payload(merchant_uid: str, d: _Decision) -> dict:
    return {
        "merchantUid": str(merchant_uid),
        "sku": str(d.sku),
        "model": str(d.model),
        "availabilities": [{
            "available": "yes" if d.available and d.stock > 0 else "no",
            "storeId": f"{merchant_uid}_{d.store_suffix}",
            "stockCount": int(d.stock),
        }],
        "cityPrices": [{"cityId": str(d.city_id), "value": int(d.price)}],
    }


def flush_store(store_id, merchant_uid: str) -> dict:
    """
    Один проход флашера по магазину. Синхронный (сетевой + БД) — вызывать
    через asyncio.to_thread.
    """
    from backend.db.models import KaspiOfferSyncDB
    from backend.state_helpers import _mc_api_request

    stats = {"candidates": 0, "no_op": 0, "written": 0, "deferred": 0, "verified": 0, "errors": 0}
    session = _session()
    if not session:
        return stats

    try:
        # Держим индикатор квоты на фронте живым даже на тихом сторе — см.
        # QUOTA_HEARTBEAT_SECONDS выше. Делаем это ДО чтения очереди: у
        # стора может не быть вообще ни одного намерения (rows пуст), и
        # раньше цикл в этом случае завершался, ни разу не тронув Kaspi.
        cached_quota = quota.peek(store_id)
        quota_stale = (
            cached_quota is None
            or (datetime.datetime.now() - cached_quota.fetched_at).total_seconds() >= QUOTA_HEARTBEAT_SECONDS
        )
        if quota_stale:
            quota.get(store_id, merchant_uid)

        rows = session.scalars(
            select(KaspiOfferSyncDB)
            .where(KaspiOfferSyncDB.store_id == store_id, KaspiOfferSyncDB.desired_at.isnot(None))
            .order_by(KaspiOfferSyncDB.priority, KaspiOfferSyncDB.desired_at)
        ).all()
        if not rows:
            return stats
        stats["candidates"] = len(rows)

        now_ts = datetime.datetime.now().timestamp()
        decisions: List[tuple] = []
        verify_left = MAX_VERIFY_PER_FLUSH

        for row in rows:
            base_price, base_stock, base_available, was_fresh = _baseline(row)
            # Половины payload'а не знаем вовсе — сверяемся независимо от того,
            # насколько "свежей" считается запись: без цены или без остатка
            # отправить нечего. Плюс сверяемся, когда намерение частичное
            # (только цена или только остаток) и база уже подвыдохлась —
            # иначе отправим устаревшее второе поле поверх свежего.
            need_verify = verify_left > 0 and (
                base_price is None
                or base_stock is None
                or (not was_fresh and (row.desired_price is None or row.desired_stock is None))
                or _looks_like_unconfirmed_noop(row, base_price, base_stock, was_fresh)
            )
            if need_verify:
                verify_left -= 1
                actual = _fetch_kaspi_state(store_id, merchant_uid, row.sku)
                if actual:
                    stats["verified"] += 1
                    if (
                        row.pushed_price is not None
                        and actual["price"]
                        and abs(int(actual["price"]) - int(row.pushed_price)) >= PRICE_EPS
                    ):
                        # Наша запись не доехала: Kaspi принял батч (200), но
                        # строку не применил. Намерение сейчас уйдёт заново —
                        # ради этого сверка и затевалась.
                        logger.warning(
                            "🔁 [OfferSync] SKU %s: писали %s ₸, на Kaspi %s ₸ — запись не применилась, отправляем снова.",
                            row.sku, row.pushed_price, actual["price"],
                        )
                    row.kaspi_price = actual["price"]
                    row.kaspi_stock = actual["stock"]
                    row.kaspi_seen_at = datetime.datetime.utcnow()
                    if actual.get("master_sku") and not row.master_sku:
                        row.master_sku = actual["master_sku"]
                    if actual.get("model") and not row.model:
                        row.model = str(actual["model"])[:500]
                    full = actual.get("store_id_full") or ""
                    if "_" in full:
                        row.store_suffix = full.split("_", 1)[1]
                    base_price, base_stock, base_available = (
                        actual["price"], actual["stock"], actual["available"],
                    )

            decision = _decide(row, base_price, base_stock, base_available, now_ts)
            if decision is INCOMPLETE:
                # Бюджет бесплатных сверок кончился (или Kaspi не ответил) —
                # намерение живёт дальше и уйдёт следующим флашем.
                stats["deferred"] += 1
                continue
            if decision is None:
                # Намерение ничего не меняет — гасим, не тратя квоту. Это и
                # есть те самые NO_CHANGE, которыми Kaspi помечал наши записи.
                stats["no_op"] += 1
                row.desired_price = row.desired_stock = row.desired_available = None
                row.desired_at = None
                row.reason = None
                row.priority = Priority.MINOR
                continue
            decisions.append((row, decision))
        session.commit()

        if not decisions:
            return stats

        state = quota.get(store_id, merchant_uid)
        allowed: List[tuple] = []
        for row, d in decisions:
            if len(allowed) < state.budget_for(d.priority):
                allowed.append((row, d))
        # Именно +=, а не =: в stats["deferred"] уже лежат намерения,
        # отложенные по неполноте данных (INCOMPLETE), и присваивание их
        # затирало — в логе получалось "отложено 0" при реально отложенных.
        cut_by_quota = len(decisions) - len(allowed)
        stats["deferred"] += cut_by_quota
        if cut_by_quota:
            logger.info(
                "⏳ [OfferSync] Стор %s: квота %d/%d, откладываем %d записей до сброса через %.0f мин.",
                store_id, state.used, state.limit, cut_by_quota,
                state.seconds_until_reset() / 60.0,
            )
        if not allowed:
            return stats

        # Последний рубеж перед сетью: цена 0 в payload снимает карточку с
        # продажи ("не указан в прайс-листе"). _decide такое уже не пропускает,
        # но цена решения — единственное поле, ошибка в котором ломает товар
        # необратимо и молча, поэтому проверяем ещё раз прямо перед отправкой.
        unsafe = [(row, d) for row, d in allowed if d.price <= 0]
        if unsafe:
            allowed = [(row, d) for row, d in allowed if d.price > 0]
            stats["deferred"] += len(unsafe)
            logger.error(
                "[OfferSync] Отброшены %d решений с неположительной ценой: %s",
                len(unsafe), ", ".join(f"{d.sku}={d.price}" for _, d in unsafe[:10]),
            )
            if not allowed:
                return stats

        for start in range(0, len(allowed), BATCH_CHUNK):
            chunk = allowed[start:start + BATCH_CHUNK]
            body = [_payload(merchant_uid, d) for _, d in chunk]
            try:
                resp = _mc_api_request(
                    "POST", BATCH_URL, store_id=store_id,
                    headers=_HEADERS, json=body, timeout=90,
                )
            except Exception as e:
                stats["errors"] += len(chunk)
                logger.error("[OfferSync] Батч из %d офферов не ушёл: %s", len(chunk), e)
                break

            if resp.status_code == 429:
                fresh = quota.mark_exhausted(store_id, merchant_uid)
                logger.warning(
                    "🛑 [OfferSync] Kaspi отдал 429 на батче из %d. Окно выбрано, "
                    "следующая попытка после сброса через %.0f мин — намерения сохранены.",
                    len(chunk), fresh.seconds_until_reset() / 60.0,
                )
                stats["deferred"] += len(allowed) - start
                break

            if resp.status_code != 200:
                stats["errors"] += len(chunk)
                msg = f"{resp.status_code}: {resp.text[:200]}"
                logger.error("[OfferSync] Батч отклонён (%s)", msg)
                for row, _ in chunk:
                    row.last_error = msg[:500]
                session.commit()
                continue

            quota.consume(store_id, len(chunk))
            written_at = datetime.datetime.utcnow()
            for row, d in chunk:
                _flap_note_price_write(store_id, d.sku, d.base_price, d.price, now_ts)
                row.pushed_price = d.price
                row.pushed_stock = d.stock
                row.pushed_at = written_at
                # kaspi_* — это "что мы ПРОЧИТАЛИ у Kaspi", и собственная
                # запись таким доказательством не является: ручка batch
                # асинхронная, 200 значит "принято", а не "применено".
                # Раньше мы копировали сюда отправленное значение, и
                # непринятая Kaspi строка залипала навсегда — следующий флаш
                # видел "уже так" и гасил намерение вхолостую. Теперь
                # фактическое состояние честно обнуляется: базой станет
                # pushed_* (was_fresh=False), а через
                # UNCONFIRMED_RECHECK_SECONDS повтор того же намерения
                # вызовет бесплатную сверку с Kaspi.
                row.kaspi_price = None
                row.kaspi_stock = None
                row.kaspi_seen_at = None
                row.desired_price = row.desired_stock = row.desired_available = None
                row.desired_at = None
                row.reason = None
                row.priority = Priority.MINOR
                row.last_error = None
            stats["written"] += len(chunk)
            session.commit()

        return stats
    except Exception as e:
        logger.exception("[OfferSync] Флаш для стора %s упал: %s", store_id, e)
        session.rollback()
        stats["errors"] += 1
        return stats
    finally:
        session.close()


async def run_offer_sync_cycle() -> dict:
    """Один проход по всем активным магазинам. Вынесен ради юнит-тестов."""
    import asyncio

    from backend.security.store_context import list_active_stores

    totals = {"candidates": 0, "no_op": 0, "written": 0, "deferred": 0, "verified": 0, "errors": 0}
    for store in await list_active_stores():
        try:
            # merchant_id берём из БД (Store.merchant_id), а НЕ через
            # discover_merchant_id: тот ходит в Kaspi (/pointofservices, на
            # проде стабильно отдаёт 500, дальше фоллбек через историю
            # заказов). Тик раз в 45 секунд на каждый стор превратил бы это в
            # постоянный лишний трафик к Kaspi — ровно тот паттерн, за который
            # IP этого VPS уже банили (см. HANDOFF 3.8/3.9).
            merchant_uid = getattr(store, "merchant_id", None)
            if not merchant_uid or not str(merchant_uid).isdigit():
                logger.warning(
                    "[OfferSync] Стор %s: merchant_id в БД отсутствует или не числовой (%r), пропускаем флаш.",
                    store.store_id, merchant_uid,
                )
                continue
            stats = await asyncio.to_thread(flush_store, store.store_id, str(merchant_uid))
            for k in totals:
                totals[k] += stats.get(k, 0)
        except Exception as e:
            logger.error("[OfferSync] Стор %s: флаш не удался: %s", store.store_id, e)
    if totals["written"] or totals["no_op"] or totals["deferred"]:
        logger.info(
            "📤 [OfferSync] Записано %d, погашено вхолостую %d, отложено %d, сверок %d.",
            totals["written"], totals["no_op"], totals["deferred"], totals["verified"],
        )
    return totals


async def offer_sync_loop(interval_seconds: float = FLUSH_INTERVAL_SECONDS):
    import asyncio

    logger.info("🟢 [OfferSync] Единый писатель цен/остатков запущен (флаш раз в %.0fс).", interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await run_offer_sync_cycle()
        except asyncio.CancelledError:
            logger.info("⏹ [OfferSync] Остановлен.")
            break
        except Exception as e:
            logger.error("❌ [OfferSync] Ошибка цикла: %s", e)
