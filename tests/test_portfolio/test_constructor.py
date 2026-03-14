import numpy as np
import pandas as pd
import pytest

from app.portfolio.constraints import PortfolioConstraints
from app.portfolio.constructor import PortfolioConstructor


def _make_prices(symbols, n_days=252, seed=42):
    """Generate synthetic price data for testing."""
    np.random.seed(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now(), periods=n_days)
    data = {}
    for sym in symbols:
        returns = np.random.normal(0.0004, 0.015, n_days)
        prices = 100 * np.exp(np.cumsum(returns))
        data[sym] = prices
    return pd.DataFrame(data, index=dates)


class TestPortfolioConstructor:
    @pytest.fixture
    def constructor(self):
        return PortfolioConstructor()

    @pytest.fixture
    def prices(self):
        return _make_prices(['SPY', 'QQQ', 'XLK', 'XLE', 'XLF'])

    @pytest.fixture
    def constraints(self):
        return PortfolioConstraints()

    def test_empty_candidates(self, constructor, prices, constraints):
        result = constructor.build_target_weights([], prices, constraints=constraints)
        assert result == {}

    def test_single_candidate(self, constructor, prices, constraints):
        result = constructor.build_target_weights(
            ['SPY'], prices, constraints=constraints,
        )
        assert 'SPY' in result
        assert result['SPY'] <= constraints.max_position_size

    def test_equal_weight(self, constructor, prices, constraints):
        candidates = ['SPY', 'QQQ', 'XLK', 'XLE', 'XLF']
        result = constructor.build_target_weights(
            candidates, prices, constraints=constraints, objective='equal_weight',
        )
        assert len(result) == 5
        for w in result.values():
            assert w <= constraints.max_position_size
        assert sum(result.values()) <= 1.0

    def test_max_sharpe(self, constructor, prices, constraints):
        candidates = ['SPY', 'QQQ', 'XLK']
        result = constructor.build_target_weights(
            candidates, prices, constraints=constraints, objective='max_sharpe',
        )
        assert len(result) > 0
        assert sum(result.values()) <= 1.0
        for w in result.values():
            assert w > 0

    def test_min_vol(self, constructor, prices, constraints):
        candidates = ['SPY', 'QQQ', 'XLK']
        result = constructor.build_target_weights(
            candidates, prices, constraints=constraints, objective='min_vol',
        )
        assert len(result) > 0
        assert sum(result.values()) <= 1.0

    def test_hrp(self, constructor, prices, constraints):
        candidates = ['SPY', 'QQQ', 'XLK', 'XLE']
        result = constructor.build_target_weights(
            candidates, prices, constraints=constraints, objective='hrp',
        )
        assert len(result) > 0
        assert sum(result.values()) <= 1.0

    def test_cash_buffer_respected(self, constructor, prices):
        constraints = PortfolioConstraints(cash_buffer_pct=0.10)
        candidates = ['SPY', 'QQQ', 'XLK']
        result = constructor.build_target_weights(
            candidates, prices, constraints=constraints, objective='equal_weight',
        )
        assert sum(result.values()) <= 0.90 + 0.001

    def test_max_position_respected(self, constructor, prices):
        constraints = PortfolioConstraints(max_position_size=0.03)
        candidates = ['SPY', 'QQQ', 'XLK']
        result = constructor.build_target_weights(
            candidates, prices, constraints=constraints, objective='equal_weight',
        )
        for w in result.values():
            assert w <= 0.03 + 0.001

    def test_sector_constraints(self, constructor, prices, constraints):
        sector_map = {
            'SPY': 'Broad',
            'QQQ': 'Tech',
            'XLK': 'Tech',
            'XLE': 'Energy',
            'XLF': 'Financials',
        }
        candidates = ['SPY', 'QQQ', 'XLK', 'XLE', 'XLF']
        result = constructor.build_target_weights(
            candidates, prices, sector_map=sector_map,
            constraints=constraints, objective='equal_weight',
        )
        # Equal weight should still produce valid result
        assert len(result) > 0

    def test_missing_price_data(self, constructor, constraints):
        prices = pd.DataFrame({'SPY': [100, 101, 102]})
        result = constructor.build_target_weights(
            ['SPY', 'QQQ'], prices, constraints=constraints,
        )
        # Only SPY has data, falls back to single candidate
        assert len(result) <= 1

    def test_insufficient_history(self, constructor, constraints):
        """Short price history falls back to equal weight."""
        np.random.seed(42)
        dates = pd.bdate_range(end=pd.Timestamp.now(), periods=10)
        prices = pd.DataFrame({
            'SPY': np.random.uniform(100, 110, 10),
            'QQQ': np.random.uniform(100, 110, 10),
        }, index=dates)
        result = constructor.build_target_weights(
            ['SPY', 'QQQ'], prices, constraints=constraints,
        )
        assert len(result) > 0
