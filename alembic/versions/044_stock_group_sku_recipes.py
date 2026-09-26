"""master_stock_groups.sku_recipes — множитель и наборы на уровне артикула.

Групповой deduct_per_unit (миграция 034) заменён рецептом у каждого артикула:
{артикул: {"qty": N, "extra": [{"group_id", "qty"}]}}. См.
backend/services/sku_recipes.py.

Группы, у которых deduct_per_unit > 1, переносятся без потери смысла: каждому
привязанному артикулу ставится qty = deduct_per_unit. На проде 27.09.2026
таких групп ноль, перенос нужен для прочих баз.

Revision ID: 044_stock_group_sku_recipes
Revises: 043_user_sessions_revoked_at
Create Date: 2026-09-27 00:00:00.000000

"""
import json
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '044_stock_group_sku_recipes'
down_revision: Union[str, None] = '043_user_sessions_revoked_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("master_stock_groups", sa.Column("sku_recipes", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT group_id, linked_skus, deduct_per_unit FROM master_stock_groups "
        "WHERE deduct_per_unit > 1"
    )).fetchall()
    for gid, linked, per_unit in rows:
        skus = json.loads(linked) if linked else []
        recipes = {" ".join(str(s).split()): {"qty": int(per_unit)} for s in skus if str(s).strip()}
        bind.execute(
            sa.text("UPDATE master_stock_groups SET sku_recipes = :r, deduct_per_unit = 1 WHERE group_id = :g"),
            {"r": json.dumps(recipes, ensure_ascii=False) if recipes else None, "g": gid},
        )


def downgrade() -> None:
    op.drop_column("master_stock_groups", "sku_recipes")
