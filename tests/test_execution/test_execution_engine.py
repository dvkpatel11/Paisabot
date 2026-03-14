import json

import fakeredis
import pytest

from app.execution.execution_engine import ExecutionEngine


@pytest.fixture
def redis():
    r = fakeredis.FakeRedis()
    r.hset('config:system', 'operational_mode', 'live')
    return r


@pytest.fixture
def engine(mock_broker, redis):
    return ExecutionEngine(
        broker=mock_broker,
        redis_client=redis,
    )


# ── direct execution ──────────────────────────────────────────────

class TestDirectExecution:
    def test_execute_single_order(self, engine):
        orders = [{'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}]
        results = engine.execute_orders(orders)
        assert len(results) == 1
        assert results[0]['status'] == 'filled'

    def test_execute_multiple_orders(self, engine):
        orders = [
            {'symbol': 'XLE', 'side': 'sell', 'notional': 2000.0},
            {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0},
            {'symbol': 'QQQ', 'side': 'buy', 'notional': 3000.0},
        ]
        results = engine.execute_orders(orders)
        assert len(results) == 3
        assert all(r['status'] == 'filled' for r in results)


# ── queue processing ──────────────────────────────────────────────

class TestQueueProcessing:
    def test_empty_queue(self, engine):
        results = engine.process_approved_orders(timeout=1)
        assert results == []

    def test_process_from_queue(self, engine, redis):
        payload = json.dumps({
            'orders': [
                {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0},
            ],
        })
        redis.lpush('channel:orders_approved', payload)
        results = engine.process_approved_orders(timeout=1)
        assert len(results) == 1
        assert results[0]['status'] == 'filled'

    def test_caches_execution_state(self, engine, redis):
        orders = [{'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}]
        engine.execute_orders(orders)
        cached = redis.get('cache:execution:latest')
        assert cached is not None
        state = json.loads(cached)
        assert state['n_filled'] == 1
        assert 'XLK' in state['symbols']


# ── force liquidation ─────────────────────────────────────────────

class TestForceLiquidation:
    def test_liquidate_positions(self, engine, redis):
        positions = {'XLK': 0.10, 'QQQ': 0.15, 'XLE': 0.05}
        results = engine.force_liquidate(positions, portfolio_value=100_000)
        assert len(results) == 3
        assert all(r['side'] == 'sell' for r in results)
        # Kill switch should be cleared
        assert redis.get('kill_switch:force_liquidate') == b'0'

    def test_liquidate_empty(self, engine):
        results = engine.force_liquidate({}, portfolio_value=100_000)
        assert results == []

    def test_liquidate_zero_weight_skipped(self, engine):
        positions = {'XLK': 0.0, 'QQQ': 0.10}
        results = engine.force_liquidate(positions, portfolio_value=100_000)
        assert len(results) == 1
        assert results[0]['symbol'] == 'QQQ'


# ── execution state ───────────────────────────────────────────────

class TestExecutionState:
    def test_get_state_empty(self, engine):
        state = engine.get_execution_state()
        assert state is None

    def test_get_state_after_execution(self, engine, redis):
        engine.execute_orders([
            {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0},
        ])
        state = engine.get_execution_state()
        assert state is not None
        assert state['n_executed'] == 1

    def test_no_redis_returns_none(self, mock_broker):
        engine = ExecutionEngine(broker=mock_broker)
        assert engine.get_execution_state() is None


# ── reconciliation ────────────────────────────────────────────────

class TestReconciliation:
    def test_in_sync(self, mock_broker, redis):
        mock_broker.set_positions([
            {'symbol': 'XLK', 'qty': 50, 'market_value': 10_000, 'avg_entry_price': 200, 'unrealized_pl': 0},
        ])
        engine = ExecutionEngine(broker=mock_broker, redis_client=redis)
        result = engine.reconcile_positions(
            internal_positions={'XLK': 0.10},
            portfolio_value=100_000,
        )
        assert result['in_sync'] is True
        assert len(result['matched']) == 1

    def test_mismatch_detected(self, mock_broker, redis):
        mock_broker.set_positions([
            {'symbol': 'XLK', 'qty': 50, 'market_value': 20_000, 'avg_entry_price': 200, 'unrealized_pl': 0},
        ])
        engine = ExecutionEngine(broker=mock_broker, redis_client=redis)
        result = engine.reconcile_positions(
            internal_positions={'XLK': 0.10},  # 10% vs 20% at broker
            portfolio_value=100_000,
        )
        assert result['in_sync'] is False
        assert len(result['mismatched']) == 1

    def test_broker_only_positions(self, mock_broker, redis):
        mock_broker.set_positions([
            {'symbol': 'XLK', 'qty': 50, 'market_value': 10_000, 'avg_entry_price': 200, 'unrealized_pl': 0},
        ])
        engine = ExecutionEngine(broker=mock_broker, redis_client=redis)
        result = engine.reconcile_positions(
            internal_positions={},
            portfolio_value=100_000,
        )
        assert len(result['broker_only']) == 1
        assert result['in_sync'] is False

    def test_internal_only_positions(self, mock_broker, redis):
        mock_broker.set_positions([])
        engine = ExecutionEngine(broker=mock_broker, redis_client=redis)
        result = engine.reconcile_positions(
            internal_positions={'XLK': 0.10},
            portfolio_value=100_000,
        )
        assert len(result['internal_only']) == 1
        assert result['in_sync'] is False

    def test_no_broker_returns_empty(self, redis):
        engine = ExecutionEngine(broker=None, redis_client=redis)
        positions = engine.get_broker_positions()
        assert positions == []
