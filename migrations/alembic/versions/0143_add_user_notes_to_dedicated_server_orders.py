"""add user_notes to dedicated_server_orders

Revision ID: 0134
Revises: 0133
"""

from typing import Sequence, Union

import sqlalchemy as sa
# Renumbered (was 0134) on the 5.2.0 update: upstream reused 0134 for another migration.
# Idempotent, because databases upgraded from our fork already ran it under the old number.
from alembic import op


revision: str = '0143'
down_revision: Union[str, None] = '0142'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if 'user_notes' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('dedicated_server_orders')}:
        return
    op.add_column(
        'dedicated_server_orders',
        sa.Column('user_notes', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dedicated_server_orders', 'user_notes')
