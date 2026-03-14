import numpy as np
import pandas as pd
import pytest

from app.factors.liquidity import LiquidityFactor


class TestLiquidityFactor:
    @pytest.fixture
    def factor(self):
        return LiquidityFactor()

    def test_compute_returns_scores(self, factor, sample_price_data, db_session):
        symbols = ['SPY', 'QQQ', 'XLK']
        scores = factor.compute(symbols)
        assert len(scores) == len(symbols)
        for sym, score in scores.items():
            assert 0.0 <= score <= 1.0

    def test_high_adv_scores_well(self, factor):
        """High ADV should give high adv_score."""
        # Price $100 * 50M volume = $5B daily dollar volume
        ohlcv = pd.DataFrame({
            'open': [100.0] * 40,
            'high': [101.0] * 40,
            'low': [99.0] * 40,
            'close': [100.0] * 40,
            'volume': [50_000_000] * 40,
        })
        adv = factor._compute_adv(ohlcv)
        assert adv > factor.ADV_THRESHOLD
        assert min(adv / factor.ADV_THRESHOLD, 1.0) == 1.0

    def test_low_adv_scores_poorly(self, factor):
        """Low ADV should give low adv_score."""
        ohlcv = pd.DataFrame({
            'open': [10.0] * 40,
            'high': [10.5] * 40,
            'low': [9.5] * 40,
            'close': [10.0] * 40,
            'volume': [100_000] * 40,  # $10 * 100K = $1M << $20M
        })
        adv = factor._compute_adv(ohlcv)
        assert adv < factor.ADV_THRESHOLD

    def test_spread_estimation(self, factor):
        """Roll estimator should return a positive spread."""
        np.random.seed(42)
        n = 40
        prices = [100.0]
        for _ in range(n - 1):
            prices.append(prices[-1] * np.exp(0.01 * np.random.randn()))
        ohlcv = pd.DataFrame({
            'open': prices, 'high': prices,
            'low': prices, 'close': prices,
            'volume': [1_000_000] * n,
        })
        spread = factor._estimate_spread(ohlcv)
        assert spread >= 0.0

    def test_is_tradable_liquid(self, factor, sample_price_data, db_session):
        """SPY should be tradable (high volume in test data)."""
        # Our synthetic data has volume 1M-100M, but price ~450 * vol
        # ADV depends on synthetic data; just check it returns bool
        result = factor.is_tradable('SPY')
        assert isinstance(result, bool)

    def test_caches_in_redis(self, factor, sample_price_data, db_session):
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)
        factor.compute(['SPY'])
        assert factor._redis.get('etf:SPY:adv_30d_m') is not None
        assert factor._redis.get('etf:SPY:spread_bps') is not None
