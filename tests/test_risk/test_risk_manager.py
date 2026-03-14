import json

import fakeredis
import numpy as np
import pandas as pd
import pytest

from app.risk.risk_manager import RiskManager


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def manager(redis):
    return RiskManager(redis_client=redis)


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


# ── pre-trade integration ──────────────────────────────────────────

class TestPreTrade:
    def test_pre_trade_approves_valid_orders(self, manager):
        orders = [{'symbol': 'XLK', 'side': 'buy', 'notional': 2000}]
        result = manager.pre_trade(orders, [], 100_000)
        assert result['approved_count'] == 1

    def test_pre_trade_blocks_during_kill_switch(self, redis, manager):
        redis.set('kill_switch:trading', '1')
        orders = [{'symbol': 'XLK', 'side': 'buy', 'notional': 2000}]
        result = manager.pre_trade(orders, [], 100_000)
        assert result['blocked_count'] == 1

    def test_pre_trade_with_regime(self, manager):
        orders = [{'symbol': 'XLK', 'side': 'buy', 'notional': 2000}]
        result = manager.pre_trade(
            orders, [], 100_000, regime='risk_off',
        )
        assert result['approved_count'] == 1


# ── continuous monitor integration ──────────────────────────────────

class TestMonitor:
    def test_monitor_returns_all_sub_results(self, manager):
        vals = _make_values([100, 101, 102, 103])
        rets = _make_returns(100)
        prices_df = _make_prices_df(['XLK'])
        positions = [
            {'symbol': 'XLK', 'entry_price': 98, 'high_watermark': 103, 'status': 'open'},
        ]

        result = manager.monitor(
            vals, rets, positions, {'XLK': 102}, prices_df, 100_000,
        )

        assert 'overall_status' in result
        assert 'drawdown' in result
        assert 'stop_loss' in result
        assert 'var' in result
        assert 'correlation' in result
        assert 'liquidity' in result


# ── risk state cache ────────────────────────────────────────────────

class TestRiskState:
    def test_get_risk_state_after_monitor(self, redis, manager):
        vals = _make_values([100, 101, 102])
        rets = _make_returns(100)
        prices_df = _make_prices_df(['XLK'])

        manager.monitor(vals, rets, [], {}, prices_df, 100_000)
        state = manager.get_risk_state()
        assert state is not None
        assert 'overall_status' in state

    def test_get_risk_state_empty(self, manager):
        state = manager.get_risk_state()
        assert state is None


# ── force liquidate ─────────────────────────────────────────────────

class TestForceLiquidate:
    def test_generates_sell_orders(self, manager):
        positions = [
            {'symbol': 'XLK', 'notional': 5000, 'status': 'open'},
            {'symbol': 'XLE', 'notional': 3000, 'status': 'open'},
            {'symbol': 'XLV', 'notional': 2000, 'status': 'closed'},
        ]
        orders = manager.force_liquidate(positions)
        assert len(orders) == 2  # only open positions
        assert all(o['side'] == 'sell' for o in orders)
        assert all(o['reason'] == 'force_liquidate' for o in orders)

    def test_force_liquidate_publishes_alert(self, redis, manager):
        positions = [{'symbol': 'XLK', 'notional': 5000, 'status': 'open'}]
        manager.force_liquidate(positions)
        alert = redis.rpop('channel:risk_alerts')
        assert alert is not None
        data = json.loads(alert)
        assert data['type'] == 'force_liquidate'


# ── convenience methods ─────────────────────────────────────────────

class TestConvenienceMethods:
    def test_vol_scaling_delegated(self, manager):
        rets = _make_returns(100)
        result = manager.check_vol_scaling(rets)
        assert 'scale_factor' in result
        assert 'action' in result

    def test_reentry_delegated(self, manager):
        rets = _make_returns(100)
        result = manager.check_reentry(rets)
        assert 'eligible' in result
        assert 'sizing_pct' in result
