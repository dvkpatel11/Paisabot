from app.extensions import db


class FactorScore(db.Model):
    __tablename__ = 'factor_scores'

    id = db.Column(db.BigInteger, primary_key=True)
    symbol = db.Column(db.String(10), nullable=False)
    calc_time = db.Column(db.DateTime(timezone=True), nullable=False)
    trend_score = db.Column(db.Numeric(6, 4))
    volatility_score = db.Column(db.Numeric(6, 4))
    sentiment_score = db.Column(db.Numeric(6, 4))
    dispersion_score = db.Column(db.Numeric(6, 4))
    correlation_score = db.Column(db.Numeric(6, 4))
    breadth_score = db.Column(db.Numeric(6, 4))
    liquidity_score = db.Column(db.Numeric(6, 4))
    slippage_score = db.Column(db.Numeric(6, 4))

    __table_args__ = (
        db.Index('ix_factor_scores_sym_time', 'symbol', 'calc_time'),
    )

    def __repr__(self):
        return f'<FactorScore {self.symbol} {self.calc_time}>'
