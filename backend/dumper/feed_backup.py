"""
Точки восстановления прайс-листа: сжатые копии и вычистка старья.

Зачем это вообще нужно
----------------------
Файл — декларация всего каталога. Если он однажды объявит не то, восстановление
идёт не «откатом транзакции», а повторной отправкой правильного файла. Значит
правильный файл должен где-то лежать, причём именно тот, что был ДО поломки.

Хранятся две разные вещи, и подменять одну другой нельзя:

· `kaspi-export` — каталог глазами Kaspi на момент снимка. Это ответ на вопрос
  «как было на самом деле», и именно он годится для восстановления карточек.
· `sent` — файл, который отправили мы. Это ответ на другой вопрос — «что мы
  объявили», — и он нужен сверке, а не восстановлению. Наш файл мог быть
  неправильным: он и есть подозреваемый.

Почему три дня
--------------
Срок выбран не «чтоб не разрослось», а по времени обнаружения. Поломка каталога
замечается либо тревогой (минуты), либо владельцем (часы), либо падением продаж
(день-два). Трое суток покрывают худший из этих случаев с запасом, а дальше
копия бесполезна: за неделю каталог уезжает так, что восстановление по ней
навредит больше, чем поможет.

Почему gzip
-----------
Прайс-лист — это XML с тысячей однотипных блоков, он жмётся примерно в десять
раз. Бархан при часовой отправке — 480 КБ на файл, ~11 МБ в сутки, около 4 ГБ
в год. После сжатия и ротации — единицы мегабайт постоянно.
"""
from typing import List, Optional
import datetime
import gzip
import logging
import pathlib
import re

logger = logging.getLogger(__name__)

KEEP_DAYS = 3

# Сколько копий каждого вида переживают ротацию независимо от возраста.
#
# Не украшение. Фид может простоять выключенным неделю — например, из-за битого
# артикула, который чинят руками, — и тогда все копии окажутся старше трёх дней.
# Удалить их означало бы остаться вообще без точки восстановления ровно в тот
# момент, когда магазин в подвешенном состоянии.
ALWAYS_KEEP = 1

_STAMP = "%Y%m%d-%H%M%S"
_NAME_RE = re.compile(r"^(?P<kind>[a-z-]+)-(?P<stamp>\d{8}-\d{6})\.xml\.gz$")


def _backup_dir(store_id) -> pathlib.Path:
    base = (
        pathlib.Path(__file__).resolve().parents[2]
        / "states" / "feeds" / str(store_id) / "backups"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _parse(path: pathlib.Path):
    m = _NAME_RE.match(path.name)
    if not m:
        return None, None
    try:
        return m.group("kind"), datetime.datetime.strptime(m.group("stamp"), _STAMP)
    except ValueError:
        return None, None


def save(store_id, data: bytes, kind: str, at: Optional[datetime.datetime] = None) -> Optional[pathlib.Path]:
    """
    Положить сжатую копию. Возвращает путь либо None, если не легло.

    Не бросает: потеря копии — плохо, но останавливать из-за неё отправку или
    выкачку каталога хуже. Тот, кто зовёт, продолжает работать.
    """
    if not data:
        return None
    at = at or datetime.datetime.now()
    path = _backup_dir(store_id) / "{}-{}.xml.gz".format(kind, at.strftime(_STAMP))
    if path.exists():
        # Тот же файл под тем же штампом — например, выгрузка Kaspi, которую мы
        # скачали повторно, пока она не перегенерировалась. Второй копии не надо.
        return path
    try:
        path.write_bytes(gzip.compress(data, compresslevel=6))
    except OSError as err:
        logger.warning("[FeedBackup] Копия %s не сохранилась: %r", path.name, err)
        return None
    logger.info(
        "[FeedBackup] %s: %s КБ -> %s КБ", path.name, len(data) // 1024, path.stat().st_size // 1024
    )
    return path


def load(path) -> bytes:
    """Разжать копию обратно."""
    return gzip.decompress(pathlib.Path(path).read_bytes())


def restore_points(store_id, kind: Optional[str] = None) -> List[dict]:
    """Копии от новых к старым. Это то, что показывают человеку при разборе."""
    points = []
    for path in _backup_dir(store_id).glob("*.xml.gz"):
        found_kind, at = _parse(path)
        if at is None or (kind and found_kind != kind):
            continue
        points.append({"path": path, "kind": found_kind, "at": at, "size": path.stat().st_size})
    return sorted(points, key=lambda p: p["at"], reverse=True)


def rotate(store_id, keep_days: int = KEEP_DAYS) -> dict:
    """
    Сжать разложенные отправленные файлы и убрать всё, что старше срока.

    Два правила, которые нельзя нарушать, и оба про восстановление:

    · файл в полёте не трогаем вообще — по нему пойдёт сверка после вердикта,
      и ей нужен и сам XML, и его `.meta.json`;
    · по одной свежей копии каждого вида остаётся всегда, каким бы старым ни
      был её возраст (см. ALWAYS_KEEP).
    """
    from backend.dumper import feed_state

    cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
    stats = {"compressed": 0, "removed": 0, "kept": 0}

    state = feed_state.load(store_id)
    in_flight = state.get("in_flight")

    store_dir = _backup_dir(store_id).parent

    # ── 1. Отправленные файлы лежат разложенными: их пишет uploader, и до
    # вердикта они обязаны быть читаемыми как есть. После — только архив.
    for path in sorted(store_dir.glob("buyshark-*.xml")):
        if in_flight and path.name == in_flight:
            stats["kept"] += 1
            continue
        at = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        if save(store_id, path.read_bytes(), "sent", at):
            meta = path.with_suffix(path.suffix + ".meta.json")
            try:
                path.unlink()
                if meta.exists():
                    save(store_id, meta.read_bytes(), "sent-meta", at)
                    meta.unlink()
                stats["compressed"] += 1
            except OSError as err:
                logger.warning("[FeedBackup] Не удалось убрать %s: %r", path.name, err)

    # ── 2. Старьё. Считается по видам отдельно: у «sent» и «kaspi-export»
    # разная частота, и общий счётчик выбил бы редкий вид ради частого.
    by_kind: dict = {}
    for point in restore_points(store_id):
        by_kind.setdefault(point["kind"], []).append(point)

    for kind, points in by_kind.items():
        for index, point in enumerate(points):
            if index < ALWAYS_KEEP or point["at"] >= cutoff:
                stats["kept"] += 1
                continue
            try:
                point["path"].unlink()
                stats["removed"] += 1
            except OSError as err:
                logger.warning("[FeedBackup] Не удалось удалить %s: %r", point["path"].name, err)

    if stats["compressed"] or stats["removed"]:
        logger.info(
            "[FeedBackup] Стор %s: сжато %s, удалено %s, оставлено %s",
            store_id, stats["compressed"], stats["removed"], stats["kept"],
        )
    return stats
