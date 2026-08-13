"""
Excel bulk-template путь для «Автодобавление товара» — путь 3 из
07_ДОБАВЛЕНИЕ_ТОВАРА.md / HANDOFF.md 3.22 (в отличие от gui_uploader.py,
который повторяет 4-шаговый визард по одному товару).

Все mc.shop.kaspi.kz-вызовы синхронные (requests через
backend.state_helpers._mc_api_request), как в category_mapper.py.

Три реальных механики, найденные живым реверс-инжинирингом кабинета
(#/products/import), магазин уже пользовался ими вручную с июня 2025:
  1. GET .../content/importer/download/template?categoryCode=<code>
     -> .xlsm с тремя листами: intro / attributes / values
  2. Заполненный .xlsm + images/<merchant_sku>/*.jpg одним zip
     -> загружается вручную через кабинет (#/products/import?step=upload) —
     сам upload-эндпоинт ещё не пойман, см. открытые вопросы в HANDOFF 3.22
  3. .../content/importer/download/template/response?type=ORIGINAL|RESULT
     -> история/результат конкретной загрузки

Схема attributes-листа: 3 строки шапки (тип поля / служебный код / RU-
заголовок), затем строки-товары. Общие для всех категорий колонки всегда
первые N (merchant_sku, name, brand, image_code, youtube_id, image_urls,
description, weight, family_id или его категорийный эквивалент) — после них
идут category-specific характеристики с именами вида
"<Категория>*Harakteristiki.<slug>*<attr>". values-лист даёт enum-списки:
колонка "Бренд" (глобальный справочник), "category" (служебный маркер,
не показывается пользователю), и по одной колонке на каждую
enum/multiValued характеристику, с ЗАГОЛОВКОМ РАВНЫМ RU-заголовку в attributes
(напр. "Тип", "Материал", "Цвет").
"""

import io
import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Optional

import openpyxl
import requests

logger = logging.getLogger(__name__)

BASE = "https://mc.shop.kaspi.kz"

_COMMON_CODES = {
    "merchant_sku", "name", "brand", "image_code",
    "youtube_id", "image_urls", "description", "weight", "family_id",
}


def _headers() -> dict:
    return {
        "Accept": "application/json, text/plain, */*, application/vnd.ms-excel.sheet.macroEnabled.12",
        "Origin": "https://kaspi.kz",
        "Referer": "https://kaspi.kz/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Auth-Version": "3",
    }


STORE_BRAND = "Barkhan"  # см. HANDOFF / project_autolist_feature: один бренд для всего магазина,
# Kaspi принимает бренд свободным текстом даже если его нет в справочнике категории.


def check_sku_exists(merchant_sku: str, merchant_code: str, store_id: Optional[str] = None) -> Optional[dict]:
    """Проверяет, есть ли у ЭТОГО мерчанта уже своя карточка с таким merchant_sku
    (точное совпадение sku и бренд == STORE_BRAND — так отличаем реально нашу
    карточку от донорских/конкурентных офферов, которые находятся тем же
    текстовым поиском под суффиксом "-1" и НЕ являются дублем в смысле апрувнутого
    Kaspi-товара). Возвращает {"masterSku":..., "title":...} если уже есть, иначе None."""
    from backend.state_helpers import _mc_api_request
    resp = _mc_api_request(
        "GET", "https://mc.shop.kaspi.kz/bff/offer-view/list",
        store_id=store_id, headers=_headers(),
        params={"m": merchant_code, "p": 0, "l": 10, "a": "true", "t": merchant_sku},
        timeout=15,
    )
    resp.raise_for_status()
    for offer in resp.json().get("data") or []:
        if offer.get("sku") == merchant_sku and offer.get("brandName") == STORE_BRAND:
            return {"masterSku": offer.get("masterSku"), "title": offer.get("title")}
    return None


def upload_zip(zip_bytes: bytes, filename: str, merchant_code: str, employee_name: str, store_id: Optional[str] = None) -> dict:
    """Загружает готовый zip в мастер-импорт кабинета (POST .../template/upload).
    Возвращает {"status": "SUCCESS"/...}; сам результат обработки (принят ли
    товар, дубль ли это) смотреть отдельно в истории загрузок — этот вызов
    подтверждает только то, что HTTP-запрос дошёл."""
    from backend.state_helpers import _mc_api_request
    resp = _mc_api_request(
        "POST", "https://mc.shop.kaspi.kz/content/importer/mc/template/upload",
        store_id=store_id,
        headers={k: v for k, v in _headers().items() if k != "Accept"},
        params={"merchantCode": merchant_code, "employeeCode": employee_name},
        files={"file": (filename, zip_bytes, "application/zip")},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def download_template(category_code: str, store_id: Optional[str] = None) -> bytes:
    """Скачивает .xlsm-шаблон для категории. category_code — как из
    category_mapper.search_categories()[i]["code"] (напр.
    "Master - Glass washers and scrapers")."""
    from backend.state_helpers import _mc_api_request
    resp = _mc_api_request(
        "GET", f"{BASE}/content/importer/download/template",
        store_id=store_id, headers=_headers(), params={"categoryCode": category_code}, timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def parse_template_schema(xlsm_bytes: bytes) -> dict:
    """Разбирает скачанный шаблон в структуру:
    {
        "common_fields": ["merchant_sku", "name", ...],   # служебные коды, в порядке колонок
        "characteristics": [                               # только category-specific колонки
            {"col_index": int, "code": str, "label": str, "type": str,
             "multi_valued": bool, "mandatory": bool},
            ...
        ],
        "values": {ru_label: [option1, option2, ...]},     # из листа values, по заголовку
        "brands": [brand_name, ...],
    }
    """
    wb = openpyxl.load_workbook(io.BytesIO(xlsm_bytes), data_only=True, keep_vba=True)
    attrs_ws = wb["attributes"]
    rows = list(attrs_ws.iter_rows(min_row=1, max_row=3, values_only=True))
    type_row, code_row, label_row = rows[0], rows[1], rows[2]

    common_fields = []
    characteristics = []
    for i, code in enumerate(code_row):
        if code is None:
            continue
        if code in _COMMON_CODES:
            common_fields.append(code)
            continue
        type_desc = (type_row[i] or "").lower()
        characteristics.append({
            "col_index": i,
            "code": code,
            "label": label_row[i],
            "type": "number" if "число" in type_desc else "string",
            "multi_valued": "множество значений" in type_desc,
            "mandatory": "обязательное поле" in type_desc and "необязательное" not in type_desc,
        })

    values_ws = wb["values"]
    values_rows = list(values_ws.iter_rows(values_only=True))
    headers = values_rows[0] if values_rows else []
    values: dict[str, list[str]] = {h: [] for h in headers if h}
    brands: list[str] = []
    for row in values_rows[1:]:
        for col_idx, h in enumerate(headers):
            if not h or col_idx >= len(row):
                continue
            v = row[col_idx]
            if v is None:
                continue
            if h == "Бренд":
                brands.append(v)
            elif h != "category":
                values.setdefault(h, []).append(v)

    return {
        "common_fields": common_fields,
        "characteristics": characteristics,
        "values": values,
        "brands": brands,
    }


def fill_workbook(xlsm_bytes: bytes, rows: list[dict]) -> bytes:
    """rows: список словарей {код_или_RU_метка_колонки: значение}. Ключи
    должны совпадать либо со служебным кодом common-поля (merchant_sku,
    name, brand, image_code, description, ...), либо с RU-меткой
    характеристики (как в схеме parse_template_schema()["characteristics"]).
    Возвращает новые байты .xlsm с заполненным листом attributes (макросы
    сохраняются — keep_vba)."""
    wb = openpyxl.load_workbook(io.BytesIO(xlsm_bytes), keep_vba=True)
    ws = wb["attributes"]
    code_row = list(next(ws.iter_rows(min_row=2, max_row=2, values_only=True)))
    label_row = list(next(ws.iter_rows(min_row=3, max_row=3, values_only=True)))

    col_key_by_index = {}
    for i, code in enumerate(code_row):
        if code in _COMMON_CODES:
            col_key_by_index[i] = code
        elif label_row[i]:
            col_key_by_index[i] = label_row[i]

    start_row = 4
    for r_offset, row_data in enumerate(rows):
        excel_row = start_row + r_offset
        for col_idx, key in col_key_by_index.items():
            if key not in row_data:
                continue
            value = row_data[key]
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            ws.cell(row=excel_row, column=col_idx + 1).value = value

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def guess_attributes_via_deepseek(title: str, schema: dict) -> dict:
    """Текстовый (без фото) разбор названия товара в характеристики этой
    категории через DeepSeek — дёшево, но точность зависит от того, что
    реально написано в названии (см. открытый вопрос HANDOFF 3.22:
    комбинировать с AI по фото для полей, которых в названии нет).

    Возвращает {ru_label_или_"brand": значение_или_[значения]}. Для полей,
    которые модель не смогла уверенно определить из текста, ключ просто
    отсутствует в результате (пусть остаются пустыми — лучше пустое поле,
    чем выдуманное значение)."""
    from backend.chatbot.config import load_chatbot_config

    api_key = (load_chatbot_config().get("deepseek_api") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY не задан в .env")

    fields_desc = []
    for c in schema["characteristics"]:
        allowed = schema["values"].get(c["label"])
        allowed_str = f" Допустимые значения: {allowed}." if allowed else ""
        multi = " (можно несколько значений)" if c["multi_valued"] else ""
        fields_desc.append(f'- "{c["label"]}"{multi} — тип {c["type"]}.{allowed_str}')

    system_prompt = (
        "Ты помогаешь заполнить карточку товара Kaspi.kz по его названию. "
        "Тебе дано название товара и список полей категории. Для КАЖДОГО поля, "
        "которое можно уверенно определить из текста названия, укажи значение "
        "СТРОГО из списка допустимых значений (если он дан), либо короткий текст "
        "(если допустимых значений нет — поле текстовое/числовое). "
        "Если поле нельзя уверенно определить из названия — НЕ включай его в ответ, "
        "не придумывай. Ответь JSON-объектом {\"поле\": значение_или_массив_значений}. "
        "Также попробуй определить бренд ('brand') — если бренда в названии нет, не включай поле."
    )
    user_prompt = f"Название товара: {title}\n\nПоля категории:\n" + "\n".join(fields_desc)

    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def build_upload_zip(filled_xlsm_bytes: bytes, xlsm_filename: str, sku_photo_map: dict[str, list[Path]]) -> bytes:
    """Собирает готовый к загрузке zip: <xlsm_filename> в корне +
    images/<sku>/<n><ext> для каждого фото. sku_photo_map — {merchant_sku:
    [photo_path, ...]} (уже отсортированные в желаемом порядке отображения,
    максимум 5 на товар — обрезаем сами)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(xlsm_filename, filled_xlsm_bytes)
        for sku, photos in sku_photo_map.items():
            for n, photo_path in enumerate(photos[:5], start=1):
                ext = os.path.splitext(photo_path)[1]
                zf.write(photo_path, f"images/{sku}/{n}{ext}")
    return out.getvalue()
