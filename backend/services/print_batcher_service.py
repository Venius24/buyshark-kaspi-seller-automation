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


def is_express_page(text: str, filename: str) -> bool:
    """Определение относится ли страница/файл к Экспресс-доставке"""
    combined_str = f"{text} {filename}".lower()
    express_keywords = ["экспресс", "express", "каспи экспресс", "kaspi express", "срочная доставка"]
    for kw in express_keywords:
        if kw in combined_str:
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


class PageItem:
    def __init__(self, pdf_path: str, page_num: int, source_filename: str, original_order: int,
                 w_mm: float, h_mm: float, category: str, order_id: Optional[str],
                 rotation: int, is_express: bool, unique_key: str):
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

        return out_doc
    finally:
        for d in doc_cache.values():
            d.close()


def process_zip_or_pdfs(
    file_bytes: bytes,
    original_filename: str,
    sort_preset: str = "filename",        # 'filename' | 'order_id' | 'original'
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
            express_flag = is_express_page(text, basename)

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
                unique_key=unique_key
            )
            all_pages.append(item)
            new_keys_to_save.add(unique_key)
        doc.close()

    # 2. Сортировка списка страниц
    if sort_preset == "order_id":
        all_pages.sort(key=lambda p: (p.order_id or "999999999999", natural_sort_key(p.source_filename), p.page_num))
    elif sort_preset == "filename":
        all_pages.sort(key=lambda p: (natural_sort_key(p.source_filename), p.page_num))
    else:  # 'original'
        all_pages.sort(key=lambda p: p.original_order)

    result_batches: Dict[str, Dict[str, Any]] = {}
    total_pages_found = len(all_pages) + deduplicated_pages_count

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
                    
                    if auto_rotate:
                        last_page = out_doc[-1]
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

    # Сохранение истории
    if new_keys_to_save and deduplicate_history:
        save_printed_history(new_keys_to_save, store_id=store_id)

    total_new_pages = len(all_pages)
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
