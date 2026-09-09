"""
Экспорт статистики продаж за период в Excel (§ запрос пользователя 2026-08-29,
настройки группировки/режима — 2026-09-10).

По умолчанию (`periods_mode=False`) одна строка = один товар (или одна
мастер-группа, см. ниже) за ВЕСЬ выбранный диапазон дат целиком, без
разбивки на периоды — так несколько экспортов за непересекающиеся диапазоны
можно склеить копипастом в одну таблицу и не получить дублей/шумных
полу-совпадающих периодов. Опционально (`periods_mode=True`, старое
поведение) одна строка = один ПЕРИОД активных продаж товара внутри
диапазона — если товар продавался, потом встал (нет ни одной продажи
≥ _GAP_DAYS дней подряд), потом снова начал продаваться, это два разных
периода с разными "Период с/по" и своими цифрами; так видно именно момент
возобновления продаж, а не смазанное "было N штук когда-то за весь
диапазон". Фото/артикул/название повторяются на каждой строке намеренно
(не merge cells) — смёрженные ячейки ломают сортировку/фильтры в Excel, а
этот файл explicitly для анализа, не для чтения глазами сверху вниз.

`group_by`: "card" (по умолчанию, как раньше — строка на карточку/SKU) или
"master_group" — товары, состоящие в активной Мастер-Группе остатка
(`master_stock_groups`, см. backend/services/stock_sync_worker.py), схлопы-
ваются в одну строку с суммарными цифрами по всем участникам; товары вне
групп остаются обычными карточками. Тот же матчинг артикул->группа, что
использует stock_forecast.py и списание остатка.

Колонки: Фото | Артикул | Наименование | Код товара Kaspi | Период с |
Период по | Дней с продажами в периоде | Продано, шт. | Средняя цена продажи.

Источник данных — sales_orders (backend/services/sales_sync_worker.py),
только записи внутри выбранного [date_from, date_to] — периоды строятся
из фактических дней с продажами ВНУТРИ диапазона, история до date_from не
привлекается (иначе пришлось бы разбираться с тем, что sales_orders не
хранит полную историю магазина, а только 90-дневный бэкафилл + то, что
накопилось с момента, когда воркер начал синкать конкретный стор).
Фото/название/артикул — из АКТУАЛЬНОГО каталога (_load_products), а не из
снимка в entries_payload на момент заказа.
"""

import datetime as dt
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from backend.product_name_overrides import is_placeholder_name

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
# Разрыв в этом количестве дней без единой продажи считается концом периода
# и стартом нового, когда продажи возобновляются.
_GAP_DAYS = 14

HEADERS = [
    "Фото",
    "Артикул",
    "Наименование",
    "Код товара Kaspi",
    "Период с",
    "Период по",
    "Дней с продажами в периоде",
    "Продано, шт.",
    "Средняя цена продажи",
    "Тип строки",
]


def _entry_group_key(entry: dict) -> str:
    """Ключ группировки строки заказа. entries_payload у Kaspi Order API
    хранит артикул (merchant SKU) в поле "sku", а настоящий числовой код
    товара Kaspi (тот же "sku"/"kaspiSku" в каталоге _load_products) —
    в "productCode" (см. KaspiAPI.get_completed_orders,
    backend/kaspi_wrapper.py:615-627). productCode — результат base64-decode
    и у части заказов не раскодировался (пустая строка, см. комментарий
    "if product_code.isdigit() else ''" там же) — группировка по нему ловила
    баг: один и тот же товар распадался на 2 разные группы (с кодом и без),
    что на выходе выглядело как два периода продаж, хотя разрыва не было
    (проверено вживую на Barkhan: "период 2" оказывался ЦЕЛИКОМ внутри
    "периода 1" того же товара). Артикул в "sku" присутствует у Kaspi
    всегда и стабилен — группируем по нему, а productCode используем только
    для отображения колонки "Код товара Kaspi"."""
    m_sku = str(entry.get("sku") or "").strip()
    if m_sku:
        return f"msku:{m_sku}"
    code = str(entry.get("productCode") or "").strip()
    return f"code:{code}" if code else "unknown"


def _split_into_periods(days_sorted: list) -> list:
    """[d1, d2, ...] отсортированные date -> [(period_start, period_end), ...],
    разрезая там, где разрыв между соседними днями с продажей > _GAP_DAYS."""
    periods = []
    start = end = days_sorted[0]
    for d in days_sorted[1:]:
        if (d - end).days > _GAP_DAYS:
            periods.append((start, end))
            start = d
        end = d
    periods.append((start, end))
    return periods


def _download_thumbnail(url: str, session: requests.Session) -> Optional[bytes]:
    if not url:
        return None
    try:
        resp = session.get(url, timeout=_IMAGE_TIMEOUT_S)
        resp.raise_for_status()
        img = PILImage.open(io.BytesIO(resp.content))
        img = img.convert("RGB")
        img.thumbnail((_THUMB_PX, _THUMB_PX))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"sales_export: не удалось скачать фото {url}: {e}")
        return None


def build_sales_export_xlsx(
    store_id: str,
    date_from: dt.date,
    date_to: dt.date,
    group_by: str = "card",
    periods_mode: bool = False,
) -> bytes:
    since = dt.datetime.combine(date_from, dt.time.min)
    until = dt.datetime.combine(date_to, dt.time.max)

    # key -> {day(date): {"qty": int, "revenue": float}}
    sku_daily: dict = {}
    # key -> {"sku": kaspi_code, "merchant_sku": ..., "name": ...}
    sku_meta: dict = {}

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
                day = r.created_at.date()
                for e in entries:
                    key = _entry_group_key(e)
                    meta = sku_meta.setdefault(key, {"sku": "", "merchant_sku": "", "name": ""})
                    kaspi_code = str(e.get("productCode") or "")
                    if kaspi_code:
                        meta["sku"] = kaspi_code
                    m_sku = e.get("sku")
                    if m_sku:
                        meta["merchant_sku"] = str(m_sku)
                    name = e.get("name")
                    if name and not is_placeholder_name(name, m_sku):
                        meta["name"] = name

                    daily = sku_daily.setdefault(key, {})
                    bucket = daily.setdefault(day, {"qty": 0, "revenue": 0.0})
                    bucket["qty"] += e.get("quantity", 0) or 0
                    bucket["revenue"] += e.get("totalPrice", 0) or 0
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
    image_url_by_key: dict = {}
    for key, meta in sku_meta.items():
        p = catalog_by_code.get(meta["sku"]) or catalog_by_merchant_sku.get(meta["merchant_sku"])
        if p:
            if p.get("name"):
                meta["name"] = p["name"]
            if p.get("merchantSku"):
                meta["merchant_sku"] = str(p["merchantSku"])
            if p.get("sku"):
                meta["sku"] = str(p["sku"])
            image_url_by_key[key] = p.get("image") or ""
        meta["name"] = meta["name"] or meta["merchant_sku"] or key
        meta["row_type"] = "Карточка"

    # group_by="master_group": товары из одной активной Мастер-Группы остатка
    # схлопываются в одну строку с суммарными цифрами — тот же матчинг
    # артикул->группа, что использует stock_forecast.py и списание остатка
    # (build_group_index/match_group_for_sku_indexed). Товар вне групп (или
    # если у магазина групп вовсе нет) остаётся обычной карточкой.
    if group_by == "master_group":
        from backend.state_helpers import _load_master_stock_groups
        from backend.services.stock_sync_worker import build_group_index, match_group_for_sku_indexed

        groups_cfg = _load_master_stock_groups(store_id)
        active_groups = {gid: g for gid, g in groups_cfg.items() if g.get("is_active")}
        if active_groups:
            index = build_group_index(active_groups)
            merged_daily: dict = {}
            merged_meta: dict = {}
            merged_image: dict = {}
            merged_members: dict = {}
            for key, daily in sku_daily.items():
                meta = sku_meta[key]
                gid, g = match_group_for_sku_indexed(meta.get("merchant_sku") or "", index)
                target_key = f"group:{gid}" if gid else key
                if gid:
                    merged_meta.setdefault(target_key, {
                        "sku": g.get("external_code") or "",
                        "merchant_sku": "",
                        "name": g.get("group_name") or gid,
                        "row_type": "Мастер-группа",
                    })
                    merged_members.setdefault(target_key, set()).add(meta.get("merchant_sku") or "")
                else:
                    merged_meta[target_key] = meta
                if image_url_by_key.get(key) and not merged_image.get(target_key):
                    merged_image[target_key] = image_url_by_key[key]
                gdaily = merged_daily.setdefault(target_key, {})
                for day, bucket in daily.items():
                    gb = gdaily.setdefault(day, {"qty": 0, "revenue": 0.0})
                    gb["qty"] += bucket["qty"]
                    gb["revenue"] += bucket["revenue"]
            for target_key, members in merged_members.items():
                merged_meta[target_key]["merchant_sku"] = ", ".join(sorted(m for m in members if m))
            sku_daily = merged_daily
            sku_meta = merged_meta
            image_url_by_key = merged_image

    # Группы: одна на ключ (карточка либо мастер-группа), с суммарной
    # выручкой (для сортировки самых продаваемых товаров наверх) и списком
    # строк внутри диапазона — либо периодами продаж (periods_mode=True),
    # либо одной строкой на весь диапазон (по умолчанию, без дублей).
    groups = []
    for key, daily in sku_daily.items():
        days_sorted = sorted(daily.keys())
        group_revenue = sum(v["revenue"] for v in daily.values())
        if periods_mode:
            period_rows = []
            for p_start, p_end in _split_into_periods(days_sorted):
                days_in_period = [d for d in days_sorted if p_start <= d <= p_end]
                qty = sum(daily[d]["qty"] for d in days_in_period)
                revenue = sum(daily[d]["revenue"] for d in days_in_period)
                period_rows.append({
                    "start": p_start,
                    "end": p_end,
                    "active_days": len(days_in_period),
                    "quantity": qty,
                    "revenue": revenue,
                })
        else:
            period_rows = [{
                "start": days_sorted[0],
                "end": days_sorted[-1],
                "active_days": len(days_sorted),
                "quantity": sum(daily[d]["qty"] for d in days_sorted),
                "revenue": group_revenue,
            }]
        groups.append({"key": key, "meta": sku_meta[key], "revenue": group_revenue, "periods": period_rows})

    groups.sort(key=lambda g: g["revenue"], reverse=True)

    thumbs: dict = {}
    # Общая Session с пулом соединений на все потоки — без неё requests.get()
    # открывает новый TCP+TLS хендшейк на КАЖДОЕ фото (~130мс), с keep-alive
    # переиспользуется одно соединение на resources.cdn-kaspi.kz (~13мс).
    # Проверено вживую на Barkhan (~1000 фото): без сессии экспорт занимал
    # ~112с почти сплошного CPU на handshake-крипто, с сессией — на порядок
    # быстрее. Session сама по себе потокобезопасна для конкурентных GET,
    # если пул адаптера рассчитан на число потоков (см. HTTPAdapter ниже).
    image_session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=_MAX_WORKERS, pool_maxsize=_MAX_WORKERS)
    image_session.mount("https://", adapter)
    image_session.mount("http://", adapter)
    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(_download_thumbnail, image_url_by_key.get(g["key"], ""), image_session): g["key"]
                for g in groups
                if image_url_by_key.get(g["key"])
            }
            for fut in as_completed(futures):
                thumbs[futures[fut]] = fut.result()
    finally:
        image_session.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Продажи"

    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    widths = {"A": 12, "B": 16, "C": 45, "D": 18, "E": 14, "F": 14, "G": 16, "H": 14, "I": 20, "J": 14}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    row_idx = 2
    for g in groups:
        meta = g["meta"]
        thumb_bytes = thumbs.get(g["key"])
        for period in g["periods"]:
            ws.row_dimensions[row_idx].height = 50
            avg_price = round(period["revenue"] / period["quantity"], 2) if period["quantity"] else 0
            ws.cell(row=row_idx, column=2, value=meta["merchant_sku"])
            ws.cell(row=row_idx, column=3, value=meta["name"])
            ws.cell(row=row_idx, column=4, value=meta["sku"])
            ws.cell(row=row_idx, column=5, value=period["start"].isoformat())
            ws.cell(row=row_idx, column=6, value=period["end"].isoformat())
            ws.cell(row=row_idx, column=7, value=period["active_days"])
            ws.cell(row=row_idx, column=8, value=period["quantity"])
            ws.cell(row=row_idx, column=9, value=avg_price)
            ws.cell(row=row_idx, column=10, value=meta.get("row_type", "Карточка"))

            if thumb_bytes:
                img = XLImage(io.BytesIO(thumb_bytes))
                img.width = 60
                img.height = 60
                ws.add_image(img, f"A{row_idx}")
            row_idx += 1

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
