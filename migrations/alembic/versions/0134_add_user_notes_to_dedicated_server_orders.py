"""add user_notes to dedicated_server_orders

Revision ID: 0134
Revises: 0133
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0134'
down_revision: Union[str, None] = '0133'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'dedicated_server_orders',
        sa.Column('user_notes', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dedicated_server_orders', 'user_notes')
