from app.extensions import db


class Quote(db.Model):
    __tablename__ = 'quotes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(10), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)
    bid = db.Column(db.Numeric(12, 4), nullable=False)
    ask = db.Column(db.Numeric(12, 4), nullable=False)
    mid = db.Column(db.Numeric(12, 4), nullable=False)
    spread_bps = db.Column(db.Numeric(8, 2))
    source = db.Column(db.String(20), default='alpaca')

    __table_args__ = (
        db.Index('ix_quotes_sym_ts', 'symbol', 'timestamp'),
    )

    def __repr__(self):
        return f'<Quote {self.symbol} {self.timestamp} bid={self.bid} ask={self.ask}>'
