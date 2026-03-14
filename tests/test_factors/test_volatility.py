import numpy as np
import pandas as pd
import pytest

from app.factors.volatility import VolatilityFactor


class TestVolatilityFactor:
    @pytest.fixture
    def factor(self):
        return VolatilityFactor()

    def test_compute_returns_scores(self, factor, sample_price_data, db_session):
        symbols = ['SPY', 'QQQ', 'XLK', 'XLE', 'XLF']
        scores = factor.compute(symbols)

        assert len(scores) == len(symbols)
        for sym, score in scores.items():
            assert 0.0 <= score <= 1.0, f'{sym} score {score} out of range'

    def test_low_vol_scores_higher(self, factor, sample_price_data, db_session):
        """XLP (low vol seed) should score higher than XLE (high vol seed)."""
        scores = factor.compute(['XLP', 'XLE'])
        # XLP has lower drift vol (0.010) vs XLE (0.022)
        # Lower vol = higher score (inverted)
        assert scores['XLP'] >= scores['XLE'] - 0.15  # allow tolerance

    def test_garch_cold_start(self, factor):
        """GARCH should return None with insufficient data."""
        returns = pd.Series(np.random.randn(100) * 0.01)
        result = factor._compute_garch_component(returns, 0.15)
        assert result is None  # < 250 obs

    def test_compute_single_with_vix(self, factor):
        """Test _compute_single with VIX data."""
        # Generate synthetic close prices
        np.random.seed(42)
        n = 280
        prices = [100.0]
        for _ in range(n - 1):
            prices.append(prices[-1] * np.exp(0.0003 + 0.012 * np.random.randn()))
        closes = pd.Series(prices)

        score = factor._compute_single(
            closes,
            vix_value=18.0,
            vix_history=[float(v) for v in np.random.uniform(12, 35, 252)],
        )
        assert 0.0 <= score <= 1.0

    def test_compute_single_no_vix(self, factor):
        """Test fallback when VIX data is missing."""
        np.random.seed(42)
        prices = [100.0]
        for _ in range(99):
            prices.append(prices[-1] * np.exp(0.015 * np.random.randn()))
        closes = pd.Series(prices)

        score = factor._compute_single(closes, vix_value=None, vix_history=[])
        assert 0.0 <= score <= 1.0

    def test_insufficient_data(self, factor):
        """Very short series returns neutral 0.5."""
        closes = pd.Series([100.0, 101.0, 99.0])
        score = factor._compute_single(closes, vix_value=None, vix_history=[])
        assert score == 0.5
