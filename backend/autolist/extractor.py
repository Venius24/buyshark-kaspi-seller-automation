"""
AI-распознавание товара по фото+названию для фичи «Автодобавление товара».

Обёртка над OpenAI GPT-4o-mini (vision + structured outputs), через сырой
httpx-вызов к REST API — как chatbot/ai.py делает для DeepSeek, без SDK.

Модуль ничего не знает про HTTP-детали Kaspi (это backend/autolist/category_mapper.py):
- guess_category() отвечает только "что это за товар", чтобы category_mapper
  мог найти подходящую категорию Kaspi через её собственный текстовый поиск.
- fill_attributes() получает уже РЕАЛЬНУЮ схему характеристик конкретной
  категории Kaspi (ответ content/pending/mc/category/<code>/info, см.
  ПСЕВДО_ДОКУМЕНТАЦИЯ_КАСПИ/07_ДОБАВЛЕНИЕ_ТОВАРА.md) и заполняет её —
  модель ограничена ТОЛЬКО значениями из defaultValues[], чтобы никогда не
  придумать enum-код, которого Kaspi не знает и отклонит на модерации.
"""

import base64
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
API_URL = "https://api.openai.com/v1/chat/completions"


def _load_env():
    """См. backend/chatbot/config.py::load_env — тот же минимальный парсер
    .env, чтобы модуль работал и вне процесса FastAPI (main.py грузит .env
    сам, но скрипты/тесты, импортирующие только этот модуль, — нет)."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_path = os.path.join(base_dir, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _api_key() -> str:
    _load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY не задан в .env")
    return key


def _image_data_url(photo_path: str) -> str:
    ext = os.path.splitext(photo_path)[1].lstrip(".").lower() or "jpeg"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    with open(photo_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def _call_openai(system_prompt: str, user_text: str, photo_path: str, schema_name: str, json_schema: dict) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": _image_data_url(photo_path)}},
                ],
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        },
        "max_tokens": 2000,
    }
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(API_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI API вернул {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


CATEGORY_GUESS_SCHEMA = {
    "type": "object",
    "properties": {
        "category_query": {
            "type": "string",
            "description": (
                "Короткий поисковый запрос на русском для поиска категории в каталоге "
                "Kaspi.kz (1-3 слова, тип товара, как его назвал бы покупатель, "
                "например 'термосумка', 'беспроводные наушники', 'настольная лампа')."
            ),
        },
        "brand_guess": {
            "type": ["string", "null"],
            "description": "Название бренда, если оно читается на фото (логотип) или явно указано в названии от поставщика. Иначе null — не придумывай бренд.",
        },
        "color_guess": {
            "type": "string",
            "description": "Основной цвет товара по фото, на русском, одно слово в им. падеже (например 'синий', 'черный', 'белый').",
        },
    },
    "required": ["category_query", "brand_guess", "color_guess"],
    "additionalProperties": False,
}


async def guess_category(photo_path: str, title: str) -> dict:
    """
    Первый проход: {"category_query": str, "brand_guess": str|None, "color_guess": str}.
    category_mapper.py использует category_query для поиска реальной категории
    через GET .../product/classification/mc/category/search.
    """
    system = (
        "Ты помогаешь категоризировать товары для маркетплейса Kaspi.kz. Смотришь на "
        "фото и на название товара от поставщика (часто неполное, с опечатками или "
        "составленное для внутреннего каталога поставщика, не для покупателя) и "
        "определяешь, к какому типу товара он относится. Название поставщика может "
        "ошибаться в цвете (скопировано с другого товара) — для color_guess доверяй "
        "тому, что реально видно на фото, а не тексту названия."
    )
    user_text = f"Название от поставщика: {title}"
    return await _call_openai(system, user_text, photo_path, "category_guess", CATEGORY_GUESS_SCHEMA)


def _flatten_category_features(category_schema: dict) -> dict:
    """
    category_schema — ответ content/pending/mc/category/<code>/info (или тот же
    формат, эхом возвращаемый в classifications[] при product/create).
    Возвращает {attributeCode: feature_dict} по всем непустым блокам.
    """
    features = {}
    for block in category_schema.get("classifications", []):
        for feat in block.get("features", []):
            code = feat.get("attributeCode")
            if code:
                features[code] = feat
    return features


def _feature_json_schema_property(feat: dict) -> dict:
    name = feat.get("name", feat["attributeCode"])
    mandatory = bool(feat.get("mandatory"))
    prefix = "[ОБЯЗАТЕЛЬНОЕ ПОЛЕ] " if mandatory else "[необязательное] "
    attr_type = (feat.get("attributeType") or {}).get("code", "string")
    multi = bool((feat.get("attributeType") or {}).get("multiValued"))
    values = [v["code"] for v in feat.get("defaultValues", []) if v.get("code")]

    if attr_type == "enum" and values:
        if multi:
            return {
                "type": "array",
                "items": {"type": "string", "enum": values},
                "description": (
                    f"{prefix}{name} — внимательно осмотри ВСЁ фото (корпус, ручки, ремни, карманы, "
                    f"крепления, надписи, кнопки и т.п.) и выбери ВСЕ подходящие значения СТРОГО из "
                    f"списка допустимых, не придумывай свои. Не возвращай пустой список для "
                    f"обязательного поля, если на фото видна хотя бы одна подходящая деталь."
                ),
            }
        return {
            "type": ["string", "null"],
            "enum": values + [None],
            "description": f"{prefix}{name} — выбери ОДНО значение строго из списка, либо null если по фото/названию не определить.",
        }

    if attr_type == "number":
        prop = {"type": ["number", "null"], "description": f"{prefix}{name}"}
        if feat.get("minValue") is not None:
            prop["minimum"] = feat["minValue"]
        if feat.get("maxValue") is not None:
            prop["maximum"] = feat["maxValue"]
        unit = (feat.get("unit") or {}).get("name")
        if unit:
            prop["description"] += f" (в {unit})"
        return prop

    if attr_type == "boolean":
        return {"type": ["boolean", "null"], "description": f"{prefix}{name}"}

    # string, или enum без готового списка значений (свободный текст)
    return {"type": ["string", "null"], "description": f"{prefix}{name}"}


def build_attributes_json_schema(category_schema: dict) -> tuple[dict, dict]:
    """Возвращает (json_schema для response_format, feature_meta по attributeCode)."""
    flat = _flatten_category_features(category_schema)
    properties = {}
    feature_meta = {}
    for code, feat in flat.items():
        properties[code] = _feature_json_schema_property(feat)
        feature_meta[code] = {
            "name": feat.get("name", code),
            "mandatory": bool(feat.get("mandatory")),
            "multiValued": bool((feat.get("attributeType") or {}).get("multiValued")),
            "type": (feat.get("attributeType") or {}).get("code", "string"),
        }

    properties["description"] = {
        "type": "string",
        "description": "Продающее описание товара для карточки Kaspi на русском, 2-4 предложения, без эмодзи и CAPS LOCK, до 500 символов.",
    }

    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }
    return schema, feature_meta


async def fill_attributes(photo_path: str, title: str, category_schema: dict, brand_hint: str = "") -> dict:
    """
    Второй проход: заполняет характеристики конкретной категории Kaspi по фото+названию.

    Возвращает:
        {
          "features": {attributeCode: value_or_list_or_None, ...},  # сырые значения модели
          "feature_meta": {attributeCode: {...}},                    # mandatory/type/multiValued — для validator.py
          "description": str,
          "missing_mandatory": [attributeCode, ...],                 # обязательные поля, которые модель не смогла заполнить
        }
    """
    json_schema, feature_meta = build_attributes_json_schema(category_schema)

    system = (
        "Ты заполняешь карточку товара для маркетплейса Kaspi.kz по фото и названию от "
        "поставщика. У каждого поля есть точный список допустимых значений (enum) — "
        "используй ТОЛЬКО их, буквально совпадающие строки, никогда не придумывай свои.\n\n"
        "ПРИОРИТЕТ ФОТО НАД ТЕКСТОМ: название от поставщика — это внутренний артикул для "
        "склада, часто скопированный с другого товара, устаревший или просто неточный "
        "(например цвет в названии может не совпадать с реальным цветом на фото — такое "
        "бывает часто). Для всего, что можно определить визуально (цвет, материал внешне, "
        "форма, наличие ручек/ремней/карманов и т.п.) — ВСЕГДА доверяй тому, что реально "
        "видно на фото, а не тексту названия, если они расходятся. Текст названия используй "
        "только для того, что нельзя увидеть на фото (объём в литрах, точные размеры в "
        "сантиметрах, материал состава и т.п., если это явно написано цифрами/словами).\n\n"
        "ОБЯЗАТЕЛЬНЫЕ ПОЛЯ (помечены [ОБЯЗАТЕЛЬНОЕ ПОЛЕ] в описании): рассмотри фото "
        "максимально внимательно перед тем как оставить такое поле пустым/null. Для "
        "обязательных полей с множественным выбором (список значений) — осмотри корпус, "
        "ручки, ремни, застёжки, карманы, крепления и другие видимые элементы товара один "
        "за другим и включи в список ТОЛЬКО те значения, для которых ты реально видишь "
        "соответствующую деталь на фото.\n\n"
        "ГЛАВНОЕ ПРАВИЛО ТОЧНОСТИ: неверное значение хуже, чем пустое поле — если ты не "
        "уверен на 100%, что видишь конкретную деталь (например значение подразумевает "
        "экран/индикатор/регулятор, а ты не видишь никакого экрана или кнопок на фото) — "
        "НЕ включай его, даже если поле обязательное и список окажется пустым или коротким. "
        "Не додумывай функциональность, которую нельзя увидеть на фото. Для необязательных "
        "полей и для остальных случаев, если значение действительно невозможно достоверно "
        "определить — верни null, не гадай наугад."
    )
    user_text = f"Название от поставщика: {title}"
    if brand_hint:
        user_text += f"\nПредполагаемый бренд: {brand_hint}"

    parsed = await _call_openai(system, user_text, photo_path, "fill_attributes", json_schema)

    description = parsed.pop("description", "")
    missing_mandatory = [
        code for code, meta in feature_meta.items()
        if meta["mandatory"] and not parsed.get(code)
    ]

    return {
        "features": parsed,
        "feature_meta": feature_meta,
        "description": description,
        "missing_mandatory": missing_mandatory,
    }
