"""
Резолв категории/бренда/фото Kaspi + сборка и отправка финального payload на
создание нового товара — для фичи «Автодобавление товара».

Все сетевые вызовы синхронные (requests через backend.state_helpers.
_mc_api_request — единая точка HTTP к mc.shop.kaspi.kz с MC-куками этого
стора, с автообновлением сессии при 401), как в backend/dumper/executor.py.
В отличие от backend/autolist/extractor.py (который ходит во внешний OpenAI
API через httpx/async) — этот модуль работает синхронно, т.к. mc.shop.kaspi.kz
вызовы в проекте всегда были синхронными (executor.py, kaspi_wrapper.py).

Источник всех эндпоинтов/форматов — живой реверс-инжиниринг на реальном
магазине, см. ПСЕВДО_ДОКУМЕНТАЦИЯ_КАСПИ/07_ДОБАВЛЕНИЕ_ТОВАРА.md. Два реальных
товара были отправлены на модерацию в процессе разведки (T219-0024,
T219-0024-2) — это НЕ тестовые данные, они правда есть в кабинете магазина.
"""

import logging
import os
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

BASE = "https://mc.shop.kaspi.kz"
NO_BRAND_NAME = "Без бренда"

_HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://kaspi.kz",
    "Referer": "https://kaspi.kz/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Auth-Version": "3",
}


class DuplicateSkuError(Exception):
    """Kaspi отклонил создание: артикул уже используется живым или
    ожидающим модерации товаром (errorCode error.validation.master.product.exist).
    Решение "предложить другой артикул" — уровень выше (validator.py/UI),
    этот модуль только сигнализирует о факте."""

    def __init__(self, sku: str, message: str):
        self.sku = sku
        self.message = message
        super().__init__(message)


def _headers(json_body: bool = False) -> dict:
    h = dict(_HEADERS_BASE)
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _mc_get(url: str, store_id: Optional[str] = None, **kwargs):
    from backend.state_helpers import _mc_api_request
    return _mc_api_request("GET", url, store_id=store_id, headers=_headers(), timeout=15, **kwargs)


def _mc_post(url: str, json_payload=None, store_id: Optional[str] = None, **kwargs):
    from backend.state_helpers import _mc_api_request
    return _mc_api_request("POST", url, store_id=store_id, headers=_headers(json_body=True), json=json_payload, timeout=20, **kwargs)


def resolve_merchant_id(token: str) -> str:
    """Настоящий числовой Kaspi merchant ID — НЕ Store.merchant_id из БД
    (тот может быть синтетическим hash-фоллбеком, см. HANDOFF раздел 3.6)."""
    from backend.dumper.config import discover_merchant_id
    return discover_merchant_id(token)


def search_existing_products(query: str, store_id: Optional[str] = None) -> list[dict]:
    """Путь 1 из 07_ДОБАВЛЕНИЕ_ТОВАРА.md — полнотекстовый поиск по ВСЕМУ
    каталогу Kaspi (не только своему). Кандидаты для показа на ревью: если
    есть уверенное совпадение, разумнее присоединиться к существующей
    карточке, чем плодить дубликат (сам join-flow этот модуль не покрывает —
    не был исследован, см. открытые вопросы в доке)."""
    resp = _mc_get(f"{BASE}/product/view/mc/products", store_id=store_id, params={"text": query})
    if resp.status_code != 200:
        return []
    return resp.json().get("products", [])


def search_categories(query: str, merchant_id: str, store_id: Optional[str] = None) -> list[dict]:
    """{"code","name","hasContentChild","barcodeRequired","restricted",...}
    — плоский список, обычно единственное точное совпадение по узкому
    запросу вроде "термосумка"."""
    resp = _mc_get(f"{BASE}/product/classification/mc/category/search", store_id=store_id, params={"search": query, "m": merchant_id})
    if resp.status_code != 200:
        return []
    return resp.json() or []


def generate_sku(merchant_id: str, store_id: Optional[str] = None) -> Optional[str]:
    """Просит у Kaspi предзаполненный артикул (тот, что кабинет сам
    подставляет в поле "Артикул" на шаге 4 визарда, например "916670938") —
    удобный дефолт, пользователь может заменить его своим в review UI.
    Точная форма ответа не была отдельно захвачена в разведке (видели только
    результат в UI-поле) — читаем защитно на случай что это строка или
    {"code": "..."}."""
    resp = _mc_get(f"{BASE}/content/pending/mc/product/{merchant_id}/new-code", store_id=store_id)
    if resp.status_code != 200:
        return None
    body = resp.json()
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        return body.get("code") or body.get("sku")
    return None


def fetch_category_schema(category_code: str, merchant_id: str, store_id: Optional[str] = None) -> dict:
    """Полная схема характеристик категории — то, что
    backend.autolist.extractor.fill_attributes() использует для построения
    json_schema под structured output модели."""
    url = f"{BASE}/content/pending/mc/category/{quote(category_code, safe='')}/info"
    resp = _mc_get(url, store_id=store_id, params={"merchantCode": merchant_id})
    resp.raise_for_status()
    return resp.json()


def _brand_search(name: str, category_code: str, merchant_id: str, store_id: Optional[str]) -> list[dict]:
    """product/brands/mc/brand/find — рабочий поисковый endpoint (в отличие
    от product/request/mc/brand/list, который вернул пустой список в живой
    проверке даже с непустым prefix)."""
    resp = _mc_get(
        f"{BASE}/product/brands/mc/brand/find",
        store_id=store_id,
        params={"c": category_code, "p": 0, "name": name, "s": 20, "m": merchant_id},
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("data", [])


def resolve_brand(brand_name: Optional[str], category_code: str, merchant_id: str, store_id: Optional[str] = None) -> dict:
    """Возвращает {"code","name","restricted","closed","personal"} — идёт
    напрямую в payload create(). brand_name пустой/None → "Без бренда".

    "Без бренда" резолвится ПО ИМЕНИ через тот же brand/find, а не
    хардкодится ("china-toys" — реальный код, подтверждён живьём, но это
    внутренний Kaspi-специфичный литерал, платформенный, не завязанный на
    конкретную категорию/магазин — на случай если Kaspi его когда-нибудь
    сменит, резолвим динамически)."""
    if not brand_name:
        no_brand = _find_no_brand(category_code, merchant_id, store_id)
        if no_brand:
            return no_brand
        raise RuntimeError("Не удалось найти код бренда 'Без бренда' через brand/find — проверить endpoint вручную")

    candidates = _brand_search(brand_name, category_code, merchant_id, store_id)
    for c in candidates:
        if c.get("name", "").strip().lower() == brand_name.strip().lower():
            return c

    logger.warning(f"Бренд {brand_name!r} не найден в справочнике Kaspi для категории {category_code!r} — используется 'Без бренда'")
    return _find_no_brand(category_code, merchant_id, store_id) or {"code": "", "name": NO_BRAND_NAME, "restricted": False, "closed": False, "personal": False}


def _find_no_brand(category_code: str, merchant_id: str, store_id: Optional[str]) -> Optional[dict]:
    for c in _brand_search("", category_code, merchant_id, store_id):
        if c.get("name") == NO_BRAND_NAME:
            return c
    return None


def upload_photo(photo_path: str, store_id: Optional[str] = None) -> dict:
    """Возвращает {"id": uuid, "url", "status": "OK", "format", ...} — id
    идёт в images[].location финального create-payload."""
    from backend.state_helpers import _mc_api_request

    filename = os.path.basename(photo_path)
    mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    with open(photo_path, "rb") as f:
        files = {"file": (filename, f, mime)}
        resp = _mc_api_request("POST", f"{BASE}/image/processor/merchant/upl/cnt/mct/i", store_id=store_id, headers=_headers(), files=files, timeout=30)
    resp.raise_for_status()
    images = resp.json().get("images", [])
    if not images:
        raise RuntimeError(f"Kaspi не вернул images[] при загрузке фото {photo_path}")
    img = images[0]
    if img.get("status") != "OK":
        raise RuntimeError(f"Kaspi отклонил фото {photo_path}: {img}")
    return img


def build_features_payload(feature_values: dict, feature_meta: dict) -> list[dict]:
    """
    feature_values / feature_meta — напрямую из
    backend.autolist.extractor.fill_attributes() (ключи "features"/"feature_meta").

    Мимикрирует ТОЧНО то, что реально шлёт кабинет Kaspi (см. §4.2
    07_ДОБАВЛЕНИЕ_ТОВАРА.md): незаполненные string-поля — [""],
    незаполненные number/boolean/enum(single) — [].
    """
    features = []
    for code, meta in feature_meta.items():
        raw = feature_values.get(code)
        if meta["multiValued"]:
            values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        elif raw is None:
            values = [""] if meta["type"] == "string" else []
        else:
            values = [raw]
        features.append({"attributeCode": code, "values": values})
    return features


def generate_name(sku: str, merchant_id: str, category_code: str, brand: dict, features_payload: list[dict], store_id: Optional[str] = None) -> str:
    payload = {
        "brand": brand,
        "masterCategoryCode": category_code,
        "productCode": sku,
        "features": features_payload,
    }
    resp = _mc_post(f"{BASE}/content/pending/mc/product/name/generate", json_payload=payload, store_id=store_id, params={"merchantCode": merchant_id})
    resp.raise_for_status()
    return resp.json()


def build_create_payload(
    sku: str,
    merchant_id: str,
    category_code: str,
    category_name: str,
    brand: dict,
    features_payload: list[dict],
    name: str,
    description: str,
    image: dict,
    classifications: list[dict],
) -> dict:
    """classifications — как есть из fetch_category_schema()["classifications"],
    с values[] каждого feature подменёнными на реальные (Kaspi ожидает
    ровно эту форму обратно, не урезанный список — см. §4.2 доки)."""
    values_by_code = {f["attributeCode"]: f["values"] for f in features_payload}
    injected = []
    for block in classifications:
        new_features = [
            {**feat, "values": values_by_code.get(feat.get("attributeCode"), feat.get("values", []))}
            for feat in block.get("features", [])
        ]
        injected.append({**block, "features": new_features})

    img_id = image["id"]
    img_base = f"{BASE}/image/processor/merchant/img/cnt/mct/i/{img_id}"

    return {
        "classifications": injected,
        "requestInfoList": [],
        "code": sku,
        "merchantCode": merchant_id,
        "name": name,
        "displayName": name,
        "description": description or "",
        "descriptionCreation": "PARTNER_WRITTEN",
        "category": {
            "code": category_code,
            "name": category_name,
            "restricted": False,
            "closed": False,
            "blocked": False,
            "image": "",
            "hasContentChild": False,
        },
        "brand": brand,
        "images": [{
            "large": f"{img_base}?format=gallery_large",
            "medium": f"{img_base}?format=gallery_medium",
            "small": f"{img_base}?format=thumbnail",
            "width": 0,
            "height": 0,
            "location": img_id,
            "bucketName": "temp-merchant-product-images",
            "generatedByAI": False,
            "visualType": "",
        }],
        "videos": [],
        "teasers": [],
        "shopLink": "",
        "videoId": "",
        "unitAmount": 0,
        "moderationDeadline": "",
        "modifications": [],
        "createdFromMaster": False,
        "requestCodes": [],
    }


def create_product(payload: dict, store_id: Optional[str] = None) -> dict:
    """Возвращает {"code","name","status","errorReason"} при успехе.
    Бросает DuplicateSkuError при дубликате артикула, RuntimeError на любую
    другую ошибку валидации — оба формата ответа задокументированы в
    07_ДОБАВЛЕНИЕ_ТОВАРА.md §4.2."""
    resp = _mc_post(f"{BASE}/content/pending/mc/product/create", json_payload=payload, store_id=store_id, params={"isMobileApp": "false"})

    if resp.status_code == 400:
        body = resp.json()
        errors = body.get("errors") or []
        first = errors[0] if errors else {}
        code = first.get("errorCode", "")
        message = first.get("message", resp.text[:300])
        if code == "error.validation.master.product.exist":
            raise DuplicateSkuError(payload.get("code", ""), message)
        raise RuntimeError(f"Kaspi отклонил создание товара ({code or 'unknown'}): {message}")

    resp.raise_for_status()
    results = resp.json()
    result = results[0] if isinstance(results, list) and results else {}
    if result.get("status") != "SUCCESS":
        raise RuntimeError(f"Kaspi вернул неуспешный статус создания: {result}")
    return result


def finalize_pricefeed(sku: str, model_name: str, brand_code: str, merchant_id: str, store_id: Optional[str] = None) -> dict:
    """ВАЖНО: несмотря на название ("process"), этот вызов НЕ передаёт
    цену/остаток — подтверждено дважды живьём с полным нефильтрованным
    сетевым логом (см. §4.3 07_ДОБАВЛЕНИЕ_ТОВАРА.md). Цену/остаток нужно
    выставлять ОТДЕЛЬНО, уже существующим пайплайном
    backend/dumper/executor.py::_update_kaspi_price_sync, но только ПОСЛЕ
    того как модерация одобрит товар и ему присвоится настоящий masterSku
    (которого у товара на модерации ещё нет — этот пайплайн требует его на
    входе). До одобрения товар закономерно висит без цены — это не баг."""
    payload = {"merchantUid": merchant_id, "sku": sku, "model": model_name, "brand": brand_code}
    resp = _mc_post(f"{BASE}/pricefeed/upload/merchant/process", json_payload=payload, store_id=store_id)
    resp.raise_for_status()
    return resp.json()


def submit_new_product(
    sku: str,
    category_code: str,
    category_name: str,
    brand_name: Optional[str],
    photo_path: str,
    features_payload: list[dict],
    description: str,
    classifications: list[dict],
    token: str,
    store_id: Optional[str] = None,
) -> dict:
    """Полный флоу §4 07_ДОБАВЛЕНИЕ_ТОВАРА.md: резолв бренда → аплоад фото →
    генерация названия → create → pricefeed/process (завершение флоу, БЕЗ
    цены/остатка — см. finalize_pricefeed). Не решает join-vs-new-card — это
    уровень выше (review UI показывает search_existing_products() кандидатов
    до вызова этой функции)."""
    merchant_id = resolve_merchant_id(token)
    brand = resolve_brand(brand_name, category_code, merchant_id, store_id)
    image = upload_photo(photo_path, store_id)
    name = generate_name(sku, merchant_id, category_code, brand, features_payload, store_id)

    payload = build_create_payload(
        sku=sku, merchant_id=merchant_id, category_code=category_code, category_name=category_name,
        brand=brand, features_payload=features_payload, name=name, description=description,
        image=image, classifications=classifications,
    )
    result = create_product(payload, store_id)
    finalize_pricefeed(sku, name, brand["code"], merchant_id, store_id)
    return {"sku": sku, "name": name, "kaspi_result": result}
