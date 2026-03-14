import pandas as pd
import pytest

from app.factors.breadth import BreadthFactor


class TestBreadthFactor:
    @pytest.fixture
    def factor(self):
        return BreadthFactor()

    def test_compute_returns_scores(self, factor, sample_price_data, db_session):
        symbols = ['SPY', 'QQQ', 'XLK']
        scores = factor.compute(symbols)
        assert len(scores) == len(symbols)
        for sym, score in scores.items():
            assert 0.0 <= score <= 1.0

    def test_above_sma_bullish(self, factor):
        """Trending up should be above SMA."""
        prices = pd.Series([100 + i * 0.5 for i in range(60)])
        assert factor._above_sma(prices, 50) == 1.0

    def test_above_sma_bearish(self, factor):
        """Trending down should be below SMA."""
        prices = pd.Series([160 - i * 0.5 for i in range(60)])
        assert factor._above_sma(prices, 50) == 0.0

    def test_above_sma_insufficient_data(self, factor):
        prices = pd.Series([100, 101, 102])
        assert factor._above_sma(prices, 50) == 0.5

    def test_ad_ema_score_range(self, factor):
        prices = pd.Series([100 + i * 0.1 for i in range(30)])
        score = factor._compute_ad_ema_score(prices)
        assert 0.0 <= score <= 1.0

    def test_sector_participation(self, factor, sample_price_data, db_session):
        from app.factors.base import FactorBase
        closes_df = factor._get_multi_closes(
            ['XLK', 'XLE', 'XLF', 'XLV', 'XLI', 'XLC', 'XLY', 'XLP', 'XLU', 'XLRE', 'XLB'],
            lookback=20,
        )
        if not closes_df.empty:
            part = factor._compute_sector_participation(closes_df)
            assert 0.0 <= part <= 1.0

    def test_deterioration_detection(self, factor):
        """Test breadth deterioration flagging."""
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)

        # Simulate declining breadth
        results = {'SPY': 0.3}
        # Pre-fill history with higher values
        for val in [0.8, 0.7, 0.6, 0.5]:
            factor._redis.lpush('breadth:history:SPY', str(val))

        factor._check_deterioration(results)
        # After push, history is [0.3, 0.8, 0.7, 0.6, 0.5]
        # 5d change = 0.3 - 0.5 = -0.2 < -0.15 → warning
        assert factor._redis.get('breadth_warning:SPY') == '1'
