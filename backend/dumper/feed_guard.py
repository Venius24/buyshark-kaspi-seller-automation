"""
Сторож каталога: ловит артикулы, которые не переживут приём прайс-листа.

Зачем отдельный модуль, если отказ уже есть в трёх местах
--------------------------------------------------------
Отправить файл с таким артикулом и до этого было нельзя:

· `build_feed_plan` считает `plan.blockers` и пишет предупреждение;
· `send_plan` на непустых `blockers` бросает `FeedUploadRefused` — безусловно,
  этот замок не снимает ни один флаг;
· боевой `run_for_store` на них зовёт `feed_state.disable` и будит человека.

Дыра была не в запрете, а в МОМЕНТЕ, когда он срабатывает. Все три проверки
живут за сборкой плана, а план собирается, только когда открылись ворота
воркера (сотня намерений или давление квоты выше 60%). Пока ворота закрыты,
битый артикул в каталоге не существует ни для кого: файл не уходит — но и не
скажет, почему. 2026-08-28 Бархан так и стоял, и узналось об этом чтением
логов руками, а не тревогой.

Сторож закрывает именно этот разрыв: он смотрит каталог там, где каталог и так
уже выкачан целиком — в синхронизации ассортимента, раз в полчаса, без единого
лишнего запроса в Kaspi. Ему не нужны ни ворота, ни план, ни квота.

Правило битого артикула ровно одно и живёт в `feed_sku_roundtrip`: артикул
битый, если после приёма Kaspi он перестаёт быть собой. Сторож не заводит
второе, а зовёт ту же функцию — иначе он начнёт расходиться с планом, и хуже
разрыва будет только разрыв, о котором оба уверены, что его нет.

Чего сторож НЕ делает
---------------------
Не включает фид обратно. Артикул починили — состояние это увидит и скажет в
лог, но рубильник поднимает человек: `disable()` несимметричен намеренно (см.
докстринг `feed_state`), и автоматический подъём съел бы ровно ту защиту, ради
которой он несимметричен.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import logging

from backend.dumper import feed_state
from backend.dumper.xml_feed import describe_sku_defect, feed_sku_roundtrip

logger = logging.getLogger(__name__)


@dataclass
class GuardVerdict:
    """Что сторож увидел в каталоге магазина и что с этим сделал."""

    store_id: str
    blockers: List[str] = field(default_factory=list)
    # Набор отличается от запомненного в прошлый проход. Тревога уходит только
    # по этому флагу: сторож ходит раз в полчаса, а чинят артикул днями.
    changed: bool = False
    # Фид выключен ЭТИМ проходом (а не был выключен раньше).
    disabled: bool = False
    reason: Optional[str] = None

    @property
    def clean(self) -> bool:
        return not self.blockers


def broken_skus(products: list) -> List[str]:
    """
    Артикулы активных офферов, которые файл не сможет адресовать.

    Активность считается тем же `_is_active`, что и в снимке фида. Импорт
    приватного имени тут осознан: своя копия правила «что такое активный
    оффер» разошлась бы со снимком на первом же изменении поля `available`, а
    разошедшийся сторож опаснее отсутствующего — он говорит «чисто» про
    каталог, который планом чист не будет.
    """
    from backend.dumper.feed_snapshot import _is_active

    bad = set()
    for product in products or []:
        if not _is_active(product):
            continue
        sku = str(product.get("merchantSku") or "")
        if sku and feed_sku_roundtrip(sku) != sku:
            bad.add(sku)
    return sorted(bad)


def _active_count(products: list) -> int:
    from backend.dumper.feed_snapshot import _is_active

    return sum(1 for p in products or [] if _is_active(p))


def check_catalogue(store_id, products: list) -> GuardVerdict:
    """
    Проверить каталог и записать вывод в состояние магазина. Тревогу не шлёт.

    Разделено на «проверить» и «сказать» ради тестов и ради того, чтобы падение
    Телеграма не мешало записать факт: состояние — это то, по чему потом
    объясняются, а сообщение можно и переслать.
    """
    verdict = GuardVerdict(store_id=str(store_id))
    state = feed_state.load(store_id)
    known = list(state.get("last_blockers") or [])

    verdict.blockers = broken_skus(products)
    verdict.changed = set(verdict.blockers) != set(known)

    if not verdict.blockers:
        if known:
            # Не поднимаем рубильник — только снимаем метку, чтобы следующий
            # битый артикул был виден как новый, а не как «тот же самый».
            feed_state.save(store_id, last_blockers=[], last_blockers_at=None)
            logger.info(
                "[FeedGuard] Стор %s: битых артикулов больше нет (было %s). "
                "Фид сам не включится — это делает человек.",
                store_id, [describe_sku_defect(s) for s in known[:5]],
            )
        return verdict

    verdict.reason = "битые артикулы в каталоге: {}".format(
        [describe_sku_defect(s) for s in verdict.blockers[:5]]
    )
    feed_state.save(
        store_id,
        last_blockers=verdict.blockers[:20],
        last_blockers_at=_now_iso(),
    )

    # Порог каталога тот же, что у `eligible`: магазину меньше 250 офферов файл
    # не положен вообще, и выключать ему нечего.
    if _active_count(products) < feed_state.MIN_OFFERS_FOR_FEED:
        logger.info(
            "[FeedGuard] Стор %s: %s, но магазин ниже порога фида — только записал.",
            store_id, verdict.reason,
        )
        return verdict

    if feed_state.is_shadow():
        # В тени рубильник не трогаем по той же причине, что и в воркере:
        # `eligible()` начал бы отбивать проход до сборки плана, и теневые
        # наблюдения, ради которых тень и включена, перестали бы собираться
        # (замерено 2026-08-27 — 12 часов без единого тика).
        logger.warning(
            "[FeedGuard] Стор %s: %s. Тень: рубильник не тронут.",
            store_id, verdict.reason,
        )
        return verdict

    if state.get("enabled"):
        feed_state.disable(store_id, verdict.reason)
        verdict.disabled = True
    else:
        logger.warning(
            "[FeedGuard] Стор %s: %s. Фид и так выключен.", store_id, verdict.reason
        )
    return verdict


async def guard_catalogue(store_id, products: list) -> GuardVerdict:
    """
    Полный проход: проверить, записать, разбудить человека при новой находке.

    Тревога уходит на СМЕНУ набора, а не на его наличие: артикул чинят руками в
    кабинете, это дни, а сторож ходит каждые полчаса. Ежечасное «всё ещё
    сломано» приучает не читать тревоги — ровно тот исход, против которого
    вообще заводился канал.
    """
    verdict = check_catalogue(store_id, products)
    if not verdict.blockers or not verdict.changed:
        return verdict

    state = feed_state.load(store_id)
    if not (verdict.disabled or state.get("enabled")):
        # Фид этому магазину сейчас не работает — сообщать не о чем, находка
        # уже лежит в состоянии и всплывёт при включении.
        return verdict

    await _notify(store_id, verdict)
    return verdict


async def _notify(store_id, verdict: GuardVerdict) -> None:
    from backend.chatbot import notify_dispatch

    head = "🚨 *ПРАЙС-ЛИСТ ОСТАНОВЛЕН*" if verdict.disabled else "⚠️ *ПРАЙС-ЛИСТ НЕ ПОЙДЁТ*"
    lines = [
        head,
        "",
        "В каталоге появились артикулы с невидимыми символами по краям:",
    ]
    lines += ["  • `{}`".format(describe_sku_defect(s)) for s in verdict.blockers[:10]]
    if len(verdict.blockers) > 10:
        lines.append("  • …и ещё {}".format(len(verdict.blockers) - 10))
    lines += [
        "",
        "Kaspi обрежет края при приёме файла, не найдёт такой артикул в "
        "каталоге и снимет оффер с продажи. Пока они активны, файл по этому "
        "магазину не отправляется вообще — магазин работает по обычному пути.",
        "",
        "Чинится только в кабинете: пересоздать карточку с чистым артикулом, "
        "битую снять с продажи.",
    ]
    try:
        await notify_dispatch.dispatch(
            str(store_id), "problems", "\n".join(lines),
            urgent=True,
            # В ключ входит сам набор: тот же набор не разбудит второй раз, а
            # новый артикул разбудит, даже если старые ещё не починены.
            dedup_key="feed-blockers-{}-{}".format(store_id, hash(tuple(verdict.blockers))),
        )
    except Exception as err:
        logger.error("[FeedGuard] Тревога о битых артикулах не ушла в Телеграм: %r", err)


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now().isoformat()
