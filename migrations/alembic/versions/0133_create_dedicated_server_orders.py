"""create dedicated_server_orders

Revision ID: 0133
Revises: 0132
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0133'
down_revision: Union[str, None] = '0132'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dedicated_server_orders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.Column('country_code', sa.String(length=8), nullable=False),
        sa.Column('country_name', sa.String(length=64), nullable=False),
        sa.Column('continent', sa.String(length=32), nullable=True),
        sa.Column('deployment_type', sa.String(length=32), nullable=False, server_default='turnkey'),
        sa.Column('cpu_cores', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('ram_gb', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('period_days', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('amount_kopeks', sa.Integer(), nullable=False),
        sa.Column('options', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('squad_uuid', sa.String(length=255), nullable=True),
        sa.Column(
            'subscription_id',
            sa.Integer(),
            sa.ForeignKey('subscriptions.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('setup_token', sa.String(length=64), unique=True, nullable=True),
        sa.Column('admin_notes', sa.Text(), nullable=True),
        sa.Column('rejected_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_dedicated_server_orders_user_id', 'dedicated_server_orders', ['user_id'])
    op.create_index('ix_dedicated_server_orders_status', 'dedicated_server_orders', ['status'])
    op.create_index('ix_dedicated_server_orders_setup_token', 'dedicated_server_orders', ['setup_token'])


def downgrade() -> None:
    op.drop_table('dedicated_server_orders')
