from datetime import datetime

from app.extensions import db


class StockUniverse(db.Model):
    __tablename__ = 'stock_universe'

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    sector = db.Column(db.String(100), nullable=False)
    industry = db.Column(db.String(200))

    # ── market metadata ──────────────────────────────────────────
    market_cap_bn = db.Column(db.Numeric(14, 2))          # billions
    avg_daily_vol_m = db.Column(db.Numeric(12, 2))        # millions $
    spread_est_bps = db.Column(db.Numeric(6, 2))
    liquidity_score = db.Column(db.Numeric(4, 2))
    float_shares_m = db.Column(db.Numeric(12, 2))         # millions
    short_interest_pct = db.Column(db.Numeric(6, 2))      # % of float
    beta = db.Column(db.Numeric(6, 3))
    options_market = db.Column(db.Boolean, default=True)

    # ── fundamental snapshot (refreshed by Celery task) ─────────
    pe_ratio = db.Column(db.Numeric(10, 2))
    forward_pe = db.Column(db.Numeric(10, 2))
    pb_ratio = db.Column(db.Numeric(10, 2))
    ps_ratio = db.Column(db.Numeric(10, 2))
    roe = db.Column(db.Numeric(8, 4))                     # return on equity
    debt_to_equity = db.Column(db.Numeric(10, 2))
    revenue_growth_yoy = db.Column(db.Numeric(8, 4))      # year-over-year %
    earnings_growth_yoy = db.Column(db.Numeric(8, 4))
    dividend_yield = db.Column(db.Numeric(6, 4))
    profit_margin = db.Column(db.Numeric(8, 4))

    # ── earnings calendar ───────────────────────────────────────
    next_earnings_date = db.Column(db.Date)
    last_earnings_date = db.Column(db.Date)
    last_earnings_surprise = db.Column(db.Numeric(8, 4))  # % surprise
    earnings_surprise_3q_avg = db.Column(db.Numeric(8, 4))

    # ── watchlist flags ──────────────────────────────────────────
    is_active = db.Column(db.Boolean, default=True)
    in_active_set = db.Column(
        db.Boolean, default=False, server_default='false', nullable=False,
    )
    active_set_reason = db.Column(db.String(200))
    active_set_changed_at = db.Column(db.DateTime(timezone=True))

    # ── operator tracking ────────────────────────────────────────
    notes = db.Column(db.Text)
    your_rating = db.Column(db.Integer)                    # conviction 1-5
    tags = db.Column(db.String(200))                       # comma-separated

    # ── cached performance (updated by scheduled task) ───────────
    last_signal_type = db.Column(db.String(10))
    last_composite_score = db.Column(db.Numeric(6, 4))
    last_signal_at = db.Column(db.DateTime(timezone=True))
    perf_1w = db.Column(db.Numeric(8, 4))
    perf_1m = db.Column(db.Numeric(8, 4))
    perf_3m = db.Column(db.Numeric(8, 4))
    correlation_to_spy = db.Column(db.Numeric(6, 4))

    # ── fundamentals refresh tracking ────────────────────────────
    fundamentals_updated_at = db.Column(db.DateTime(timezone=True))

    # ── timestamps ───────────────────────────────────────────────
    added_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self):
        flag = '*' if self.in_active_set else ''
        return f'<Stock {self.symbol}{flag}>'
