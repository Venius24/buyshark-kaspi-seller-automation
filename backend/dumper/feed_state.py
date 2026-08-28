"""
Состояние прайс-фида по магазину: включён ли, когда была последняя отправка, чем кончилась.

Почему файл, а не таблица. Состояний тут ровно одно на магазин, читается оно
раз в цикл воркера, и ни один запрос к нему не идёт из веба. Заводить ради
этого миграцию, модель и alembic-ревизию — это три новых места, где можно
ошибиться, ради данных, которые помещаются в тридцать байт. Проект уже держит
такую мелочь в `states/` (см. `backend/chatbot/config.py`), и файл лежит рядом
с архивом отправленных фидов — то есть всё про фид данного магазина в одном
каталоге.

Ключевое поведение — `disable()`. Фид выключается автоматически при любом
подозрении на потерю карточек, и обратно его включает только человек. Это
намеренно несимметрично: цена ложной тревоги — полчаса без фида, цена
пропущенной — ночь ручного восстановления каталога.
"""
from typing import Optional
import datetime
import json
import logging
import pathlib

logger = logging.getLogger(__name__)

# Магазин меньше этого числа активных офферов фид не получает никогда. Порог не
# произвольный: 250 — это ровно размер окна `OFFER` у Kaspi (250 записей за 30
# минут). Магазин, который целиком помещается в одно окно, обновится JSON-ом
# быстрее и точнее, а файл принёс бы ему только риск объявить весь каталог
# разом. Решение владельца 2026-08-25.
MIN_OFFERS_FOR_FEED = 250


def is_shadow() -> bool:
    """
    Теневой режим: планы собираются, файлы не уходят.

    Предикат живёт здесь, а не в воркере, потому что спрашивают его двое —
    воркер и сторож каталога (`feed_guard`), — а импорт воркера из сторожа дал
    бы цикл. Модуль состояния зависимостей не имеет вообще, и это делает его
    единственным местом, куда оба могут смотреть.
    """
    import os

    return os.getenv("XML_FEED_WORKER", "0") != "1"


def _state_path(store_id) -> pathlib.Path:
    base = pathlib.Path(__file__).resolve().parents[2] / "states" / "feeds" / str(store_id)
    base.mkdir(parents=True, exist_ok=True)
    return base / "state.json"


def load(store_id) -> dict:
    """Состояние магазина. Отсутствие файла = фид выключен, и это правильный дефолт."""
    try:
        return json.loads(_state_path(store_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False, "blocked_reason": None}


def save(store_id, **fields) -> dict:
    state = load(store_id)
    state.update(fields)
    try:
        _state_path(store_id).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as err:
        logger.error("[FeedState] Не удалось сохранить состояние %s: %r", store_id, err)
    return state


def is_enabled(store_id) -> bool:
    return bool(load(store_id).get("enabled"))


def enable(store_id) -> dict:
    """Включить фид. Только явным действием — воркер сам себя не включает."""
    return save(store_id, enabled=True, blocked_reason=None)


def disable(store_id, reason: str) -> dict:
    """
    Выключить фид и запомнить, почему.

    Обратно включает только человек: раз мы заподозрили потерю карточек, второй
    файл поверх непонятного состояния каталога — худшее, что можно сделать.
    """
    logger.error("[FeedState] Фид выключен для %s: %s", store_id, reason)
    return save(
        store_id,
        enabled=False,
        blocked_reason=reason,
        blocked_at=datetime.datetime.now().isoformat(),
    )


# Сколько считать окно полёта, пока своего замера нет. Живой замер на Бархане
# 2026-08-26: снимок 19:05:53, файл применён 19:10:31 — 4 минуты 38 секунд на
# 1100 офферах. Берём пять минут: ошибиться в большую сторону здесь безопаснее,
# потому что число это показывается человеку как «не трогай карточки до».
DEFAULT_FLIGHT_SECONDS = 5 * 60


def expected_flight_seconds(store_id) -> float:
    """
    Сколько продлится окно, в котором правка в кабинете будет потеряна.

    Считается от факта прошлой обработки (`endDate − createdDate` из карточки
    файла), а не константой: время разбора растёт с размером каталога, и за
    пределами замеренных ~1100 офферов оно неизвестно.
    """
    processed = load(store_id).get("last_processing_seconds")
    if not processed:
        return DEFAULT_FLIGHT_SECONDS
    return max(float(processed), 60.0)


def mark_sent(store_id, file_name: str, snapshot_at) -> dict:
    """
    Отметить файл в полёте и посчитать, когда окно слепоты закроется.

    `in_flight_until` — не украшение и не отладка. Пока файл летит, он несёт
    значения на момент снимка, и правка, сделанная человеком в кабинете за эти
    минуты, будет молча откачена: сверка сравнивает объявленное с фактом, а
    после отката они совпадают (см. докстринг feed_settle). Ловушки против
    этого нет — есть только предупреждение, и оно должно быть предъявимо
    человеку, а не жить в голове у того, кто читал код.

    Отсчёт идёт от СНИМКА, а не от момента отправки: значения устарели именно
    тогда, а не когда файл ушёл.
    """
    now = datetime.datetime.now()
    started = snapshot_at if isinstance(snapshot_at, datetime.datetime) else now
    until = started + datetime.timedelta(seconds=expected_flight_seconds(store_id))
    return save(
        store_id,
        in_flight=file_name,
        sent_at=now.isoformat(),
        in_flight_until=until.isoformat(),
        snapshot_at=snapshot_at.isoformat() if hasattr(snapshot_at, "isoformat") else snapshot_at,
    )


def mark_settled(store_id, report) -> dict:
    return save(
        store_id,
        in_flight=None,
        in_flight_until=None,
        settled_at=datetime.datetime.now().isoformat(),
        last_file_id=report.file_id,
        last_summary=report.summary(),
    )


def blind_window(store_id) -> Optional[dict]:
    """
    Открытое сейчас окно слепоты, либо None.

    Отдельная функция, а не чтение поля, потому что читателей будет несколько —
    лог, бот, а со временем и API панели, — и «истёк ли срок» каждый из них
    считал бы сам, по-своему и рано или поздно неправильно.
    """
    state = load(store_id)
    raw = state.get("in_flight_until")
    if not raw:
        return None
    try:
        until = datetime.datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    left = (until - datetime.datetime.now()).total_seconds()
    if left <= 0:
        return None
    return {"until": until, "seconds_left": left, "file": state.get("in_flight")}


def eligible(store_id, active_offers: int) -> Optional[str]:
    """
    Причина, по которой фид этому магазину сейчас не положен. None = положен.

    Возвращается именно причина, а не bool: воркер должен уметь объяснить в
    логе, почему он промолчал, иначе «фид не работает» превращается в
    расследование.
    """
    state = load(store_id)
    if not state.get("enabled"):
        return state.get("blocked_reason") or "фид выключен для магазина"
    if active_offers < MIN_OFFERS_FOR_FEED:
        return (
            "в магазине {} активных офферов — меньше порога {}. Такой каталог "
            "целиком помещается в одно окно квоты OFFER, файл ему не нужен".format(
                active_offers, MIN_OFFERS_FOR_FEED
            )
        )
    if state.get("in_flight"):
        return "предыдущий файл ещё в полёте ({})".format(state["in_flight"])
    return None
