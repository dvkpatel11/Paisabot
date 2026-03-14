import json

import fakeredis
import pandas as pd
import pytest

from app.risk.drawdown_monitor import DrawdownMonitor


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def monitor(redis):
    return DrawdownMonitor(redis_client=redis)


def _make_values(prices):
    """Build a portfolio value Series from a list of floats."""
    idx = pd.date_range('2025-01-01', periods=len(prices), freq='B')
    return pd.Series(prices, index=idx)


# ── basic ok ────────────────────────────────────────────────────────

class TestDrawdownOk:
    def test_no_drawdown(self, monitor):
        vals = _make_values([100, 101, 102, 103, 104])
        result = monitor.check(vals)
        assert result['status'] == 'ok'
        assert result['breach_reason'] is None

    def test_small_drawdown_is_ok(self, monitor):
        vals = _make_values([100, 105, 104, 103])
        result = monitor.check(vals)
        assert result['status'] == 'ok'

    def test_empty_series(self, monitor):
        result = monitor.check(pd.Series(dtype=float))
        assert result['status'] == 'ok'

    def test_single_value(self, monitor):
        result = monitor.check(_make_values([100]))
        assert result['status'] == 'ok'


# ── warning ─────────────────────────────────────────────────────────

class TestDrawdownWarn:
    def test_drawdown_triggers_warn(self, monitor):
        # Gradual decline to -9% so daily loss (-3%) doesn't fire first
        vals = _make_values([100, 100, 98, 96, 94, 92, 91])
        result = monitor.check(vals)
        assert result['status'] == 'warn'

    def test_drawdown_at_boundary(self, monitor):
        # Gradual decline to -7.5% — below warn threshold of -8%
        vals = _make_values([100, 100, 98, 96, 94, 92.5])
        result = monitor.check(vals)
        assert result['status'] == 'ok'


# ── critical ────────────────────────────────────────────────────────

class TestDrawdownCritical:
    def test_drawdown_triggers_critical(self, monitor):
        # Gradual decline to -13%: daily steps < 3%
        vals = _make_values([100, 100, 98, 96, 94, 92, 90, 88, 87])
        result = monitor.check(vals)
        assert result['status'] == 'critical'


# ── halt ────────────────────────────────────────────────────────────

class TestDrawdownHalt:
    def test_max_drawdown_halts_trading(self, redis, monitor):
        # Gradual decline to -16%: daily steps < 3%
        vals = _make_values([100, 100, 98, 96, 94, 92, 90, 88, 86, 84])
        result = monitor.check(vals)
        assert result['status'] == 'halt'
        assert 'drawdown' in result['breach_reason']
        assert redis.get('kill_switch:trading') == b'1'

    def test_daily_loss_halts_trading(self, redis, monitor):
        # -3.5% daily loss: yesterday=100, today=96.5
        vals = _make_values([100, 100, 96.5])
        result = monitor.check(vals)
        assert result['status'] == 'halt'
        assert 'daily_loss' in result['breach_reason']
        assert redis.get('kill_switch:trading') == b'1'

    def test_daily_loss_not_triggered_for_small_drop(self, monitor):
        # -2% daily: yesterday=100, today=98 → not triggered
        vals = _make_values([100, 100, 98])
        result = monitor.check(vals)
        assert result['status'] != 'halt' or 'daily' not in (result['breach_reason'] or '')


# ── alert publishing ────────────────────────────────────────────────

class TestDrawdownAlerts:
    def test_halt_publishes_alert(self, redis, monitor):
        vals = _make_values([100, 100, 84])
        monitor.check(vals)
        alert_raw = redis.rpop('channel:risk_alerts')
        assert alert_raw is not None
        alert = json.loads(alert_raw)
        assert alert['level'] == 'critical'

    def test_warn_publishes_alert(self, redis, monitor):
        # Gradual decline to -9% to avoid daily loss halt
        vals = _make_values([100, 100, 98, 96, 94, 92, 91])
        monitor.check(vals)
        alert_raw = redis.rpop('channel:risk_alerts')
        assert alert_raw is not None
        alert = json.loads(alert_raw)
        assert alert['level'] == 'warning'


# ── custom thresholds via Redis config ──────────────────────────────

class TestDrawdownConfig:
    def test_custom_drawdown_threshold(self, redis):
        redis.hset('config:risk', 'max_drawdown', '-0.10')
        mon = DrawdownMonitor(redis_client=redis)
        # -11% should halt with custom -10% limit
        vals = _make_values([100, 100, 89])
        result = mon.check(vals)
        assert result['status'] == 'halt'

    def test_custom_daily_loss(self, redis):
        redis.hset('config:risk', 'daily_loss_limit', '-0.01')
        mon = DrawdownMonitor(redis_client=redis)
        # -1.5% daily → halt
        vals = _make_values([100, 100, 98.5])
        result = mon.check(vals)
        assert result['status'] == 'halt'
