"""Add incoming_emails table — one row per email received by Cloudflare Email
Routing on *@buyshark.kz and forwarded via the Email Worker webhook (see
backend/routers/webhooks_incoming_email.py). Raw MIME stored encrypted
(backend.security.crypto) since these are mostly Kaspi MC welcome emails
carrying temp passwords for auto-created employees.

Revision ID: 028_incoming_emails
Revises: 027_billing_tariffs
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '028_incoming_emails'
down_revision: Union[str, None] = '027_billing_tariffs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incoming_emails",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("to_address", sa.String(length=255), nullable=False),
        sa.Column("from_address", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=True),
        sa.Column("encrypted_raw", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_incoming_emails_to_address", "incoming_emails", ["to_address"])


def downgrade() -> None:
    op.drop_table("incoming_emails")
