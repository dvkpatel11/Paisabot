"""add stock mode: stock_universe, accounts, asset_class columns

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── accounts table ───────────────────────────────────────────
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("asset_class", sa.String(10), nullable=False),
        sa.Column(
            "initial_capital",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="100000",
        ),
        sa.Column(
            "cash_balance", sa.Numeric(14, 2), nullable=False, server_default="100000"
        ),
        sa.Column("portfolio_value", sa.Numeric(14, 2), server_default="0"),
        sa.Column("total_pnl", sa.Numeric(14, 2), server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(14, 2), server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(14, 2), server_default="0"),
        sa.Column("high_watermark", sa.Numeric(14, 2), nullable=True),
        sa.Column("current_drawdown", sa.Numeric(10, 6), server_default="0"),
        sa.Column("broker", sa.String(20), server_default="alpaca"),
        sa.Column("operational_mode", sa.String(15), server_default="simulation"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("max_positions", sa.Integer(), server_default="20"),
        sa.Column("max_position_pct", sa.Numeric(6, 4), server_default="0.05"),
        sa.Column("max_sector_pct", sa.Numeric(6, 4), server_default="0.25"),
        sa.Column("vol_target", sa.Numeric(6, 4), server_default="0.12"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", "asset_class", name="uq_account_name_class"),
    )
    op.create_index("ix_accounts_asset_class", "accounts", ["asset_class"])

    # ── stock_universe table ─────────────────────────────────────
    op.create_table(
        "stock_universe",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(10), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sector", sa.String(100), nullable=False),
        sa.Column("industry", sa.String(200), nullable=True),
        # market metadata
        sa.Column("market_cap_bn", sa.Numeric(14, 2), nullable=True),
        sa.Column("avg_daily_vol_m", sa.Numeric(12, 2), nullable=True),
        sa.Column("spread_est_bps", sa.Numeric(6, 2), nullable=True),
        sa.Column("liquidity_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("float_shares_m", sa.Numeric(12, 2), nullable=True),
        sa.Column("short_interest_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("beta", sa.Numeric(6, 3), nullable=True),
        sa.Column("options_market", sa.Boolean(), server_default="true"),
        # fundamentals
        sa.Column("pe_ratio", sa.Numeric(10, 2), nullable=True),
        sa.Column("forward_pe", sa.Numeric(10, 2), nullable=True),
        sa.Column("pb_ratio", sa.Numeric(10, 2), nullable=True),
        sa.Column("ps_ratio", sa.Numeric(10, 2), nullable=True),
        sa.Column("roe", sa.Numeric(8, 4), nullable=True),
        sa.Column("debt_to_equity", sa.Numeric(10, 2), nullable=True),
        sa.Column("revenue_growth_yoy", sa.Numeric(8, 4), nullable=True),
        sa.Column("earnings_growth_yoy", sa.Numeric(8, 4), nullable=True),
        sa.Column("dividend_yield", sa.Numeric(6, 4), nullable=True),
        sa.Column("profit_margin", sa.Numeric(8, 4), nullable=True),
        # earnings calendar
        sa.Column("next_earnings_date", sa.Date(), nullable=True),
        sa.Column("last_earnings_date", sa.Date(), nullable=True),
        sa.Column("last_earnings_surprise", sa.Numeric(8, 4), nullable=True),
        sa.Column("earnings_surprise_3q_avg", sa.Numeric(8, 4), nullable=True),
        # watchlist
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column(
            "in_active_set", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("active_set_reason", sa.String(200), nullable=True),
        sa.Column("active_set_changed_at", sa.DateTime(timezone=True), nullable=True),
        # operator tracking
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("your_rating", sa.Integer(), nullable=True),
        sa.Column("tags", sa.String(200), nullable=True),
        # cached performance
        sa.Column("last_signal_type", sa.String(10), nullable=True),
        sa.Column("last_composite_score", sa.Numeric(6, 4), nullable=True),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("perf_1w", sa.Numeric(8, 4), nullable=True),
        sa.Column("perf_1m", sa.Numeric(8, 4), nullable=True),
        sa.Column("perf_3m", sa.Numeric(8, 4), nullable=True),
        sa.Column("correlation_to_spy", sa.Numeric(6, 4), nullable=True),
        # refresh tracking
        sa.Column("fundamentals_updated_at", sa.DateTime(timezone=True), nullable=True),
        # timestamps
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_stock_universe_symbol", "stock_universe", ["symbol"], unique=True
    )

    # ── asset_class column on existing tables ────────────────────

    # price_bars
    op.add_column(
        "price_bars",
        sa.Column(
            "asset_class",
            sa.String(10),
            nullable=False,
            server_default="etf",
        ),
    )
    op.create_index("ix_price_bars_asset_class", "price_bars", ["asset_class"])

    # factor_scores — add asset_class + stock factor columns
    op.add_column(
        "factor_scores",
        sa.Column(
            "fundamentals_score",
            sa.Numeric(6, 4),
            nullable=True,
        ),
    )
    op.add_column(
        "factor_scores",
        sa.Column(
            "earnings_score",
            sa.Numeric(6, 4),
            nullable=True,
        ),
    )
    op.add_column(
        "factor_scores",
        sa.Column(
            "asset_class",
            sa.String(10),
            nullable=False,
            server_default="etf",
        ),
    )
    op.create_index("ix_factor_scores_asset_class", "factor_scores", ["asset_class"])

    # signals
    op.add_column(
        "signals",
        sa.Column(
            "asset_class",
            sa.String(10),
            nullable=False,
            server_default="etf",
        ),
    )
    op.add_column(
        "signals",
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_signals_asset_class", "signals", ["asset_class"])

    # trades
    op.add_column(
        "trades",
        sa.Column(
            "asset_class",
            sa.String(10),
            nullable=False,
            server_default="etf",
        ),
    )
    op.add_column(
        "trades",
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
    )

    # positions
    op.add_column(
        "positions",
        sa.Column(
            "asset_class",
            sa.String(10),
            nullable=False,
            server_default="etf",
        ),
    )
    op.add_column(
        "positions",
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
    )

    # performance_metrics — drop old unique on date, add composite
    op.drop_index("ix_performance_metrics_date", table_name="performance_metrics")
    op.add_column(
        "performance_metrics",
        sa.Column(
            "asset_class",
            sa.String(10),
            nullable=False,
            server_default="etf",
        ),
    )
    op.add_column(
        "performance_metrics",
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_perf_date_asset_class",
        "performance_metrics",
        ["date", "asset_class"],
    )
    op.create_index(
        "ix_performance_asset_class", "performance_metrics", ["asset_class"]
    )


def downgrade() -> None:
    # ── performance_metrics ──────────────────────────────────────
    op.drop_index("ix_performance_asset_class", "performance_metrics")
    op.drop_constraint(
        "uq_perf_date_asset_class", "performance_metrics", type_="unique"
    )
    op.drop_column("performance_metrics", "account_id")
    op.drop_column("performance_metrics", "asset_class")
    op.create_unique_constraint(
        "performance_metrics_date_key", "performance_metrics", ["date"]
    )
    op.create_index(
        "ix_performance_metrics_date", "performance_metrics", ["date"], unique=True
    )

    # ── positions ────────────────────────────────────────────────
    op.drop_column("positions", "account_id")
    op.drop_column("positions", "asset_class")

    # ── trades ───────────────────────────────────────────────────
    op.drop_column("trades", "account_id")
    op.drop_column("trades", "asset_class")

    # ── signals ──────────────────────────────────────────────────
    op.drop_index("ix_signals_asset_class", "signals")
    op.drop_column("signals", "account_id")
    op.drop_column("signals", "asset_class")

    # ── factor_scores ────────────────────────────────────────────
    op.drop_index("ix_factor_scores_asset_class", "factor_scores")
    op.drop_column("factor_scores", "asset_class")
    op.drop_column("factor_scores", "earnings_score")
    op.drop_column("factor_scores", "fundamentals_score")

    # ── price_bars ───────────────────────────────────────────────
    op.drop_index("ix_price_bars_asset_class", "price_bars")
    op.drop_column("price_bars", "asset_class")

    # ── drop new tables ──────────────────────────────────────────
    op.drop_index("ix_stock_universe_symbol", "stock_universe")
    op.drop_table("stock_universe")
    op.drop_index("ix_accounts_asset_class", "accounts")
    op.drop_table("accounts")
