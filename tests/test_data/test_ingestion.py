from datetime import date, datetime, timezone

import fakeredis
import pandas as pd
import pytest

from app.data.ingestion import (
    detect_gaps,
    fill_gaps_with_synthetic,
    ingest_daily_bars,
    update_redis_cache,
)
from app.models.price_bars import PriceBar


class TestIngestDailyBars:
    def test_ingest_empty_df(self, db_session):
        df = pd.DataFrame()
        result = ingest_daily_bars('SPY', df)
        assert result == 0

    def test_ingest_single_bar(self, db_session):
        df = pd.DataFrame([{
            'timestamp': datetime(2024, 1, 2, tzinfo=timezone.utc),
            'open': 470.0,
            'high': 475.0,
            'low': 469.0,
            'close': 474.0,
            'volume': 80000000,
            'vwap': 472.0,
            'trade_count': 500000,
        }])
        result = ingest_daily_bars('SPY', df)
        assert result == 1

        bar = PriceBar.query.filter_by(symbol='SPY').first()
        assert bar is not None
        assert float(bar.close) == 474.0
        assert bar.is_synthetic is False

    def test_ingest_deduplication(self, db_session):
        df = pd.DataFrame([{
            'timestamp': datetime(2024, 1, 2, tzinfo=timezone.utc),
            'open': 470.0,
            'high': 475.0,
            'low': 469.0,
            'close': 474.0,
            'volume': 80000000,
            'vwap': 472.0,
            'trade_count': 500000,
        }])
        first = ingest_daily_bars('SPY', df)
        second = ingest_daily_bars('SPY', df)
        assert first == 1
        assert second == 0  # duplicate skipped

    def test_ingest_multiple_bars(self, db_session):
        df = pd.DataFrame([
            {
                'timestamp': datetime(2024, 1, 2, tzinfo=timezone.utc),
                'open': 470.0, 'high': 475.0, 'low': 469.0,
                'close': 474.0, 'volume': 80000000,
                'vwap': 472.0, 'trade_count': 500000,
            },
            {
                'timestamp': datetime(2024, 1, 3, tzinfo=timezone.utc),
                'open': 474.0, 'high': 478.0, 'low': 473.0,
                'close': 477.0, 'volume': 75000000,
                'vwap': 475.0, 'trade_count': 450000,
            },
        ])
        result = ingest_daily_bars('SPY', df)
        assert result == 2


class TestDetectGaps:
    def test_detect_gaps_no_data(self, db_session):
        gaps = detect_gaps('SPY', date(2024, 1, 2), date(2024, 1, 5))
        # Should detect all weekdays as gaps
        assert len(gaps) > 0

    def test_detect_gaps_with_calendar(self, db_session):
        # Insert one bar
        bar = PriceBar(
            symbol='SPY', timeframe='1d',
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open=470, high=475, low=469, close=474, volume=80000000,
        )
        db_session.add(bar)
        db_session.commit()

        calendar = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        gaps = detect_gaps('SPY', date(2024, 1, 2), date(2024, 1, 4),
                           trading_calendar=calendar)
        assert date(2024, 1, 3) in gaps
        assert date(2024, 1, 4) in gaps
        assert date(2024, 1, 2) not in gaps


class TestFillGapsWithSynthetic:
    def test_fill_empty_gaps(self, db_session):
        result = fill_gaps_with_synthetic('SPY', [])
        assert result == 0

    def test_fill_gaps_carry_forward(self, db_session):
        # Insert a real bar
        bar = PriceBar(
            symbol='SPY', timeframe='1d',
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open=470, high=475, low=469, close=474, volume=80000000,
            source='alpaca',
        )
        db_session.add(bar)
        db_session.commit()

        result = fill_gaps_with_synthetic('SPY', [date(2024, 1, 3)])
        assert result == 1

        synthetic = PriceBar.query.filter_by(
            symbol='SPY', is_synthetic=True,
        ).first()
        assert synthetic is not None
        assert float(synthetic.close) == 474.0
        assert float(synthetic.open) == 474.0
        assert int(synthetic.volume) == 0

    def test_fill_gap_no_prior_bar(self, db_session):
        result = fill_gaps_with_synthetic('SPY', [date(2024, 1, 2)])
        assert result == 0


class TestUpdateRedisCache:
    def test_cache_bars(self):
        redis = fakeredis.FakeRedis()
        df = pd.DataFrame([{
            'timestamp': datetime(2024, 1, 2, tzinfo=timezone.utc),
            'open': 470.0,
            'high': 475.0,
            'low': 469.0,
            'close': 474.0,
            'volume': 80000000,
            'vwap': 472.0,
        }])
        count = update_redis_cache('SPY', df, redis)
        assert count == 1
        assert redis.hget('ohlcv:SPY:2024-01-02', 'close') == b'474.0'

    def test_cache_empty_df(self):
        redis = fakeredis.FakeRedis()
        count = update_redis_cache('SPY', pd.DataFrame(), redis)
        assert count == 0

    def test_cache_no_redis(self):
        df = pd.DataFrame([{
            'timestamp': datetime(2024, 1, 2, tzinfo=timezone.utc),
            'open': 470.0, 'high': 475.0, 'low': 469.0,
            'close': 474.0, 'volume': 80000000,
        }])
        count = update_redis_cache('SPY', df, None)
        assert count == 0
