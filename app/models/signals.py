from app.extensions import db


class Signal(db.Model):
    __tablename__ = 'signals'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(10), nullable=False)
    signal_time = db.Column(db.DateTime(timezone=True), nullable=False)
    composite_score = db.Column(db.Numeric(6, 4), nullable=False)
    trend_score = db.Column(db.Numeric(6, 4))
    volatility_score = db.Column(db.Numeric(6, 4))
    sentiment_score = db.Column(db.Numeric(6, 4))
    breadth_score = db.Column(db.Numeric(6, 4))
    dispersion_score = db.Column(db.Numeric(6, 4))
    liquidity_score = db.Column(db.Numeric(6, 4))
    regime = db.Column(db.String(20))
    regime_confidence = db.Column(db.Numeric(5, 4))
    signal_type = db.Column(db.String(10))
    block_reason = db.Column(db.String(200))
    asset_class = db.Column(
        db.String(10), nullable=False, default='etf', server_default='etf', index=True,
    )  # 'etf' or 'stock'
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=True)

    __table_args__ = (
        db.Index('ix_signals_sym_time', 'symbol', 'signal_time'),
        db.Index('ix_signals_asset_class', 'asset_class'),
    )

    def __repr__(self):
        return f'<Signal {self.symbol} {self.signal_type} {self.composite_score}>'
