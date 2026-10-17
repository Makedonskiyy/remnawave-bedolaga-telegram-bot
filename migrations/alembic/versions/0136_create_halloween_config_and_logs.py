"""create halloween_config_and_logs

Revision ID: 0136
Revises: 0135
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0136'
down_revision: Union[str, None] = '0135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Config table
    op.create_table(
        'halloween_config',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 2. Game logs table
    op.create_table(
        'halloween_game_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('is_treat', sa.Boolean(), nullable=False),
        sa.Column('chosen_pumpkin', sa.Integer(), nullable=False),
        sa.Column('reward_type', sa.String(length=50), nullable=False),
        sa.Column('reward_title', sa.String(length=255), nullable=False),
        sa.Column('reward_value', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_halloween_game_logs_user_id', 'halloween_game_logs', ['user_id'])
    op.create_index('ix_halloween_game_logs_created_at', 'halloween_game_logs', ['created_at'])

    # 3. Exchange logs table
    op.create_table(
        'halloween_exchange_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('days_claimed', sa.Integer(), nullable=False),
        sa.Column('minutes_spent', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_halloween_exchange_logs_user_id', 'halloween_exchange_logs', ['user_id'])
    op.create_index('ix_halloween_exchange_logs_created_at', 'halloween_exchange_logs', ['created_at'])


def downgrade() -> None:
    op.drop_table('halloween_exchange_logs')
    op.drop_table('halloween_game_logs')
    op.drop_table('halloween_config')
