import json

import fakeredis
import numpy as np
import pandas as pd
import pytest

from app.portfolio.constraints import PortfolioConstraints
from app.portfolio.portfolio_manager import PortfolioManager


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def manager(redis):
    return PortfolioManager(redis_client=redis)


def _make_prices(symbols, n=252, seed=42):
    np.random.seed(seed)
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    data = {}
    for s in symbols:
        data[s] = 100 * np.exp(np.cumsum(np.random.normal(0.0005, 0.015, n)))
    return pd.DataFrame(data, index=idx)


def _signal(composite, signal_type='long', tradable=True):
    return {
        'composite_score': composite,
        'signal_type': signal_type,
        'rank': 1,
        'tradable': tradable,
    }


SECTOR_MAP = {
    'XLK': 'Technology',
    'XLV': 'Health Care',
    'XLE': 'Energy',
    'XLF': 'Financials',
    'SPY': 'Broad',
}


# ── full pipeline ───────────────────────────────────────────────────

class TestFullPipeline:
    def test_basic_run(self, manager):
        signals = {
            'XLK': _signal(0.90),
            'XLV': _signal(0.85),
            'XLE': _signal(0.80),
        }
        prices = _make_prices(['XLK', 'XLV', 'XLE'])
        result = manager.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000,
            prices_df=prices,
            regime='trending',
            sector_map=SECTOR_MAP,
        )
        assert len(result['target_weights']) > 0
        assert len(result['orders']) > 0
        assert result['regime'] == 'trending'
        assert result['candidates'] == ['XLK', 'XLV', 'XLE']
        assert result['n_orders'] > 0
        assert result['timestamp'] is not None

    def test_no_long_signals_produces_no_orders(self, manager):
        signals = {
            'XLK': _signal(0.50, 'neutral'),
            'XLV': _signal(0.30, 'avoid'),
        }
        prices = _make_prices(['XLK', 'XLV'])
        result = manager.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000,
            prices_df=prices,
        )
        assert result['orders'] == []
        assert result['target_weights'] == {}

    def test_existing_positions_generate_sells(self, manager):
        signals = {
            'XLK': _signal(0.90),
        }
        prices = _make_prices(['XLK', 'XLE'])
        result = manager.run(
            signals=signals,
            current_positions={'XLE': 0.10},  # XLE should be sold
            portfolio_value=100_000,
            prices_df=prices,
        )
        sell_orders = [o for o in result['orders'] if o['side'] == 'sell']
        buy_orders = [o for o in result['orders'] if o['side'] == 'buy']
        assert len(sell_orders) >= 1
        assert len(buy_orders) >= 0


# ── regime integration ──────────────────────────────────────────────

class TestRegimeIntegration:
    def test_risk_off_increases_cash_buffer(self, manager):
        signals = {
            'XLK': _signal(0.90),
            'XLV': _signal(0.85),
        }
        prices = _make_prices(['XLK', 'XLV'])

        result = manager.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000,
            prices_df=prices,
            regime='risk_off',
        )
        # In risk_off, cash buffer goes to 20%
        total_weight = sum(result['target_weights'].values())
        assert total_weight <= 0.80 + 0.01  # 80% invested max

    def test_risk_off_caps_positions(self, manager):
        signals = {f'ETF{i}': _signal(0.90 - i * 0.01) for i in range(8)}
        prices = _make_prices([f'ETF{i}' for i in range(8)])

        result = manager.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000,
            prices_df=prices,
            regime='risk_off',
        )
        assert len(result['candidates']) <= 5


# ── exposure reporting ──────────────────────────────────────────────

class TestExposureReporting:
    def test_exposure_included_in_result(self, manager):
        signals = {
            'XLK': _signal(0.90),
            'XLV': _signal(0.85),
        }
        prices = _make_prices(['XLK', 'XLV', 'SPY'])
        result = manager.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000,
            prices_df=prices,
            sector_map=SECTOR_MAP,
        )
        assert result['exposure'] is not None
        assert 'sector_exposures' in result['exposure']
        assert 'concentration' in result['exposure']


# ── caching ─────────────────────────────────────────────────────────

class TestCaching:
    def test_caches_to_redis(self, redis, manager):
        signals = {'XLK': _signal(0.90), 'XLV': _signal(0.85)}
        prices = _make_prices(['XLK', 'XLV'])

        manager.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000,
            prices_df=prices,
        )
        cached = redis.get('cache:portfolio:latest')
        assert cached is not None
        data = json.loads(cached)
        assert 'target_weights' in data

    def test_pushes_orders_to_queue(self, redis, manager):
        signals = {'XLK': _signal(0.90)}
        prices = _make_prices(['XLK'])

        manager.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000,
            prices_df=prices,
        )
        msg = redis.rpop('channel:orders_proposed')
        assert msg is not None


# ── constraints ─────────────────────────────────────────────────────

class TestConstraintOverrides:
    def test_regime_override_creates_new_constraints(self):
        original = PortfolioConstraints(max_positions=10, cash_buffer_pct=0.05)
        overridden = PortfolioManager._apply_regime_overrides(original, 'risk_off')
        assert overridden.max_positions == 5
        assert overridden.cash_buffer_pct == 0.20
        # Original unchanged
        assert original.max_positions == 10
        assert original.cash_buffer_pct == 0.05

    def test_no_override_in_trending(self):
        original = PortfolioConstraints(max_positions=10, cash_buffer_pct=0.05)
        result = PortfolioManager._apply_regime_overrides(original, 'trending')
        assert result.max_positions == 10
        assert result.cash_buffer_pct == 0.05
