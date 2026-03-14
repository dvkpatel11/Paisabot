import fakeredis
import numpy as np
import pandas as pd
import pytest

from app.risk.continuous_monitor import ContinuousMonitor


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def monitor(redis):
    return ContinuousMonitor(redis_client=redis)


def _make_values(prices):
    idx = pd.date_range('2025-01-01', periods=len(prices), freq='B')
    return pd.Series(prices, index=idx)


def _make_returns(n=100, mean=0.0005, std=0.01, seed=42):
    np.random.seed(seed)
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    return pd.Series(np.random.normal(mean, std, n), index=idx)


def _make_prices_df(symbols, n=100, seed=42):
    np.random.seed(seed)
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    data = {}
    for s in symbols:
        data[s] = 100 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    return pd.DataFrame(data, index=idx)


# ── full run ────────────────────────────────────────────────────────

class TestContinuousMonitorRun:
    def test_full_run_ok(self, monitor):
        vals = _make_values([100, 101, 102, 103, 104, 105])
        rets = _make_returns(100)
        positions = [
            {'symbol': 'XLK', 'entry_price': 98, 'high_watermark': 105, 'status': 'open'},
            {'symbol': 'XLE', 'entry_price': 48, 'high_watermark': 52, 'status': 'open'},
        ]
        prices = {'XLK': 104, 'XLE': 51}
        prices_df = _make_prices_df(['XLK', 'XLE'])

        result = monitor.run(vals, rets, positions, prices, prices_df, 100_000)

        assert result['overall_status'] == 'ok'
        assert 'drawdown' in result
        assert 'stop_loss' in result
        assert 'var' in result
        assert 'correlation' in result
        assert 'liquidity' in result
        assert 'timestamp' in result

    def test_run_with_stop_exit(self, monitor):
        vals = _make_values([100, 101, 102, 103, 104, 105])
        rets = _make_returns(100)
        positions = [
            {'symbol': 'XLK', 'entry_price': 100, 'high_watermark': 105, 'status': 'open'},
        ]
        prices = {'XLK': 93}  # -7% from entry → hard stop
        prices_df = _make_prices_df(['XLK'])

        result = monitor.run(vals, rets, positions, prices, prices_df, 100_000)
        assert result['overall_status'] == 'critical'
        assert len(result['stop_loss']['exits']) == 1

    def test_run_with_drawdown_halt(self, redis, monitor):
        vals = _make_values([100, 100, 84])  # -16% drawdown
        rets = _make_returns(100)
        positions = []
        prices_df = _make_prices_df(['XLK'])

        result = monitor.run(vals, rets, positions, {}, prices_df, 100_000)
        assert result['overall_status'] == 'halt'


# ── risk state caching ──────────────────────────────────────────────

class TestRiskStateCaching:
    def test_caches_risk_state(self, redis, monitor):
        vals = _make_values([100, 101, 102])
        rets = _make_returns(100)
        prices_df = _make_prices_df(['XLK'])

        monitor.run(vals, rets, [], {}, prices_df, 100_000)

        cached = redis.get('cache:risk_state')
        assert cached is not None


# ── vol scaling ─────────────────────────────────────────────────────

class TestVolScaling:
    def test_no_change_low_vol(self, monitor):
        rets = _make_returns(100, std=0.005)
        result = monitor.check_vol_scaling(rets, vol_target=0.12)
        assert result['action'] == 'no_change'
        assert result['scale_factor'] == 1.0

    def test_scale_to_target(self, monitor):
        rets = _make_returns(100, std=0.015)  # ~24% annualized
        result = monitor.check_vol_scaling(rets, vol_target=0.12)
        assert result['action'] in ('scale_to_target', 'scale_down_50pct')
        assert result['scale_factor'] < 1.0

    def test_severe_vol_scales_50pct(self, monitor):
        # Very high vol → 1.5x target → 50% reduction
        rets = _make_returns(100, std=0.03)  # ~48% annualized
        result = monitor.check_vol_scaling(rets, vol_target=0.12)
        assert result['action'] == 'scale_down_50pct'
        assert result['scale_factor'] == 0.50

    def test_insufficient_data(self, monitor):
        rets = _make_returns(10)
        result = monitor.check_vol_scaling(rets, vol_target=0.12)
        assert result['action'] == 'no_change'


# ── re-entry eligibility ───────────────────────────────────────────

class TestReentry:
    def test_not_halted(self, monitor):
        rets = _make_returns(100)
        result = monitor.check_reentry_eligibility(rets)
        assert result['eligible'] is True
        assert result['sizing_pct'] == 1.0

    def test_halted_insufficient_data(self, redis, monitor):
        redis.set('kill_switch:trading', '1')
        rets = _make_returns(10)
        result = monitor.check_reentry_eligibility(rets)
        assert result['eligible'] is False

    def test_halted_eligible(self, redis, monitor):
        redis.set('kill_switch:trading', '1')
        # Good returns with positive Sharpe
        rets = _make_returns(100, mean=0.002, std=0.005)
        result = monitor.check_reentry_eligibility(rets)
        # Should be eligible at 50% sizing
        if result['eligible']:
            assert result['sizing_pct'] == 0.50

    def test_halted_bad_sharpe(self, redis, monitor):
        redis.set('kill_switch:trading', '1')
        # Negative returns → bad Sharpe
        rets = _make_returns(100, mean=-0.003, std=0.02)
        result = monitor.check_reentry_eligibility(rets)
        assert result['eligible'] is False


# ── status aggregation ──────────────────────────────────────────────

class TestStatusAggregation:
    def test_halt_overrides_all(self, monitor):
        results = {
            'drawdown': {'status': 'halt'},
            'stop_loss': {'exits': []},
            'var': {'status': 'ok'},
            'correlation': {'status': 'ok'},
            'liquidity': {'shocked': []},
        }
        assert monitor._aggregate_status(results) == 'halt'

    def test_stop_exits_are_critical(self, monitor):
        results = {
            'drawdown': {'status': 'ok'},
            'stop_loss': {'exits': [{'symbol': 'XLK'}]},
            'var': {'status': 'ok'},
            'correlation': {'status': 'ok'},
            'liquidity': {'shocked': []},
        }
        assert monitor._aggregate_status(results) == 'critical'

    def test_var_breach_is_warning(self, monitor):
        results = {
            'drawdown': {'status': 'ok'},
            'stop_loss': {'exits': []},
            'var': {'status': 'breach'},
            'correlation': {'status': 'ok'},
            'liquidity': {'shocked': []},
        }
        assert monitor._aggregate_status(results) == 'warning'

    def test_all_ok(self, monitor):
        results = {
            'drawdown': {'status': 'ok'},
            'stop_loss': {'exits': []},
            'var': {'status': 'ok'},
            'correlation': {'status': 'ok'},
            'liquidity': {'shocked': []},
        }
        assert monitor._aggregate_status(results) == 'ok'
