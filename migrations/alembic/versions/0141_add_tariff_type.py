"""add tariff_type to tariffs

Revision ID: 0132
Revises: 0131
"""

from typing import Sequence, Union

import sqlalchemy as sa
# Renumbered (was 0132) on the 5.2.0 update: upstream reused 0132 for another migration.
# Idempotent, because databases upgraded from our fork already ran it under the old number.
from alembic import op


revision: str = '0141'
down_revision: Union[str, None] = '0140'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('tariffs')}
    if 'tariff_type' not in columns:
        op.add_column(
            'tariffs',
            sa.Column('tariff_type', sa.String(length=32), nullable=False, server_default='standard'),
        )


def downgrade() -> None:
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('tariffs')}
    if 'tariff_type' in columns:
        op.drop_column('tariffs', 'tariff_type')
