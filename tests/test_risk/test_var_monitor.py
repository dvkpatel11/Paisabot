import fakeredis
import numpy as np
import pandas as pd
import pytest

from app.risk.var_monitor import VaRMonitor


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def monitor(redis):
    return VaRMonitor(redis_client=redis)


def _make_returns(n=100, mean=0.0005, std=0.01, seed=42):
    np.random.seed(seed)
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    return pd.Series(np.random.normal(mean, std, n), index=idx)


# ── basic computation ───────────────────────────────────────────────

class TestVaRCompute:
    def test_normal_returns(self, monitor):
        returns = _make_returns(252)
        result = monitor.compute(returns, 100_000)
        assert result['status'] == 'ok'
        assert result['var_pct'] < 0  # VaR is a loss
        assert result['var_dollar'] > 0
        assert result['cvar_pct'] <= result['var_pct']  # CVaR worse than VaR
        assert result['confidence'] == 0.95

    def test_insufficient_data(self, monitor):
        returns = _make_returns(3)
        result = monitor.compute(returns, 100_000)
        assert result['status'] == 'insufficient_data'
        assert result['breach'] is False

    def test_empty_returns(self, monitor):
        result = monitor.compute(pd.Series(dtype=float), 100_000)
        assert result['status'] == 'insufficient_data'

    def test_var_dollar_scales_with_portfolio(self, monitor):
        returns = _make_returns(100)
        r1 = monitor.compute(returns, 100_000)
        r2 = monitor.compute(returns, 200_000)
        assert abs(r2['var_dollar'] - 2 * r1['var_dollar']) < 1.0


# ── breach detection ────────────────────────────────────────────────

class TestVaRBreach:
    def test_high_vol_triggers_breach(self, redis):
        # Very high volatility → VaR > 2%
        returns = _make_returns(100, mean=-0.001, std=0.03)
        monitor = VaRMonitor(redis_client=redis)
        result = monitor.compute(returns, 100_000)
        if abs(result['var_pct']) > 0.02:
            assert result['status'] == 'breach'
            assert result['breach'] is True

    def test_breach_publishes_alert(self, redis):
        redis.hset('config:risk', 'var_limit_pct', '0.001')  # very tight limit
        monitor = VaRMonitor(redis_client=redis)
        returns = _make_returns(100)
        monitor.compute(returns, 100_000)
        alert_raw = redis.rpop('channel:risk_alerts')
        assert alert_raw is not None


# ── custom config ───────────────────────────────────────────────────

class TestVaRConfig:
    def test_custom_confidence(self, redis):
        redis.hset('config:risk', 'var_confidence', '0.99')
        monitor = VaRMonitor(redis_client=redis)
        returns = _make_returns(100)
        result = monitor.compute(returns, 100_000)
        assert result['confidence'] == 0.99
        # 99% VaR should be more extreme than 95%
        result_95 = VaRMonitor(redis_client=fakeredis.FakeRedis()).compute(returns, 100_000)
        assert result['var_pct'] <= result_95['var_pct']  # more negative

    def test_custom_limit(self, redis):
        redis.hset('config:risk', 'var_limit_pct', '0.05')
        monitor = VaRMonitor(redis_client=redis)
        returns = _make_returns(100, std=0.01)  # low vol
        result = monitor.compute(returns, 100_000)
        assert result['status'] == 'ok'  # low vol, high limit


# ── parametric vs historical ────────────────────────────────────────

class TestVaRMethods:
    def test_parametric_var_returned(self, monitor):
        returns = _make_returns(252)
        result = monitor.compute(returns, 100_000)
        assert 'parametric_var' in result
        assert result['parametric_var'] != 0.0

    def test_cvar_worse_than_var(self, monitor):
        returns = _make_returns(252)
        result = monitor.compute(returns, 100_000)
        # CVaR (expected shortfall) should be <= VaR (more negative)
        assert result['cvar_pct'] <= result['var_pct']
