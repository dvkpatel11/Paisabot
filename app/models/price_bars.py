from app.extensions import db


class PriceBar(db.Model):
    __tablename__ = 'price_bars'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(10), nullable=False)
    timeframe = db.Column(db.String(5), nullable=False, default='1d')
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)
    open = db.Column(db.Numeric(12, 4), nullable=False)
    high = db.Column(db.Numeric(12, 4), nullable=False)
    low = db.Column(db.Numeric(12, 4), nullable=False)
    close = db.Column(db.Numeric(12, 4), nullable=False)
    volume = db.Column(db.BigInteger, nullable=False)
    vwap = db.Column(db.Numeric(12, 4))
    trade_count = db.Column(db.Integer)
    is_synthetic = db.Column(db.Boolean, default=False)
    source = db.Column(db.String(20), default='alpaca')
    asset_class = db.Column(
        db.String(10), nullable=False, default='etf', server_default='etf',
    )  # 'etf' or 'stock'

    __table_args__ = (
        db.UniqueConstraint(
            'symbol', 'timeframe', 'timestamp',
            name='uq_bar_symbol_tf_ts',
        ),
        db.Index('ix_price_bars_sym_ts', 'symbol', 'timestamp'),
        db.Index('ix_price_bars_asset_class', 'asset_class'),
    )

    def __repr__(self):
        return f'<Bar {self.symbol} {self.timeframe} {self.timestamp}>'
