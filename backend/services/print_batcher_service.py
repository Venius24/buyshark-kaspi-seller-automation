import io
import os
import re
import uuid
import json
import hashlib
import logging
import math
import tempfile
import threading
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional, Tuple, Set
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

PT_TO_MM = 25.4 / 72.0
A4_WIDTH_PT = 595.28
A4_HEIGHT_PT = 841.89

# Категории печатных форм
CATEGORY_META = {
    "EXPRESS_STICKER_75x120": {
        "title": "⚡ 🚀 Экспресс-Этикетки Kaspi (75x120 мм)",
        "icon": "⚡",
        "printer_type": "Термопринтер этикеток (ПРИОРИТЕТНАЯ ПЕЧАТЬ)",
        "is_express": True
    },
    "EXPRESS_STICKER_100x150": {
        "title": "⚡ 🚀 Экспресс-Этикетки (100x150 мм)",
        "icon": "⚡",
        "printer_type": "Термопринтер этикеток (ПРИОРИТЕТНАЯ ПЕЧАТЬ)",
        "is_express": True
    },
    "EXPRESS_PAPER_A4": {
        "title": "⚡ 🚀 Экспресс-Накладные A4 (210x297 мм)",
        "icon": "⚡",
        "printer_type": "Офисный принтер A4 (СРОЧНАЯ СБОРКА)",
        "is_express": True
    },
    # Настоящий лист Kaspi Express — 656x928 мм (три панели на листе), т.е.
    # LARGE_FORMAT. Без этой категории режим «Выделять в VIP-батчи» молча не
    # срабатывал для экспресса: express_cat_key не находился в CATEGORY_META,
    # и лист оставался в общей крупноформатной пачке.
    "EXPRESS_LARGE_FORMAT": {
        "title": "⚡ 🚀 Экспресс-Накладные Kaspi (лист 3 панели)",
        "icon": "⚡",
        "printer_type": "Офисный принтер A4 (СРОЧНАЯ СБОРКА)",
        "is_express": True
    },
    "STICKER_75x120": {
        "title": "Термоэтикетки Kaspi (75x120 мм)",
        "icon": "🏷️",
        "printer_type": "Термопринтер этикеток (75x120 / Xprinter / Zebra)",
        "is_express": False
    },
    "STICKER_100x150": {
        "title": "Термоэтикетки 100x150 мм",
        "icon": "🏷️",
        "printer_type": "Термопринтер этикеток (100x150)",
        "is_express": False
    },
    "PAPER_A4": {
        "title": "Документы и накладные A4 (210x297 мм)",
        "icon": "📄",
        "printer_type": "Офисный принтер A4 (Лазерный / Струйный)",
        "is_express": False
    },
    "PAPER_A3": {
        "title": "Формат A3 (297x420 мм)",
        "icon": "📜",
        "printer_type": "Принтер A3",
        "is_express": False
    },
    "LARGE_FORMAT": {
        "title": "Крупноформатные печатные формы",
        "icon": "📐",
        "printer_type": "Плоттер / Крупноформатный принтер",
        "is_express": False
    },
    "CUSTOM_SIZE": {
        "title": "Нестандартный / Кастомный размер",
        "icon": "📦",
        "printer_type": "Универсальный принтер",
        "is_express": False
    }
}

# Директория временных файлов батчей и истории
TEMP_BATCH_DIR = Path(tempfile.gettempdir()) / "print_batcher_storage"
TEMP_BATCH_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE_PATH = TEMP_BATCH_DIR / "printed_history.json"


def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_printed_history(store_id: Optional[str] = None, date_str: Optional[str] = None) -> Set[str]:
    """
    Загрузка истории напечатанных идентификаторов заказов/страниц за день.

    store_id given (Слайс 5): PrintHistoryDB этого магазина.
    store_id=None (легаси, Telegram-бот — общий на все магазины): старый
    файл в системном temp, как было всегда.
    """
    if date_str is None:
        date_str = get_today_str()

    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        from sqlalchemy import select
        session = _get_sync_db_session()
        if not session:
            return set()
        try:
            from backend.db.models import PrintHistoryDB
            rows = session.scalars(
                select(PrintHistoryDB).where(PrintHistoryDB.store_id == store_id, PrintHistoryDB.date == date_str)
            ).all()
            return {r.order_key for r in rows}
        finally:
            session.close()

    if not HISTORY_FILE_PATH.exists():
        return set()

    try:
        with open(HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get(date_str, []))
    except Exception:
        return set()


def save_printed_history(new_keys: Set[str], store_id: Optional[str] = None, date_str: Optional[str] = None):
    """Сохранение истории напечатанных заказов этого магазина."""
    if not new_keys:
        return
    if date_str is None:
        date_str = get_today_str()

    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        if not session:
            return
        try:
            from backend.db.models import PrintHistoryDB
            for key in new_keys:
                row = session.get(PrintHistoryDB, (store_id, date_str, key))
                if not row:
                    session.add(PrintHistoryDB(store_id=store_id, date=date_str, order_key=key))
            session.commit()
        except Exception as e:
            logger.error(f"Error persisting print history to DB: {e}")
            session.rollback()
        finally:
            session.close()
        return

    data: Dict[str, List[str]] = {}
    if HISTORY_FILE_PATH.exists():
        try:
            with open(HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    current_set = set(data.get(date_str, []))
    current_set.update(new_keys)
    data[date_str] = list(current_set)

    with open(HISTORY_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def reset_printed_history(store_id: Optional[str] = None, date_str: Optional[str] = None):
    """Сброс истории печати этого магазина за указанную дату."""
    if date_str is None:
        date_str = get_today_str()

    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        from sqlalchemy import delete
        session = _get_sync_db_session()
        if not session:
            return
        try:
            from backend.db.models import PrintHistoryDB
            session.execute(
                delete(PrintHistoryDB).where(PrintHistoryDB.store_id == store_id, PrintHistoryDB.date == date_str)
            )
            session.commit()
        except Exception as e:
            logger.error(f"Error resetting print history in DB: {e}")
            session.rollback()
        finally:
            session.close()
        return

    if HISTORY_FILE_PATH.exists():
        try:
            with open(HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if date_str in data:
                del data[date_str]
            with open(HISTORY_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def validate_pdf_geometry(
    pdf_path: str,
    expected_w_mm: Optional[float] = None,
    expected_h_mm: Optional[float] = None
) -> Dict[str, Any]:
    """
    Валидатор геометрии холста PDF (MediaBox/CropBox audit).
    Проверяет точный физический размер сгенерированных страниц в миллиметрах.
    """
    if not os.path.exists(pdf_path):
        return {
            "is_valid": False,
            "actual_dimensions": "0x0 мм",
            "mediabox_pt": "0x0 pt",
            "audit_message": "❌ ОШИБКА: Файл PDF не найден"
        }

    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return {
                "is_valid": False,
                "actual_dimensions": "0x0 мм",
                "mediabox_pt": "0x0 pt",
                "audit_message": "❌ ОШИБКА: Документ PDF пуст"
            }

        page = doc[0]
        rect = page.rect
        w_mm = round(rect.width * PT_TO_MM, 1)
        h_mm = round(rect.height * PT_TO_MM, 1)
        doc.close()

        dim_str = f"{w_mm}x{h_mm} мм"
        box_pt = f"{rect.width:.1f}x{rect.height:.1f} pt"

        if expected_w_mm is not None and expected_h_mm is not None:
            w_ok = abs(w_mm - expected_w_mm) <= 3.0
            h_ok = abs(h_mm - expected_h_mm) <= 3.0
            if w_ok and h_ok:
                audit_msg = f"✅ УТВЕРЖДЕНО: Точный MediaBox {dim_str} (Без белых полей A4)"
                is_valid = True
            else:
                audit_msg = f"⚠️ ВНИМАНИЕ: Размер холста {dim_str} (Ожидалось {expected_w_mm}x{expected_h_mm} мм)"
                is_valid = False
        else:
            audit_msg = f"✅ УТВЕРЖДЕНО: Геометрия холста {dim_str}"
            is_valid = True

        return {
            "is_valid": is_valid,
            "actual_dimensions": dim_str,
            "mediabox_pt": box_pt,
            "audit_message": audit_msg
        }
    except Exception as e:
        return {
            "is_valid": False,
            "actual_dimensions": "Ошибка",
            "mediabox_pt": "Ошибка",
            "audit_message": f"❌ Ошибка проверки геометрии: {str(e)}"
        }


def get_content_rect(page: fitz.Page) -> fitz.Rect:
    """
    Вычисление точного прямоугольника содержимого накладной/этикетки (без пустых белых полей).
    """
    blocks = page.get_text("blocks")
    rects = []
    for b in blocks:
        r = fitz.Rect(b[:4])
        if r.width > 5 and r.height > 5:
            rects.append(r)
            
    drawings = page.get_drawings()
    for d in drawings:
        r = d["rect"]
        if r.width > 5 and r.height > 5:
            rects.append(r)
            
    if not rects:
        return page.rect
        
    content_box = rects[0]
    for r in rects[1:]:
        content_box |= r
        
    padding = 4.0
    crop_rect = fitz.Rect(
        max(page.rect.x0, content_box.x0 - padding),
        max(page.rect.y0, content_box.y0 - padding),
        min(page.rect.x1, content_box.x1 + padding),
        min(page.rect.y1, content_box.y1 + padding)
    )
    return crop_rect


def classify_page_size(width_mm: float, height_mm: float) -> str:
    """Определение категории формата по физическим размерам в мм"""
    w, h = sorted([width_mm, height_mm])
    
    if 65 <= w <= 85 and 110 <= h <= 130:
        return "STICKER_75x120"
    elif 90 <= w <= 115 and 140 <= h <= 165:
        return "STICKER_100x150"
    elif 200 <= w <= 225 and 280 <= h <= 310:
        return "PAPER_A4"
    elif 285 <= w <= 315 and 400 <= h <= 435:
        return "PAPER_A3"
    elif min(w, h) > 350 or max(w, h) > 500:
        return "LARGE_FORMAT"
    else:
        return "CUSTOM_SIZE"


# Габариты листа Kaspi Express (три панели на одном листе: «Образец партнёра»,
# «Образец курьера», «На товар»). Замерено на живых накладных Бархана
# 2026-08-17: ровно 656.2 x 928.2 мм у всех 8 экспресс-листов пачки, при том
# что все 69 обычных накладных — строго 210x297. Размер служит запасным
# признаком на случай, если Kaspi поменяет подписи панелей.
_EXPRESS_SHEET_MM = (656.2, 928.2)
_EXPRESS_SHEET_TOLERANCE_MM = 15.0

# Структурные маркеры экспресс-листа — подписи его панелей.
#
# ВАЖНО: видимая надпись «Экспресс Доставка» на самом листе — РАСТР (картинка),
# в текстовом слое её нет. Поэтому старый поиск слова «экспресс»/«express» по
# тексту страницы не находил НИ ОДНОГО настоящего экспресса, зато давал ложные
# срабатывания на обычных заказах, у которых слово попадало в адрес ПВЗ клиента
# («улица Толстого, 1, В экспресс»). На пачке Бархана 2026-08-17 это давало
# 1 ложный экспресс и 7 просочившихся настоящих из 8.
#
# Подписи панелей ниже есть на всех экспресс-листах и ни на одной обычной
# накладной (проверено: 8/8 попаданий, 0/69 ложных).
_EXPRESS_TEXT_MARKERS = ("образец курьера", "образец партнера", "образец партнёра")


def is_express_page(
    text: str,
    filename: str,
    w_mm: Optional[float] = None,
    h_mm: Optional[float] = None,
) -> bool:
    """Определение относится ли страница/файл к Экспресс-доставке.

    Габариты (w_mm/h_mm) опциональны только ради обратной совместимости со
    старыми вызовами — при разборе накладных их всегда нужно передавать,
    иначе теряется запасной признак по размеру листа.
    """
    fn = (filename or "").lower()
    if "express" in fn or "экспресс" in fn:
        return True

    t = (text or "").lower()
    if any(marker in t for marker in _EXPRESS_TEXT_MARKERS):
        return True

    if w_mm is not None and h_mm is not None:
        exp_w, exp_h = _EXPRESS_SHEET_MM
        tol = _EXPRESS_SHEET_TOLERANCE_MM
        # Проверяем обе ориентации — лист может приехать повёрнутым.
        for page_w, page_h in ((w_mm, h_mm), (h_mm, w_mm)):
            if abs(page_w - exp_w) <= tol and abs(page_h - exp_h) <= tol:
                return True

    return False


# Номер заказа Kaspi — 10 цифр, начинается с 1 (1042443281). Прежний шаблон
# был `100\d{7,8}`, то есть требовал буквально "100" в начале и перестал
# срабатывать, как только нумерация перевалила за 1009999999 — а это было
# давно. Последствие: НИ ОДНА накладная не опознавалась по номеру заказа, ключ
# истории печати вырождался в хэш текста страницы (на проде Бархана за
# 2026-08-21: 466 ключей `hash_`, ноль `order_`). Хэш меняется от любой правки
# накладной на стороне Kaspi — сменилась дата доставки или курьер, и тот же
# заказ снова считается новым. По этой же причине молча не работала стыковка
# со спулом автосборки, который ждёт ключ `order_<код>`.
_ORDER_ID_RE = re.compile(r"\b(1\d{9})\b")
# На самой накладной номер напечатан с разбивкой по разрядам и вплотную к
# счётчику мест: "1 042 443 281 1". Склеивать в тексте все пробелы подряд
# нельзя — так номер срастается с соседним числом в 11 цифр и перестаёт
# опознаваться. Поэтому разряды собираем шаблоном, разрешая только пробелы и
# табы (но не переносы строк, иначе склеятся соседние поля накладной).
# Хвостовую цифру (счётчик мест "1/1") лениво отбрасываем: берём первые десять
# цифр разрядной группы и не требуем, чтобы за ними ничего не шло. Разделитель —
# любой пробельный символ кроме перевода строки (Kaspi ставит и неразрывный),
# иначе соседние поля накладной склеятся в одно число.
_ORDER_ID_SPACED_RE = re.compile(r"(?<!\d)(1(?:[^\S\r\n]*\d){9})")


def extract_order_id(text: str, filename: str) -> Optional[str]:
    """Извлечение номера заказа (Kaspi Order ID) из имени файла или текста страницы."""
    # Имя файла авторитетнее: Kaspi кладёт номер прямо в него
    # (KASPI_SHOP-1042206932.pdf), там нечему шуметь.
    if filename:
        fn_match = _ORDER_ID_RE.search(re.sub(r"\D", " ", filename))
        if fn_match:
            return fn_match.group(1)

    if text:
        spaced = _ORDER_ID_SPACED_RE.search(text)
        if spaced:
            return re.sub(r"\D", "", spaced.group(1))
        # Слитно написанный номер, в том числе вплотную к кириллице
        # ("Заказ1042443281") — \b рядом с буквой не сработает, поэтому всё
        # нецифровое заменяем разделителем.
        match = _ORDER_ID_RE.search(re.sub(r"\D", " ", text))
        if match:
            return match.group(1)

    return None


def get_page_unique_key(text: str, filename: str, order_id: Optional[str]) -> str:
    """Сборка уникального ключа страницы для дедупликации"""
    if order_id:
        return f"order_{order_id}"
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"hash_{h}"


def get_page_history_keys(text: str, filename: str, order_id: Optional[str]) -> Set[str]:
    """
    Все ключи, под которыми страница может встретиться в журнале печати.

    Пишем и сверяем ОБА вида — по номеру заказа и по хэшу текста. Номер
    надёжнее (переживает перегенерацию накладной на стороне Kaspi), но до
    починки `extract_order_id` вся история копилась в хэшах, и переход только
    на номера означал бы один день, когда уже напечатанное считается новым.
    Хэш заодно остаётся страховкой для накладных, где номер не распознался.
    """
    keys = {f"hash_{hashlib.md5(text.encode('utf-8')).hexdigest()}"}
    if order_id:
        keys.add(f"order_{order_id}")
    return keys


def peek_already_printed(
    file_bytes: bytes,
    original_filename: str,
    store_id: Optional[str] = None,
) -> Tuple[int, int]:
    """
    Быстрая прикидка «сколько в этом архиве уже печаталось сегодня» —
    (уже_печатали, всего_заказов), БЕЗ разбора PDF.

    Kaspi кладёт в архив по файлу на заказ и зашивает номер заказа прямо в имя
    (KASPI_SHOP-1042206932.pdf), а ключ истории строится ровно из него — тот же
    `order_<id>`, что и в полном проходе. Значит достаточно прочитать оглавление
    zip: миллисекунды против десятков секунд на PyMuPDF.

    Нужно, чтобы не дёргать вопросом «печатать все или только новые» там, где
    выбирать не из чего: если пересечения с историей нет, спрашивать не о чем.

    Возвращает (0, 0) на всём, что не разобрать (одиночный PDF, битый zip,
    непривычные имена файлов) — вызывающий код в этом случае ведёт себя
    по-старому и спрашивает.
    """
    if not original_filename.lower().endswith(".zip"):
        return 0, 0

    keys: Set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as z:
            for member_name in z.namelist():
                if not member_name.lower().endswith(".pdf") or member_name.startswith("__MACOSX"):
                    continue
                order_id = extract_order_id("", os.path.basename(member_name))
                if order_id:
                    keys.add(get_page_unique_key("", member_name, order_id))
    except Exception as e:
        logger.warning(f"Print Batcher: не удалось заглянуть в архив '{original_filename}': {e}")
        return 0, 0

    if not keys:
        return 0, 0

    printed = load_printed_history(store_id=store_id)
    return len(keys & printed), len(keys)


def count_already_printed_orders(order_codes: List[str], store_id: Optional[str] = None) -> int:
    """То же, но для спула автосборки — там номера заказов уже известны из БД."""
    if not order_codes:
        return 0
    printed = load_printed_history(store_id=store_id)
    return sum(1 for code in order_codes if f"order_{code}" in printed)


def natural_sort_key(s: str) -> List[Any]:
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


# Пунктирная линия перед "N шт." — основной шаблон термоэтикетки Kaspi:
# "Barkhan S517-0027 черный . . . . . . . . . . . .  1 шт."
# Порог был {4,} — на живой партии Бархана 2026-08-22 нашлась накладная с
# пунктиром всего в 3 точки ("Веревка Джутовый аркан ... джут . .  2 шт.",
# т.е. фактически 3 символа "." после сжатия пробелов), и товар молча
# проваливался в "Прочее" вместо категории по коду. Понижено до {2,} — двух
# точек достаточно, чтобы отличить настоящий пунктир от случайного "N шт."
# без него, а короче Kaspi эту линию не печатает.
_PRODUCT_DOT_LEADER_RE = re.compile(r'^\s*(?:\d+\.\s*)?(.+?)\s*(?:\.\s*){2,}\d+\s*шт\.?\s*$', re.MULTILINE)
_ORDER_ID_LINE_RE = re.compile(r'^\d[\d\s]{8,}\d$')

# Когда название товара длинное, счётчик "N шт." переносится PDF-рендерером
# Kaspi на следующую строку ОТДЕЛЬНО от числа: "...10 шт . .  2 \nшт.\n" —
# число и единица измерения физически разорваны переводом строки. Из-за
# этого _PRODUCT_DOT_LEADER_RE (работает построчно, ^...$ на одной строке)
# не находил совпадение вообще, и накладная проваливалась в "Прочее" даже
# при наличии кода артикула в самом названии (живой случай: "H477-0005
# Набор воздушных шаров ... 10 шт . .  2 \nшт." — партия Бархана
# 2026-08-22). Склеиваем разорванные "N" + "шт" обратно перед поиском.
_WRAPPED_QTY_UNIT_RE = re.compile(r'(\d)\s*\n\s*(шт\.?)')

# Название, не влезающее в ширину этикетки, Kaspi переносит на вторую строку
# ЦЕЛИКОМ вместе с пунктиром и счётчиком: "Barkhan H974-0004 Праздничная
# одноразовая посуда для праздника \nна 10 пе . . . .  1 шт.". Построчный
# _PRODUCT_DOT_LEADER_RE в таком тексте находил ХВОСТ переноса и возвращал
# его как название товара — "на 10 пе", "нержавеющей стали", "пластик 4шт",
# "объемом 1", "см". Артикул (H974-0004, X171-0027, D970-0022) остаётся в
# первой строке, поэтому накладная теряла и Мастер-Группу, и категорию по
# префиксу и уезжала в "Прочее" (11 накладных из 33 в партии Бархана
# 2026-09-04). Склеиваем перенос обратно перед поиском.
#
# Закрывающей считается строка, кончающаяся на "N шт." ИМЕННО С ТОЧКОЙ:
# точка — единица измерения в конце строки списка, а количество внутри
# названия Kaspi печатает без неё ("...Большой футбол 116 шт"), иначе
# первая строка выглядела бы уже закрытой и склейки не случилось бы.
_CLOSING_QTY_RE = re.compile(r'\d+\s*шт\.\s*$')
_HAS_LETTER_RE = re.compile(r'[A-Za-zА-Яа-яЁё]')

# Пунктира может не быть вовсе: если название занимает всю ширину, точкам
# просто не остаётся места ("H477-0030 Набор воздушных шаров Barkhan "Большой
# футбол" 116 шт  \n1 шт."). Такие накладные ловим вторым, ослабленным
# проходом — только когда основной шаблон с пунктиром не сработал.
_PRODUCT_LOOSE_RE = re.compile(r'^\s*(?:\d+\.\s*)?(.+?)\s*(?:\.\s*)*\d+\s*шт\.\s*$', re.MULTILINE)


# Kaspi (и рендерер PDF под ним) печатает в накладных типографские варианты
# пробела и дефиса: неразрывный пробел вместо обычного встречается в 12
# накладных из 248 живой партии 2026-09-04, а перенос строки внутри артикула
# приходит мягким дефисом. Для глаза они неотличимы, а для _ARTICLE_RE
# "S517\xad0003" — уже не артикул, и товар теряет категорию на ровном месте.
_TEXT_LOOKALIKES = {
    "\xa0": " ", " ": " ", " ": " ", " ": " ",  # пробелы
    "\xad": "-", "‐": "-", "‑": "-", "‒": "-",  # дефисы
    "–": "-", "—": "-",
}
_LOOKALIKE_RE = re.compile("[" + "".join(_TEXT_LOOKALIKES) + "]")


def normalize_waybill_text(text: str) -> str:
    """Типографские пробелы и дефисы → обычные ASCII."""
    if not text:
        return text
    return _LOOKALIKE_RE.sub(lambda m: _TEXT_LOOKALIKES[m.group(0)], text)


def _is_open_product_line(line: str) -> bool:
    """Строка похожа на НЕзакрытое название товара — есть буквы, нет "N шт."."""
    s = line.strip()
    return bool(s) and bool(_HAS_LETTER_RE.search(s)) and not _CLOSING_QTY_RE.search(s)


def merge_wrapped_product_lines(text: str) -> str:
    """Приклеивает перенесённый хвост названия обратно к его первой строке."""
    out: List[str] = []
    for line in text.splitlines():
        if out and _CLOSING_QTY_RE.search(line) and _is_open_product_line(out[-1]):
            out[-1] = out[-1].rstrip() + " " + line.strip()
            continue
        out.append(line)
    return "\n".join(out)


def extract_product_name(text: str, filename: str) -> Optional[str]:
    """
    Извлечение названия первого товара накладной из текстового слоя PDF.

    Kaspi использует минимум два шаблона накладных:
    1) Термоэтикетка с нумерованным списком товаров и пунктирной линией
       перед "N шт." — основной, покрывает большинство заказов.
    2) 3-панельная квитанция курьера "НА ТОВАР" — без пунктира, название
       товара стоит отдельной строкой прямо перед последним вхождением
       номера заказа (панель "ОБРАЗЕЦ ПАРТНЕРА").
    Порядок обхода не важен для многотоварных заказов — сортировка/группа
    берётся по ПЕРВОМУ найденному товару в тексте (решение пользователя).
    """
    if not text:
        return None

    text = normalize_waybill_text(text)
    prepared = merge_wrapped_product_lines(_WRAPPED_QTY_UNIT_RE.sub(r'\1 \2', text))

    m = _PRODUCT_DOT_LEADER_RE.search(prepared)
    if m:
        name = m.group(1).strip(" .")
        if name:
            return name

    if "НА ТОВАР" in text:
        lines = [l.strip() for l in text.splitlines()]
        for i in range(len(lines) - 1, 0, -1):
            digits = lines[i].replace(" ", "")
            if digits.isdigit() and 9 <= len(digits) <= 10:
                candidate = lines[i - 1].strip()
                if candidate and not candidate.replace(" ", "").isdigit():
                    return candidate
                break

    # Ослабленный проход — последним: у квитанции "НА ТОВАР" свой шаблон, и в
    # её тексте хватает строк с "N шт.", из которых он выдёргивает обрывки
    # ('"Happy Birthday" 53 шт', 'ABS-пластик'), перехватывая управление у
    # точной ветки выше.
    m = _PRODUCT_LOOSE_RE.search(prepared)
    if m:
        name = m.group(1).strip(" .")
        # Без пунктира отличить название от случайной строки с "N шт." нечем,
        # кроме наличия букв: иначе в название попадала бы служебная цифирь
        # накладной.
        if name and _HAS_LETTER_RE.search(name):
            return name

    return None


# Сопоставление накладной с Мастер-Группой идёт ПО НАЗВАНИЯМ ТОВАРОВ.
#
# В linked_skus группы лежат артикулы (X147-0002, 175489569), а на накладной
# Kaspi печатает название витрины — артикул попадает в него далеко не всегда.
# Прежняя реализация искала артикул/название группы подстрокой прямо в
# названии товара и на живой партии Бархана ловила 341 накладную из 1141.
# Теперь linked_skus сначала разворачиваются в НАЗВАНИЯ товаров каталога, и
# сравнивается название с названием — 816 из 1141 на той же партии.
_NAME_STOP_TOKENS = {"шт", "см", "мл", "кг", "г", "л", "мм", "и", "для", "с", "на", "в"}

# "Код" в названии — артикул продавца (S517-0045, X147-0002, WL0043) или
# модель производителя (7665-106, 429078). Именно он отличает друг от друга
# соседние почти одинаковые карточки ("Выжимка для цепи Barkhan S517-0045 1"
# против "...S517-0049 1"), поэтому по нему нельзя ошибаться.
_CODE_TOKEN_RE = re.compile(r"^(?=.*\d)(?=.*[a-zа-я])[a-zа-я0-9-]+$|^\d[\d-]{3,}$")

# ...но под ту же маску попадают ГАБАРИТЫ и объёмы, которые кодом не являются:
# "240x200", "19x28", "33х3", "16х7см", "250мл", "6.5л", "95х89". Разные товары
# сплошь и рядом делят габарит, а совпадение по "коду" считается сильным
# признаком с мягким порогом 0.35 — то есть любая накладная с "10x10" рисковала
# получить штамп чужой группы просто потому, что там тоже "10x10".
_MEASURE_TOKEN_RE = re.compile(
    r"^\d+([.,]\d+)?([xх]\d+([.,]\d+)?)*(мм|см|дм|м|мл|л|кг|гр|г|шт)?$"
)


def _is_measure_token(token: str) -> bool:
    """Габарит/объём ("240x200", "16х7см", "250мл"), а не артикул."""
    return bool(_MEASURE_TOKEN_RE.match(token))

# Порог для "похожего названия" — применяется только когда в названии
# накладной кода нет вообще и спутать соседние карточки нечем.
_NAME_SIMILARITY_THRESHOLD = 0.5
# Для совпадения по коду порог мягче: код уже сам по себе сильный признак,
# названия при этом могут расходиться ("Ключ Barkhan S517-0013 1" против
# "Товар S517-0013").
_CODE_SIMILARITY_THRESHOLD = 0.35

# Насколько победитель должен оторваться от лучшей ЧУЖОЙ группы, чтобы штамп
# считался обоснованным. У Бархана есть наборы почти неразличимых по названию
# групп ("T219-0006 Термосумка" / "T219-0007 Термосумка"): раньше между ними
# выигрывала та, что раньше попалась в обходе, и на накладной появлялось
# правдоподобное, но выбранное монеткой название. Лучше не ставить штамп вовсе,
# чем отправить упаковщика не к той полке.
_AMBIGUITY_MARGIN = 0.08


def _is_ambiguous(best: float, runner_up: float) -> bool:
    return runner_up > 0.0 and (best - runner_up) < _AMBIGUITY_MARGIN


def normalize_name_tokens(name: str) -> List[str]:
    """Название → значимые токены: нижний регистр, без пунктуации и единиц."""
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^\wЀ-ӿ-]+", " ", s)
    out = []
    for raw in s.split():
        t = raw.strip("-")
        if t and t not in _NAME_STOP_TOKENS:
            out.append(t)
    return out


def _code_tokens(tokens: List[str]) -> Set[str]:
    return {t for t in tokens if _CODE_TOKEN_RE.match(t) and not _is_measure_token(t)}


def _name_similarity(a: Set[str], b: Set[str]) -> float:
    """
    Доля общих токенов, взвешенная по их длине: "мыльница" весит больше, чем
    "1", иначе любые два товара с "1 шт" выглядели бы похожими.

    Оставлено для вызовов без каталога под рукой; основной путь —
    MasterGroupMatcher._similarity, который взвешивает токены по редкости и
    не штрафует длинные названия (см. там).
    """
    inter = a & b
    if not inter:
        return 0.0
    weight = lambda s: sum(len(t) for t in s)
    return weight(inter) / max(weight(a), weight(b))


class MasterGroupMatcher:
    """
    Индекс Мастер-Групп для одной партии печати: строится один раз, дальше
    match() зовётся на каждую накладную.
    """

    def __init__(self, master_groups: List[Dict[str, Any]], products: Optional[List[Dict[str, Any]]] = None):
        # Артикул (в любом из трёх видов) → товар каталога
        by_sku: Dict[str, Dict[str, Any]] = {}
        for p in products or []:
            for key in ("sku", "merchantSku", "kaspiSku"):
                val = p.get(key)
                if val:
                    by_sku.setdefault(str(val).strip(), p)

        self._build_idf(products or [])

        # Группа → названия её товаров (разложенные на токены)
        self._groups: List[Tuple[Dict[str, Any], List[Tuple[Set[str], Set[str]]]]] = []
        # Артикул → группа: фолбэк для накладных, где артикул напечатан прямо
        # в названии, а названия товаров группы на него не похожи
        # ("Barkhan мыльница X147-0002 1 шт" ↔ группа "X147-0002 мыльница Urban White").
        self._by_code: Dict[str, Dict[str, Any]] = {}

        # Артикулы, встречающиеся больше чем в одной группе: по ним выбрать
        # группу нельзя, любой выбор был бы монеткой.
        self._ambiguous_codes: Set[str] = set()

        # Точный индекс по ВСЕМ linked_skus — для матча по артикулу из заказа
        # (см. match_by_sku). В отличие от _by_code сюда попадают и числовые
        # kaspiSku (175489569), которые под маску артикула не подходят, но в
        # ответе Kaspi Order API приходят как есть.
        self._by_linked_sku: Dict[str, Dict[str, Any]] = {}
        self._ambiguous_linked_skus: Set[str] = set()

        for group in master_groups:
            if not group.get("is_active", True):
                continue

            names: Set[str] = set()
            for sku in group.get("linked_skus") or []:
                sku = str(sku).strip()
                if not sku:
                    continue
                low = sku.lower()
                owner_exact = self._by_linked_sku.setdefault(low, group)
                if owner_exact is not group:
                    self._ambiguous_linked_skus.add(low)
                if _CODE_TOKEN_RE.match(low) and not _is_measure_token(low):
                    owner = self._by_code.setdefault(low, group)
                    if owner is not group:
                        self._ambiguous_codes.add(low)
                product = by_sku.get(sku)
                if product and product.get("name"):
                    names.add(product["name"])

            entries = []
            for name in names:
                tokens = normalize_name_tokens(name)
                if tokens:
                    entries.append((set(tokens), _code_tokens(tokens)))
            if entries:
                self._groups.append((group, entries))

    def _build_idf(self, products: List[Dict[str, Any]]) -> None:
        """
        Вес токена = его редкость в каталоге магазина, а не длина.

        Длина проваливала два живых случая Бархана сразу:
          * "barkhan" стоит почти в каждом названии витрины и ничего не
            различает, но как слово из 6 букв весило больше, чем "мдф";
          * знаменатель max(вес накладной, вес группы) штрафовал группы с
            длинными названиями, поэтому короткое мусорное совпадение
            обыгрывало точное длинное.
        Вместе это ставило на накладную "Органайзер Barkhan для часов МДФ"
        штамп "X179-0014 Органайзер пластик" (общего у них — только бренд и
        слово "органайзер"), а верную группу "D618-0011 Органайзер для
        хранения ... МДФ" отбрасывало как непохожую.
        """
        doc_freq: Dict[str, int] = {}
        total_docs = 0
        for p in products:
            name = p.get("name")
            if not name:
                continue
            total_docs += 1
            for token in set(normalize_name_tokens(name)):
                doc_freq[token] = doc_freq.get(token, 0) + 1

        self._idf: Dict[str, float] = {}
        if total_docs:
            for token, freq in doc_freq.items():
                self._idf[token] = math.log(total_docs / freq)
            # Токен, которого в каталоге нет вообще, максимально информативен.
            self._max_idf = math.log(total_docs) if total_docs > 1 else 1.0
        else:
            self._max_idf = 1.0

    def _token_weight(self, token: str) -> float:
        # Пол в 0.05 — чтобы совсем универсальное слово ("шт", "1") не весило
        # ровно ноль и не делало пустое пересечение "идеальным".
        return max(self._idf.get(token, self._max_idf), 0.05)

    def _weight(self, tokens: Set[str]) -> float:
        return sum(self._token_weight(t) for t in tokens)

    def _similarity(self, name_set: Set[str], group_tokens: Set[str]) -> float:
        """
        Гармоническое среднее двух покрытий: «сколько смысла накладной нашлось
        в группе» и «сколько смысла группы нашлось в накладной». Симметрично,
        но, в отличие от деления на max(), не наказывает группу за подробное
        название — наказывает только за РАСХОЖДЕНИЕ по значимым словам.
        """
        inter = name_set & group_tokens
        if not inter:
            return 0.0
        w_inter = self._weight(inter)
        w_name = self._weight(name_set)
        w_group = self._weight(group_tokens)
        if w_name <= 0 or w_group <= 0:
            return 0.0
        cov_name = w_inter / w_name
        cov_group = w_inter / w_group
        if cov_name + cov_group == 0:
            return 0.0
        return 2 * cov_name * cov_group / (cov_name + cov_group)

    def match_by_sku(self, sku: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Группа по АРТИКУЛУ товара из заказа — без единой эвристики.

        Артикул приходит из Kaspi Order API по коду заказа (см.
        fetch_order_skus), то есть это те же данные, что лежат в linked_skus,
        а не текст, распознанный с картинки накладной. Совпало — группа та
        самая; не совпало — молчим и оставляем разбираться match() по названию.
        Артикул из нескольких групп пропускаем по той же причине, что и в
        match(): любой выбор был бы монеткой.
        """
        if not sku:
            return None
        low = str(sku).strip().lower()
        if not low or low in self._ambiguous_linked_skus:
            return None
        return self._by_linked_sku.get(low)

    def match(self, product_name: Optional[str]) -> Optional[Dict[str, Any]]:
        if not product_name:
            return None

        tokens = normalize_name_tokens(product_name)
        if not tokens:
            return None
        name_set = set(tokens)
        name_codes = _code_tokens(tokens)

        best_by_code: Tuple[Optional[Dict[str, Any]], float] = (None, 0.0)
        best_by_name: Tuple[Optional[Dict[str, Any]], float] = (None, 0.0)
        # Лучший результат среди ДРУГИХ групп — им проверяем, что победитель
        # действительно один, а не первый из нескольких равных.
        runner_by_code = 0.0
        runner_by_name = 0.0

        for group, entries in self._groups:
            for group_tokens, group_codes in entries:
                if name_set == group_tokens:
                    return group  # то же самое название — сомнений нет

                score = self._similarity(name_set, group_tokens)
                if name_codes and group_codes:
                    # Коды есть с обеих сторон: совпали — сильный признак,
                    # разошлись — это соседняя карточка, а не наша.
                    if name_codes & group_codes and score > best_by_code[1]:
                        if best_by_code[0] is not None and best_by_code[0] is not group:
                            runner_by_code = max(runner_by_code, best_by_code[1])
                        best_by_code = (group, score)
                    elif name_codes & group_codes and group is not best_by_code[0]:
                        runner_by_code = max(runner_by_code, score)
                elif not name_codes:
                    if score > best_by_name[1]:
                        if best_by_name[0] is not None and best_by_name[0] is not group:
                            runner_by_name = max(runner_by_name, best_by_name[1])
                        best_by_name = (group, score)
                    elif group is not best_by_name[0]:
                        runner_by_name = max(runner_by_name, score)

        if best_by_code[0] and best_by_code[1] >= _CODE_SIMILARITY_THRESHOLD:
            if not _is_ambiguous(best_by_code[1], runner_by_code):
                return best_by_code[0]
            return None
        if best_by_name[0] and best_by_name[1] >= _NAME_SIMILARITY_THRESHOLD:
            if not _is_ambiguous(best_by_name[1], runner_by_name):
                return best_by_name[0]
            return None

        # По названиям не сошлось — но если в названии накладной напечатан
        # артикул и он есть в linked_skus ровно ОДНОЙ группы, товар всё равно её.
        # Артикул из нескольких групп пропускаем: раньше здесь возвращалась та,
        # что попалась первой при построении индекса, — то есть штамп ставился
        # наугад и выглядел как выдумка.
        for code in sorted(name_codes):
            if code in self._ambiguous_codes:
                continue
            group = self._by_code.get(code)
            if group:
                return group

        return None


# ---------------------------------------------------------------------------
# Состав заказа из Kaspi Order API — источник артикулов вместо распознавания
#
# Всё, что печатает Kaspi на накладной — это название ВИТРИНЫ, и артикул
# продавца в него попадает не всегда. Отсюда весь ворох эвристик: разбор
# пунктирной строки, склейка переносов, схожесть названий. Между тем код
# заказа на накладной есть всегда (он же в имени файла KASPI_SHOP-<код>.pdf),
# а Order API по нему отдаёт entries[].offer.code — ровно тот артикул, что
# лежит в linked_skus Мастер-Группы.
#
# Ограничения, из-за которых это дополнение, а не замена: API отдаёт окно в
# 13 дней и только заказы в обработке, поэтому накладную из старой выгрузки
# в карте не найти — для неё остаётся прежний путь по названию.
# ---------------------------------------------------------------------------

# Обе вкладки кабинета: «На сборке» (assembled=False) и «Ожидает курьера»
# (assembled=True). Печатают и то, и другое, а один запрос отдаёт что-то одно.
_ORDER_FETCH_STAGES = (False, True)
_ORDER_FETCH_LIMIT = 1000


def fetch_order_skus(token: str, limit: int = _ORDER_FETCH_LIMIT) -> Dict[str, List[str]]:
    """
    Карта «код заказа → артикулы его товаров» для одной партии печати.

    Сетевая часть намеренно вынесена из конвейера: в process_zip_or_pdfs
    карта приходит готовым словарём, поэтому пайплайн остаётся синхронным,
    детерминированным и тестируемым без Kaspi.
    """
    from backend.kaspi_wrapper import KaspiAPI

    order_skus: Dict[str, List[str]] = {}
    for assembled in _ORDER_FETCH_STAGES:
        try:
            orders = KaspiAPI.get_orders_with_products(
                token,
                state="KASPI_DELIVERY",
                status="ACCEPTED_BY_MERCHANT",
                limit=limit,
                assembled=assembled,
            )
        except Exception as e:
            # Печать не должна падать из-за Kaspi: без карты партия просто
            # раскладывается по-старому, как до этой фичи.
            logger.warning(f"Print Batcher: состав заказов не получен (assembled={assembled}): {e}")
            continue

        for order in orders or []:
            code = str(order.get("id") or "").strip()
            if not code:
                continue
            skus = [
                str(e.get("sku") or "").strip()
                for e in (order.get("entries") or [])
                if str(e.get("sku") or "").strip()
            ]
            if skus:
                order_skus.setdefault(code, skus)

    logger.info(f"Print Batcher: состав получен по {len(order_skus)} заказам")
    return order_skus


# ---------------------------------------------------------------------------
# Категории магазина («огромные категории» поверх Мастер-Групп)
#
# У Бархана артикул продавца устроен как <БУКВА><3 цифры>-<4 цифры>
# (S517-0027, D618-0011, X179-0014). Первая буква — крупный раздел склада
# (6 штук на 2026-08-21: D-538 товаров, S-282, X-249, T-226, H-163, K-61),
# следующие три цифры — семейство внутри него. Упаковщику удобнее сначала
# получить всю стопку одного раздела, а уже внутри неё — блоки Мастер-Групп.
#
# Правила НЕ зашиты в код: префиксы находятся сканом каталога магазина, а
# человеческие названия к ним магазин задаёт сам и только для себя
# (StorePrintSettingsDB, ключ 'store_categories'). Другому магазину с иной
# схемой артикулов это ничего не навязывает.
# ---------------------------------------------------------------------------

# Артикул ищем В ЛЮБОМ месте строки, а не только в начале: у части групп
# Бархана он стоит внутри названия («Кольцо для салфеток K474-0003, металл»).
_ARTICLE_RE = re.compile(r"(?<![A-Za-zА-Яа-я0-9])([A-Za-zА-Яа-я]{1,2})(\d{3})-(\d{2,4})")

UNCATEGORIZED_KEY = "__other__"
DEFAULT_UNCATEGORIZED_TITLE = "Прочее"


def extract_article_prefix(*candidates: Optional[str]) -> Optional[str]:
    """Первая буква артикула продавца из любого из переданных текстов."""
    for text in candidates:
        if not text:
            continue
        m = _ARTICLE_RE.search(normalize_waybill_text(str(text)))
        if m:
            return m.group(1).upper()
    return None


def detect_article_prefixes(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Скан каталога: какие префиксы артикулов вообще есть у магазина.

    Отдаётся в кабинет, чтобы человеку осталось только проставить названия —
    вписывать буквы руками и следить за появлением новых не нужно.
    """
    stats: Dict[str, Dict[str, Any]] = {}
    for p in products or []:
        prefix = None
        for key in ("merchantSku", "sku", "kaspiSku"):
            prefix = extract_article_prefix(p.get(key))
            if prefix:
                break
        if not prefix:
            continue
        entry = stats.setdefault(prefix, {"prefix": prefix, "products": 0, "examples": []})
        entry["products"] += 1
        if len(entry["examples"]) < 3:
            sku = p.get("merchantSku") or p.get("sku") or ""
            if sku:
                entry["examples"].append(str(sku))
    return sorted(stats.values(), key=lambda e: -e["products"])


class StoreCategoryResolver:
    """
    Правила магазина «префикс артикула → крупная категория», применяемые к
    одной партии печати.

    rules — то, что лежит в настройках магазина:
        {"enabled": true,
         "separator_pages": true,
         "uncategorized_title": "Прочее",
         "categories": [{"prefix": "S", "title": "Спорт и туризм"}, ...]}
    Порядок печати = порядок списка categories. Всё, что не описано, уходит
    последним блоком под названием uncategorized_title — не теряется молча.
    """

    def __init__(self, rules: Optional[Dict[str, Any]]):
        rules = rules or {}
        self.enabled: bool = bool(rules.get("enabled"))
        self.separator_pages: bool = bool(rules.get("separator_pages", True))
        self.uncategorized_title: str = (
            rules.get("uncategorized_title") or DEFAULT_UNCATEGORIZED_TITLE
        )
        self._by_prefix: Dict[str, Dict[str, Any]] = {}
        for idx, item in enumerate(rules.get("categories") or []):
            prefix = str(item.get("prefix") or "").strip().upper()
            if not prefix:
                continue
            self._by_prefix[prefix] = {
                "key": prefix,
                "title": (item.get("title") or "").strip() or prefix,
                "order": idx,
            }

    def resolve(self, *candidates: Optional[str]) -> Tuple[str, str, int]:
        """→ (ключ, отображаемое название, порядок печати)."""
        if not self.enabled:
            return UNCATEGORIZED_KEY, self.uncategorized_title, 0
        prefix = extract_article_prefix(*candidates)
        found = self._by_prefix.get(prefix) if prefix else None
        if found:
            return found["key"], found["title"], found["order"]
        # Неописанный префикс — в конец, но с сохранением самого префикса в
        # названии, чтобы владелец увидел, что именно он забыл назвать.
        title = self.uncategorized_title if not prefix else f"{self.uncategorized_title} ({prefix})"
        return (prefix or UNCATEGORIZED_KEY), title, len(self._by_prefix) + 1


_STAMP_FONT_BOLD: Optional[str] = None
_STAMP_FONT_REGULAR: Optional[str] = None
_STAMP_FONT_RESOLVED = False


def _resolve_stamp_fonts() -> Tuple[Optional[str], Optional[str]]:
    """
    Поиск кириллического TTF-шрифта для штампа группы. Base14-шрифты
    PyMuPDF (helv/Helvetica) не содержат кириллицу — без внешнего файла
    штамп рисуется знаками "?????". Проверяем Linux (прод-VPS, DejaVu/
    Liberation стоят по умолчанию) и Windows (dev) пути.
    """
    global _STAMP_FONT_BOLD, _STAMP_FONT_REGULAR, _STAMP_FONT_RESOLVED
    if _STAMP_FONT_RESOLVED:
        return _STAMP_FONT_BOLD, _STAMP_FONT_REGULAR

    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    regular_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    _STAMP_FONT_BOLD = next((p for p in bold_candidates if os.path.exists(p)), None)
    _STAMP_FONT_REGULAR = next((p for p in regular_candidates if os.path.exists(p)), None)
    _STAMP_FONT_RESOLVED = True

    if not _STAMP_FONT_BOLD or not _STAMP_FONT_REGULAR:
        logger.warning("Print Batcher: кириллический шрифт для штампа Мастер-Группы не найден, штамп пропускается")

    return _STAMP_FONT_BOLD, _STAMP_FONT_REGULAR


def _collect_blockers(src_page: fitz.Page, clip: fitz.Rect) -> List[fitz.Rect]:
    """
    Занятые участки накладной (текст + картинки). Разбор текстового слоя —
    самая дорогая часть поиска места под штамп, поэтому он делается ОДИН раз
    на страницу и переиспользуется для обеих попыток подобрать высоту рамки.
    """
    blockers = []
    for b in src_page.get_text("blocks"):
        r = fitz.Rect(b[:4])
        if r.width > 2 and r.height > 2 and r.intersects(clip):
            blockers.append(r)
    for img in src_page.get_image_info():
        r = fitz.Rect(img["bbox"])
        if r.intersects(clip):
            blockers.append(r)
    return blockers


_STORE_NAME_RE = re.compile(r'^[A-ZА-ЯЁ][A-ZА-ЯЁ \-]{1,24}$')


def _find_store_name_anchor(src_page: fitz.Page, clip: fitz.Rect) -> Optional[fitz.Rect]:
    """
    Название магазина — поле шаблона Kaspi, а не наша догадка: короткая
    строка ЗАГЛАВНЫМИ на фиксированном месте. Проверено на 236 из 236 обычных
    накладных живой партии Бархана (2026-09-04) — все день в день на одних и
    тех же координатах, с одним и тем же зазором 45pt до следующего блока.
    Штамп встаёт под этим полем, а не там, где нашлось свободное место —
    упаковщик видит его в одном и том же углу на каждой накладной.

    Верхние 10% высоты клипа исключены: под ту же регулярку (ЗАГЛАВНЫЕ,
    короткая строка) попадает заголовок "ОБРАЗЕЦ ПАРТНЕРА" на экспресс-листах
    Kaspi (другой размер страницы, 656х928мм) — он сидит у самого верха.
    """
    top_guard = clip.y0 + clip.height * 0.1
    candidates = []
    for b in src_page.get_text("blocks"):
        r = fitz.Rect(b[:4])
        if not r.intersects(clip) or r.y0 < top_guard:
            continue
        text = b[4].strip()
        if _STORE_NAME_RE.match(text) and r.width < clip.width * 0.3 and r.height < 20:
            candidates.append(r)
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.y0)
    return candidates[0]


def _find_stamp_band(blockers: List[fitz.Rect], clip: fitz.Rect, box_w: float, box_h: float, min_gap: float = 3.0) -> Optional[fitz.Rect]:
    """
    Ищет первую свободную от текста/картинок горизонтальную полосу внутри
    clip (по левому краю, шириной box_w) — туда впишется штамп группы, не
    перекрывая существующее содержимое накладной. Возвращает None, если
    подходящего места не нашлось (штамп в этом случае просто не рисуется).
    """
    x0 = clip.x0
    x1 = min(clip.x1, x0 + box_w)
    column_blockers = sorted(
        (r for r in blockers if r.x0 < x1 and r.x1 > x0),
        key=lambda r: r.y0
    )

    cursor = clip.y0
    for r in column_blockers:
        if r.y0 - cursor >= box_h + 2 * min_gap:
            return fitz.Rect(x0, cursor + min_gap, x1, cursor + min_gap + box_h)
        cursor = max(cursor, r.y1)

    if clip.y1 - cursor >= box_h + 2 * min_gap:
        return fitz.Rect(x0, cursor + min_gap, x1, cursor + min_gap + box_h)

    return None


_STAMP_FONT_OBJECTS: Dict[str, "fitz.Font"] = {}


def _stamp_font(path: str) -> "fitz.Font":
    """
    fitz.Font кэшируется на процесс: раньше шрифт встраивался в страницу на
    КАЖДЫЙ штамп (insert_font + CheckFont сканируют ресурсы страницы), и на
    партии в 1200 накладных это съедало десятки секунд.
    """
    font = _STAMP_FONT_OBJECTS.get(path)
    if font is None:
        font = fitz.Font(fontfile=path)
        _STAMP_FONT_OBJECTS[path] = font
    return font


def _wrap_text(font: "fitz.Font", text: str, fontsize: float, width: float) -> List[str]:
    """Перенос по словам под ширину рамки; слово длиннее строки режется."""
    lines: List[str] = []
    for word in text.split():
        if lines and font.text_length(lines[-1] + " " + word, fontsize) <= width:
            lines[-1] += " " + word
            continue
        if font.text_length(word, fontsize) <= width:
            lines.append(word)
            continue
        chunk = ""
        for ch in word:
            if font.text_length(chunk + ch, fontsize) <= width:
                chunk += ch
            else:
                if chunk:
                    lines.append(chunk)
                chunk = ch
        if chunk:
            lines.append(chunk)
    return lines


def _draw_fitted_text(target_page: fitz.Page, rect: fitz.Rect, text: str, font: "fitz.Font",
                      fontsize: float, color: tuple, min_fontsize: float,
                      max_lines: Optional[int] = None, center: bool = False) -> bool:
    """
    Вписывает название группы в рамку: кегль подбирается АРИФМЕТИКОЙ по
    ширине глифов, а не пробной отрисовкой. Прежняя версия перебирала кегли,
    каждый раз реально вызывая insert_textbox (тот молча возвращает минус,
    если текст не влез) — на 816 штампов выходило 5700 отрисовок.
    Если даже минимальный кегль не помещается, название режется многоточием.
    """
    if not text or rect.width <= 1 or rect.height <= 1:
        return False

    # Название категории на разделительном листе — как правило одно слово
    # ("Спорт", "Хозка", "Праздник"). _wrap_text умеет резать слово по буквам,
    # если оно не помещается целиком в текущий кегль ("Спорт" → "Спор"/"т") —
    # это нужно для длинных многословных подписей, но для одного слова читается
    # как опечатка. Пока слово не влезает целиком в одну строку — продолжаем
    # уменьшать кегль вместо того, чтобы принять такой перенос.
    single_word = " " not in text.strip()

    step = max(0.25, fontsize * 0.06)
    fs = fontsize
    while fs >= min_fontsize:
        lines = _wrap_text(font, text, fs, rect.width)
        if single_word and len(lines) > 1:
            fs -= step
            continue
        # max_lines — для разделительных листов: технически «влезающие» четыре
        # строки во всю высоту читаются хуже, чем две покрупнее, и упираются
        # в рамку.
        fits_height = lines and len(lines) * fs * 1.25 <= rect.height
        fits_lines = max_lines is None or (lines and len(lines) <= max_lines)
        if fits_height and fits_lines:
            break
        fs -= step
    else:
        # Не влезло даже минимальным кеглем — режем с многоточием
        fs = min_fontsize
        max_lines = max(1, int(rect.height // (fs * 1.25)))
        trimmed = text
        while len(trimmed) > 4:
            trimmed = trimmed[:-2]
            lines = _wrap_text(font, trimmed + "…", fs, rect.width)
            if len(lines) <= max_lines:
                break
        else:
            return False

    writer = fitz.TextWriter(target_page.rect, color=color)
    block_h = len(lines) * fs * 1.25
    y = rect.y0 + fs * font.ascender
    if center and block_h < rect.height:
        y += (rect.height - block_h) / 2.0
    for line in lines:
        if y > rect.y1:
            break
        writer.append(fitz.Point(rect.x0, y), line, font=font, fontsize=fs)
        y += fs * 1.25
    writer.write_text(target_page)
    return True


def draw_group_stamp(target_page: fitz.Page, target_rect: fitz.Rect, src_page: fitz.Page,
                     src_clip: fitz.Rect, label: Optional[str],
                     tags: Optional[List[str]] = None) -> None:
    """
    Штамп группы/меток — маленькая рамка прямо под названием магазина на
    накладной (см. _find_store_name_anchor): это поле шаблона Kaspi, а не
    угаданное место, и упаковщик видит штамп в одном и том же углу каждой
    накладной, а не там, где случайно нашлось свободное место. Место
    ищется на ИСХОДНОЙ странице (src_page/src_clip) в её собственных
    координатах, а рисуется в target_page/target_rect — координаты и размер
    шрифта масштабируются так же, как show_pdf_page масштабирует src_clip в
    target_rect (единый механизм для склеенных термоэтикеток, A4-сеток и
    страниц "как есть").

    Метки товара печатаются строкой в том же боксе — упаковщику нужен один
    ориентир, а не два разных прямоугольника в разных углах этикетки. Товар
    без Мастер-Группы, но с метками, получает бокс из одних меток: раньше
    ему не доставалось вообще ничего.

    Если названия магазина на странице нет (другой лист Kaspi, смена
    шаблона) — прежний обход: первая свободная полоса на странице, с прежним
    более крупным боксом и явным заголовком "ГРУППА"/"МЕТКИ", раз позиция
    больше не подсказана сама.
    """
    tags = [t for t in (tags or []) if str(t).strip()]
    if not label and not tags:
        return

    font_bold, font_regular = _resolve_stamp_fonts()
    if not font_bold or not font_regular:
        return

    blockers = _collect_blockers(src_page, src_clip)

    compact = False
    band = None
    anchor = _find_store_name_anchor(src_page, src_clip)
    if anchor is not None:
        box_h = 32.0
        top = anchor.y1 + 3.0
        bottom = top + box_h
        # Ширина бокса не берётся фиксированной долей страницы — сразу под
        # названием магазина (правее и чуть ниже) на накладной Kaspi лежит
        # штрихкод, и фиксированная доля в него упиралась на 100% реальной
        # партии (штрихкод стабильно стартует на x=175.4, temp/-замер на 236
        # накладных). Вместо этого берём максимум доступного вправо до
        # ближайшего блокера в этой же горизонтальной полосе.
        right_limit = src_clip.x1
        for r in blockers:
            if r == anchor:
                continue
            if r.y1 > top and r.y0 < bottom and r.x0 >= anchor.x0:
                right_limit = min(right_limit, r.x0)
        box_w = min(src_clip.width * 0.32, right_limit - anchor.x0 - 4.0)
        if box_w >= 60.0:
            candidate = fitz.Rect(anchor.x0, top, anchor.x0 + box_w, bottom)
            if candidate.y1 <= src_clip.y1 and not any(r.intersects(candidate) for r in blockers if r != anchor):
                band = candidate
                compact = True

    if band is None:
        box_w = src_clip.width * 0.54
        # Две строки названия при читаемом кегле требуют более высокой рамки, чем
        # прежние 7.8% высоты накладной. Если такой полосы на накладной не нашлось —
        # откатываемся к прежней высоте, чтобы штамп не пропал совсем.
        box_h = src_clip.height * 0.105
        band = _find_stamp_band(blockers, src_clip, box_w, box_h)
        if band is None:
            box_h = src_clip.height * 0.078
            band = _find_stamp_band(blockers, src_clip, box_w, box_h)
    if band is None:
        return

    scale = min(target_rect.width / src_clip.width, target_rect.height / src_clip.height)
    disp_w = src_clip.width * scale
    disp_h = src_clip.height * scale
    off_x = target_rect.x0 + (target_rect.width - disp_w) / 2.0
    off_y = target_rect.y0 + (target_rect.height - disp_h) / 2.0

    box = fitz.Rect(
        off_x + (band.x0 - src_clip.x0) * scale,
        off_y + (band.y0 - src_clip.y0) * scale,
        off_x + (band.x1 - src_clip.x0) * scale,
        off_y + (band.y1 - src_clip.y0) * scale,
    )

    size_ref = max(0.4, min(1.3, target_rect.height / 425.2))
    bold = _stamp_font(font_bold)
    regular = _stamp_font(font_regular)
    tags_line = " · ".join(tags)

    if compact:
        # Бокс встал под названием магазина, а не подобран по доле высоты
        # страницы, поэтому и кегль берём от РЕАЛЬНОЙ высоты этого маленького
        # бокса, а не от size_ref — иначе на A4 без масштабирования (scale≈1)
        # текст, рассчитанный на прежний бокс втрое выше, туда не влезет.
        # Заголовка "ГРУППА"/"МЕТКИ" нет: позиция под названием магазина уже
        # даёт упаковщику контекст, а в маленьком боксе он только крал бы
        # место у самого текста.
        border_w = 0.8 * size_ref
        pad = max(1.5, box.height * 0.08)
        target_page.draw_rect(box, color=(0.45, 0.45, 0.45), width=border_w)
        inner = fitz.Rect(box.x0 + pad, box.y0 + pad, box.x1 - pad, box.y1 - pad)

        if label and tags:
            label_rect = fitz.Rect(inner.x0, inner.y0, inner.x1, inner.y0 + inner.height * 0.58)
            tags_rect = fitz.Rect(inner.x0, label_rect.y1, inner.x1, inner.y1)
            if not _draw_fitted_text(target_page, label_rect, label, bold, label_rect.height * 0.8,
                                     (0.3, 0.3, 0.3), 2.5):
                logger.warning(f"Print Batcher: название группы '{label}' не поместилось в штамп")
            _draw_fitted_text(target_page, tags_rect, tags_line, regular, tags_rect.height * 0.8,
                              (0.4, 0.4, 0.4), 2.2)
        elif label:
            if not _draw_fitted_text(target_page, inner, label, bold, inner.height * 0.68,
                                     (0.3, 0.3, 0.3), 2.5, center=True):
                logger.warning(f"Print Batcher: название группы '{label}' не поместилось в штамп")
        else:
            if not _draw_fitted_text(target_page, inner, tags_line, bold, inner.height * 0.55,
                                     (0.3, 0.3, 0.3), 2.2, center=True):
                logger.warning(f"Print Batcher: метки '{tags_line}' не поместились в штамп")
        return

    border_w = 1.0 * size_ref
    pad = 5.0 * size_ref
    label_fs = 6.5 * size_ref
    name_fs = 12.0 * size_ref

    target_page.draw_rect(box, color=(0.45, 0.45, 0.45), width=border_w)

    top_pad = 2.0 * size_ref
    label_h = label_fs * 1.5
    label_rect = fitz.Rect(box.x0 + pad, box.y0 + top_pad, box.x1 - pad, box.y0 + top_pad + label_h)
    _draw_fitted_text(target_page, label_rect, "ГРУППА" if label else "МЕТКИ", regular, label_fs,
                      (0.55, 0.55, 0.55), label_fs * 0.7)

    bottom = box.y1 - 1.5 * size_ref
    if tags and label:
        # Метки забирают нижнюю треть бокса, название группы — остальное.
        # Полоса под метки не должна съедать название: оно главный ориентир.
        tags_h = min((bottom - label_rect.y1) * 0.34, name_fs * 1.4)
        tags_rect = fitz.Rect(box.x0 + pad, bottom - tags_h, box.x1 - pad, bottom)
        bottom = tags_rect.y0
        _draw_fitted_text(target_page, tags_rect, tags_line, regular, name_fs * 0.62,
                          (0.35, 0.35, 0.35), max(3.0, 4.0 * size_ref))

    if label:
        name_rect = fitz.Rect(box.x0 + pad, label_rect.y1, box.x1 - pad, bottom)
        if not _draw_fitted_text(target_page, name_rect, label, bold, name_fs,
                                 (0.3, 0.3, 0.3), max(3.5, 5.0 * size_ref)):
            logger.warning(f"Print Batcher: название группы '{label}' не поместилось в штамп")
    else:
        tags_rect = fitz.Rect(box.x0 + pad, label_rect.y1, box.x1 - pad, bottom)
        if not _draw_fitted_text(target_page, tags_rect, tags_line, bold, name_fs,
                                 (0.3, 0.3, 0.3), max(3.5, 5.0 * size_ref), center=True):
            logger.warning(f"Print Batcher: метки '{tags_line}' не поместились в штамп")


def draw_separator_page(out_doc: fitz.Document, width_pt: float, height_pt: float,
                        title: str, subtitle: str = "") -> None:
    """
    Разделительный лист перед блоком категории: крупная надпись во всю
    страницу, чтобы упаковщик видел границу стопки, не вчитываясь.

    Печатается в геометрии самой партии (термоэтикетка 75x120, A4 и т.д.),
    иначе принтер этикеток подавится страницей чужого размера.
    """
    page = out_doc.new_page(width=width_pt, height=height_pt)
    font_bold, font_regular = _resolve_stamp_fonts()
    if not font_bold or not font_regular:
        # Без кириллического шрифта надпись вышла бы «?????» — лучше отдать
        # пустой лист-разделитель, он всё равно виден в стопке.
        return

    bold = _stamp_font(font_bold)
    regular = _stamp_font(font_regular)

    margin = min(width_pt, height_pt) * 0.08
    inner = fitz.Rect(margin, margin, width_pt - margin, height_pt - margin)
    page.draw_rect(inner, color=(0.2, 0.2, 0.2), width=max(1.0, min(width_pt, height_pt) * 0.012))

    label_fs = max(6.0, inner.height * 0.05)
    label_rect = fitz.Rect(inner.x0 + margin * 0.4, inner.y0 + margin * 0.4,
                           inner.x1 - margin * 0.4, inner.y0 + margin * 0.4 + label_fs * 1.6)
    _draw_fitted_text(page, label_rect, "СЛЕДУЮЩАЯ КАТЕГОРИЯ", regular, label_fs,
                      (0.4, 0.4, 0.4), label_fs * 0.6)

    title_fs = max(10.0, inner.height * 0.16)
    title_rect = fitz.Rect(inner.x0 + margin * 0.4, label_rect.y1 + margin * 0.3,
                           inner.x1 - margin * 0.4, inner.y1 - margin * 0.9)
    _draw_fitted_text(page, title_rect, title, bold, title_fs, (0.0, 0.0, 0.0), 8.0,
                      max_lines=3, center=True)

    if subtitle:
        sub_fs = max(6.0, inner.height * 0.05)
        sub_rect = fitz.Rect(inner.x0 + margin * 0.4, inner.y1 - margin * 0.9,
                             inner.x1 - margin * 0.4, inner.y1 - margin * 0.2)
        _draw_fitted_text(page, sub_rect, subtitle, regular, sub_fs, (0.35, 0.35, 0.35), 5.0)


class PageItem:
    def __init__(self, pdf_path: str, page_num: int, source_filename: str, original_order: int,
                 w_mm: float, h_mm: float, category: str, order_id: Optional[str],
                 rotation: int, is_express: bool, unique_key: str,
                 product_name: Optional[str] = None, stamp_label: Optional[str] = None,
                 group_sort_key: Optional[str] = None,
                 store_category_key: Optional[str] = None,
                 store_category_title: Optional[str] = None,
                 store_category_order: int = 0,
                 tags: Optional[List[str]] = None):
        self.pdf_path = pdf_path
        self.page_num = page_num
        self.source_filename = source_filename
        self.original_order = original_order
        self.w_mm = w_mm
        self.h_mm = h_mm
        self.category = category
        self.order_id = order_id
        self.rotation = rotation
        self.is_express = is_express
        self.unique_key = unique_key
        self.product_name = product_name
        self.stamp_label = stamp_label
        self.tags = tags or []
        self.group_sort_key = group_sort_key
        # Крупная категория магазина (S/T/K/D/... у Бархана) — см.
        # StoreCategoryResolver. У магазина без настроенных категорий пусто.
        self.store_category_key = store_category_key
        self.store_category_title = store_category_title
        self.store_category_order = store_category_order


# Архив Kaspi — это ОДИН PDF на заказ, и на партии в 1200+ накладных склейка
# упиралась в лимит открытых файлов процесса (у systemd по умолчанию 1024):
# наружу это вылезало как "Failed to open file ... as type pdf", а внутри было
# OSError 24 (Too many open files), который заодно ронял приём HTTP-соединений
# всего сервиса.
#
# Причин было две, и лечить нужно обе:
#  1) словарь открытых документов рос без ограничения — теперь это LRU;
#  2) show_pdf_page/insert_pdf прививают исходный документ к результирующему,
#     и MuPDF держит файловый хэндл источника ДАЖЕ ПОСЛЕ .close() — до тех пор,
#     пока жив итоговый документ. Проверено: при открытии по пути склейка падала
#     на 509-й накладной, при открытии из памяти 1400 проходят штатно.
#     Поэтому источник читается в память и открывается как stream — файловый
#     дескриптор не удерживается вообще.
DOC_CACHE_LIMIT = 32


class _DocCache:
    """LRU-кэш исходных PDF, открытых из памяти (без файловых дескрипторов)."""

    def __init__(self, limit: int = DOC_CACHE_LIMIT):
        self.limit = max(1, limit)
        self._docs: "OrderedDict[str, fitz.Document]" = OrderedDict()

    def get(self, path: str) -> fitz.Document:
        doc = self._docs.get(path)
        if doc is not None:
            self._docs.move_to_end(path)
            return doc

        with open(path, "rb") as fh:
            data = fh.read()
        doc = fitz.open(stream=data, filetype="pdf")
        self._docs[path] = doc
        while len(self._docs) > self.limit:
            _, oldest = self._docs.popitem(last=False)
            try:
                oldest.close()
            except Exception:
                pass
        return doc

    def close_all(self) -> None:
        for doc in self._docs.values():
            try:
                doc.close()
            except Exception:
                pass
        self._docs.clear()

    def __enter__(self) -> "_DocCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close_all()


def _summarize_categories(pages: List["PageItem"]) -> List[Dict[str, Any]]:
    """Сводка «категория → сколько накладных», в порядке печати."""
    seen: "OrderedDict[Any, Dict[str, Any]]" = OrderedDict()
    for pg in pages:
        entry = seen.get(pg.store_category_key)
        if entry is None:
            seen[pg.store_category_key] = {
                "key": pg.store_category_key,
                "title": pg.store_category_title or "",
                "pages": 1,
            }
        else:
            entry["pages"] += 1
    return list(seen.values())


def _report(progress_cb: Optional[Callable[[str, int, int], None]], stage: str, done: int, total: int) -> None:
    """
    Отчёт о прогрессе наружу (в фоновую задачу роутера). Партия в 1200
    накладных считается около минуты — без этого пользователь минуту смотрит
    в неподвижный спиннер. Дёргаем не чаще, чем раз в 25 шагов.
    """
    if progress_cb is None:
        return
    if done != total and done % 25:
        return
    try:
        progress_cb(stage, done, total)
    except Exception:
        pass


# Отдельный часовой, а не None: у страницы без настроенных категорий
# store_category_key РАВЕН None, и разделитель перед самой первой стопкой
# всё равно должен появиться.
_SENTINEL_CATEGORY = object()


def _plural_waybills(n: int) -> str:
    """«1 накладная» / «2 накладные» / «5 накладных» — надпись читает человек."""
    if 11 <= n % 100 <= 14:
        return f"{n} накладных"
    last = n % 10
    if last == 1:
        return f"{n} накладная"
    if 2 <= last <= 4:
        return f"{n} накладные"
    return f"{n} накладных"


def _compute_category_page_ranges(
    items: List["PageItem"], items_per_page: int, separators: bool
) -> Dict[Any, Tuple[int, int]]:
    """
    Стартовая/конечная страница блока категории в итоговом PDF (сам
    разделительный лист в диапазон не входит — считаются только страницы с
    накладными). Нужно для подписи "стр. 12-40" на разделителе следующей
    категории, чтобы упаковщик мог сразу пролистать пачку до нужного места.

    Бухгалтерия страниц здесь ДОЛЖНА зеркалить порядок вставки разделителей и
    сетки в самом цикле генерации ниже — если тот поменяется, менять и это.
    """
    ranges: Dict[Any, List[int]] = {}
    page = 0
    item_count_on_page = 0
    prev_category = _SENTINEL_CATEGORY
    for item in items:
        if separators and item.store_category_key != prev_category:
            page += 1  # сам разделительный лист
            item_count_on_page = 0
            prev_category = item.store_category_key
        if item_count_on_page % items_per_page == 0:
            page += 1
        item_count_on_page += 1
        entry = ranges.setdefault(item.store_category_key, [page, page])
        entry[1] = page
    return {k: (v[0], v[1]) for k, v in ranges.items()}


def _category_subtitle(
    items: List["PageItem"], item: "PageItem", page_range: Optional[Tuple[int, int]] = None
) -> str:
    count = sum(1 for i in items if i.store_category_key == item.store_category_key)
    subtitle = _plural_waybills(count)
    if page_range:
        start, end = page_range
        subtitle += f" · стр. {start}-{end}" if start != end else f" · стр. {start}"
    return subtitle


def generate_nup_a4_pdf(
    items: List[PageItem],
    cols: int,
    rows: int,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    separators: bool = False
) -> fitz.Document:
    """Генератор N-up раскладки на листах A4 с подгонкой"""
    out_doc = fitz.open()
    items_per_page = cols * rows
    margin = 10.0
    gap = 6.0

    cell_w = (A4_WIDTH_PT - 2 * margin - (cols - 1) * gap) / cols
    cell_h = (A4_HEIGHT_PT - 2 * margin - (rows - 1) * gap) / rows

    doc_cache = _DocCache()
    page_ranges = _compute_category_page_ranges(items, items_per_page, separators) if separators else {}

    try:
        current_a4_page = None
        item_count_on_page = 0
        prev_category = _SENTINEL_CATEGORY

        for idx, item in enumerate(items):
            if separators and item.store_category_key != prev_category:
                # Разделитель — отдельный лист, и следующая категория начинается
                # с чистой сетки: иначе её первые накладные дожимали бы хвост
                # предыдущей стопки и граница переставала читаться.
                draw_separator_page(out_doc, A4_WIDTH_PT, A4_HEIGHT_PT,
                                    item.store_category_title or "",
                                    _category_subtitle(items, item, page_ranges.get(item.store_category_key)))
                prev_category = item.store_category_key
                item_count_on_page = 0
                current_a4_page = None
            if item_count_on_page % items_per_page == 0:
                current_a4_page = out_doc.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
                item_count_on_page = 0

            c = item_count_on_page % cols
            r = item_count_on_page // cols

            x0 = margin + c * (cell_w + gap)
            y0 = margin + r * (cell_h + gap)
            x1 = x0 + cell_w
            y1 = y0 + cell_h
            target_rect = fitz.Rect(x0, y0, x1, y1)

            src_doc = doc_cache.get(item.pdf_path)
            src_page = src_doc[item.page_num]

            c_rect = get_content_rect(src_page)

            current_a4_page.show_pdf_page(target_rect, src_doc, item.page_num, clip=c_rect, keep_proportion=True)
            if item.stamp_label or item.tags:
                try:
                    draw_group_stamp(current_a4_page, target_rect, src_page, c_rect, item.stamp_label, item.tags)
                except Exception as e:
                    logger.warning(f"Print Batcher: не удалось нарисовать штамп группы: {e}")
            item_count_on_page += 1
            _report(progress_cb, "Компоновка страниц", idx + 1, len(items))

        return out_doc
    finally:
        doc_cache.close_all()


def generate_thermal_pdf(
    items: List[PageItem],
    target_w_mm: float,
    target_h_mm: float,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    separators: bool = False
) -> fitz.Document:
    """Трансформация с ПОДГОНКОЙ во весь размер термоэтикетки (75x120 / 100x150 мм)"""
    target_w_pt = target_w_mm * 72.0 / 25.4
    target_h_pt = target_h_mm * 72.0 / 25.4
    margin = 4.0
    target_rect = fitz.Rect(margin, margin, target_w_pt - margin, target_h_pt - margin)

    out_doc = fitz.open()
    doc_cache = _DocCache()
    page_ranges = _compute_category_page_ranges(items, 1, separators) if separators else {}

    try:
        prev_category = _SENTINEL_CATEGORY
        for idx, item in enumerate(items):
            if separators and item.store_category_key != prev_category:
                draw_separator_page(out_doc, target_w_pt, target_h_pt,
                                    item.store_category_title or "",
                                    _category_subtitle(items, item, page_ranges.get(item.store_category_key)))
                prev_category = item.store_category_key
            new_p = out_doc.new_page(width=target_w_pt, height=target_h_pt)
            src_doc = doc_cache.get(item.pdf_path)
            src_page = src_doc[item.page_num]


            c_rect = get_content_rect(src_page)
            new_p.show_pdf_page(target_rect, src_doc, item.page_num, clip=c_rect, keep_proportion=True)
            if item.stamp_label or item.tags:
                try:
                    draw_group_stamp(new_p, target_rect, src_page, c_rect, item.stamp_label, item.tags)
                except Exception as e:
                    logger.warning(f"Print Batcher: не удалось нарисовать штамп группы: {e}")
            _report(progress_cb, "Компоновка страниц", idx + 1, len(items))

        return out_doc
    finally:
        doc_cache.close_all()


_STORE_PRINT_LOCKS: Dict[str, threading.Lock] = {}
_STORE_PRINT_LOCKS_GUARD = threading.Lock()


def _store_print_lock(store_id: Optional[str]) -> threading.Lock:
    """
    Один замок на магазин вокруг всей склейки.

    История печати читается в начале задачи, а пишется в самом конце — между
    этими точками проходят десятки секунд разбора PDF. Две пачки, отправленные
    подряд (типовой сценарий: утренний архив и дневной), успевали стартовать
    обе до того, как первая сохранит историю, и вторая честно считала все
    страницы новыми — «Исключено дубликатов: 0» на архиве, который на 90%
    состоял из уже напечатанного (прод Бархана 2026-08-21: 6 прогонов подряд,
    все с нулевым дедупом). Замок делает пары «прочитать историю → дописать
    историю» последовательными; вторая пачка ждёт и видит актуальные ключи.
    """
    key = store_id or "__legacy_global__"
    with _STORE_PRINT_LOCKS_GUARD:
        lock = _STORE_PRINT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _STORE_PRINT_LOCKS[key] = lock
    return lock


def process_zip_or_pdfs(
    file_bytes: bytes,
    original_filename: str,
    sort_preset: str = "filename",        # 'filename' | 'order_id' | 'original' | 'product_name' | 'master_group'
    auto_rotate: bool = True,
    express_mode: str = "separate",        # 'separate' | 'exclude' | 'ignore'
    deduplicate_history: bool = True,
    force_reprint: bool = False,
    output_layout: str = "auto",          # 'auto' | 'a4_4' | 'a4_8' | 'a4_9' | 'a4_1' | 'thermal_75_120' | 'thermal_100_150' | 'just_merge'
    store_id: Optional[str] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    filter_duplicates: Optional[bool] = None,
    store_categories: Optional[Dict[str, Any]] = None,
    order_skus: Optional[Dict[str, List[str]]] = None,
    product_tags: Optional[Dict[str, List[str]]] = None
) -> Dict[str, Any]:
    """
    Основной пайплайн обработки, подгонки и аудита геометрии накладных.

    store_id given (Слайс 5, дашборд): суточная дедупликация печати этого
    магазина. store_id=None (легаси, Telegram-бот — общий на все магазины,
    вне scope Слайса 5): общая история, как было всегда.

    Два РАЗНЫХ переключателя, которые раньше были одним:
      * deduplicate_history — вести ли журнал напечатанного вообще;
      * filter_duplicates   — отсеивать ли в ЭТОТ прогон уже напечатанное
        (None = как deduplicate_history).
    Раньше выбор «печатать всё» в боте гасил и запись журнала тоже, поэтому
    такая пачка не оставляла следов и СЛЕДУЮЩАЯ пачка не могла отличить в ней
    новое от повторов. Печать «всё» — это про текущий прогон, а не команда
    забыть, что эти заказы напечатаны.
    """
    with _store_print_lock(store_id):
        return _process_zip_or_pdfs_locked(
            file_bytes=file_bytes,
            original_filename=original_filename,
            sort_preset=sort_preset,
            auto_rotate=auto_rotate,
            express_mode=express_mode,
            deduplicate_history=deduplicate_history,
            force_reprint=force_reprint,
            output_layout=output_layout,
            store_id=store_id,
            progress_cb=progress_cb,
            filter_duplicates=filter_duplicates,
            store_categories=store_categories,
            order_skus=order_skus,
            product_tags=product_tags,
        )


def _process_zip_or_pdfs_locked(
    file_bytes: bytes,
    original_filename: str,
    sort_preset: str,
    auto_rotate: bool,
    express_mode: str,
    deduplicate_history: bool,
    force_reprint: bool,
    output_layout: str,
    store_id: Optional[str],
    progress_cb: Optional[Callable[[str, int, int], None]],
    filter_duplicates: Optional[bool],
    store_categories: Optional[Dict[str, Any]] = None,
    order_skus: Optional[Dict[str, List[str]]] = None,
    product_tags: Optional[Dict[str, List[str]]] = None
) -> Dict[str, Any]:
    batch_id = str(uuid.uuid4())
    batch_out_dir = TEMP_BATCH_DIR / batch_id
    batch_out_dir.mkdir(parents=True, exist_ok=True)

    record_history = deduplicate_history
    skip_already_printed = (
        deduplicate_history if filter_duplicates is None else filter_duplicates
    ) and not force_reprint

    history_printed_keys = load_printed_history(store_id=store_id) if skip_already_printed else set()

    extracted_pdf_paths: List[Tuple[str, int]] = []

    if original_filename.lower().endswith(".zip"):
        zip_tmp_path = batch_out_dir / "input_archive.zip"
        with open(zip_tmp_path, "wb") as f:
            f.write(file_bytes)
        
        extracted_dir = batch_out_dir / "extracted"
        extracted_dir.mkdir(exist_ok=True)
        
        with zipfile.ZipFile(zip_tmp_path, "r") as z:
            idx = 0
            for member_name in sorted(z.namelist()):
                if member_name.lower().endswith(".pdf") and not member_name.startswith("__MACOSX"):
                    extracted_path = z.extract(member_name, extracted_dir)
                    extracted_pdf_paths.append((extracted_path, idx))
                    idx += 1
    elif original_filename.lower().endswith(".pdf"):
        pdf_tmp_path = batch_out_dir / original_filename
        with open(pdf_tmp_path, "wb") as f:
            f.write(file_bytes)
        extracted_pdf_paths.append((str(pdf_tmp_path), 0))
    else:
        raise ValueError("Поддерживаются только .zip архивы или .pdf файлы")

    if not extracted_pdf_paths:
        raise ValueError("В загруженном архиве не найдено валидных PDF файлов")

    all_pages: List[PageItem] = []
    deduplicated_pages_count = 0
    excluded_express_count = 0
    grouped_pages_count = 0
    ungrouped_pages_count = 0
    sku_matched_pages_count = 0
    new_keys_to_save: Set[str] = set()

    category_resolver = StoreCategoryResolver(store_categories)
    # Крупные категории имеют смысл только там, где вообще извлекается товар:
    # сортировки по имени файла/номеру заказа о товаре ничего не знают.
    use_categories = category_resolver.enabled and sort_preset in ("master_group", "product_name")

    # Метки товаров — локальная таблица, тянем прямо здесь (в отличие от
    # состава заказов, который идёт по сети и приходит параметром). Явно
    # переданная карта побеждает: на ней держатся тесты.
    if product_tags is None and store_id and sort_preset in ("master_group", "product_name"):
        from backend.state_helpers import _load_product_tags
        product_tags = _load_product_tags(store_id)

    group_matcher: Optional[MasterGroupMatcher] = None
    if sort_preset == "master_group" and store_id:
        from backend.state_helpers import _load_master_stock_groups, _load_products
        # Группы и каталог грузятся ОДИН раз на всю очередь, а не на каждую
        # страницу: каталог нужен, чтобы развернуть linked_skus группы в
        # названия товаров — сопоставление идёт название-к-названию.
        master_groups = list((_load_master_stock_groups(store_id) or {}).values())
        group_matcher = MasterGroupMatcher(master_groups, _load_products(store_id) or [])

    # 1. Извлечение страниц и анализ геометрии
    unreadable_files: List[str] = []
    for scanned, (pdf_path, orig_order) in enumerate(extracted_pdf_paths, start=1):
        _report(progress_cb, "Чтение накладных", scanned, len(extracted_pdf_paths))
        basename = os.path.basename(pdf_path)
        # Один битый файл в архиве не должен ронять печать всей остальной партии
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logger.warning(f"Print Batcher: не удалось открыть '{basename}': {e}")
            unreadable_files.append(basename)
            continue

        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                rect = page.rect

                w_mm = rect.width * PT_TO_MM
                h_mm = rect.height * PT_TO_MM
                base_category = classify_page_size(w_mm, h_mm)

                text = page.get_text()
                order_id = extract_order_id(text, basename)
                unique_key = get_page_unique_key(text, basename, order_id)
                history_keys = get_page_history_keys(text, basename, order_id)
                express_flag = is_express_page(text, basename, w_mm, h_mm)

                product_name = None
                stamp_label = None
                page_tags: List[str] = []
                group_sort_key = None
                cat_key = cat_title = None
                cat_order = 0
                matched_group = None
                if sort_preset in ("product_name", "master_group"):
                    product_name = extract_product_name(text, basename)
                    # Артикул из состава заказа, если Kaspi его отдал. Это
                    # данные, а не результат распознавания картинки, поэтому
                    # он главнее всего, что удалось вычитать с накладной.
                    order_sku = None
                    if order_skus and order_id:
                        order_sku = next(iter(order_skus.get(order_id) or []), None)

                    if sort_preset == "master_group":
                        matched_group = group_matcher.match_by_sku(order_sku) if group_matcher else None
                        matched_by_sku = matched_group is not None
                        if matched_group is None and group_matcher:
                            matched_group = group_matcher.match(product_name)
                        if matched_group:
                            stamp_label = matched_group.get("group_name") or product_name
                            group_sort_key = f"0_{stamp_label}"
                            grouped_pages_count += 1
                            if matched_by_sku:
                                sku_matched_pages_count += 1
                        else:
                            ungrouped_pages_count += 1
                            # Товар не привязан ни к одной Мастер-Группе — фолбэк на сортировку по названию товара
                            group_sort_key = f"1_{product_name or basename}"

                    if product_tags:
                        # Метки ищем по артикулу из заказа, а если его нет —
                        # по привязкам найденной группы: у товара в группе тег
                        # может висеть на любом из её артикулов.
                        tag_skus = [order_sku] if order_sku else []
                        if not tag_skus and matched_group:
                            tag_skus = [str(s) for s in (matched_group.get("linked_skus") or [])]
                        seen_tags: Set[str] = set()
                        for candidate in tag_skus:
                            for tag in product_tags.get(str(candidate).strip(), []):
                                if tag not in seen_tags:
                                    seen_tags.add(tag)
                                    page_tags.append(tag)

                    if use_categories:
                        # Артикул ищем сначала в составе заказа, потом в
                        # привязках Мастер-Группы (там он лежит как данные, а не
                        # как текст на накладной), потом в названии группы и лишь
                        # затем в названии товара — по убыванию надёжности.
                        linked = " ".join(str(x) for x in ((matched_group or {}).get("linked_skus") or []))
                        cat_key, cat_title, cat_order = category_resolver.resolve(
                            order_sku,
                            linked or None,
                            (matched_group or {}).get("group_name"),
                            product_name,
                        )

                # Фильтрация по суточной истории — по любому из ключей страницы
                if skip_already_printed and (history_keys & history_printed_keys):
                    deduplicated_pages_count += 1
                    continue

                # Фильтрация по Экспресс в режиме exclude. Считаем отброшенное:
                # иначе экспресс-накладные просто исчезали между «Файлов в
                # архиве» и «Всего листов» без единой строчки объяснения.
                if express_mode == "exclude" and express_flag:
                    excluded_express_count += 1
                    continue

                final_category = base_category
                if express_mode == "separate" and express_flag:
                    express_cat_key = f"EXPRESS_{base_category}"
                    if express_cat_key in CATEGORY_META:
                        final_category = express_cat_key

                item = PageItem(
                    pdf_path=pdf_path,
                    page_num=page_num,
                    source_filename=basename,
                    original_order=orig_order * 1000 + page_num,
                    w_mm=round(w_mm, 1),
                    h_mm=round(h_mm, 1),
                    category=final_category,
                    order_id=order_id,
                    rotation=page.rotation,
                    is_express=express_flag,
                    unique_key=unique_key,
                    product_name=product_name,
                    stamp_label=stamp_label,
                    tags=page_tags,
                    group_sort_key=group_sort_key,
                    store_category_key=cat_key,
                    store_category_title=cat_title,
                    store_category_order=cat_order
                )
                all_pages.append(item)
                new_keys_to_save |= history_keys
        except Exception as e:
            logger.warning(f"Print Batcher: файл '{basename}' пропущен при разборе: {e}")
            unreadable_files.append(basename)
        finally:
            doc.close()

    if unreadable_files:
        logger.warning(
            f"Print Batcher: не прочитано файлов {len(unreadable_files)} из {len(extracted_pdf_paths)}"
        )
    if unreadable_files and not all_pages:
        raise ValueError(
            f"Ни один PDF из архива не удалось прочитать (файлов в архиве: {len(extracted_pdf_paths)})"
        )

    # 2. Сортировка списка страниц
    if sort_preset == "order_id":
        all_pages.sort(key=lambda p: (p.order_id or "999999999999", natural_sort_key(p.source_filename), p.page_num))
    elif sort_preset == "filename":
        all_pages.sort(key=lambda p: (natural_sort_key(p.source_filename), p.page_num))
    elif sort_preset == "product_name":
        all_pages.sort(key=lambda p: (natural_sort_key(p.product_name or p.source_filename), p.page_num))
    elif sort_preset == "master_group":
        # group_sort_key: "0_<название группы>" для привязанных к Мастер-Группе
        # (сортируются вместе по названию группы), "1_<название товара>" — фолбэк
        # для товаров без группы (сортируются по названию товара после всех групп)
        all_pages.sort(key=lambda p: (natural_sort_key(p.group_sort_key or p.source_filename), p.page_num))
    else:  # 'original'
        all_pages.sort(key=lambda p: p.original_order)

    # Крупная категория — ВНЕШНИЙ уровень поверх уже применённой сортировки.
    # Сортировка устойчивая, поэтому порядок внутри категории (по Мастер-Группам
    # или по названию товара) сохраняется как есть — «категория → группа».
    if use_categories:
        all_pages.sort(key=lambda p: (p.store_category_order, p.store_category_title or ""))

    use_separators = use_categories and category_resolver.separator_pages

    result_batches: Dict[str, Dict[str, Any]] = {}
    total_pages_found = len(all_pages) + deduplicated_pages_count + excluded_express_count

    # 2.5. Выделение Экспресса в отдельный батч при ЖЁСТКО выбранной раскладке.
    #
    # Разделение по категориям живёт только в ветке 'auto', поэтому при выборе
    # конкретного формата (термо/сетка A4) режим «Выделять в VIP-батчи» молча
    # ничего не выделял — экспресс уезжал в общую пачку. Для термоэтикеток это
    # особенно ломало печать: лист Kaspi Express (656x928 мм, три панели) вжимался
    # в этикетку 75x120 мм и становился нечитаемым.
    #
    # Экспресс-лист по пропорциям — ровно A4, увеличенный в ~3.1 раза, поэтому
    # его собственный батч печатаем 1-на-A4: получается исходный документ 1:1.
    express_pages_split: List[PageItem] = []
    if express_mode == "separate" and output_layout not in ("auto", "just_merge"):
        express_pages_split = [p for p in all_pages if p.is_express]
        if express_pages_split:
            all_pages = [p for p in all_pages if not p.is_express]

    # 3. Обработка режимов output_layout
    if output_layout in ["a4_4", "a4_8", "a4_9", "a4_1"]:
        if all_pages:
            grid_map = {
                "a4_4": (2, 2, "4 на A4 (Сетка 2x2)", "🖨️ Офисный принтер A4 (4 на листе)"),
                "a4_8": (2, 4, "8 на A4 (Сетка 2x4)", "🖨️ Офисный принтер A4 (8 на листе)"),
                "a4_9": (3, 3, "9 на A4 (Сетка 3x3)", "🖨️ Офисный принтер A4 (9 на листе)"),
                "a4_1": (1, 1, "1 на A4 (По центру)", "🖨️ Офисный принтер A4 (1 на листе)"),
            }
            cols, rows, layout_title, printer_desc = grid_map[output_layout]
            nup_doc = generate_nup_a4_pdf(all_pages, cols, rows, progress_cb, separators=use_separators)
            
            cat_key = f"LAYOUT_{output_layout.upper()}"
            output_pdf_filename = f"PRINT_BATCH_{cat_key}.pdf"
            output_pdf_path = batch_out_dir / output_pdf_filename
            nup_doc.save(str(output_pdf_path))
            total_pdf_pages = len(nup_doc)
            nup_doc.close()

            audit = validate_pdf_geometry(str(output_pdf_path), 210.0, 297.0)

            result_batches[cat_key] = {
                "category": cat_key,
                "title": layout_title,
                "icon": "🖼️",
                "printer_type": printer_desc,
                "is_express": False,
                "file_name": output_pdf_filename,
                "total_pages": total_pdf_pages,
                "download_url": f"/api/print-batcher/download/{batch_id}/{cat_key}",
                "preview_url": f"/api/print-batcher/file-preview/{batch_id}/{cat_key}",
                "sample_dimensions": f"210.0x297.0 мм ({cols}x{rows} на лист)",
                "geometry_audit": audit
            }

    elif output_layout in ["thermal_75_120", "thermal_100_150"]:
        if all_pages:
            thermal_map = {
                "thermal_75_120": (75.0, 120.0, "THERMAL_75x120", "🏷️ Термоэтикетки (75×120 мм)", "Термопринтер этикеток 75x120 / Xprinter / Zebra"),
                "thermal_100_150": (100.0, 150.0, "THERMAL_100x150", "🏷️ Термоэтикетки (100×150 мм)", "Термопринтер этикеток 100x150 / Xprinter / Zebra")
            }
            t_w, t_h, cat_key, title, printer_desc = thermal_map[output_layout]
            thermal_doc = generate_thermal_pdf(all_pages, t_w, t_h, progress_cb, separators=use_separators)
            
            output_pdf_filename = f"PRINT_BATCH_{cat_key}.pdf"
            output_pdf_path = batch_out_dir / output_pdf_filename
            thermal_doc.save(str(output_pdf_path))
            total_pdf_pages = len(thermal_doc)
            thermal_doc.close()

            audit = validate_pdf_geometry(str(output_pdf_path), t_w, t_h)

            result_batches[cat_key] = {
                "category": cat_key,
                "title": title,
                "icon": "🏷️",
                "printer_type": printer_desc,
                "is_express": False,
                "file_name": output_pdf_filename,
                "total_pages": total_pdf_pages,
                "download_url": f"/api/print-batcher/download/{batch_id}/{cat_key}",
                "preview_url": f"/api/print-batcher/file-preview/{batch_id}/{cat_key}",
                "sample_dimensions": f"{t_w}x{t_h} мм",
                "geometry_audit": audit
            }

    elif output_layout == "just_merge":
        if all_pages:
            out_doc = fitz.open()
            doc_cache = _DocCache()
            page_ranges = _compute_category_page_ranges(all_pages, 1, use_separators) if use_separators else {}
            try:
                prev_category = _SENTINEL_CATEGORY
                for item in all_pages:
                    src_doc = doc_cache.get(item.pdf_path)
                    if use_separators and item.store_category_key != prev_category:
                        # Размер разделителя берём у самой накладной — здесь
                        # страницы идут «как есть», общей геометрии нет.
                        sep_rect = src_doc[item.page_num].rect
                        draw_separator_page(out_doc, sep_rect.width, sep_rect.height,
                                            item.store_category_title or "",
                                            _category_subtitle(all_pages, item, page_ranges.get(item.store_category_key)))
                        prev_category = item.store_category_key
                    out_doc.insert_pdf(src_doc, from_page=item.page_num, to_page=item.page_num)
                    if item.stamp_label or item.tags:
                        try:
                            last_page = out_doc[-1]
                            c_rect = get_content_rect(src_doc[item.page_num])
                            draw_group_stamp(last_page, last_page.rect, src_doc[item.page_num], c_rect, item.stamp_label, item.tags)
                        except Exception as e:
                            logger.warning(f"Print Batcher: не удалось нарисовать штамп группы: {e}")

                cat_key = "MERGED_ALL"
                output_pdf_filename = f"PRINT_BATCH_{cat_key}.pdf"
                output_pdf_path = batch_out_dir / output_pdf_filename
                out_doc.save(str(output_pdf_path))
                total_pdf_pages = len(out_doc)
                out_doc.close()
            finally:
                doc_cache.close_all()

            audit = validate_pdf_geometry(str(output_pdf_path))

            result_batches[cat_key] = {
                "category": cat_key,
                "title": "Просто объединить (Все файлы как есть)",
                "icon": "📑",
                "printer_type": "Универсальный печатный пакет",
                "is_express": False,
                "file_name": output_pdf_filename,
                "total_pages": total_pdf_pages,
                "download_url": f"/api/print-batcher/download/{batch_id}/{cat_key}",
                "preview_url": f"/api/print-batcher/file-preview/{batch_id}/{cat_key}",
                "sample_dimensions": "Исходные форматы",
                "geometry_audit": audit
            }

    else:  # output_layout == 'auto'
        categorized_pages: Dict[str, List[PageItem]] = {cat: [] for cat in CATEGORY_META.keys()}
        for p in all_pages:
            if p.category in categorized_pages:
                categorized_pages[p.category].append(p)
            else:
                categorized_pages[p.category] = [p]

        doc_cache = _DocCache()
        try:
            for cat, items in categorized_pages.items():
                if not items:
                    continue

                out_doc = fitz.open()
                page_ranges = _compute_category_page_ranges(items, 1, use_separators) if use_separators else {}
                prev_category = _SENTINEL_CATEGORY
                for item in items:
                    src_doc = doc_cache.get(item.pdf_path)
                    if use_separators and item.store_category_key != prev_category:
                        sep_rect = src_doc[item.page_num].rect
                        draw_separator_page(out_doc, sep_rect.width, sep_rect.height,
                                            item.store_category_title or "",
                                            _category_subtitle(items, item, page_ranges.get(item.store_category_key)))
                        prev_category = item.store_category_key
                    out_doc.insert_pdf(src_doc, from_page=item.page_num, to_page=item.page_num)
                    last_page = out_doc[-1]

                    if item.stamp_label or item.tags:
                        try:
                            c_rect = get_content_rect(src_doc[item.page_num])
                            draw_group_stamp(last_page, last_page.rect, src_doc[item.page_num], c_rect, item.stamp_label, item.tags)
                        except Exception as e:
                            logger.warning(f"Print Batcher: не удалось нарисовать штамп группы: {e}")

                    if auto_rotate:
                        rect = last_page.rect
                        if "STICKER" in cat or "PAPER_A4" in cat:
                            if rect.width > rect.height:
                                last_page.set_rotation(90)

                output_pdf_filename = f"PRINT_BATCH_{cat}.pdf"
                output_pdf_path = batch_out_dir / output_pdf_filename
                out_doc.save(str(output_pdf_path))
                total_pdf_pages = len(out_doc)
                out_doc.close()

                audit = validate_pdf_geometry(str(output_pdf_path))

                meta = CATEGORY_META.get(cat, {
                    "title": f"Категория {cat}",
                    "icon": "📦",
                    "printer_type": "Стандартный принтер",
                    "is_express": False
                })

                result_batches[cat] = {
                    "category": cat,
                    "title": meta["title"],
                    "icon": meta["icon"],
                    "printer_type": meta["printer_type"],
                    "is_express": meta.get("is_express", False),
                    "file_name": output_pdf_filename,
                    "total_pages": total_pdf_pages,
                    "download_url": f"/api/print-batcher/download/{batch_id}/{cat}",
                    "preview_url": f"/api/print-batcher/file-preview/{batch_id}/{cat}",
                    "sample_dimensions": f"{items[0].w_mm}x{items[0].h_mm} мм",
                    "geometry_audit": audit
                }
        finally:
            doc_cache.close_all()

    # 3.5. Отдельный VIP-батч Экспресса (см. шаг 2.5) — печатаем 1-на-A4,
    # чтобы трёхпанельный лист сохранил исходные пропорции и читаемость.
    if express_pages_split:
        exp_doc = generate_nup_a4_pdf(express_pages_split, 1, 1, progress_cb)
        exp_cat_key = "EXPRESS_PRIORITY"
        exp_filename = f"PRINT_BATCH_{exp_cat_key}.pdf"
        exp_path = batch_out_dir / exp_filename
        exp_doc.save(str(exp_path))
        exp_total = len(exp_doc)
        exp_doc.close()

        result_batches[exp_cat_key] = {
            "category": exp_cat_key,
            "title": "⚡ 🚀 Экспресс-заказы (приоритет, 1 на A4)",
            "icon": "⚡",
            "printer_type": "Офисный принтер A4 (СРОЧНАЯ СБОРКА)",
            "is_express": True,
            "file_name": exp_filename,
            "total_pages": exp_total,
            "download_url": f"/api/print-batcher/download/{batch_id}/{exp_cat_key}",
            "preview_url": f"/api/print-batcher/file-preview/{batch_id}/{exp_cat_key}",
            "sample_dimensions": "210.0x297.0 мм",
            "geometry_audit": validate_pdf_geometry(str(exp_path), 210.0, 297.0)
        }

    # Сохранение истории. Пишем ВСЕГДА, когда журнал включён — даже если этот
    # прогон печатал всё подряд без фильтрации (см. докстрингу выше).
    if new_keys_to_save and record_history:
        save_printed_history(new_keys_to_save, store_id=store_id)

    total_new_pages = len(all_pages) + len(express_pages_split)
    all_duplicates = (deduplicated_pages_count > 0 and total_new_pages == 0)

    manifest = {
        "batch_id": batch_id,
        "original_filename": original_filename,
        "total_files": len(extracted_pdf_paths),
        "total_pages": total_pages_found,
        "deduplicated_pages": deduplicated_pages_count,
        "excluded_express_pages": excluded_express_count,
        # «Не нашло группу» — теперь измеримо, а не ощущение. Считаем только на
        # сортировке по Мастер-Группам, иначе сопоставление вообще не делалось.
        "grouped_pages": grouped_pages_count,
        "ungrouped_pages": ungrouped_pages_count,
        # Сколько групп проставлено по артикулу из состава заказа, а не
        # угадано по названию: видно, работает ли связь с Order API вообще.
        "sku_matched_pages": sku_matched_pages_count,
        "tagged_pages": sum(1 for p in all_pages if p.tags),
        # Что реально попало в каждую крупную категорию — чтобы владелец видел,
        # не уехала ли треть партии в «Прочее» из-за неназванного префикса.
        "store_categories": _summarize_categories(all_pages) if use_categories else [],
        "separator_pages": use_separators,
        "skipped_files": len(unreadable_files),
        "new_pages": total_new_pages,
        "filter_duplicates": skip_already_printed,
        "all_duplicates": all_duplicates,
        "sort_preset": sort_preset,
        "auto_rotate": auto_rotate,
        "express_mode": express_mode,
        "output_layout": output_layout,
        "deduplicate_history": deduplicate_history,
        "batches": result_batches
    }

    return manifest


def get_batch_file_path(batch_id: str, category: str) -> Optional[Path]:
    target_path = TEMP_BATCH_DIR / batch_id / f"PRINT_BATCH_{category}.pdf"
    if target_path.exists():
        return target_path
    return None
