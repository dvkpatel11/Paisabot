"""Tests for the SimulationTracker service."""
import json

import fakeredis
import pytest

from app.simulation.simulation_tracker import (
    SimulatedPosition,
    SimulationSnapshot,
    SimulationState,
    SimulationTracker,
)


# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def redis():
    r = fakeredis.FakeRedis()
    # Seed mid prices for cost model
    r.hset('cache:mid_prices', 'SPY', '515.0')
    r.hset('cache:mid_prices', 'QQQ', '414.0')
    r.hset('cache:mid_prices', 'XLE', '94.0')
    return r


@pytest.fixture
def tracker(redis):
    return SimulationTracker(redis_client=redis)


# ── session tests ─────────────────────────────────────────────────


class TestSessionManagement:
    def test_create_session(self, tracker):
        state = tracker.create_session(initial_capital=50_000)
        assert state.session_id.startswith('sim_')
        assert state.initial_capital == 50_000
        assert state.cash == 50_000
        assert len(state.positions) == 0

    def test_load_session(self, tracker, redis):
        state = tracker.create_session(initial_capital=100_000)
        loaded = tracker.load_session(state.session_id)
        assert loaded is not None
        assert loaded.session_id == state.session_id
        assert loaded.cash == 100_000

    def test_get_active_session(self, tracker, redis):
        state = tracker.create_session()
        active = tracker.get_active_session()
        assert active is not None
        assert active.session_id == state.session_id

    def test_load_nonexistent_session(self, tracker):
        result = tracker.load_session('nonexistent')
        assert result is None


# ── order execution tests ─────────────────────────────────────────


class TestSimulatedExecution:
    def test_buy_order(self, tracker, redis):
        state = tracker.create_session(initial_capital=100_000)
        result = tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
        })

        assert result['status'] == 'filled'
        assert result['operational_mode'] == 'simulation'
        assert result['fill_price'] > 515.0  # buy fills above mid
        assert result['filled_qty'] > 0
        assert 'cost_breakdown' in result
        assert result['cost_breakdown']['total_bps'] > 0

        # Position should exist
        assert 'SPY' in state.positions
        assert state.cash < 100_000

    def test_sell_order(self, tracker, redis):
        state = tracker.create_session(initial_capital=100_000)

        # First buy
        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
        })

        # Then sell
        result = tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'sell', 'notional': 5000.0,
        })

        assert result['status'] == 'filled'
        assert result['fill_price'] < 515.0  # sell fills below mid
        # Position should be closed
        assert 'SPY' not in state.positions
        assert state.realized_pnl != 0  # should have some PnL (likely negative from costs)

    def test_insufficient_cash(self, tracker, redis):
        state = tracker.create_session(initial_capital=1000)
        result = tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 50_000.0,
        })
        assert result['status'] == 'error'
        assert result['reason'] == 'insufficient_cash'

    def test_no_price_errors(self, tracker):
        """No mid price available → error."""
        redis_empty = fakeredis.FakeRedis()
        t = SimulationTracker(redis_client=redis_empty)
        state = t.create_session()
        result = t.execute_order(state, {
            'symbol': 'UNKNOWN', 'side': 'buy', 'notional': 1000.0,
        })
        assert result['status'] == 'error'
        assert result['reason'] == 'no_price'

    def test_batch_execution(self, tracker, redis):
        state = tracker.create_session(initial_capital=100_000)
        orders = [
            {'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0},
            {'symbol': 'QQQ', 'side': 'buy', 'notional': 4000.0},
        ]
        results = tracker.execute_batch(state, orders)
        assert len(results) == 2
        assert all(r['status'] == 'filled' for r in results)
        assert len(state.positions) == 2

    def test_batch_sells_first(self, tracker, redis):
        """Sells should execute before buys in batch."""
        state = tracker.create_session(initial_capital=100_000)
        # Buy SPY first
        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
        })

        orders = [
            {'symbol': 'QQQ', 'side': 'buy', 'notional': 4000.0},
            {'symbol': 'SPY', 'side': 'sell', 'notional': 5000.0},
        ]
        results = tracker.execute_batch(state, orders)
        # Sell should come first despite being second in input
        assert results[0]['side'] == 'sell'
        assert results[1]['side'] == 'buy'


# ── position tracking tests ──────────────────────────────────────


class TestPositionTracking:
    def test_position_averaging(self, tracker, redis):
        """Buying more of same symbol averages the position."""
        state = tracker.create_session(initial_capital=100_000)
        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
        })
        qty1 = state.positions['SPY'].quantity

        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
        })
        qty2 = state.positions['SPY'].quantity

        assert qty2 > qty1  # more shares after second buy

    def test_partial_sell(self, tracker, redis):
        """Selling less than full position reduces quantity."""
        state = tracker.create_session(initial_capital=100_000)
        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 10_000.0,
        })
        full_qty = state.positions['SPY'].quantity

        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'sell', 'notional': 3000.0,
        })

        assert 'SPY' in state.positions
        assert state.positions['SPY'].quantity < full_qty

    def test_mark_to_market(self, tracker, redis):
        state = tracker.create_session(initial_capital=100_000)
        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 10_000.0,
        })

        state = tracker.mark_to_market(state)
        assert state.positions['SPY'].current_price is not None


# ── equity curve tests ────────────────────────────────────────────


class TestEquityCurve:
    def test_equity_curve_grows(self, tracker, redis):
        state = tracker.create_session(initial_capital=100_000)
        assert len(state.equity_curve) == 0

        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
        })
        assert len(state.equity_curve) == 1
        assert state.equity_curve[0]['portfolio_value'] > 0

    def test_portfolio_value_reflects_positions(self, tracker, redis):
        state = tracker.create_session(initial_capital=100_000)
        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 50_000.0,
        })

        # Portfolio value should be close to initial (minus transaction costs)
        assert 99_000 < state.portfolio_value < 100_001


# ── data model tests ──────────────────────────────────────────────


class TestDataModels:
    def test_simulated_position_mark_to_market(self):
        pos = SimulatedPosition(
            symbol='SPY', side='long', entry_price=500.0,
            quantity=10.0, notional=5000.0, entry_time='2026-01-01',
        )
        pnl = pos.mark_to_market(510.0)
        assert pnl == 100.0  # (510-500)*10
        assert pos.current_price == 510.0

    def test_simulated_position_short_mtm(self):
        pos = SimulatedPosition(
            symbol='SPY', side='short', entry_price=500.0,
            quantity=10.0, notional=5000.0, entry_time='2026-01-01',
        )
        pnl = pos.mark_to_market(490.0)
        assert pnl == 100.0  # (500-490)*10

    def test_simulation_state_properties(self):
        state = SimulationState(
            session_id='test', initial_capital=100_000,
            cash=90_000, started_at='2026-01-01',
        )
        state.positions['SPY'] = SimulatedPosition(
            symbol='SPY', side='long', entry_price=500.0,
            quantity=20.0, notional=10_000.0,
            entry_time='2026-01-01', current_price=510.0,
            unrealized_pnl=200.0,
        )
        assert state.portfolio_value == 90_000 + 510.0 * 20.0
        assert state.unrealized_pnl == 200.0

    def test_simulation_state_to_dict(self):
        state = SimulationState(
            session_id='test', initial_capital=100_000,
            cash=100_000, started_at='2026-01-01',
        )
        d = state.to_dict()
        assert d['session_id'] == 'test'
        assert d['cash'] == 100_000
        assert 'portfolio_value' in d


# ── publishing tests ──────────────────────────────────────────────


class TestPublishing:
    def test_publishes_fill_event(self, tracker, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:simulation')
        pubsub.get_message()  # sub confirmation

        state = tracker.create_session()
        tracker.execute_order(state, {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
        })

        msg = pubsub.get_message()
        assert msg is not None
        data = json.loads(msg['data'])
        assert data['symbol'] == 'SPY'
        assert data['status'] == 'filled'
