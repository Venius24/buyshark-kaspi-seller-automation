"""Seller "quality metrics" (chat response rate/time, etc.) — WITHHELD in the public version.

The private version reads them from the seller cabinet's internal GraphQL API.
"""
from typing import Optional

from backend.services._withheld import WithheldError


def fetch_quality_metrics(merchant_uid: str, store_id: Optional[str] = None) -> dict:
    raise WithheldError("quality metrics")
