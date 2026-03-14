from datetime import datetime, timezone

from app.models.price_bars import PriceBar


class TestPriceBar:
    def test_create_bar(self, db_session):
        bar = PriceBar(
            symbol='SPY',
            timeframe='1d',
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open=470.0,
            high=475.0,
            low=469.0,
            close=474.0,
            volume=80000000,
            source='alpaca',
        )
        db_session.add(bar)
        db_session.commit()

        result = PriceBar.query.filter_by(symbol='SPY').first()
        assert result is not None
        assert float(result.close) == 474.0

    def test_unique_constraint(self, db_session):
        ts = datetime(2024, 1, 2, tzinfo=timezone.utc)
        b1 = PriceBar(symbol='SPY', timeframe='1d', timestamp=ts,
                       open=470, high=475, low=469, close=474, volume=80000000)
        b2 = PriceBar(symbol='SPY', timeframe='1d', timestamp=ts,
                       open=471, high=476, low=470, close=475, volume=90000000)
        db_session.add(b1)
        db_session.commit()
        db_session.add(b2)

        from sqlalchemy.exc import IntegrityError
        import pytest
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_synthetic_flag(self, db_session):
        bar = PriceBar(
            symbol='XLK',
            timeframe='1d',
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            open=200, high=200, low=200, close=200,
            volume=0,
            is_synthetic=True,
        )
        db_session.add(bar)
        db_session.commit()

        result = PriceBar.query.filter_by(symbol='XLK').first()
        assert result.is_synthetic is True
