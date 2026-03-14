from datetime import datetime

from app.extensions import db


class ETFUniverse(db.Model):
    __tablename__ = 'etf_universe'

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    sector = db.Column(db.String(100), nullable=False)
    aum_bn = db.Column(db.Numeric(10, 2))
    avg_daily_vol_m = db.Column(db.Numeric(12, 2))
    spread_est_bps = db.Column(db.Numeric(6, 2))
    liquidity_score = db.Column(db.Numeric(4, 2))
    inception_date = db.Column(db.Date)
    options_market = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    mt5_symbol = db.Column(db.String(20))
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self):
        return f'<ETF {self.symbol}>'
