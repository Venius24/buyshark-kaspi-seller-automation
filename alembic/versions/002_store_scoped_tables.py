"""Add nullable store_id to dumping_configs, master_stock_groups, product_overrides

Additive-only migration (Phase 4a of the multi-tenant refactor). No column
is dropped, no NOT NULL constraint is added, no primary key changes — the
app keeps working unmodified before and after this runs. store_id stays
NULL until Phase 4d's router migration starts writing/filtering by it.

FK/index creation is skipped on SQLite: production always runs Postgres
(SQLite is only the local dev fallback in backend/db/database.py), and
SQLite can't ADD a FK constraint via plain ALTER TABLE — only via a full
batch table-rebuild, which isn't worth the complexity for a dev-only path
that doesn't enforce FKs by default anyway.

Revision ID: 002_store_scoped_tables
Revises: 001_initial_schema
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_store_scoped_tables'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("dumping_configs", "master_stock_groups", "product_overrides")


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    for table in TABLES:
        op.add_column(table, sa.Column("store_id", sa.String(length=36), nullable=True))
        op.create_index(f"ix_{table}_store_id", table, ["store_id"])
        if not is_sqlite:
            op.create_foreign_key(
                f"fk_{table}_store_id", table, "stores", ["store_id"], ["id"], ondelete="CASCADE"
            )

    # Backfill only when the backfill is unambiguous: today's production data
    # has exactly one real tenant, so if exactly one Store row exists, every
    # existing (currently store-less) row can only have belonged to it. If
    # zero or multiple stores exist, skip backfill entirely — rows stay NULL
    # rather than guessing, and legacy fallback logic (removed in Phase 4e)
    # keeps serving them until Phase 4d migrates the consuming routers.
    store_ids = bind.execute(sa.text("SELECT id FROM stores")).fetchall()
    if len(store_ids) == 1:
        only_store_id = store_ids[0][0]
        for table in TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET store_id = :sid WHERE store_id IS NULL"),
                {"sid": only_store_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    for table in TABLES:
        if not is_sqlite:
            op.drop_constraint(f"fk_{table}_store_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_store_id", table_name=table)
        op.drop_column(table, "store_id")
