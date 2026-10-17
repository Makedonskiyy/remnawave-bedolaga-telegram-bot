"""create halloween_user_state

Revision ID: 0135
Revises: 0134
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0135'
down_revision: Union[str, None] = '0134'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'halloween_user_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False),
        sa.Column('earned_minutes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('available_minutes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('level', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('energy', sa.Integer(), nullable=False, server_default='1000'),
        sa.Column('max_energy', sa.Integer(), nullable=False, server_default='1000'),
        sa.Column('last_energy_updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_passive_collected_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('boost_active_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_cursed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('cursed_frozen_minutes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_trick_or_treat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_halloween_user_state_user_id', 'halloween_user_state', ['user_id'])


def downgrade() -> None:
    op.drop_table('halloween_user_state')
