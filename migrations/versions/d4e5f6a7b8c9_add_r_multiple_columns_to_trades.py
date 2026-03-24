"""add direction, stop_distance_at_entry, and r_multiple to trades

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('trades', sa.Column('direction', sa.String(5), nullable=True))
    op.add_column('trades', sa.Column(
        'stop_distance_at_entry', sa.Numeric(8, 6), nullable=True,
    ))
    op.add_column('trades', sa.Column('r_multiple', sa.Numeric(8, 4), nullable=True))


def downgrade() -> None:
    op.drop_column('trades', 'r_multiple')
    op.drop_column('trades', 'stop_distance_at_entry')
    op.drop_column('trades', 'direction')
