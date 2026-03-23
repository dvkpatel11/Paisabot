from app.extensions import db


class Trade(db.Model):
    __tablename__ = 'trades'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(10), nullable=False, index=True)
    broker = db.Column(db.String(20), nullable=False)
    broker_order_id = db.Column(db.String(50))
    side = db.Column(db.String(4), nullable=False)
    order_type = db.Column(db.String(10), default='market')
    requested_notional = db.Column(db.Numeric(14, 2))
    filled_notional = db.Column(db.Numeric(14, 2))
    filled_quantity = db.Column(db.Numeric(12, 6))
    fill_price = db.Column(db.Numeric(12, 4))
    mid_at_submission = db.Column(db.Numeric(12, 4))
    slippage_bps = db.Column(db.Numeric(8, 4))
    estimated_slippage_bps = db.Column(db.Numeric(8, 4))
    status = db.Column(db.String(15), default='pending')
    operational_mode = db.Column(db.String(15))
    trade_time = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    fill_time = db.Column(db.DateTime(timezone=True))
    signal_composite = db.Column(db.Numeric(6, 4))
    regime = db.Column(db.String(20))
    asset_class = db.Column(
        db.String(10), nullable=False, default='etf', server_default='etf', index=True,
    )  # 'etf' or 'stock'
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=True)

    def __repr__(self):
        return f'<Trade {self.symbol} {self.side} {self.status}>'
