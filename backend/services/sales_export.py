"""
Экспорт статистики продаж за период в Excel (§ запрос пользователя 2026-08-29).

Колонки: Фото | Артикул | Наименование | Код товара Kaspi | Продано, шт. |
Средняя цена продажи | Дней с продажами (период) | Дней с первой продажи.

Источник данных — те же sales_orders, что и /api/analytics/sales
(backend/services/sales_sync_worker.py). Фото/название/артикул по
возможности берутся из АКТУАЛЬНОГО каталога (_load_products), а не из
снимка в entries_payload на момент заказа — иначе в файле были бы старые
названия/фото после переименования товара.

«Дней с первой продажи» — не то же самое, что «дней в каталоге Kaspi»:
дата первого добавления товара в кабинет нигде не хранится (StoreProductDB
хранит только текущий снимок каталога, без истории). Это ближайший честный
прокси, который можно посчитать из sales_orders — календарные дни от самой
ранней сохранённой продажи этого SKU (за всю историю, не только за
выбранный период) до конца периода.
"""

import datetime as dt
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import openpyxl
import requests
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font
from PIL import Image as PILImage
from sqlalchemy import select

from backend.state_helpers import _get_sync_db_session, _load_products

logger = logging.getLogger(__name__)

_THUMB_PX = 64
_IMAGE_TIMEOUT_S = 6
_MAX_WORKERS = 8

HEADERS = [
    "Фото",
    "Артикул",
    "Наименование",
    "Код товара Kaspi",
    "Продано, шт.",
    "Средняя цена продажи",
    "Дней с продажами (период)",
    "Дней с первой продажи",
]


def _entry_group_key(entry: dict) -> str:
    """Ключ группировки строки заказа. entries_payload у Kaspi Order API
    хранит артикул (merchant SKU) в поле "sku", а настоящий числовой код
    товара Kaspi (тот же "sku"/"kaspiSku" в каталоге _load_products) —
    в "productCode" (см. KaspiAPI.get_completed_orders,
    backend/kaspi_wrapper.py:615-627 — там же явный комментарий, что
    offer.code почти всегда нечисловой, это НЕ код Kaspi). Группируем по
    productCode, когда он есть — так строки одного товара из разных заказов
    гарантированно схлопнутся и совпадут с каталогом; иначе — по артикулу."""
    code = str(entry.get("productCode") or "").strip()
    if code:
        return f"code:{code}"
    m_sku = str(entry.get("sku") or "").strip()
    return f"msku:{m_sku}" if m_sku else "unknown"


def _download_thumbnail(url: str) -> Optional[io.BytesIO]:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=_IMAGE_TIMEOUT_S)
        resp.raise_for_status()
        img = PILImage.open(io.BytesIO(resp.content))
        img = img.convert("RGB")
        img.thumbnail((_THUMB_PX, _THUMB_PX))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception as e:
        logger.warning(f"sales_export: не удалось скачать фото {url}: {e}")
        return None


def build_sales_export_xlsx(store_id: str, date_from: dt.date, date_to: dt.date) -> bytes:
    since = dt.datetime.combine(date_from, dt.time.min)
    until = dt.datetime.combine(date_to, dt.time.max)

    sku_data: dict = {}
    session = _get_sync_db_session()
    if session:
        try:
            from backend.db.models import SalesOrderDB

            rows = session.scalars(
                select(SalesOrderDB)
                .where(
                    SalesOrderDB.store_id == store_id,
                    SalesOrderDB.created_at >= since,
                    SalesOrderDB.created_at <= until,
                )
                .order_by(SalesOrderDB.created_at.asc())
            ).all()

            for r in rows:
                try:
                    entries = json.loads(r.entries_payload)
                except Exception:
                    entries = []
                day_key = r.created_at.strftime("%Y-%m-%d")
                for e in entries:
                    key = _entry_group_key(e)
                    info = sku_data.setdefault(
                        key,
                        {
                            "_key": key,
                            "sku": "",
                            "merchant_sku": "",
                            "name": "",
                            "quantity": 0,
                            "revenue": 0.0,
                            "days": set(),
                        },
                    )
                    info["quantity"] += e.get("quantity", 0) or 0
                    info["revenue"] += e.get("totalPrice", 0) or 0
                    info["days"].add(day_key)
                    kaspi_code = str(e.get("productCode") or "")
                    if kaspi_code:
                        info["sku"] = kaspi_code
                    m_sku = e.get("sku")
                    if m_sku:
                        info["merchant_sku"] = str(m_sku)
                    name = e.get("name")
                    if name and name != "Товар":
                        info["name"] = name

            if sku_data:
                pending = set(sku_data.keys())
                first_seen: dict = {}
                hist_rows = session.scalars(
                    select(SalesOrderDB)
                    .where(SalesOrderDB.store_id == store_id, SalesOrderDB.created_at <= until)
                    .order_by(SalesOrderDB.created_at.asc())
                ).all()
                for r in hist_rows:
                    if not pending:
                        break
                    try:
                        entries = json.loads(r.entries_payload)
                    except Exception:
                        entries = []
                    for e in entries:
                        key = _entry_group_key(e)
                        if key in pending:
                            first_seen[key] = r.created_at.date()
                            pending.discard(key)
                for key, info in sku_data.items():
                    fs = first_seen.get(key)
                    info["days_since_first_sale"] = (date_to - fs).days + 1 if fs else len(info["days"])
        finally:
            session.close()

    # Каталог у Kaspi ключуется числовым productCode (то же поле "sku"/
    # "kaspiSku" в _load_products) — заодно строим индекс по артикулу
    # (merchantSku) на случай, если у записи заказа productCode не
    # раскодировался (см. _entry_group_key).
    catalog_products = _load_products(store_id)
    catalog_by_code = {str(p.get("sku")): p for p in catalog_products if p.get("sku")}
    catalog_by_merchant_sku = {
        str(p.get("merchantSku")): p for p in catalog_products if p.get("merchantSku")
    }
    for key, info in sku_data.items():
        p = catalog_by_code.get(info["sku"]) or catalog_by_merchant_sku.get(info["merchant_sku"])
        if p:
            if p.get("name"):
                info["name"] = p["name"]
            if p.get("merchantSku"):
                info["merchant_sku"] = str(p["merchantSku"])
            if p.get("sku"):
                info["sku"] = str(p["sku"])
            info["image_url"] = p.get("image") or ""
        info["name"] = info["name"] or info["merchant_sku"] or key
        info.setdefault("image_url", "")
        info.setdefault("days_since_first_sale", len(info["days"]))

    items = sorted(sku_data.values(), key=lambda x: x["revenue"], reverse=True)

    thumbs: dict = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_download_thumbnail, it["image_url"]): it["_key"]
            for it in items
            if it["image_url"]
        }
        for fut in as_completed(futures):
            thumbs[futures[fut]] = fut.result()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Продажи"

    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    widths = {"A": 12, "B": 16, "C": 45, "D": 18, "E": 14, "F": 20, "G": 24, "H": 22}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    row_idx = 2
    for it in items:
        ws.row_dimensions[row_idx].height = 50
        avg_price = round(it["revenue"] / it["quantity"], 2) if it["quantity"] else 0
        ws.cell(row=row_idx, column=2, value=it["merchant_sku"])
        ws.cell(row=row_idx, column=3, value=it["name"])
        ws.cell(row=row_idx, column=4, value=it["sku"])
        ws.cell(row=row_idx, column=5, value=it["quantity"])
        ws.cell(row=row_idx, column=6, value=avg_price)
        ws.cell(row=row_idx, column=7, value=len(it["days"]))
        ws.cell(row=row_idx, column=8, value=it["days_since_first_sale"])

        thumb = thumbs.get(it["_key"])
        if thumb:
            img = XLImage(thumb)
            img.width = 60
            img.height = 60
            ws.add_image(img, f"A{row_idx}")
        row_idx += 1

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
