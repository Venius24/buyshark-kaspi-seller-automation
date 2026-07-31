"""Composite (store_id, sku) primary keys + store_id NOT NULL

Closes the second half of the multi-tenancy gap left by migration 002:
store_id was nullable there specifically so not-yet-migrated (legacy) callers
could keep writing unscoped rows. Now that Слайс 1 (per-store product
catalog) and the dual-auth routers are live, dumping_configs and
product_overrides having sku as their ONLY primary key means two different
stores selling the same Kaspi SKU physically overwrite each other's config —
this makes store_id part of the primary key so that can no longer happen.
master_stock_groups only gets store_id NOT NULL (group_id already carries a
random uuid suffix, so it's globally unique on its own — no composite key
needed there).

Safety: before touching constraints, any row still carrying a NULL store_id
is backfilled IF exactly one Store exists (same rule as migration 002); if
that leaves any NULL store_id row un-backfillable (zero or multiple stores,
truly ambiguous ownership), the migration aborts loudly instead of guessing
or dropping data.

Irreversible in the general case: downgrade() only succeeds if, at downgrade
time, no two rows share the same sku across different stores — which is
exactly the scenario this migration exists to make possible. That's an
inherent, expected limitation of downgrading after the composite key has
actually been relied upon by more than one store, not an oversight.

Revision ID: 006_composite_store_keys
Revises: 005_store_products
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '006_composite_store_keys'
down_revision: Union[str, None] = '005_store_products'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COMPOSITE_TABLES = ("dumping_configs", "product_overrides")
NOT_NULL_ONLY_TABLES = ("master_stock_groups",)


def _backfill_and_verify_no_nulls(bind, table: str) -> None:
    null_count = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE store_id IS NULL")).scalar()
    if not null_count:
        return

    store_ids = bind.execute(sa.text("SELECT id FROM stores")).fetchall()
    if len(store_ids) == 1:
        only_store_id = store_ids[0][0]
        bind.execute(
            sa.text(f"UPDATE {table} SET store_id = :sid WHERE store_id IS NULL"),
            {"sid": only_store_id},
        )
        null_count = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE store_id IS NULL")).scalar()

    if null_count:
        raise RuntimeError(
            f"Migration 006 aborted: {table} still has {null_count} row(s) with NULL store_id "
            f"and {len(store_ids)} store(s) exist in the DB — ownership is ambiguous, refusing to "
            "guess. Resolve these rows manually (assign or delete them) before re-running."
        )


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    for table in COMPOSITE_TABLES + NOT_NULL_ONLY_TABLES:
        _backfill_and_verify_no_nulls(bind, table)

    for table in COMPOSITE_TABLES:
        if is_sqlite:
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column("store_id", existing_type=sa.String(length=36), nullable=False)
                batch_op.create_primary_key(f"pk_{table}", ["store_id", "sku"])
        else:
            op.alter_column(table, "store_id", existing_type=sa.String(length=36), nullable=False)
            op.drop_constraint(f"{table}_pkey", table, type_="primary")
            op.create_primary_key(f"pk_{table}", table, ["store_id", "sku"])

    for table in NOT_NULL_ONLY_TABLES:
        if is_sqlite:
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column("store_id", existing_type=sa.String(length=36), nullable=False)
        else:
            op.alter_column(table, "store_id", existing_type=sa.String(length=36), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    for table in NOT_NULL_ONLY_TABLES:
        if is_sqlite:
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column("store_id", existing_type=sa.String(length=36), nullable=True)
        else:
            op.alter_column(table, "store_id", existing_type=sa.String(length=36), nullable=True)

    for table in COMPOSITE_TABLES:
        if is_sqlite:
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_constraint(f"pk_{table}", type_="primary")
                batch_op.create_primary_key(f"{table}_pkey", ["sku"])
                batch_op.alter_column("store_id", existing_type=sa.String(length=36), nullable=True)
        else:
            op.drop_constraint(f"pk_{table}", table, type_="primary")
            op.create_primary_key(f"{table}_pkey", table, ["sku"])
            op.alter_column(table, "store_id", existing_type=sa.String(length=36), nullable=True)
