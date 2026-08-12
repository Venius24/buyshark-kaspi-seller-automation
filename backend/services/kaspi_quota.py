"""
Учётчик квоты Kaspi на обновление офферов.

Kaspi отдаёт собственное состояние квоты — это и есть источник правды, поэтому
локальную таблицу-счётчик мы НЕ заводим (см. обсуждение "цени БД"):

    GET https://mc.shop.kaspi.kz/pricefeed/upload/merchant/upload/limit/settings?m=<uid>
    {"merchantUid":"20186986","unlimited":false,"limitTime":"30m",
     "limits":{"FILE":2,"OFFER":250},
     "statistics":[{"documentType":"OFFER","uploadedCount":49,
                    "expirationDate":"2026-08-12T17:31:26.494"}]}

Замерено вживую на проде 2026-08-12:

* Считаются ОФФЕРЫ, а не запросы. Пачка из 8 офферов на batch-эндпоинт
  сдвинула uploadedCount ровно на 8 (49 -> 57). То есть batch даёт скорость
  и атомарность, но потолок не поднимает.
* Окно ФИКСИРОВАННОЕ, не скользящее: expirationDate не двигается при новых
  записях, счётчик обнуляется разом. Значит при 429 надо спать ровно до
  expirationDate, а не 60 секунд вслепую (старое поведение executor.py,
  из-за которого прод крутился в мёртвой петле: 429 -> sleep(60) -> 429).
* Квоту тратит ТОЛЬКО шаг /pricefeed/upload/merchant/process[/process/batch].
  Шаги validate/v2 (ON_SALE__PRICE_EDIT / ON_SALE__PRICE_SAVE),
  price/trends/discount и весь /bff/offer-view/* — бесплатны: проверено в
  момент, когда process уже отдавал 429, все они отвечали 200.
"""
import datetime
import logging
import re
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

LIMIT_SETTINGS_URL = (
    "https://mc.shop.kaspi.kz/pricefeed/upload/merchant/upload/limit/settings?m={muid}"
)

# Сколько записей из окна держим в резерве под P0 (снятие с продажи / стоп
# кол-во). Обычные записи цены и остатка останавливаются на отметке
# (limit - P0_RESERVE); оставшееся может потратить только P0. Решение
# пользователя 2026-08-12: 30 записей — хватает на две крупные Мастер-Группы.
P0_RESERVE = 30

# Насколько долго доверяем прочитанному состоянию квоты, прежде чем перечитать.
_QUOTA_TTL_SECONDS = 20.0

# Предельный возраст снимка, который ещё имеет смысл показывать в UI. Кэш
# наполняет флашер раз в 45 секунд; старше — значит писатель встал.
_PEEK_MAX_AGE_SECONDS = 180.0

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


def _parse_window_seconds(limit_time: Optional[str]) -> float:
    """'30m' -> 1800.0. Неизвестный формат -> 30 минут."""
    if not limit_time:
        return 1800.0
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", str(limit_time).lower())
    if not match:
        return 1800.0
    value, unit = int(match.group(1)), match.group(2)
    return float(value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])


@dataclass
class QuotaState:
    """Снимок окна квоты OFFER для одного мерчанта."""

    limit: int = 250
    used: int = 0
    reset_at: Optional[datetime.datetime] = None
    fetched_at: Optional[datetime.datetime] = None
    known: bool = False  # False = прочитать не удалось, работаем по консервативной оценке
    window_seconds: float = 1800.0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def budget_for(self, priority: int) -> int:
        """
        Сколько офферов можно записать прямо сейчас с данным приоритетом.
        P0 (0) видит всё окно, остальные — за вычетом резерва.
        """
        if priority <= 0:
            return self.remaining
        return max(0, self.remaining - P0_RESERVE)

    def seconds_until_reset(self, now: Optional[datetime.datetime] = None) -> float:
        """
        Сколько секунд до обнуления счётчика.

        Kaspi отдаёт expirationDate БЕЗ смещения зоны и в своём локальном
        времени (Алматы, UTC+5), а прод-сервер живёт в UTC — прямое вычитание
        давало +5 часов (наблюдалось живьём: 18394 секунды при окне в 30
        минут). Поэтому результат приводим к длине окна: смещение зоны всегда
        кратно часу, значит остаток от деления на час и есть настоящее время
        до сброса. Значение не гейтит записи (для этого есть remaining) —
        только логи и индикатор, поэтому в сомнительном случае округляем в
        большую сторону, к полному окну.
        """
        if not self.reset_at:
            return self.window_seconds
        now = now or datetime.datetime.now()
        raw = (self.reset_at - now).total_seconds()
        if raw < 0:
            return 0.0
        if raw <= self.window_seconds:
            return raw
        normalized = raw % 3600.0
        return normalized if normalized <= self.window_seconds else self.window_seconds


def _parse_quota(payload: dict) -> QuotaState:
    limits = payload.get("limits") or {}
    limit = int(limits.get("OFFER") or 250)
    used, reset_at = 0, None
    for stat in payload.get("statistics") or []:
        if stat.get("documentType") != "OFFER":
            continue
        used = int(stat.get("uploadedCount") or 0)
        raw = stat.get("expirationDate")
        if raw:
            try:
                reset_at = datetime.datetime.fromisoformat(raw)
            except ValueError:
                logger.debug("Не разобрали expirationDate квоты Kaspi: %r", raw)
        break
    if payload.get("unlimited"):
        # Мерчанту сняли лимит — не выдумываем ограничение, которого нет.
        limit = 10 ** 9
    return QuotaState(
        limit=limit,
        used=used,
        reset_at=reset_at,
        fetched_at=datetime.datetime.now(),
        known=True,
        window_seconds=_parse_window_seconds(payload.get("limitTime")),
    )


class KaspiOfferQuota:
    """
    Кэш состояния квоты по магазинам + оптимистичный локальный расход между
    перечитываниями (чтобы пачка из 40 офферов не ушла дважды, пока Kaspi
    ещё не успел отразить первую в своей статистике).
    """

    def __init__(self):
        self._states: dict = {}
        self._lock = threading.Lock()

    def _fetch(self, store_id, merchant_uid: str) -> QuotaState:
        from backend.state_helpers import _mc_api_request

        resp = _mc_api_request(
            "GET",
            LIMIT_SETTINGS_URL.format(muid=merchant_uid),
            store_id=store_id,
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"limit/settings вернул {resp.status_code}: {resp.text[:200]}")
        return _parse_quota(resp.json())

    def get(self, store_id, merchant_uid: str, force: bool = False) -> QuotaState:
        """
        Актуальное состояние квоты. Перечитывает у Kaspi не чаще раза в
        _QUOTA_TTL_SECONDS, либо принудительно (force=True — например сразу
        после 429, когда локальная оценка заведомо разошлась с реальностью).
        """
        now = datetime.datetime.now()
        with self._lock:
            cached: Optional[QuotaState] = self._states.get(store_id)
            if cached and not force:
                fresh = cached.fetched_at and (now - cached.fetched_at).total_seconds() < _QUOTA_TTL_SECONDS
                # Окно могло обнулиться, пока снимок лежал в кэше — тогда он
                # недействителен независимо от возраста.
                expired_window = cached.reset_at is not None and now >= cached.reset_at
                if fresh and not expired_window:
                    return cached

        try:
            state = self._fetch(store_id, merchant_uid)
        except Exception as e:
            logger.warning("Не удалось прочитать квоту Kaspi для стора %s: %s", store_id, e)
            with self._lock:
                cached = self._states.get(store_id)
            if cached and cached.reset_at and now < cached.reset_at:
                # Есть прошлый снимок в пределах того же окна — он всё ещё
                # осмысленен (used мог только вырасти, а мы его вели локально).
                return cached
            # Нет ничего: считаем окно ИСЧЕРПАННЫМ, а не свободным. Ошибиться
            # в сторону "не пишем" дешевле, чем словить 429 и остановить очередь.
            return QuotaState(limit=250, used=250, reset_at=None, fetched_at=now, known=False)

        with self._lock:
            self._states[store_id] = state
        return state

    def peek(self, store_id) -> Optional[QuotaState]:
        """
        Последний известный снимок БЕЗ похода в Kaspi.

        Ровно для показа в UI: `/api/dumping/status` фронтенд поллит раз в 10
        секунд, и дёргать оттуда limit/settings значило бы гонять к Kaspi 360
        запросов в час на каждую открытую вкладку — тот самый паттерн, что уже
        приводил к бану IP (см. HANDOFF 3.8). Снимок наполняет флашер раз в 45
        секунд, этого для индикатора достаточно.
        """
        with self._lock:
            state = self._states.get(store_id)
        if not state or not state.fetched_at:
            return None
        # Свежесть меряем по возрасту снимка, а НЕ по reset_at: тот приходит в
        # алматинском времени без смещения и с UTC-сервером напрямую не
        # сравнивается (см. seconds_until_reset). Флашер обновляет кэш раз в 45
        # секунд, поэтому протухший снимок означает, что писатель встал — и
        # честнее не показать ничего, чем показать залипшие цифры.
        if (datetime.datetime.now() - state.fetched_at).total_seconds() > _PEEK_MAX_AGE_SECONDS:
            return None
        return state

    def consume(self, store_id, count: int) -> None:
        """Локально засчитать уже отправленные офферы, не дожидаясь перечитывания."""
        if count <= 0:
            return
        with self._lock:
            state = self._states.get(store_id)
            if state:
                state.used += count

    def mark_exhausted(self, store_id, merchant_uid: str) -> QuotaState:
        """Вызывается при 429: перечитываем правду у Kaspi и возвращаем её."""
        state = self.get(store_id, merchant_uid, force=True)
        if state.known and state.remaining > 0:
            # Kaspi сказал 429, но по статистике место есть — значит счётчик
            # ещё не догнал наши записи. Считаем окно выбранным до сброса.
            with self._lock:
                state.used = state.limit
        return state


quota = KaspiOfferQuota()
