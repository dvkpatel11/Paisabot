from app.extensions import db


class SentimentRaw(db.Model):
    __tablename__ = 'sentiment_raw'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(10), nullable=False)
    headline = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(30), nullable=False)  # finnhub, reddit, etc.
    raw_score = db.Column(db.Numeric(6, 4))  # model output [-1, 1]
    model = db.Column(db.String(30), default='finbert')  # finbert, vader, etc.
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        db.Index('ix_sentiment_sym_ts', 'symbol', 'timestamp'),
        db.Index('ix_sentiment_source', 'source'),
    )

    def __repr__(self):
        return f'<Sentiment {self.symbol} {self.source} {self.raw_score}>'
