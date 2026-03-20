import json

import fakeredis
import numpy as np
import pandas as pd
import pytest

from app.risk.correlation_monitor import CorrelationMonitor


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def monitor(redis):
    return CorrelationMonitor(redis_client=redis)


def _make_prices(symbols, n=100, corr=0.5, seed=42):
    """Generate correlated price series for testing."""
    np.random.seed(seed)
    k = len(symbols)
    # Build correlation matrix
    C = np.full((k, k), corr)
    np.fill_diagonal(C, 1.0)
    L = np.linalg.cholesky(C)
    raw = np.random.randn(n, k) @ L.T
    # Convert to prices
    returns = raw * 0.01
    prices = 100 * np.exp(np.cumsum(returns, axis=0))
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    return pd.DataFrame(prices, index=idx, columns=symbols)


# ── basic checks ────────────────────────────────────────────────────

class TestCorrelationBasic:
    def test_low_correlation_ok(self, monitor):
        prices = _make_prices(['XLK', 'XLE', 'XLV'], corr=0.3)
        result = monitor.check(['XLK', 'XLE', 'XLV'], prices)
        assert result['status'] == 'ok'
        assert result['avg_corr'] < 0.85

    def test_high_correlation_warns(self, monitor):
        prices = _make_prices(['XLK', 'XLE', 'XLV'], corr=0.95)
        result = monitor.check(['XLK', 'XLE', 'XLV'], prices)
        assert result['status'] in ('warn', 'force_diversify')
        assert result['avg_corr'] > 0.80

    def test_single_position_ok(self, monitor):
        prices = _make_prices(['XLK'])
        result = monitor.check(['XLK'], prices)
        assert result['status'] == 'ok'
        assert result['avg_corr'] == 0.0

    def test_no_positions(self, monitor):
        prices = _make_prices(['XLK', 'XLE'])
        result = monitor.check([], prices)
        assert result['status'] == 'ok'


# ── consecutive breach days ─────────────────────────────────────────

class TestCorrelationConsecutive:
    def test_three_consecutive_days_force_diversify(self, redis, monitor):
        prices = _make_prices(['XLK', 'XLE'], corr=0.95, n=100)

        # Pre-seed the breach counter to simulate 2 prior breach days.
        # The sentinel key for today doesn't exist yet, so the next
        # check() call will increment to 3.
        redis.set('risk:corr_breach_days', '2')

        result = monitor.check(['XLK', 'XLE'], prices)
        assert result['consecutive_breach_days'] >= 3
        assert result['status'] == 'force_diversify'

    def test_breach_counter_resets_on_ok(self, redis, monitor):
        high_corr = _make_prices(['XLK', 'XLE'], corr=0.95)
        low_corr = _make_prices(['XLK', 'XLE'], corr=0.3)

        # First call: breach → counter goes to 1
        monitor.check(['XLK', 'XLE'], high_corr)
        # Second call (same day): ok → decrement/reset
        monitor.check(['XLK', 'XLE'], low_corr)

        # Clear today's sentinel so a new breach can register
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        redis.delete(f'risk:corr_breach_date:{today}')

        # Third call: breach again — should be 1 (counter was reset by ok)
        result = monitor.check(['XLK', 'XLE'], high_corr)
        assert result['consecutive_breach_days'] <= 1


# ── pairs above limit ──────────────────────────────────────────────

class TestCorrelationPairs:
    def test_identifies_high_correlation_pairs(self, monitor):
        prices = _make_prices(['XLK', 'XLE', 'XLV'], corr=0.95)
        result = monitor.check(['XLK', 'XLE', 'XLV'], prices)
        # With 0.95 corr, most pairs should be above 0.85
        assert len(result['pairs_above_limit']) > 0

    def test_no_pairs_above_with_low_corr(self, monitor):
        prices = _make_prices(['XLK', 'XLE', 'XLV'], corr=0.3)
        result = monitor.check(['XLK', 'XLE', 'XLV'], prices)
        assert len(result['pairs_above_limit']) == 0


# ── alert publishing ────────────────────────────────────────────────

class TestCorrelationAlerts:
    def test_publishes_alert_on_breach(self, redis, monitor):
        prices = _make_prices(['XLK', 'XLE'], corr=0.95)
        monitor.check(['XLK', 'XLE'], prices)
        alert_raw = redis.rpop('channel:risk_alerts')
        assert alert_raw is not None
        alert = json.loads(alert_raw)
        assert alert['type'] == 'correlation_warning'


# ── insufficient data ──────────────────────────────────────────────

class TestCorrelationEdgeCases:
    def test_insufficient_data(self, monitor):
        prices = _make_prices(['XLK', 'XLE'], n=20)
        result = monitor.check(['XLK', 'XLE'], prices)
        assert result['status'] == 'insufficient_data'

    def test_missing_symbols_in_prices(self, monitor):
        prices = _make_prices(['XLK'])
        result = monitor.check(['XLK', 'MISSING'], prices)
        assert result['status'] == 'ok'
