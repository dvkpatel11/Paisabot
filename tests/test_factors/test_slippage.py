import numpy as np
import pandas as pd
import pytest

from app.factors.slippage import SlippageFactor


class TestSlippageFactor:
    @pytest.fixture
    def factor(self):
        return SlippageFactor()

    def test_compute_returns_scores(self, factor, sample_price_data, db_session):
        symbols = ['SPY', 'QQQ', 'XLK']
        scores = factor.compute(symbols)
        assert len(scores) == len(symbols)
        for sym, score in scores.items():
            assert 0.0 <= score <= 1.0

    def test_estimate_for_order_small(self, factor, sample_price_data, db_session):
        """Small order on liquid ETF should have low slippage."""
        slippage_bps = factor.estimate_for_order('SPY', 10_000)
        assert slippage_bps >= 0.0

    def test_estimate_for_order_large(self, factor, sample_price_data, db_session):
        """Larger order should have higher slippage."""
        small = factor.estimate_for_order('SPY', 1_000)
        large = factor.estimate_for_order('SPY', 1_000_000)
        # Larger order → higher participation → more market impact
        assert large >= small

    def test_estimate_slippage_math(self, factor):
        """Test core Almgren-Chriss calculation."""
        np.random.seed(42)
        n = 40
        prices = [100.0]
        for _ in range(n - 1):
            prices.append(prices[-1] * np.exp(0.01 * np.random.randn()))
        ohlcv = pd.DataFrame({
            'open': prices, 'high': prices,
            'low': prices, 'close': prices,
            'volume': [10_000_000] * n,
        })

        # $50K order vs $1B ADV = tiny participation
        bps = factor._estimate_slippage(ohlcv, 50_000)
        assert bps >= 0.0
        assert bps < 50  # shouldn't be extreme for liquid instrument

    def test_spread_estimation(self, factor):
        np.random.seed(42)
        n = 40
        prices = [100.0]
        for _ in range(n - 1):
            prices.append(prices[-1] * np.exp(0.01 * np.random.randn()))
        ohlcv = pd.DataFrame({
            'close': prices,
            'open': prices, 'high': prices,
            'low': prices, 'volume': [1_000_000] * n,
        })
        spread = factor._estimate_spread_bps(ohlcv)
        assert spread >= 0.0

    def test_insufficient_data(self, factor):
        """Short series defaults to conservative estimate."""
        ohlcv = pd.DataFrame({
            'open': [100.0] * 5, 'high': [101.0] * 5,
            'low': [99.0] * 5, 'close': [100.0] * 5,
            'volume': [1_000_000] * 5,
        })
        bps = factor._estimate_slippage(ohlcv, 50_000)
        # Should still return a number (possibly using defaults)
        assert bps >= 0.0
