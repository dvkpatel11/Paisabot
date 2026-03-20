"""add stop_price and take_profit_price to positions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('positions', sa.Column('stop_price', sa.Numeric(12, 4), nullable=True))
    op.add_column('positions', sa.Column('take_profit_price', sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    op.drop_column('positions', 'take_profit_price')
    op.drop_column('positions', 'stop_price')
