"""Сотрудники и роли: разделы кабинета на UserStore + одноразовый пароль сотрудника.

user_stores.allowed_sections — JSON-список ключей разделов (NULL = без
ограничений, так остаются все существующие строки: сузить права задним
числом миграцией нельзя, это молча отобрало бы доступ у работающих людей).
users.must_change_password — сотрудник, которому владелец выдал временный
пароль, обязан сменить его при первом входе.

Revision ID: 040_employees_and_sections
Revises: 039_stock_group_stop_notified
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '040_employees_and_sections'
down_revision: Union[str, None] = '039_stock_group_stop_notified'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_stores", sa.Column("allowed_sections", sa.Text(), nullable=True))
    op.add_column("user_stores", sa.Column("job_preset", sa.String(length=30), nullable=True))
    op.add_column("user_stores", sa.Column("job_title", sa.String(length=100), nullable=True))
    op.add_column("user_stores", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_column("user_stores", "created_at")
    op.drop_column("user_stores", "job_title")
    op.drop_column("user_stores", "job_preset")
    op.drop_column("user_stores", "allowed_sections")
