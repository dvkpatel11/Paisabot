from datetime import datetime

from app.extensions import db


class ETFUniverse(db.Model):
    __tablename__ = 'etf_universe'

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    sector = db.Column(db.String(100), nullable=False)

    # ── market metadata ──────────────────────────────────────────
    aum_bn = db.Column(db.Numeric(10, 2))
    avg_daily_vol_m = db.Column(db.Numeric(12, 2))
    spread_est_bps = db.Column(db.Numeric(6, 2))
    liquidity_score = db.Column(db.Numeric(4, 2))
    inception_date = db.Column(db.Date)
    options_market = db.Column(db.Boolean, default=True)
    mt5_symbol = db.Column(db.String(20))

    # ── watchlist flags ──────────────────────────────────────────
    is_active = db.Column(db.Boolean, default=True)        # visible in UI / data backfill
    in_active_set = db.Column(db.Boolean, default=False)   # enters trading pipeline
    active_set_reason = db.Column(db.String(200))           # why added/removed
    active_set_changed_at = db.Column(db.DateTime(timezone=True))

    # ── operator tracking columns ────────────────────────────────
    notes = db.Column(db.Text)                              # free-text research notes
    your_rating = db.Column(db.Integer)                     # conviction 1-5
    tags = db.Column(db.String(200))                        # comma-separated tags

    # ── cached performance (updated by scheduled task) ───────────
    last_signal_type = db.Column(db.String(10))             # long / neutral / avoid
    last_composite_score = db.Column(db.Numeric(6, 4))
    last_signal_at = db.Column(db.DateTime(timezone=True))
    perf_1w = db.Column(db.Numeric(8, 4))                  # trailing 1-week return
    perf_1m = db.Column(db.Numeric(8, 4))                  # trailing 1-month return
    perf_3m = db.Column(db.Numeric(8, 4))                  # trailing 3-month return
    correlation_to_spy = db.Column(db.Numeric(6, 4))        # rolling 60d corr

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
        return f'<ETF {self.symbol}{flag}>'
