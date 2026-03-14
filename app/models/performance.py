from app.extensions import db


class PerformanceMetric(db.Model):
    __tablename__ = 'performance_metrics'

    id = db.Column(db.BigInteger, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    portfolio_value = db.Column(db.Numeric(14, 2))
    daily_return = db.Column(db.Numeric(10, 6))
    cumulative_return = db.Column(db.Numeric(10, 6))
    drawdown = db.Column(db.Numeric(10, 6))
    sharpe_30d = db.Column(db.Numeric(8, 4))
    volatility_30d = db.Column(db.Numeric(8, 4))
    var_95 = db.Column(db.Numeric(10, 6))
    regime = db.Column(db.String(20))
    num_positions = db.Column(db.Integer)
    cash_pct = db.Column(db.Numeric(6, 4))

    def __repr__(self):
        return f'<Perf {self.date} val={self.portfolio_value}>'
