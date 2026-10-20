"""Local, public-only catalog retrieval for buyer replies (no model/tools)."""

import logging
import math
import os
import re

from backend.assistant.rag.base import Document
from backend.assistant.rag.bm25 import BM25Retriever, tokenize
from backend.chatbot.order_context import _card_text, _reviewed_cards, _stock_value

logger = logging.getLogger(__name__)

_TRIGGER = re.compile(
    r"\b(?:какие\s+(?:у\s+вас\s+)?(?:еще|есть)|что\s+еще\s+есть|"
    r"есть\s+(?:ли|такие)|а\s+есть|друг(?:ие|ой|ое|ая|ую|их|ого|ом)|"
    r"похож\w*|аналог\w*|подобн\w*|размер\w*|высот\w*|ширин\w*|"
    r"длин(?:а|ы|у|е|ой|нее|ный|ная|ное|ные)|диаметр\w*|цвет\w*|расцвет\w*)\b"
)
_STOP_WORDS = frozenset("какие какой какая какое у вас еще есть что ли а такие такой такую "
                        "другие другой другое другая похожий похожие аналог аналоги подобный подобные "
                        "размер размеры высота высоты ширина ширины длина длины диаметр цвет цвета "
                        "расцветка в на по для из и или с со не мне пожалуйста подскажите можно "
                        "товар товары наличии см мм м".split())
_PRIVATE_FEATURE = re.compile(r"цен|price|sku|артикул|остат|stock|демп|dump|merchant|идентификатор", re.I)
_MIN_SCORE = 0.5


def catalog_search_enabled() -> bool:
    return os.environ.get("CHATBOT_CATALOG_SEARCH_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def is_catalog_question(text: str) -> bool:
    return isinstance(text, str) and bool(_TRIGGER.search(text.lower().replace("ё", "е")))


def _search_text(text: str) -> str:
    # Small lexical normalization, not a semantic search: шторы/штора,
    # душевые/душевая. Keep numbers and short words unchanged.
    words = []
    for word in tokenize(text.lower().replace("ё", "е")):
        if word in _STOP_WORDS:
            continue
        if len(word) > 4 and re.fullmatch(r"[а-я]+", word):
            word = re.sub(r"(?:иями|ами|ого|ему|ому|ыми|ими|ая|яя|ое|ее|ые|ие|ой|ый|ий|ов|ев|ам|ям|ах|ях|ы|и|а|я|у|ю|е|о)$", "", word)
        words.append(word)
    return " ".join(words)


def find_catalog_matches(store_id, query, k=3) -> list[dict]:
    """Rank public product+reviewed-card documents from store-scoped loaders.

    Deliberately avoid the general RAG index: it loads private war/log data
    and catches/logs source exception messages. No event loop or shared cache
    is needed here; the worker runs this synchronous path in a thread.
    """
    try:
        if not store_id or not isinstance(query, str) or k <= 0:
            return []
        search_query = _search_text(query)
        if not search_query:
            return []
        from backend.state_helpers import _apply_overrides, _load_products

        # _apply_overrides supplies a synthetic stock=10 when stock is absent.
        # Never use that default as evidence that a buyer can buy this item.
        products = [p for p in (_load_products(store_id) or [])
                    if p.get("store_id", store_id) == store_id and _stock_value(p) is not None]
        products = _apply_overrides(products, store_id)
        cards = _reviewed_cards(store_id)
        public = []
        documents = []
        seen = set()
        for product in products:
            if product.get("status") != "Активен" or (_stock_value(product) or 0) <= 0:
                continue
            sku = str(product.get("merchantSku") or "").strip()
            if not sku or sku in seen:
                continue
            seen.add(sku)
            name = _card_text(product.get("name"), 100)
            if not name:
                continue
            card = cards.get(sku) or {}
            features = [[name, value] for name, value in card.get("features", [])
                        if not _PRIVATE_FEATURE.search(name)]
            url = product.get("url")
            url = url if (isinstance(url, str) and url.startswith("https://kaspi.kz/")
                          and url == _card_text(url, 500)) else None
            index = str(len(public))
            public.append({"name": name, "url": url, "features": features})
            documents.append(Document(index + ":product", "product", index, _search_text(name)))
            if features:
                text = "; ".join(f"{n}: {v}" for n, v in features)[:600]
                documents.append(Document(index + ":card", "card", index, _search_text(name + " " + text)))
        retriever = BM25Retriever(documents)
        hits = retriever.search(search_query, k=len(documents))
        # Normalize by query IDF, including absent terms. An exact category
        # match stays useful even if the whole store sells that category;
        # a lone color hit in a question about an unknown product stays weak.
        query_weight = sum(math.log(1 + (len(documents) - retriever.df[word] + 0.5)
                                    / (retriever.df[word] + 0.5))
                           for word in set(tokenize(search_query)))
        threshold = _MIN_SCORE * query_weight
        if not hits or hits[0].score < threshold:
            return []
        matches = []
        selected = set()
        for hit in hits:
            if hit.score < max(threshold, hits[0].score * 0.5):
                break
            index = int(hit.document.sku)
            if index not in selected:
                matches.append(public[index])
                selected.add(index)
                if len(matches) >= min(3, k):
                    break
        return matches
    except Exception:
        # Queries and exception messages may contain private data.
        logger.warning("[Chatbot] Поиск товаров недоступен", exc_info=False)
        return []
