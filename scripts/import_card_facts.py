"""Import public card facts. Dry-run unless --apply is explicitly supplied."""
import argparse
import asyncio
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Store, StoreCardFactDB
from scripts.ai_baseline import _async_url


def scalar(value, limit):
    if value is None:
        return None
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("Expected scalar public field")
    return str(value).strip()[:limit] or None


def derive_curtain_dimensions(features: list[list[str]]) -> list[list[str]]:
    """Derive shower-curtain dimensions without mutating the input pairs."""
    names = {name.strip().casefold() for name, _ in features}
    if names & {"высота шторы, см", "ширина шторы, см"}:
        return features
    number = r"[0-9]{1,4}(?:[.,][0-9]{1,3})?"
    for name, value in features:
        if name.strip().casefold() != "размер шторы, см":
            continue
        match = re.fullmatch(rf"\s*({number})\s*[xх×*]\s*({number})\s*", value, re.IGNORECASE)
        if match is None:
            continue
        parts = match.groups()
        if any(sum(c.isdigit() for c in part) > 4 for part in parts):
            continue
        height, width = sorted(Decimal(part.replace(",", ".")) for part in parts)
        if height <= 0:
            continue

        def formatted(value):
            return format(value, "f").rstrip("0").rstrip(".") if value % 1 else str(int(value))

        return features + [["Высота шторы, см", formatted(height)],
                           ["Ширина шторы, см", formatted(width)]]
    return features


def read_records(products_path, rows_path, source="baitaq-cards", reviewed=False):
    products = json.loads(Path(products_path).read_text(encoding="utf-8-sig"))
    rows = json.loads(Path(rows_path).read_text(encoding="utf-8-sig"))
    if not isinstance(products, list) or not isinstance(rows, dict):
        raise ValueError("Expected products list and rows object")
    if not source.strip() or len(source) > 50:
        raise ValueError("source must contain 1..50 characters")
    by_sku = {}
    skipped = 0
    for product in products:
        if not isinstance(product, dict):
            raise ValueError("Expected product object")
        sku = scalar(product.get("sku"), 101)
        if not sku:
            skipped += 1
            continue
        if len(sku) > 100 or sku in by_sku:
            raise ValueError("Invalid or duplicate product SKU")
        by_sku[sku] = product
    normalized_rows = {}
    for raw_sku, features in rows.items():
        sku = raw_sku.strip()
        if not sku:
            skipped += 1
            continue
        if len(sku) > 100 or sku in normalized_rows or not isinstance(features, dict):
            raise ValueError("Invalid or duplicate rows SKU or feature object")
        normalized_rows[sku] = features
    records = []
    derived_curtain_dimensions = 0
    # Technical/private input columns never become public feature pairs.
    excluded = {"price", "min_price", "donors", "qty", "group", "sheet", "photo_order"}
    for sku in sorted(by_sku.keys() | normalized_rows.keys()):
        product = by_sku.get(sku, {})
        row = normalized_rows.get(sku, {})
        features = []
        for key, value in row.items():
            if key.strip().lower() in excluded:
                continue
            if value is None or value == "" or value == [] or value == {}:
                continue
            name = scalar(key, 100)
            value = scalar(value, 600)
            if name and value:
                features.append([name, value])
        derived_features = derive_curtain_dimensions(features)
        derived_curtain_dimensions += len(derived_features) > len(features)
        features = derived_features
        records.append({"sku": sku, "name": scalar(product.get("name"), 300),
                        "category": scalar(product.get("category"), 200),
                        "brand": scalar(product.get("brand") or row.get("Бренд"), 100),
                        "description": scalar(product.get("description"), 2000),
                        "features": json.dumps(features, ensure_ascii=False),
                        "source": source, "reviewed": reviewed})
    return records, {"skipped": skipped,
                     "derived_curtain_dimensions": derived_curtain_dimensions,
                     "products_without_rows": sorted(by_sku.keys() - normalized_rows.keys()),
                     "rows_without_products": sorted(normalized_rows.keys() - by_sku.keys())}


async def upsert_records(database_url, store_id, records):
    url = _async_url(database_url)
    if url.startswith("sqlite://"):
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            # Lock the store to serialize concurrent imports into the same store.
            store = await session.scalar(select(Store.id).where(Store.id == store_id).with_for_update())
            if store is None:
                raise ValueError("store_id does not exist")
            for record in records:
                existing = await session.scalar(select(StoreCardFactDB).where(
                    StoreCardFactDB.store_id == store_id, StoreCardFactDB.sku == record["sku"]))
                if existing is None:
                    session.add(StoreCardFactDB(store_id=store_id, **record))
                else:
                    for key, value in record.items():
                        setattr(existing, key, value)
    finally:
        await engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--products", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--source", default="baitaq-cards")
    parser.add_argument("--reviewed", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        records, warnings = read_records(args.products, args.rows, args.source, args.reviewed)
        derived_curtain_dimensions = warnings.pop("derived_curtain_dimensions")
        if args.apply:
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                parser.error("DATABASE_URL must be set for --apply")
            asyncio.run(upsert_records(database_url, args.store_id, records))
    except (ValueError, OSError, SQLAlchemyError):
        parser.error("Invalid input or missing store; no transaction committed")
    print(json.dumps({"applied": len(records) if args.apply else 0,
                      "records": len(records), "skus": [r["sku"] for r in records],
                      "reviewed": sum(r["reviewed"] for r in records),
                      "derived_curtain_dimensions": derived_curtain_dimensions,
                      "warnings": warnings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
