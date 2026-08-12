"""Add client_mailboxes table — скелет провижининга персональных email на
*@buyshark.kz (см. backend/services/client_mailbox.py). Реального клиента
под это ещё нет, таблица не заполняется на проде — задел под Фазу 1/2
AUTOCONNECT_PLAN.md. Плюс incoming_emails += mailbox_id для автосопоставления
входящих писем с зарезервированным адресом.

Revision ID: 029_client_mailboxes
Revises: 028_incoming_emails
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '029_client_mailboxes'
down_revision: Union[str, None] = '028_incoming_emails'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "client_mailboxes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("store_id", sa.String(length=36), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_client_mailboxes_email", "client_mailboxes", ["email"], unique=True)

    if not is_sqlite:
        op.create_foreign_key(
            "fk_client_mailboxes_owner_user_id", "client_mailboxes", "users", ["owner_user_id"], ["id"], ondelete="CASCADE"
        )
        op.create_foreign_key(
            "fk_client_mailboxes_store_id", "client_mailboxes", "stores", ["store_id"], ["id"], ondelete="SET NULL"
        )

    op.add_column("incoming_emails", sa.Column("mailbox_id", sa.String(length=36), nullable=True))
    if not is_sqlite:
        op.create_foreign_key(
            "fk_incoming_emails_mailbox_id", "incoming_emails", "client_mailboxes", ["mailbox_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if not is_sqlite:
        op.drop_constraint("fk_incoming_emails_mailbox_id", "incoming_emails", type_="foreignkey")
    op.drop_column("incoming_emails", "mailbox_id")
    op.drop_table("client_mailboxes")
