"""
Поля идентичности оффера (`<model>`, `<brand>`) — из принятого Kaspi файла.

Зачем не из BFF, откуда берётся всё остальное. Бренд в прайс-листе — это СЛАГ,
а не название: Kaspi хранит `volat` для «Волат», `steklopribor` для
«Стеклоприбор», `top-race` для «Top Race». BFF отдаёт display-имя, и вывести из
него слаг нельзя — ни регистром, ни транслитерацией. Замерено на живом каталоге
Бархана 2026-08-24: после отсечения наших подстановок оставалось 770 расхождений
(738 только по регистру, 32 принципиально другие строки). Отправить такой файл =
переписать бренды у 770 карточек.

Решение: единственный источник, который заведомо совпадает с тем, что у Kaspi в
голове, — это файл, который Kaspi сама у нас приняла и сама же отдаёт обратно
(`GET /pricefeed/upload/merchant/files/{id}/download`, бесплатный GET). Оттуда
поля идентичности берутся ДОСЛОВНО, и файл физически не может их изменить: он
меняет только цену и остаток, ради которых он и нужен.

Для SKU, которых в принятом файле нет (новые товары), поля остаются пустыми —
`<model/>`/`<brand/>` Kaspi принимает, их в эталоне 171 и 351 соответственно.
"""
from typing import Dict, Optional, Tuple
import logging
import re

from backend.dumper.xml_feed import unescape_attr, xml_attr_value

logger = logging.getLogger(__name__)

BASE = "https://mc.shop.kaspi.kz/pricefeed/upload"
LAST_FILE_URL = f"{BASE}/merchant/files/last"
DOWNLOAD_URL = f"{BASE}/merchant/files/{{file_id}}/download"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://kaspi.kz",
    "Referer": "https://kaspi.kz/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Auth-Version": "3",
}

_OFFER_RE = re.compile(r'<offer sku="([^"]*)"[^>]*>(.*?)</offer>', re.S)
_MODEL_RE = re.compile(r"<model>(.*?)</model>", re.S)
_BRAND_RE = re.compile(r"<brand>(.*?)</brand>", re.S)


def _unescape(text: str) -> str:
    """Значение атрибута из сырого текста файла — включая `&#9;`.

    Свой вариант этой функции жил здесь, пока табуляция ехала литералом и
    разбирать было нечего. Теперь артикул с табуляцией записан ссылкой, и
    читатель обязан знать про неё столько же, сколько писатель.
    """
    return unescape_attr(text)


def parse_identity(xml_bytes: bytes) -> Dict[str, dict]:
    """
    Принятый файл -> {нормализованный sku: {"name": ..., "brand": ...}}.

    Ключ нормализуется так же, как это сделает XML-парсер: у трёх артикулов
    Бархана в SKU живёт табуляция/пробел, и без нормализации они не совпадут с
    тем, что придёт из BFF.
    """
    text = xml_bytes.decode("utf-8", errors="replace")
    out: Dict[str, dict] = {}
    for m in _OFFER_RE.finditer(text):
        body = m.group(2)
        model = _MODEL_RE.search(body)
        brand = _BRAND_RE.search(body)
        out[xml_attr_value(_unescape(m.group(1)))] = {
            "name": _unescape(model.group(1)) if model else None,
            "brand": _unescape(brand.group(1)) if brand else None,
        }
    return out


def fetch_accepted_identity(store_id, merchant_uid: str) -> Tuple[Optional[str], Dict[str, dict]]:
    """
    Скачать последний принятый файл и вынуть из него поля идентичности.

    Возвращает (id файла, карта). Ничего не пишет, квоту не тратит. Если файла
    нет или он не скачался — (None, {}): это не повод падать, но повод не
    отправлять (решает `feed_uploader`).
    """
    from backend.state_helpers import _mc_api_request

    try:
        resp = _mc_api_request(
            "GET", LAST_FILE_URL, store_id=store_id,
            headers=_HEADERS, params={"m": merchant_uid}, timeout=20,
        )
        if resp.status_code != 200:
            logger.warning("[FeedIdentity] files/last -> %s", resp.status_code)
            return None, {}
        card = resp.json() or {}
    except Exception as err:
        logger.warning("[FeedIdentity] files/last не прочитан: %r", err)
        return None, {}

    file_id = card.get("id")
    if not file_id:
        return None, {}
    if card.get("status") != "FLUSHED":
        # Незавершённый файл не является утверждённым состоянием каталога.
        logger.warning("[FeedIdentity] последний файл в статусе %s — не берём", card.get("status"))
        return None, {}

    try:
        resp = _mc_api_request(
            "GET", DOWNLOAD_URL.format(file_id=file_id), store_id=store_id,
            headers=_HEADERS, params={"m": merchant_uid}, timeout=120,
        )
        if resp.status_code != 200:
            logger.warning("[FeedIdentity] download -> %s", resp.status_code)
            return None, {}
        identity = parse_identity(resp.content)
    except Exception as err:
        logger.warning("[FeedIdentity] файл не разобран: %r", err)
        return None, {}

    logger.info("[FeedIdentity] Из файла %s взято %s офферов", file_id, len(identity))
    return file_id, identity


def apply_identity(offers, identity: Dict[str, dict]):
    """
    Наложить поля идентичности на офферы. Возвращает (новые офферы, статистика).

    Оффер, которого в принятом файле нет, получает пустые поля: выдумывать
    название и бренд нельзя — это ровно та ошибка, из-за которой в каталог
    Бархана уехали 65 заглушек «Товар <sku>».

    Статистика различает ДВА разных «пусто», и это не педантизм:

    · `unknown` — SKU в источнике отсутствует. Лечится сменой источника.
    · `empty`   — SKU есть, но `<model/>` в нём пуст. Не лечится ничем и часто
      является правдой: в выгрузке Бархана 28.08 таких 115 при нормальных
      названиях карточек в кабинете.

    Считать их вместе нельзя, потому что счётчик `unknown` **сам себя гасит**.
    Оффер с пустым `<model/>` уезжает в наш файл; файл становится принятым;
    на следующем цикле SKU в источнике уже есть — `unknown` его больше не
    видит, а пустота осталась навсегда. Предупреждение, которое замолкает,
    не починив причину, хуже отсутствующего.
    """
    applied = 0
    unknown = []
    empty = []
    result = []
    for offer in offers:
        clean = dict(offer)
        key = xml_attr_value(offer.get("merchantSku"))
        known = identity.get(key)
        if known is None:
            unknown.append(key)
            clean["name"] = None
            clean["brand"] = None
        else:
            clean["name"] = known.get("name")
            clean["brand"] = known.get("brand")
            applied += 1
            if not clean["name"]:
                empty.append(key)
        result.append(clean)
    return result, {"applied": applied, "unknown": unknown, "empty": empty}
