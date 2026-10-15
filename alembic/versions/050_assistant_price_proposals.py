"""Предложения цен помощника; только dry run после одобрения человеком.

Revision ID: 050_assistant_price_proposals
Revises: 049_stock_group_float_stock
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '050_assistant_price_proposals'
down_revision: Union[str, None] = '049_stock_group_float_stock'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'assistant_price_proposals',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('store_id', sa.String(36), sa.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('assistant_conversations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('sku', sa.String(100), nullable=False),
        sa.Column('old_price', sa.BigInteger(), nullable=False),
        sa.Column('new_price', sa.BigInteger(), nullable=False),
        sa.Column('min_price_at_creation', sa.Float(), nullable=False),
        sa.Column('max_price_at_creation', sa.Float(), nullable=True),
        sa.Column('reason', sa.String(300), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_by_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('decided_by_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('apply_result', sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'failed')", name='ck_assistant_proposal_status'),
    )
    op.create_index('ix_assistant_proposals_store_status_created', 'assistant_price_proposals',
                    ['store_id', 'status', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_assistant_proposals_store_status_created', table_name='assistant_price_proposals')
    op.drop_table('assistant_price_proposals')
