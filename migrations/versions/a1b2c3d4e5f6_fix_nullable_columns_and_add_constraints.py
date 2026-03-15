"""fix nullable columns and add db constraints

Revision ID: a1b2c3d4e5f6
Revises: 223e641c8d79
Create Date: 2026-03-15 00:00:00.000000

Fixes:
  - system_config.is_secret:   migration added it as nullable=True; model says nullable=False
  - etf_universe.in_active_set: migration added it as nullable=True; model says nullable=False
  - Adds CHECK constraints on price_bars (price sanity) and factor_scores (score range)
  - Adds unique index on (symbol, calc_time) in factor_scores and signals
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '223e641c8d79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Fix is_secret nullable mismatch ──────────────────────────────────
    # Backfill any NULLs introduced by the original migration, then tighten.
    op.execute("UPDATE system_config SET is_secret = false WHERE is_secret IS NULL")
    op.alter_column(
        'system_config', 'is_secret',
        existing_type=sa.Boolean(),
        nullable=False,
        server_default='false',
    )

    # ── 2. Fix in_active_set nullable mismatch ───────────────────────────────
    op.execute("UPDATE etf_universe SET in_active_set = false WHERE in_active_set IS NULL")
    op.alter_column(
        'etf_universe', 'in_active_set',
        existing_type=sa.Boolean(),
        nullable=False,
        server_default='false',
    )

    # ── 3. CHECK constraints on price_bars ───────────────────────────────────
    # Ensure OHLCV values are positive and high >= low.
    op.create_check_constraint(
        'ck_price_bars_positive_prices',
        'price_bars',
        'open > 0 AND high > 0 AND low > 0 AND close > 0 AND volume >= 0',
    )
    op.create_check_constraint(
        'ck_price_bars_high_gte_low',
        'price_bars',
        'high >= low',
    )

    # ── 4a. Add composite_score column (missing from initial schema) ─────────
    op.add_column(
        'factor_scores',
        sa.Column('composite_score', sa.Numeric(6, 4), nullable=True),
    )

    # ── 4b. CHECK constraints on factor_scores ───────────────────────────────
    # All component scores must be in [0, 1].
    score_cols = [
        'trend_score', 'volatility_score', 'sentiment_score',
        'breadth_score', 'dispersion_score', 'correlation_score',
        'liquidity_score', 'slippage_score', 'composite_score',
    ]
    conditions = ' AND '.join(
        f'({c} IS NULL OR ({c} >= 0 AND {c} <= 1))' for c in score_cols
    )
    op.create_check_constraint(
        'ck_factor_scores_range',
        'factor_scores',
        conditions,
    )

    # ── 5. Unique index on factor_scores (symbol, calc_time) ─────────────────
    op.create_index(
        'uq_factor_scores_sym_time',
        'factor_scores',
        ['symbol', 'calc_time'],
        unique=True,
    )

    # ── 6. Unique index on signals (symbol, signal_time) ─────────────────────
    op.create_index(
        'uq_signals_sym_time',
        'signals',
        ['symbol', 'signal_time'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_signals_sym_time', table_name='signals')
    op.drop_index('uq_factor_scores_sym_time', table_name='factor_scores')
    op.drop_constraint('ck_factor_scores_range', 'factor_scores', type_='check')
    op.drop_column('factor_scores', 'composite_score')
    op.drop_constraint('ck_price_bars_high_gte_low', 'price_bars', type_='check')
    op.drop_constraint('ck_price_bars_positive_prices', 'price_bars', type_='check')

    op.alter_column('etf_universe', 'in_active_set', nullable=True, server_default=None)
    op.alter_column('system_config', 'is_secret', nullable=True, server_default=None)
