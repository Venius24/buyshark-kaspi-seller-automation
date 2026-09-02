"""
Сторож снятых с продажи товаров: ловит переход `available: true → false`
между двумя синхронизациями каталога и будит владельца в Телеграм.

Зачем отдельный модуль, а не строчка в `products_sync_worker`
---------------------------------------------------------------
`products_sync_worker._sync_store_products` и так выкачивает весь каталог
раз в 30 минут — тем же вызовом, что `feed_snapshot` и `feed_guard`, через
VPS, без единого лишнего запроса в Kaspi (`bff/offer-view/list` бесплатен).
Сторож садится на готовый список, ровно как `feed_guard.guard_catalogue`.

Правило одно: смотрим только на поле `available`. Оффер, пропавший из ответа
API по любой другой причине (сетевой сбой, неполная страница), это не
«снят с продажи» — это дефект выкачки, и о нём уже говорит `feed_snapshot`
своими собственными проверками (усадка, `AssortmentTruncated`). Здесь эта
путаница не нужна: `available` — явный флаг Kaspi, а не наша реконструкция.

Мастер-группы — не потеря
--------------------------
Веерная рассылка остатка (`stock_sync_worker._execute_broadcast_sync_impl`)
разносит `shared_stock` группы по всем `linked_skus`. Когда пул исчерпан
(`shared_stock <= 0`), КАЖДЫЙ SKU группы честно уходит в 0 остатка и следом
в архив — это ожидаемое поведение самой механики группы, а не событие,
о котором нужно будить человека. Считать его тревогой значило бы получать
уведомление о снятии каждый раз, когда заканчивается общий сток, — то есть
регулярно и бессмысленно.

Первый прогон
--------------
Если для магазина ещё нет запомненного набора архива (`archived_skus is
None` в состоянии), сравнивать не с чем — весь текущий архив может быть
старым, накопленным годами. Такой прогон только сеет базу для сравнения,
без единого уведомления: иначе первый же тик по новому магазину высыпал бы
сотни «снятых» товаров, ни один из которых снят не только что.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import datetime
import logging

from backend.dumper import feed_state

logger = logging.getLogger(__name__)


@dataclass
class ArchiveVerdict:
    """Что сторож увидел на этом проходе."""

    store_id: str
    # Новые снятия, не объяснённые мастер-группой — по ним и будим человека.
    new: List[str] = field(default_factory=list)
    # Новые снятия, объяснённые исчерпанием пула группы — в уведомление не
    # идут, но видны в объекте (для логов и тестов).
    skipped_group: List[str] = field(default_factory=list)
    first_run: bool = False

    @property
    def needs_attention(self) -> bool:
        return bool(self.new)


def _available_false_skus(products: list) -> dict:
    """SKU → название, для офферов, которых Kaspi считает снятыми.

    Изначально здесь читалось сырое поле `available`. Оказалось, что до
    этого словаря оно не доезжает: `KaspiAPI.sync_merchant_assortment`
    ходит двумя раздельными запросами (`a=true`/`a=false` — см.
    kaspi_wrapper.py:1155) и КАЖДЫЙ оффер уже приходит из своей однозначной
    выборки; сырого булева поля в самом объекте offer нет, есть только
    посчитанный по этому же признаку `status` ("Активен"/"Архив",
    kaspi_wrapper.py:1244). Значит `status == "Архив"` здесь и есть тот
    самый явный, а не выведенный нами сигнал — то же самое, чем в
    `feed_snapshot._is_active` служит фолбэк, только здесь он единственный
    источник, а не подстраховка.
    """
    out = {}
    for p in products or []:
        if p.get("status") != "Архив":
            continue
        sku = str(p.get("merchantSku") or "")
        if sku:
            out[sku] = p.get("name") or sku
    return out


def _group_exhausted_skus(store_id) -> set:
    """SKU, чей пул общего остатка исчерпан — их снятие объясняет группа."""
    try:
        from backend.state_helpers import _load_master_stock_groups
    except ImportError:
        return set()

    try:
        groups = _load_master_stock_groups(store_id=store_id)
    except Exception as err:
        logger.warning("[ArchiveWatch] Мастер-группы не прочитались: %r", err)
        return set()

    out = set()
    for g in (groups or {}).values():
        if (g.get("shared_stock") or 0) <= 0:
            out.update(g.get("linked_skus") or [])
    return out


def check_archived(store_id, products: list) -> ArchiveVerdict:
    """
    Сравнить текущий срез `available=false` с прошлым, записать новый срез.

    Разделено на «проверить» и «уведомить» по тому же принципу, что у
    `feed_guard`: падение Телеграма не должно мешать записать факт.
    """
    current = _available_false_skus(products)
    state = feed_state.load(store_id)
    known = state.get("archived_skus")

    feed_state.save(
        store_id,
        archived_skus=sorted(current.keys()),
        archived_skus_at=datetime.datetime.now().isoformat(),
    )

    verdict = ArchiveVerdict(store_id=str(store_id))
    if known is None:
        verdict.first_run = True
        logger.info(
            "[ArchiveWatch] Стор %s: первый прогон, база из %s снятых офферов "
            "запомнена без тревоги.", store_id, len(current),
        )
        return verdict

    newly = set(current.keys()) - set(known)
    if not newly:
        return verdict

    exhausted = _group_exhausted_skus(store_id)
    verdict.skipped_group = sorted(newly & exhausted)
    verdict.new = sorted(newly - exhausted)
    return verdict


async def _notify(store_id, verdict: ArchiveVerdict, names: dict) -> None:
    from backend.chatbot import notify_dispatch

    lines = ["📦 *СНЯТО С ПРОДАЖИ*", ""]
    lines.append("Kaspi сняла {} офферов:".format(len(verdict.new)))
    for sku in verdict.new[:10]:
        label = names.get(sku, sku)
        lines.append("  • `{}` — {}".format(sku, label) if label != sku else "  • `{}`".format(sku))
    if len(verdict.new) > 10:
        lines.append("  • …и ещё {}".format(len(verdict.new) - 10))
    if verdict.skipped_group:
        lines += [
            "",
            "(Ещё {} снято из-за исчерпания остатка мастер-группы — не показываю, "
            "это ожидаемо.)".format(len(verdict.skipped_group)),
        ]

    try:
        await notify_dispatch.dispatch(
            str(store_id), "problems", "\n".join(lines),
            urgent=True,
            dedup_key="archive-watch-{}-{}".format(store_id, hash(tuple(verdict.new))),
        )
    except Exception as err:
        logger.error("[ArchiveWatch] Тревога о снятых товарах не ушла в Телеграм: %r", err)


async def watch_archived(store_id, products: list) -> ArchiveVerdict:
    """Полный проход: проверить, записать, разбудить человека при находке."""
    verdict = check_archived(store_id, products)
    if not verdict.needs_attention:
        return verdict

    names = _available_false_skus(products)
    await _notify(store_id, verdict, names)
    return verdict
