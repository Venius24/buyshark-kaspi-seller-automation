import os
import re
import uuid
import json
import hashlib
import logging
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set
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


def extract_order_id(text: str, filename: str) -> Optional[str]:
    """Извлечение номера заказа (Kaspi Order ID) из текста страницы или имени файла"""
    fn_match = re.search(r"100\d{7,8}", filename)
    if fn_match:
        return fn_match.group(0)

    if text:
        text_clean = text.replace(" ", "")
        match = re.search(r"100\d{7,8}", text_clean)
        if match:
            return match.group(0)
        
        match_digits = re.search(r"\b\d{9,10}\b", text_clean)
        if match_digits:
            return match_digits.group(0)

    return None


def get_page_unique_key(text: str, filename: str, order_id: Optional[str]) -> str:
    """Сборка уникального ключа страницы для дедупликации"""
    if order_id:
        return f"order_{order_id}"
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"hash_{h}"


def natural_sort_key(s: str) -> List[Any]:
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


# Пунктирная линия перед "N шт." — основной шаблон термоэтикетки Kaspi:
# "Barkhan S517-0027 черный . . . . . . . . . . . .  1 шт."
_PRODUCT_DOT_LEADER_RE = re.compile(r'^\s*(?:\d+\.\s*)?(.+?)\s*(?:\.\s*){4,}\d+\s*шт\.?\s*$', re.MULTILINE)
_ORDER_ID_LINE_RE = re.compile(r'^\d[\d\s]{8,}\d$')


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

    m = _PRODUCT_DOT_LEADER_RE.search(text)
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

    return None


def match_master_group(product_name: Optional[str], master_groups: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Сопоставление названия товара накладной с Мастер-Группой остатка по
    подстроке (артикул/модель группы или её linked_skus встречается в
    названии товара). При нескольких совпадениях побеждает самое длинное
    (более специфичное) — короче 3 символов не рассматриваем, чтобы не
    ловить случайные буквенные совпадения.
    """
    if not product_name:
        return None

    name_lower = product_name.lower()
    best_group = None
    best_len = 0

    for group in master_groups:
        if not group.get("is_active", True):
            continue
        candidates = [group.get("group_name") or ""]
        candidates.extend(str(sku) for sku in (group.get("linked_skus") or []) if sku)

        for raw in candidates:
            token = raw.strip()
            if len(token) < 3:
                continue
            if token.lower() in name_lower and len(token) > best_len:
                best_group = group
                best_len = len(token)

    return best_group


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


def _find_stamp_band(src_page: fitz.Page, clip: fitz.Rect, box_w: float, box_h: float, min_gap: float = 3.0) -> Optional[fitz.Rect]:
    """
    Ищет первую свободную от текста/картинок горизонтальную полосу внутри
    clip (по левому краю, шириной box_w) — туда впишется штамп группы, не
    перекрывая существующее содержимое накладной. Возвращает None, если
    подходящего места не нашлось (штамп в этом случае просто не рисуется).
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


def _insert_textbox_fitted(target_page: fitz.Page, rect: fitz.Rect, text: str, fontname: str,
                           fontsize: float, color: tuple, min_fontsize: float) -> bool:
    """
    insert_textbox молча НЕ рисует ничего и возвращает отрицательное значение,
    если текст не влезает в прямоугольник. Из-за этого в штампе оставалось
    только слово "ГРУППА": название группы длиннее одного короткого слова
    переносилось на вторую строку, не помещалось по высоте и исчезало
    целиком. Подбираем кегль вниз, а если и минимальный не помог — режем
    название с многоточием.
    """
    step = max(0.25, fontsize * 0.06)
    fs = fontsize
    while fs >= min_fontsize:
        if target_page.insert_textbox(rect, text, fontname=fontname, fontsize=fs,
                                      color=color, align=0) >= 0:
            return True
        fs -= step

    trimmed = text
    while len(trimmed) > 4:
        trimmed = trimmed[:-2]
        if target_page.insert_textbox(rect, trimmed + "…", fontname=fontname, fontsize=min_fontsize,
                                      color=color, align=0) >= 0:
            return True
    return False


def draw_group_stamp(target_page: fitz.Page, target_rect: fitz.Rect, src_page: fitz.Page, src_clip: fitz.Rect, label: str) -> None:
    """
    Штамп "ГРУППА: <название>" — контурная рамка с подписью, вставляется в
    первый найденный свободный участок накладной. Место ищется на ИСХОДНОЙ
    странице (src_page/src_clip) в её собственных координатах, а рисуется
    в target_page/target_rect — координаты и размер шрифта масштабируются
    так же, как show_pdf_page масштабирует src_clip в target_rect (единый
    механизм для склеенных термоэтикеток, A4-сеток и страниц "как есть").
    """
    font_bold, font_regular = _resolve_stamp_fonts()
    if not font_bold or not font_regular:
        return

    box_w = src_clip.width * 0.54
    # Две строки названия при читаемом кегле требуют более высокой рамки, чем
    # прежние 7.8% высоты накладной. Если такой полосы на накладной не нашлось —
    # откатываемся к прежней высоте, чтобы штамп не пропал совсем.
    box_h = src_clip.height * 0.105
    band = _find_stamp_band(src_page, src_clip, box_w, box_h)
    if band is None:
        box_h = src_clip.height * 0.078
        band = _find_stamp_band(src_page, src_clip, box_w, box_h)
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
    border_w = 1.0 * size_ref
    pad = 5.0 * size_ref
    label_fs = 6.5 * size_ref
    name_fs = 12.0 * size_ref

    try:
        target_page.insert_font(fontname="F_STAMP_B", fontfile=font_bold)
        target_page.insert_font(fontname="F_STAMP_R", fontfile=font_regular)
    except Exception:
        pass

    target_page.draw_rect(box, color=(0.45, 0.45, 0.45), width=border_w)

    top_pad = 2.0 * size_ref
    label_h = label_fs * 1.5
    label_rect = fitz.Rect(box.x0 + pad, box.y0 + top_pad, box.x1 - pad, box.y0 + top_pad + label_h)
    target_page.insert_textbox(label_rect, "ГРУППА", fontname="F_STAMP_R", fontsize=label_fs,
                                color=(0.55, 0.55, 0.55), align=0)

    name_rect = fitz.Rect(box.x0 + pad, label_rect.y1, box.x1 - pad, box.y1 - 1.5 * size_ref)
    if not _insert_textbox_fitted(target_page, name_rect, label, "F_STAMP_B", name_fs,
                                  (0.3, 0.3, 0.3), max(3.5, 5.0 * size_ref)):
        logger.warning(f"Print Batcher: название группы '{label}' не поместилось в штамп")


class PageItem:
    def __init__(self, pdf_path: str, page_num: int, source_filename: str, original_order: int,
                 w_mm: float, h_mm: float, category: str, order_id: Optional[str],
                 rotation: int, is_express: bool, unique_key: str,
                 product_name: Optional[str] = None, stamp_label: Optional[str] = None,
                 group_sort_key: Optional[str] = None):
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
        self.group_sort_key = group_sort_key


def generate_nup_a4_pdf(
    items: List[PageItem],
    cols: int,
    rows: int
) -> fitz.Document:
    """Генератор N-up раскладки на листах A4 с подгонкой"""
    out_doc = fitz.open()
    items_per_page = cols * rows
    margin = 10.0
    gap = 6.0

    cell_w = (A4_WIDTH_PT - 2 * margin - (cols - 1) * gap) / cols
    cell_h = (A4_HEIGHT_PT - 2 * margin - (rows - 1) * gap) / rows

    doc_cache: Dict[str, fitz.Document] = {}

    try:
        current_a4_page = None
        item_count_on_page = 0

        for idx, item in enumerate(items):
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

            if item.pdf_path not in doc_cache:
                doc_cache[item.pdf_path] = fitz.open(item.pdf_path)
            src_doc = doc_cache[item.pdf_path]
            src_page = src_doc[item.page_num]
            
            c_rect = get_content_rect(src_page)

            current_a4_page.show_pdf_page(target_rect, src_doc, item.page_num, clip=c_rect, keep_proportion=True)
            if item.stamp_label:
                try:
                    draw_group_stamp(current_a4_page, target_rect, src_page, c_rect, item.stamp_label)
                except Exception as e:
                    logger.warning(f"Print Batcher: не удалось нарисовать штамп группы: {e}")
            item_count_on_page += 1

        return out_doc
    finally:
        for d in doc_cache.values():
            d.close()


def generate_thermal_pdf(
    items: List[PageItem],
    target_w_mm: float,
    target_h_mm: float
) -> fitz.Document:
    """Трансформация с ПОДГОНКОЙ во весь размер термоэтикетки (75x120 / 100x150 мм)"""
    target_w_pt = target_w_mm * 72.0 / 25.4
    target_h_pt = target_h_mm * 72.0 / 25.4
    margin = 4.0
    target_rect = fitz.Rect(margin, margin, target_w_pt - margin, target_h_pt - margin)

    out_doc = fitz.open()
    doc_cache: Dict[str, fitz.Document] = {}

    try:
        for item in items:
            new_p = out_doc.new_page(width=target_w_pt, height=target_h_pt)
            if item.pdf_path not in doc_cache:
                doc_cache[item.pdf_path] = fitz.open(item.pdf_path)
            src_doc = doc_cache[item.pdf_path]
            src_page = src_doc[item.page_num]
            
            c_rect = get_content_rect(src_page)
            new_p.show_pdf_page(target_rect, src_doc, item.page_num, clip=c_rect, keep_proportion=True)
            if item.stamp_label:
                try:
                    draw_group_stamp(new_p, target_rect, src_page, c_rect, item.stamp_label)
                except Exception as e:
                    logger.warning(f"Print Batcher: не удалось нарисовать штамп группы: {e}")

        return out_doc
    finally:
        for d in doc_cache.values():
            d.close()


def process_zip_or_pdfs(
    file_bytes: bytes,
    original_filename: str,
    sort_preset: str = "filename",        # 'filename' | 'order_id' | 'original' | 'product_name' | 'master_group'
    auto_rotate: bool = True,
    express_mode: str = "separate",        # 'separate' | 'exclude' | 'ignore'
    deduplicate_history: bool = True,
    force_reprint: bool = False,
    output_layout: str = "auto",          # 'auto' | 'a4_4' | 'a4_8' | 'a4_9' | 'a4_1' | 'thermal_75_120' | 'thermal_100_150' | 'just_merge'
    store_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Основной пайплайн обработки, подгонки и аудита геометрии накладных.

    store_id given (Слайс 5, дашборд): суточная дедупликация печати этого
    магазина. store_id=None (легаси, Telegram-бот — общий на все магазины,
    вне scope Слайса 5): общая история, как было всегда.
    """
    batch_id = str(uuid.uuid4())
    batch_out_dir = TEMP_BATCH_DIR / batch_id
    batch_out_dir.mkdir(parents=True, exist_ok=True)

    history_printed_keys = load_printed_history(store_id=store_id) if (deduplicate_history and not force_reprint) else set()

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
    new_keys_to_save: Set[str] = set()

    master_groups: List[Dict[str, Any]] = []
    if sort_preset == "master_group" and store_id:
        from backend.state_helpers import _load_master_stock_groups
        # Мастер-Группы остатка грузятся ОДИН раз на всю очередь, а не на каждую страницу
        master_groups = list((_load_master_stock_groups(store_id) or {}).values())

    # 1. Извлечение страниц и анализ геометрии
    for pdf_path, orig_order in extracted_pdf_paths:
        doc = fitz.open(pdf_path)
        basename = os.path.basename(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            rect = page.rect
            
            w_mm = rect.width * PT_TO_MM
            h_mm = rect.height * PT_TO_MM
            base_category = classify_page_size(w_mm, h_mm)
            
            text = page.get_text()
            order_id = extract_order_id(text, basename)
            unique_key = get_page_unique_key(text, basename, order_id)
            express_flag = is_express_page(text, basename, w_mm, h_mm)

            product_name = None
            stamp_label = None
            group_sort_key = None
            if sort_preset in ("product_name", "master_group"):
                product_name = extract_product_name(text, basename)
                if sort_preset == "master_group":
                    matched_group = match_master_group(product_name, master_groups)
                    if matched_group:
                        stamp_label = matched_group.get("group_name") or product_name
                        group_sort_key = f"0_{stamp_label}"
                    else:
                        # Товар не привязан ни к одной Мастер-Группе — фолбэк на сортировку по названию товара
                        group_sort_key = f"1_{product_name or basename}"

            # Фильтрация по суточной истории
            if deduplicate_history and not force_reprint:
                if unique_key in history_printed_keys:
                    deduplicated_pages_count += 1
                    continue

            # Фильтрация по Экспресс в режиме exclude
            if express_mode == "exclude" and express_flag:
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
                group_sort_key=group_sort_key
            )
            all_pages.append(item)
            new_keys_to_save.add(unique_key)
        doc.close()

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

    result_batches: Dict[str, Dict[str, Any]] = {}
    total_pages_found = len(all_pages) + deduplicated_pages_count

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
            nup_doc = generate_nup_a4_pdf(all_pages, cols, rows)
            
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
            thermal_doc = generate_thermal_pdf(all_pages, t_w, t_h)
            
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
            doc_cache: Dict[str, fitz.Document] = {}
            try:
                for item in all_pages:
                    if item.pdf_path not in doc_cache:
                        doc_cache[item.pdf_path] = fitz.open(item.pdf_path)
                    src_doc = doc_cache[item.pdf_path]
                    out_doc.insert_pdf(src_doc, from_page=item.page_num, to_page=item.page_num)
                    if item.stamp_label:
                        try:
                            last_page = out_doc[-1]
                            c_rect = get_content_rect(src_doc[item.page_num])
                            draw_group_stamp(last_page, last_page.rect, src_doc[item.page_num], c_rect, item.stamp_label)
                        except Exception as e:
                            logger.warning(f"Print Batcher: не удалось нарисовать штамп группы: {e}")

                cat_key = "MERGED_ALL"
                output_pdf_filename = f"PRINT_BATCH_{cat_key}.pdf"
                output_pdf_path = batch_out_dir / output_pdf_filename
                out_doc.save(str(output_pdf_path))
                total_pdf_pages = len(out_doc)
                out_doc.close()
            finally:
                for d in doc_cache.values():
                    d.close()

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

        doc_cache: Dict[str, fitz.Document] = {}
        try:
            for cat, items in categorized_pages.items():
                if not items:
                    continue

                out_doc = fitz.open()
                for item in items:
                    if item.pdf_path not in doc_cache:
                        doc_cache[item.pdf_path] = fitz.open(item.pdf_path)
                    src_doc = doc_cache[item.pdf_path]
                    out_doc.insert_pdf(src_doc, from_page=item.page_num, to_page=item.page_num)
                    last_page = out_doc[-1]

                    if item.stamp_label:
                        try:
                            c_rect = get_content_rect(src_doc[item.page_num])
                            draw_group_stamp(last_page, last_page.rect, src_doc[item.page_num], c_rect, item.stamp_label)
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
            for d in doc_cache.values():
                d.close()

    # 3.5. Отдельный VIP-батч Экспресса (см. шаг 2.5) — печатаем 1-на-A4,
    # чтобы трёхпанельный лист сохранил исходные пропорции и читаемость.
    if express_pages_split:
        exp_doc = generate_nup_a4_pdf(express_pages_split, 1, 1)
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

    # Сохранение истории
    if new_keys_to_save and deduplicate_history:
        save_printed_history(new_keys_to_save, store_id=store_id)

    total_new_pages = len(all_pages) + len(express_pages_split)
    all_duplicates = (total_pages_found > 0 and total_new_pages == 0)

    manifest = {
        "batch_id": batch_id,
        "original_filename": original_filename,
        "total_files": len(extracted_pdf_paths),
        "total_pages": total_pages_found,
        "deduplicated_pages": deduplicated_pages_count,
        "new_pages": total_new_pages,
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
