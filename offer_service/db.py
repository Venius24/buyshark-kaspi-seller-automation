"""SQLite-хранилище для персональных КП (offer.buyshark.kz).

Паттерн скопирован с lead_service/db.py — тот же WAL/busy_timeout, тот же
принцип "простой SQLite-файл, не Postgres" для маленького вспомогательного
сервиса, который не участвует в основном биллинге.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "offers.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    slug TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    shop_url TEXT,
    product_count INTEGER,
    orders_label TEXT,
    years_label TEXT,
    categories TEXT NOT NULL DEFAULT '[]',
    discount_pct INTEGER NOT NULL DEFAULT 30,
    tier_override TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours'))
);
"""


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


def upsert_offer(row: dict):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO offers (slug, client_name, shop_url, product_count, orders_label,
                                 years_label, categories, discount_pct, tier_override, updated_at)
            VALUES (:slug, :client_name, :shop_url, :product_count, :orders_label,
                    :years_label, :categories, :discount_pct, :tier_override, datetime('now', '+5 hours'))
            ON CONFLICT(slug) DO UPDATE SET
                client_name=excluded.client_name,
                shop_url=excluded.shop_url,
                product_count=excluded.product_count,
                orders_label=excluded.orders_label,
                years_label=excluded.years_label,
                categories=excluded.categories,
                discount_pct=excluded.discount_pct,
                tier_override=excluded.tier_override,
                updated_at=excluded.updated_at
            """,
            {
                "slug": row["slug"],
                "client_name": row["client_name"],
                "shop_url": row.get("shop_url"),
                "product_count": row.get("product_count"),
                "orders_label": row.get("orders_label"),
                "years_label": row.get("years_label"),
                "categories": json.dumps(row.get("categories") or [], ensure_ascii=False),
                "discount_pct": row.get("discount_pct", 30),
                "tier_override": row.get("tier_override"),
            },
        )


def get_offer(slug: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM offers WHERE slug = :slug", {"slug": slug}
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["categories"] = json.loads(data["categories"] or "[]")
        return data


def list_offers() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slug, client_name, product_count, created_at, updated_at FROM offers ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_offer(slug: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM offers WHERE slug = :slug", {"slug": slug})
