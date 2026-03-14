from app.extensions import db


class Position(db.Model):
    __tablename__ = 'positions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(10), nullable=False, index=True)
    broker = db.Column(db.String(20), nullable=False)
    broker_ref = db.Column(db.String(50))
    direction = db.Column(db.String(5), nullable=False)
    entry_price = db.Column(db.Numeric(12, 4), nullable=False)
    current_price = db.Column(db.Numeric(12, 4))
    quantity = db.Column(db.Numeric(12, 6))
    notional = db.Column(db.Numeric(14, 2))
    weight = db.Column(db.Numeric(6, 4))
    high_watermark = db.Column(db.Numeric(12, 4))
    unrealized_pnl = db.Column(db.Numeric(14, 2))
    realized_pnl = db.Column(db.Numeric(14, 2), default=0)
    sector = db.Column(db.String(100))
    status = db.Column(db.String(10), default='open', index=True)
    opened_at = db.Column(db.DateTime(timezone=True), nullable=False)
    closed_at = db.Column(db.DateTime(timezone=True))
    close_reason = db.Column(db.String(50))

    def __repr__(self):
        return f'<Position {self.symbol} {self.direction} {self.status}>'
