"""Add store-scoped processed_orders, print_history, dumper_logs, analytics_snapshots

Closes the remaining operational-state gaps from the multi-tenancy audit:

- states/processed_orders.json / processed_packaging_orders.json were single
  shared files — an order matched in one store's radar/packaging pass could
  block a DIFFERENT store's order with the same order_id from ever being
  processed. processed_orders now carries (store_id, kind, order_id) so the
  two kinds ('radar', 'packaging') and every store stay independent.
- %TEMP%/print_batcher_storage/printed_history.json was a single file
  outside the repo entirely — print_history is now (store_id, date,
  order_key), one row per printed order per store per day.
- states/dumper_logs.json (Overview widget "recent events") and
  states/analytics.json (Overview widget summary counts) were single shared
  files — any user's dashboard could see another store's dumping events.

Backfill: mirrors 002/006/007/008's rule — only when exactly one Store
exists do we know unambiguously which store the existing shared files
belong to. processed_packaging_orders.json/print history in %TEMP% are
intentionally NOT backfilled here (the former path was never read
alongside a determinable "kind" without guessing which entries are radar
vs packaging — since production has always run radar and packaging as
separate files already, both get backfilled straightforwardly by file).

Revision ID: 009_operational_state_per_store
Revises: 008_chatbot_per_store
Create Date: 2026-08-01 00:00:00.000000

"""
import json
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '009_operational_state_per_store'
down_revision: Union[str, None] = '008_chatbot_per_store'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "processed_orders",
        sa.Column("store_id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=20), primary_key=True),
        sa.Column("order_id", sa.String(length=100), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_processed_orders_store_id", "processed_orders", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "print_history",
        sa.Column("store_id", sa.String(length=36), primary_key=True),
        sa.Column("date", sa.String(length=10), primary_key=True),
        sa.Column("order_key", sa.String(length=255), primary_key=True),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_print_history_store_id", "print_history", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "dumper_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index("ix_dumper_logs_store_id", "dumper_logs", ["store_id"])
    op.create_index("ix_dumper_logs_created_at", "dumper_logs", ["created_at"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_dumper_logs_store_id", "dumper_logs", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "analytics_snapshots",
        sa.Column("store_id", sa.String(length=36), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.String(length=50), nullable=True),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_analytics_snapshots_store_id", "analytics_snapshots", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    _backfill(bind)


def _backfill(bind) -> None:
    store_ids = bind.execute(sa.text("SELECT id FROM stores")).fetchall()
    if len(store_ids) != 1:
        return
    only_store_id = store_ids[0][0]

    for legacy_file, kind in (
        ("states/processed_orders.json", "radar"),
        ("states/processed_packaging_orders.json", "packaging"),
    ):
        if not os.path.exists(legacy_file):
            continue
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                orders = json.load(f)
        except Exception:
            continue
        for order_id, payload in (orders or {}).items():
            bind.execute(
                sa.text(
                    "INSERT INTO processed_orders (store_id, kind, order_id, payload) "
                    "VALUES (:sid, :kind, :oid, :payload)"
                ),
                {"sid": only_store_id, "kind": kind, "oid": order_id, "payload": json.dumps(payload, ensure_ascii=False)},
            )

    if os.path.exists("states/analytics.json"):
        try:
            with open("states/analytics.json", "r", encoding="utf-8") as f:
                analytics = json.load(f)
        except Exception:
            analytics = None
        if analytics:
            bind.execute(
                sa.text("INSERT INTO analytics_snapshots (store_id, payload) VALUES (:sid, :payload)"),
                {"sid": only_store_id, "payload": json.dumps(analytics, ensure_ascii=False)},
            )

    if os.path.exists("states/dumper_logs.json"):
        try:
            with open("states/dumper_logs.json", "r", encoding="utf-8") as f:
                events = json.load(f)
        except Exception:
            events = None
        for event in (events or []):
            bind.execute(
                sa.text("INSERT INTO dumper_logs (store_id, payload) VALUES (:sid, :payload)"),
                {"sid": only_store_id, "payload": json.dumps(event, ensure_ascii=False)},
            )


def downgrade() -> None:
    op.drop_table("analytics_snapshots")
    op.drop_table("dumper_logs")
    op.drop_table("print_history")
    op.drop_table("processed_orders")
