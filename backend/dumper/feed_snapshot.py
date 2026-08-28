"""
Сборщик снимка каталога для прайс-фида — с инвариантом полноты.

Зачем отдельный модуль, а не «взять кэш товаров и отдать в сериализатор».
Прайс-лист объявляет ВЕСЬ каталог: оффер, которого в файле нет, Kaspi снимает
с продажи с причиной «не указан в прайс-листе». Файл на два SKU однажды погасил
у владельца ~798 карточек (HANDOFF.md §3.67). Значит неполный снимок — это не
«чуть меньше данных», это команда на массовое снятие с продажи, и отличить её
от нормального файла Kaspi не может. Отличать обязаны мы, до отправки.

Отсюда принцип: **отказ вместо частичного результата**. Любое сомнение в
полноте — исключение `FeedSnapshotRefused`, а не файл с дырой. Пропущенный
оффер стоит дороже, чем пропущенная выгрузка: следующая выгрузка будет через
полчаса, а снятые карточки владелец восстанавливал руками всю ночь.

Три независимые проверки, потому что каждая ловит свой класс беды:
  1. обрыв пагинации  — Kaspi/сеть отдали не весь каталог;
  2. нечитаемые поля  — каталог весь, но про часть офферов мы врём;
  3. усадка снимка    — каталог весь и читаемый, но подозрительно похудел
                        относительно того, что мы видели в прошлый раз.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import datetime
import logging

from backend.kaspi_wrapper import AssortmentTruncated, KaspiAPI

logger = logging.getLogger(__name__)

# Насколько снимок может похудеть относительно прошлого наблюдения, прежде чем
# мы сочтём это не распродажей, а поломкой. 10% каталога Бархана — это ~110
# офферов; настоящая убыль такого размера за один цикл означает, что случилось
# что-то, о чём человек должен узнать раньше, чем Kaspi.
MAX_SHRINK_RATIO = 0.10

# Абсолютный пол: снимок меньше этого не бывает у живого магазина, а вот у
# сломанной выкачки — запросто.
MIN_PLAUSIBLE_OFFERS = 5


class FeedSnapshotRefused(Exception):
    """Снимок не собран: собрать его честно нельзя, а нечестно — нельзя тем более."""

    def __init__(self, reason: str, detail: Optional[str] = None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason if not detail else f"{reason} — {detail}")


@dataclass
class CatalogSnapshot:
    """Полный, проверенный снимок активного каталога, годный для сериализации."""

    store_id: str
    merchant_id: str
    offers: List[dict]
    taken_at: datetime.datetime
    archived_count: int = 0
    # Артикулы снятых офферов — поимённо, а не счётчиком. Нужны сверке после
    # вердикта: карточка, уехавшая в архив, пока файл был в полёте, в счётчике
    # не видна (её создание и гашение дают нетто-ноль), а в разности множеств
    # видна сразу. См. feed_settle.
    archived_skus: List[str] = field(default_factory=list)
    baseline_count: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.offers)


def _is_active(product: dict) -> bool:
    """Активность оффера. `available` — поле BFF, `status` — наш ярлык поверх него."""
    if "available" in product and product["available"] is not None:
        return bool(product["available"])
    return product.get("status") == "Активен"


# Значения, которые придумывает kaspi_wrapper, когда BFF не отдал поле
# ([backend/kaspi_wrapper.py:1214](backend/kaspi_wrapper.py:1214)). Для интерфейса
# это безобидная косметика, для прайс-листа — запись выдумки в чужой каталог.
BRAND_PLACEHOLDER = "Без бренда"


def _undecorate(product: dict) -> dict:
    """
    Убрать из оффера подставленные нами значения, вернув «поля нет».

    Замечено на первом живом dry-run Бархана: файл содержал 180 названий вида
    `Товар S517-0015` и 336 брендов `Без бренда` — там, где в собственной
    выгрузке Kaspi стоят пустые `<model/>` и `<brand/>`. Хуже того, 65 таких
    заглушек уже лежат в каталоге: они уехали туда с ручной загрузкой бэкапа
    15.08. Фид не имеет права дописывать то, чего в источнике не было.
    """
    clean = dict(product)
    sku = str(product.get("merchantSku") or "")
    if clean.get("name") and sku and clean["name"] == f"Товар {sku}":
        clean["name"] = None
    if clean.get("brand") == BRAND_PLACEHOLDER:
        clean["brand"] = None
    return clean


def _baseline_active_count(store_id: str) -> Optional[int]:
    """
    Сколько активных офферов мы видели в прошлый раз — по кэшу ассортимента.

    Кэш здесь не источник данных для файла (он может быть устаревшим и сам
    собран усечённой выкачкой), а независимый свидетель порядка величины.
    Его задача — заметить «было 1119, стало 60», а не выверять точное число.
    """
    try:
        from backend.state_helpers import _load_products

        cached = _load_products(store_id) or []
    except Exception as err:  # кэша может не быть вовсе — это не повод падать
        logger.warning("[FeedSnapshot] Кэш ассортимента недоступен: %r", err)
        return None
    if not cached:
        return None
    return sum(1 for p in cached if _is_active(p))


def build_catalog_snapshot(
    token: str,
    store_id: str,
    baseline_count: Optional[int] = None,
    max_shrink_ratio: float = MAX_SHRINK_RATIO,
) -> CatalogSnapshot:
    """
    Выкачать и проверить полный активный каталог магазина.

    Ничего не пишет в Kaspi и не тратит квоту OFFER/FILE: `bff/offer-view/list`
    бесплатен (HANDOFF.md §3.39, п.5).

    baseline_count — с чем сравнивать размер. По умолчанию берётся из кэша
    ассортимента; передать явно имеет смысл в тестах и когда кэшу веры нет.

    Бросает FeedSnapshotRefused, если снимок нельзя считать полным.
    """
    # ── 1. Выкачка. strict=True: обрыв пагинации — исключение, а не короткий
    # список, и фоллбеки на заказы/кэш запрещены (полного каталога не дают).
    try:
        products = KaspiAPI.sync_merchant_assortment(token, store_id=store_id, strict=True)
    except AssortmentTruncated as err:
        raise FeedSnapshotRefused(
            "выкачка каталога оборвалась — снимок неполный",
            f"{err.reason}; успели набрать {err.collected}",
        ) from err
    except Exception as err:
        raise FeedSnapshotRefused("не удалось выкачать каталог", repr(err)) from err

    active = [p for p in products if _is_active(p)]
    archived_products = [p for p in products if not _is_active(p)]
    archived = len(archived_products)

    if len(active) < MIN_PLAUSIBLE_OFFERS:
        raise FeedSnapshotRefused(
            "активных офферов подозрительно мало",
            f"{len(active)} штук при пороге {MIN_PLAUSIBLE_OFFERS}",
        )

    # ── 2. Читаемость. Отбрасывать «плохие» офферы нельзя: отброшенный оффер
    # исчезнет из файла, а исчезнуть из файла = быть снятым с продажи. Поэтому
    # любая дырка останавливает сборку целиком.
    no_sku, bad_price, bad_stock = [], [], []
    for p in active:
        if not p.get("merchantSku"):
            no_sku.append(p.get("sku"))
            continue
        try:
            price = int(p.get("price_num"))
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            bad_price.append(p.get("merchantSku"))
        try:
            stock = float(p.get("stock"))
        except (TypeError, ValueError):
            stock = -1.0
        if stock < 0:
            bad_stock.append(p.get("merchantSku"))

    if no_sku or bad_price or bad_stock:
        raise FeedSnapshotRefused(
            "в каталоге есть офферы, которые нельзя записать в фид честно",
            "без merchantSku: {} (напр. {}); без цены: {} (напр. {}); "
            "без остатка: {} (напр. {})".format(
                len(no_sku), no_sku[:5],
                len(bad_price), bad_price[:5],
                len(bad_stock), bad_stock[:5],
            ),
        )

    duplicates = len(active) - len({p["merchantSku"] for p in active})
    if duplicates:
        raise FeedSnapshotRefused(
            "в снимке есть дубли merchantSku",
            f"{duplicates} повторов — неясно, чья цена настоящая",
        )

    # ── 3. Усадка. Каталог целый и читаемый, но резко похудел — возможно, это
    # правда (массовое снятие), а возможно, выкачка соврала «успешно». Решает
    # человек, а не мы: до его решения файл не собирается.
    notes = []
    if baseline_count is None:
        baseline_count = _baseline_active_count(store_id)
    if baseline_count:
        allowed = baseline_count * (1 - max_shrink_ratio)
        if len(active) < allowed:
            raise FeedSnapshotRefused(
                "снимок резко меньше прошлого наблюдения",
                f"было ~{baseline_count} активных, стало {len(active)} "
                f"(порог усадки {int(max_shrink_ratio * 100)}%)",
            )
        if len(active) > baseline_count:
            notes.append(f"каталог вырос: было ~{baseline_count}, стало {len(active)}")
    else:
        notes.append("не с чем сравнить размер — базового наблюдения нет")

    merchant_id = str(KaspiAPI.get_merchant_id(token))
    snapshot = CatalogSnapshot(
        store_id=store_id,
        merchant_id=merchant_id,
        offers=[_undecorate(p) for p in active],
        taken_at=datetime.datetime.now(),
        archived_count=archived,
        archived_skus=sorted(
            str(p.get("merchantSku")) for p in archived_products if p.get("merchantSku")
        ),
        baseline_count=baseline_count,
        notes=notes,
    )
    logger.info(
        "[FeedSnapshot] Снимок собран: %s активных, %s снятых, база сравнения %s. %s",
        snapshot.size, archived, baseline_count, "; ".join(notes) or "замечаний нет",
    )
    return snapshot
