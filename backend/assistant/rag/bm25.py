"""Чистый Python BM25; адаптирован из barkhan-agent/rag/bm25.py."""

from collections import Counter
import math
import re

from backend.assistant.rag.base import Document, Hit, Retriever

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def tokenize(value: str) -> list[str]:
    return [match.group().lower() for match in TOKEN_RE.finditer(value)]


class BM25Retriever(Retriever):
    def __init__(self, documents: list[Document], k1: float = 1.5, b: float = 0.75):
        self.documents = list(documents)
        self.k1 = k1
        self.b = b
        self.counts = [Counter(tokenize(item.text)) for item in self.documents]
        self.lengths = [sum(counts.values()) for counts in self.counts]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        self.df = Counter(token for counts in self.counts for token in counts)

    def search(self, query: str, k: int = 5, doc_types: list[str] | None = None,
               sku: str | None = None) -> list[Hit]:
        if k <= 0:
            return []
        words = set(tokenize(query))
        if not words:
            return []
        results = []
        for index, document in enumerate(self.documents):
            if doc_types is not None and document.doc_type not in doc_types:
                continue
            if sku is not None and document.sku != sku:
                continue
            score = 0.0
            for word in words:
                frequency = self.counts[index][word]
                if not frequency:
                    continue
                idf = math.log(1 + (len(self.documents) - self.df[word] + 0.5) / (self.df[word] + 0.5))
                length_factor = self.k1 * (1 - self.b + self.b * self.lengths[index] / (self.avg_length or 1))
                score += idf * frequency * (self.k1 + 1) / (frequency + length_factor)
            if score > 0:
                results.append(Hit(document=document, score=score))
        return sorted(results, key=lambda item: (-item.score, item.document.doc_id))[:k]
