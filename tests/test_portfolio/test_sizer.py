import numpy as np
import pandas as pd
import pytest

from app.portfolio.sizer import PositionSizer


def _make_prices(symbols, n_days=120, seed=42, vol=0.015):
    np.random.seed(seed)
    dates = pd.date_range('2025-01-01', periods=n_days, freq='B')
    data = {}
    for sym in symbols:
        returns = np.random.normal(0.0003, vol, n_days)
        prices = 100 * np.exp(np.cumsum(returns))
        data[sym] = prices
    return pd.DataFrame(data, index=dates)


class TestPositionSizer:
    @pytest.fixture
    def sizer(self):
        return PositionSizer(vol_target=0.12, lookback=60)

    def test_no_scaling_low_vol(self, sizer):
        """Low vol portfolio should not be scaled down."""
        prices = _make_prices(['SPY', 'QQQ'], vol=0.005)
        weights = {'SPY': 0.30, 'QQQ': 0.30}
        result = sizer.apply_vol_target(weights, prices)
        # Low vol → no scaling, weights unchanged
        assert result['SPY'] == weights['SPY']
        assert result['QQQ'] == weights['QQQ']

    def test_scaling_high_vol(self, sizer):
        """High vol portfolio should be scaled down."""
        prices = _make_prices(['SPY', 'QQQ'], vol=0.04)
        weights = {'SPY': 0.45, 'QQQ': 0.45}
        result = sizer.apply_vol_target(weights, prices)
        # High vol → scaled down
        assert result['SPY'] < weights['SPY']
        assert result['QQQ'] < weights['QQQ']

    def test_never_leverages(self, sizer):
        """Scale factor should never exceed 1.0."""
        prices = _make_prices(['SPY', 'QQQ'], vol=0.002)
        weights = {'SPY': 0.20, 'QQQ': 0.20}
        result = sizer.apply_vol_target(weights, prices)
        for sym in weights:
            assert result[sym] <= weights[sym] + 0.0001

    def test_empty_weights(self, sizer):
        prices = _make_prices(['SPY'])
        assert sizer.apply_vol_target({}, prices) == {}

    def test_single_symbol(self, sizer):
        """Single symbol → not enough for covariance, returns unchanged."""
        prices = _make_prices(['SPY'])
        weights = {'SPY': 0.50}
        result = sizer.apply_vol_target(weights, prices)
        assert result == weights

    def test_insufficient_data(self, sizer):
        dates = pd.date_range('2025-01-01', periods=20, freq='B')
        prices = pd.DataFrame({
            'SPY': np.random.uniform(100, 110, 20),
            'QQQ': np.random.uniform(100, 110, 20),
        }, index=dates)
        weights = {'SPY': 0.30, 'QQQ': 0.30}
        result = sizer.apply_vol_target(weights, prices)
        assert result == weights  # unchanged due to insufficient data

    def test_estimate_portfolio_vol(self, sizer):
        prices = _make_prices(['SPY', 'QQQ', 'XLK'])
        weights = {'SPY': 0.30, 'QQQ': 0.30, 'XLK': 0.30}
        vol = sizer.estimate_portfolio_vol(weights, prices)
        assert vol is not None
        assert vol > 0

    def test_estimate_vol_insufficient_data(self, sizer):
        dates = pd.date_range('2025-01-01', periods=10, freq='B')
        prices = pd.DataFrame({
            'SPY': np.random.uniform(100, 110, 10),
            'QQQ': np.random.uniform(100, 110, 10),
        }, index=dates)
        vol = sizer.estimate_portfolio_vol({'SPY': 0.5, 'QQQ': 0.5}, prices)
        assert vol is None
