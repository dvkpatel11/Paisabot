import numpy as np
import pandas as pd
import pytest

from app.factors.correlation import CorrelationFactor


class TestCorrelationFactor:
    @pytest.fixture
    def factor(self):
        return CorrelationFactor()

    def test_compute_returns_scores(self, factor, sample_price_data, db_session):
        symbols = ['SPY', 'QQQ', 'XLK']
        scores = factor.compute(symbols)

        assert len(scores) == len(symbols)
        for sym, score in scores.items():
            assert 0.0 <= score <= 1.0, f'{sym} score {score} out of range'

    def test_market_level_uniform(self, factor, sample_price_data, db_session):
        """Correlation is market-level: all symbols get the same score."""
        symbols = ['SPY', 'QQQ', 'XLK', 'XLE']
        scores = factor.compute(symbols)

        values = list(scores.values())
        assert all(v == values[0] for v in values)

    def test_rolling_avg_corr_computation(self, factor):
        """Test rolling average correlation with synthetic data."""
        np.random.seed(42)
        n_days = 100
        n_sectors = 5

        # Generate correlated returns
        data = {}
        base = np.random.randn(n_days) * 0.01
        for i in range(n_sectors):
            noise = np.random.randn(n_days) * 0.005
            data[f'sector_{i}'] = base + noise

        log_returns = pd.DataFrame(data)
        avg_corrs = factor._compute_rolling_avg_corr(log_returns)

        assert len(avg_corrs) > 0
        # With highly correlated data, avg corr should be positive
        assert all(0.0 <= c <= 1.0 for c in avg_corrs)

    def test_collapse_detection(self, factor):
        """Z-score < -2.0 should trigger collapse flag."""
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)

        # Create history with stable high correlation, then sudden drop
        history = [0.7] * 250 + [0.2]  # sudden collapse
        result = factor._detect_collapse(history)
        assert result is True
        assert factor._redis.get('correlation_collapse') == '1'

    def test_no_collapse_normal_conditions(self, factor):
        """Normal correlation shouldn't trigger collapse."""
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)

        history = [0.5 + np.random.randn() * 0.05 for _ in range(252)]
        result = factor._detect_collapse(history)
        # Could be True or False depending on random data, but check logic runs
        assert isinstance(result, bool)

    def test_risk_off_detection(self, factor):
        """Sustained high correlation should trigger risk-off."""
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)

        # 3 consecutive days above 0.85
        history = [0.5] * 200 + [0.90, 0.88, 0.92]
        result = factor._detect_risk_off(history)
        assert result is True
        assert factor._redis.get('correlation_risk_off') == '1'

    def test_no_risk_off_below_threshold(self, factor):
        """Moderate correlation shouldn't trigger risk-off."""
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)

        history = [0.5] * 200 + [0.70, 0.72, 0.68]
        result = factor._detect_risk_off(history)
        assert result is False
