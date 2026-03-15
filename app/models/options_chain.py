from app.extensions import db


class OptionsChain(db.Model):
    __tablename__ = 'options_chains'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(10), nullable=False)
    expiry = db.Column(db.Date, nullable=False)
    strike = db.Column(db.Numeric(12, 2), nullable=False)
    call_put = db.Column(db.String(4), nullable=False)  # 'call' or 'put'
    iv = db.Column(db.Numeric(8, 6))
    volume = db.Column(db.Integer)
    oi = db.Column(db.Integer)  # open interest
    delta = db.Column(db.Numeric(8, 6))
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        db.Index('ix_options_sym_expiry', 'symbol', 'expiry'),
        db.Index('ix_options_sym_ts', 'symbol', 'timestamp'),
    )

    def __repr__(self):
        return f'<Option {self.symbol} {self.expiry} {self.strike}{self.call_put[0].upper()}>'
