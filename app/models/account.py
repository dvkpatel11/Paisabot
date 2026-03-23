from datetime import datetime

from app.extensions import db


class Account(db.Model):
    """Separate tracked account per asset class.

    Each account has its own cash pool, NAV, and trade history.
    The asset_class field partitions ETF vs Stock operations so
    positions, trades, and performance are tracked independently.
    """

    __tablename__ = 'accounts'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    asset_class = db.Column(
        db.String(10), nullable=False, index=True,
    )  # 'etf' or 'stock'

    # ── capital ──────────────────────────────────────────────────
    initial_capital = db.Column(db.Numeric(14, 2), nullable=False, default=100_000)
    cash_balance = db.Column(db.Numeric(14, 2), nullable=False, default=100_000)
    portfolio_value = db.Column(db.Numeric(14, 2), default=0)  # sum of position notionals

    # ── performance snapshot (updated EOD) ───────────────────────
    total_pnl = db.Column(db.Numeric(14, 2), default=0)
    realized_pnl = db.Column(db.Numeric(14, 2), default=0)
    unrealized_pnl = db.Column(db.Numeric(14, 2), default=0)
    high_watermark = db.Column(db.Numeric(14, 2))
    current_drawdown = db.Column(db.Numeric(10, 6), default=0)  # as fraction

    # ── operational ──────────────────────────────────────────────
    broker = db.Column(db.String(20), default='alpaca')
    operational_mode = db.Column(db.String(15), default='simulation')
    is_active = db.Column(db.Boolean, default=True)

    # ── risk constraints (per-account overrides) ─────────────────
    max_positions = db.Column(db.Integer, default=20)
    max_position_pct = db.Column(db.Numeric(6, 4), default=0.05)    # 5%
    max_sector_pct = db.Column(db.Numeric(6, 4), default=0.25)      # 25%
    vol_target = db.Column(db.Numeric(6, 4), default=0.12)          # 12% annualized

    # ── timestamps ───────────────────────────────────────────────
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.UniqueConstraint('name', 'asset_class', name='uq_account_name_class'),
    )

    @property
    def nav(self) -> float:
        """Net Asset Value = cash + portfolio value."""
        cash = float(self.cash_balance or 0)
        positions = float(self.portfolio_value or 0)
        return cash + positions

    @property
    def cash_pct(self) -> float:
        """Cash as percentage of NAV."""
        nav = self.nav
        if nav <= 0:
            return 1.0
        return float(self.cash_balance or 0) / nav

    def __repr__(self):
        return f'<Account {self.name} [{self.asset_class}] NAV={self.nav:.0f}>'
