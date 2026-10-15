"""Контракт поиска; адаптирован из barkhan-agent/rag/base.py."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Document:
    doc_id: str
    doc_type: str
    sku: str | None
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Hit:
    document: Document
    score: float

    @property
    def source(self) -> dict:
        return {"doc_id": self.document.doc_id, "doc_type": self.document.doc_type,
                "sku": self.document.sku}


class Retriever(ABC):
    @abstractmethod
    def search(self, query: str, k: int = 5, doc_types: list[str] | None = None,
               sku: str | None = None) -> list[Hit]: ...
