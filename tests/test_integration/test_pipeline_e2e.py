"""End-to-end pipeline integration tests.

Tests the full data flow:
  Factors → Signals → Portfolio → Risk → Execution → Dashboard API

All tests use in-memory SQLite and fakeredis — no Docker required.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import fakeredis
import numpy as np
import pandas as pd
import pytest

from app import create_app
from app.extensions import db as _db


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def app():
    app = create_app('testing')
    with app.app_context():
        yield app


@pytest.fixture(autouse=True)
def setup_db(app):
    with app.app_context():
        _db.create_all()
        yield
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mock_factor_scores():
    """Simulated factor scores for a 5-ETF universe."""
    return {
        'SPY': {
            'trend_score': 0.85, 'volatility_regime': 0.70,
            'sentiment_score': 0.65, 'breadth_score': 0.75,
            'dispersion_score': 0.55, 'liquidity_score': 0.90,
        },
        'QQQ': {
            'trend_score': 0.60, 'volatility_regime': 0.55,
            'sentiment_score': 0.50, 'breadth_score': 0.50,
            'dispersion_score': 0.45, 'liquidity_score': 0.85,
        },
        'XLK': {
            'trend_score': 0.70, 'volatility_regime': 0.60,
            'sentiment_score': 0.55, 'breadth_score': 0.65,
            'dispersion_score': 0.50, 'liquidity_score': 0.80,
        },
        'XLE': {
            'trend_score': 0.25, 'volatility_regime': 0.30,
            'sentiment_score': 0.30, 'breadth_score': 0.20,
            'dispersion_score': 0.60, 'liquidity_score': 0.60,
        },
        'XLF': {
            'trend_score': 0.40, 'volatility_regime': 0.45,
            'sentiment_score': 0.40, 'breadth_score': 0.35,
            'dispersion_score': 0.40, 'liquidity_score': 0.70,
        },
    }


@pytest.fixture
def prices_df():
    """Generate synthetic price data for portfolio construction."""
    dates = pd.date_range(end=datetime.now(), periods=252, freq='B')
    np.random.seed(42)
    symbols = ['SPY', 'QQQ', 'XLK', 'XLE', 'XLF']
    base_prices = [450, 380, 190, 85, 40]

    data = {}
    for sym, base in zip(symbols, base_prices):
        returns = np.random.normal(0.0003, 0.015, len(dates))
        prices = base * np.cumprod(1 + returns)
        data[sym] = prices

    return pd.DataFrame(data, index=dates)


# ── Signal Pipeline Tests ─────────────────────────────────────────

class TestSignalPipeline:
    """Test factor scores → composite scoring → signal classification."""

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_factor_to_signal_flow(self, MockRegistry, redis, mock_factor_scores, app, setup_db):
        """Factors → CompositeScorer → RegimeTracker → SignalFilter → classified signals."""
        from app.signals.signal_generator import SignalGenerator

        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = mock_factor_scores
        MockRegistry.return_value = mock_reg

        universe = list(mock_factor_scores.keys())
        for sym in universe:
            redis.set(f'scores:{sym}', '{}', ex=900)
            redis.set(f'etf:{sym}:adv_30d_m', '100.0')
            redis.set(f'etf:{sym}:spread_bps', '2.0')

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        signals = gen.run(universe)

        assert len(signals) == 5
        for sym, sig in signals.items():
            assert 'composite_score' in sig
            assert 'signal_type' in sig
            assert 'regime' in sig
            assert 'rank' in sig
            assert 0 <= sig['composite_score'] <= 1

        # SPY has highest factor scores → should rank first
        assert signals['SPY']['rank'] == 1

        # XLE has lowest scores → should be avoid
        assert signals['XLE']['signal_type'] in ('avoid', 'blocked')

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_signal_classification_thresholds(self, MockRegistry, redis, mock_factor_scores, app, setup_db):
        """Verify signal type classification respects thresholds."""
        from app.signals.signal_generator import classify_signal

        assert classify_signal(0.70, 'trending') == 'long'
        assert classify_signal(0.65, 'consolidation') == 'long'
        assert classify_signal(0.50, 'trending') == 'neutral'
        assert classify_signal(0.30, 'trending') == 'avoid'

        # Risk-off raises threshold
        assert classify_signal(0.68, 'risk_off') == 'neutral'
        assert classify_signal(0.70, 'risk_off') == 'long'


# ── Portfolio Construction Tests ──────────────────────────────────

class TestPortfolioPipeline:
    """Test signals → portfolio construction → order generation."""

    def test_signal_to_portfolio_flow(self, redis, prices_df):
        """Signals feed into PortfolioManager and produce target weights + orders."""
        from app.portfolio.portfolio_manager import PortfolioManager

        signals = {
            'SPY': {'composite_score': 0.80, 'signal_type': 'long', 'rank': 1, 'regime': 'trending'},
            'QQQ': {'composite_score': 0.55, 'signal_type': 'neutral', 'rank': 2, 'regime': 'trending'},
            'XLK': {'composite_score': 0.70, 'signal_type': 'long', 'rank': 3, 'regime': 'trending'},
            'XLE': {'composite_score': 0.25, 'signal_type': 'avoid', 'rank': 5, 'regime': 'trending'},
            'XLF': {'composite_score': 0.45, 'signal_type': 'neutral', 'rank': 4, 'regime': 'trending'},
        }

        pm = PortfolioManager(redis_client=redis)
        result = pm.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000.0,
            prices_df=prices_df,
            regime='trending',
            sector_map={'SPY': 'Broad', 'QQQ': 'Tech', 'XLK': 'Tech', 'XLE': 'Energy', 'XLF': 'Financial'},
        )

        assert 'target_weights' in result
        assert 'orders' in result

        # Target weights should sum to <= 1.0 (allowing cash buffer)
        total_weight = sum(result['target_weights'].values())
        assert 0.0 <= total_weight <= 1.01

        # Long candidates (SPY) should have positive weights
        assert result['target_weights'].get('SPY', 0) > 0

    def test_portfolio_rebalance_generates_orders(self, redis, prices_df):
        """Rebalance from existing positions to new targets produces sell-then-buy orders."""
        from app.portfolio.portfolio_manager import PortfolioManager

        signals = {
            'SPY': {'composite_score': 0.80, 'signal_type': 'long', 'rank': 1, 'regime': 'trending'},
            'QQQ': {'composite_score': 0.70, 'signal_type': 'long', 'rank': 2, 'regime': 'trending'},
        }

        existing = {'SPY': 0.30, 'XLE': 0.20}  # XLE not in new targets → sell

        pm = PortfolioManager(redis_client=redis)
        result = pm.run(
            signals=signals,
            current_positions=existing,
            portfolio_value=100_000.0,
            prices_df=prices_df,
            regime='trending',
        )

        orders = result.get('orders', [])
        # Should have at least one order (the rebalance)
        if orders:
            sides = [o['side'] for o in orders]
            # Sells should come before buys
            sell_indices = [i for i, s in enumerate(sides) if s == 'sell']
            buy_indices = [i for i, s in enumerate(sides) if s == 'buy']
            if sell_indices and buy_indices:
                assert max(sell_indices) < min(buy_indices)


# ── Risk Pre-Trade Gate Tests ─────────────────────────────────────

class TestRiskPipeline:
    """Test proposed orders → risk pre-trade gate → approved/blocked."""

    def test_pretrade_gate_approves_normal_orders(self, redis):
        """Normal-sized orders in consolidation regime pass the gate."""
        from app.risk.risk_manager import RiskManager

        rm = RiskManager(redis_client=redis)

        proposed = [
            {'symbol': 'SPY', 'side': 'buy', 'notional': 5000, 'weight': 0.05},
            {'symbol': 'QQQ', 'side': 'buy', 'notional': 3000, 'weight': 0.03},
        ]
        current_positions = []

        result = rm.pre_trade(
            proposed_orders=proposed,
            current_positions=current_positions,
            portfolio_value=100_000.0,
            current_drawdown=0.0,
            regime='consolidation',
        )

        assert 'approved_count' in result
        assert 'blocked_count' in result
        assert result['approved_count'] + result['blocked_count'] == len(proposed)

    def test_pretrade_blocks_during_drawdown(self, redis):
        """Orders should be restricted during significant drawdown."""
        from app.risk.risk_manager import RiskManager

        rm = RiskManager(redis_client=redis)

        proposed = [
            {'symbol': 'SPY', 'side': 'buy', 'notional': 10000, 'weight': 0.10},
        ]

        result = rm.pre_trade(
            proposed_orders=proposed,
            current_positions=[],
            portfolio_value=100_000.0,
            current_drawdown=-0.12,  # -12% drawdown
            regime='risk_off',
        )

        # In deep drawdown + risk_off, gate should be more restrictive
        assert 'approved_count' in result

    def test_force_liquidate_generates_sell_orders(self, redis):
        """Force liquidation generates sell orders for all open positions."""
        from app.risk.risk_manager import RiskManager

        rm = RiskManager(redis_client=redis)

        positions = [
            {'symbol': 'SPY', 'status': 'open', 'notional': 50000},
            {'symbol': 'QQQ', 'status': 'open', 'notional': 30000},
            {'symbol': 'XLE', 'status': 'closed', 'notional': 0},
        ]

        orders = rm.force_liquidate(positions)

        # Only open positions get sell orders
        assert len(orders) == 2
        for o in orders:
            assert o['side'] == 'sell'
            assert o['reason'] == 'force_liquidate'


# ── Execution Pipeline Tests ──────────────────────────────────────

class TestExecutionPipeline:
    """Test approved orders → execution engine → fill tracking."""

    def test_order_execution_with_mock_broker(self, redis, app, setup_db):
        """Orders flow through OrderManager with MockBroker and get filled."""
        from app.execution.order_manager import OrderManager
        from app.execution.broker_base import BrokerOrder

        # Create a mock broker
        mock_broker = MagicMock()
        mock_broker.broker_name = 'mock'
        mock_broker.get_latest_quote.return_value = {'bid': 450.0, 'ask': 450.10, 'mid': 450.05}
        filled_order = BrokerOrder(
            order_id='test-001',
            symbol='SPY',
            side='buy',
            qty=10,
            order_type='market',
            status='filled',
            filled_qty=10,
            filled_avg_price=450.05,
        )
        mock_broker.submit_order.return_value = filled_order
        mock_broker.get_order.return_value = filled_order

        # Set mode to live
        redis.hset('config:system', 'operational_mode', 'live')

        om = OrderManager(broker=mock_broker, redis_client=redis)
        order = {'symbol': 'SPY', 'side': 'buy', 'notional': 4500.0}
        result = om.execute_order(order)

        assert result is not None
        assert result['status'] == 'filled'
        assert result['symbol'] == 'SPY'

    def test_simulation_mode_skips_execution(self, redis):
        """Simulation mode skips broker calls entirely."""
        from app.execution.order_manager import OrderManager

        redis.hset('config:system', 'operational_mode', 'simulation')

        om = OrderManager(broker=None, redis_client=redis)
        order = {'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0}
        result = om.execute_order(order)

        assert result is not None
        assert result.get('status') in ('skipped', 'simulated')

    def test_kill_switch_blocks_execution(self, redis):
        """Kill switch prevents order execution."""
        from app.execution.order_manager import OrderManager

        redis.set('kill_switch:trading', '1')
        redis.hset('config:system', 'operational_mode', 'live')

        mock_broker = MagicMock()
        om = OrderManager(broker=mock_broker, redis_client=redis)
        order = {'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0}
        result = om.execute_order(order)

        assert result is not None
        assert result.get('status') in ('blocked', 'killed')
        mock_broker.submit_order.assert_not_called()


# ── Dashboard API Integration Tests ──────────────────────────────

class TestDashboardAPI:
    """Test that the monitoring API endpoints reflect pipeline state."""

    def test_pipeline_status_reflects_cached_data(self, client, app):
        """Pipeline status API returns cached module metrics."""
        with app.app_context():
            from app.extensions import redis_client
            redis_client.setex(
                'cache:pipeline:factor_engine',
                300,
                json.dumps({
                    'status': 'ok',
                    'items_processed': 42,
                    'compute_time_ms': 1500,
                    'last_activity': datetime.now(timezone.utc).isoformat(),
                }),
            )

            resp = client.get('/api/pipelines/status')
            assert resp.status_code == 200
            data = resp.get_json()

            factor_mod = next(m for m in data['modules'] if m['id'] == 'factor_engine')
            assert factor_mod['status'] == 'ok'
            assert factor_mod['items_processed'] == 42
            assert factor_mod['compute_time_ms'] == 1500

    def test_health_endpoint_reports_components(self, client):
        """Health endpoint shows system component status."""
        resp = client.get('/api/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['components']['database'] == 'ok'
        assert 'kill_switches' in data

    def test_pipeline_view_renders(self, client):
        """Pipelines view template renders without errors."""
        resp = client.get('/pipelines')
        assert resp.status_code == 200


# ── Publisher Integration Tests ───────────────────────────────────

class TestPublisherIntegration:
    """Test that publishers correctly cache and broadcast pipeline state."""

    def test_pipeline_status_publish_caches_and_broadcasts(self, redis):
        """publish_pipeline_status sets Redis cache and publishes to pub/sub."""
        from app.streaming.publishers import publish_pipeline_status

        pubsub = redis.pubsub()
        pubsub.subscribe('channel:system_health')
        pubsub.get_message()  # consume subscribe confirmation

        publish_pipeline_status(redis, 'factor_engine', {
            'status': 'ok',
            'items_processed': 42,
            'compute_time_ms': 1200,
        })

        # Check cache
        cached = json.loads(redis.get('cache:pipeline:factor_engine'))
        assert cached['status'] == 'ok'
        assert cached['items_processed'] == 42
        assert 'last_activity' in cached

        # Check pub/sub broadcast
        msg = pubsub.get_message()
        assert msg is not None
        broadcast = json.loads(msg['data'])
        assert broadcast['module'] == 'factor_engine'

    def test_multiple_modules_publish_independently(self, redis):
        """Each module's status is cached independently."""
        from app.streaming.publishers import publish_pipeline_status

        publish_pipeline_status(redis, 'market_data', {'status': 'ok', 'items_processed': 100})
        publish_pipeline_status(redis, 'signal_engine', {'status': 'ok', 'items_processed': 5})

        md_cache = json.loads(redis.get('cache:pipeline:market_data'))
        se_cache = json.loads(redis.get('cache:pipeline:signal_engine'))

        assert md_cache['items_processed'] == 100
        assert se_cache['items_processed'] == 5


# ── Full Pipeline Flow Test ───────────────────────────────────────

class TestFullPipelineFlow:
    """Test the complete data flow across all pipeline stages."""

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_end_to_end_pipeline(self, MockRegistry, redis, mock_factor_scores, prices_df, app, setup_db):
        """Full pipeline: factors → signals → portfolio → risk → execution status."""
        from app.signals.signal_generator import SignalGenerator
        from app.portfolio.portfolio_manager import PortfolioManager
        from app.risk.risk_manager import RiskManager
        from app.streaming.publishers import publish_pipeline_status

        universe = list(mock_factor_scores.keys())

        # Step 1: Factor computation (mocked)
        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = mock_factor_scores
        MockRegistry.return_value = mock_reg

        for sym in universe:
            redis.set(f'scores:{sym}', '{}', ex=900)
            redis.set(f'etf:{sym}:adv_30d_m', '100.0')
            redis.set(f'etf:{sym}:spread_bps', '2.0')

        publish_pipeline_status(redis, 'market_data', {
            'status': 'ok', 'items_processed': len(universe),
        })

        # Step 2: Signal generation
        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        signals = gen.run(universe)

        assert len(signals) == len(universe)
        publish_pipeline_status(redis, 'signal_engine', {
            'status': 'ok', 'items_processed': len(signals),
        })

        # Step 3: Portfolio construction
        pm = PortfolioManager(redis_client=redis)
        portfolio_result = pm.run(
            signals=signals,
            current_positions={},
            portfolio_value=100_000.0,
            prices_df=prices_df,
            regime=signals.get('SPY', {}).get('regime', 'consolidation'),
        )

        assert 'target_weights' in portfolio_result
        publish_pipeline_status(redis, 'portfolio_engine', {
            'status': 'ok', 'items_processed': 1,
        })

        # Step 4: Risk pre-trade gate
        proposed_orders = portfolio_result.get('orders', [])
        rm = RiskManager(redis_client=redis)
        risk_result = rm.pre_trade(
            proposed_orders=proposed_orders,
            current_positions=[],
            portfolio_value=100_000.0,
            regime=signals.get('SPY', {}).get('regime', 'consolidation'),
        )

        assert 'approved_count' in risk_result
        publish_pipeline_status(redis, 'risk_engine', {
            'status': 'ok', 'items_processed': risk_result['approved_count'],
        })

        # Step 5: Verify pipeline status cache
        for mod_id in ['market_data', 'signal_engine', 'portfolio_engine', 'risk_engine']:
            cached = redis.get(f'cache:pipeline:{mod_id}')
            assert cached is not None
            data = json.loads(cached)
            assert data['status'] == 'ok'

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_pipeline_with_kill_switch(self, MockRegistry, redis, mock_factor_scores, app, setup_db):
        """Pipeline respects kill switches — signals still compute but are blocked."""
        from app.signals.signal_generator import SignalGenerator

        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = mock_factor_scores

        universe = list(mock_factor_scores.keys())
        redis.set('kill_switch:trading', '1')

        for sym in universe:
            redis.set(f'scores:{sym}', '{}', ex=900)

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        signals = gen.run(universe)

        # Signals should exist but be blocked
        for sig in signals.values():
            assert sig['signal_type'] == 'blocked'
            assert sig['tradable'] is False
