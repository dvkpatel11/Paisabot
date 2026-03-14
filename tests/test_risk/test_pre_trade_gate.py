import json

import fakeredis
import pytest

from app.risk.pre_trade_gate import PreTradeGate


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def gate(redis):
    return PreTradeGate(redis_client=redis)


def _order(symbol='XLK', side='buy', notional=2000.0):
    return {'symbol': symbol, 'side': side, 'notional': notional}


def _position(symbol='XLK', weight=0.03, sector='Technology', status='open'):
    return {'symbol': symbol, 'weight': weight, 'sector': sector, 'status': status}


# ── kill switches ───────────────────────────────────────────────────

class TestKillSwitches:
    def test_trading_kill_switch_blocks_all(self, redis, gate):
        redis.set('kill_switch:trading', '1')
        result = gate.evaluate([_order()], [], 100_000)
        assert result['approved_count'] == 0
        assert result['blocked_count'] == 1
        assert 'kill_switch:trading' in result['blocked'][0]['block_reason']

    def test_all_kill_switch_blocks(self, redis, gate):
        redis.set('kill_switch:all', '1')
        result = gate.evaluate([_order()], [], 100_000)
        assert result['blocked_count'] == 1

    def test_rebalance_kill_switch_blocks(self, redis, gate):
        redis.set('kill_switch:rebalance', '1')
        result = gate.evaluate([_order()], [], 100_000)
        assert result['blocked_count'] == 1

    def test_no_kill_switches_approves(self, gate):
        result = gate.evaluate([_order()], [], 100_000)
        assert result['approved_count'] == 1
        assert result['blocked_count'] == 0


# ── drawdown headroom ──────────────────────────────────────────────

class TestDrawdownHeadroom:
    def test_blocks_buys_near_drawdown_limit(self, gate):
        # Drawdown at -14% with -15% limit → 1% headroom < 2% → block buys
        result = gate.evaluate(
            [_order(side='buy'), _order(side='sell', symbol='XLE')],
            [], 100_000, current_drawdown=-0.14,
        )
        # Sells should still pass, buys blocked
        assert result['blocked_count'] == 1
        assert result['approved_count'] == 1
        assert result['approved'][0]['side'] == 'sell'

    def test_allows_when_headroom_ok(self, gate):
        result = gate.evaluate(
            [_order()], [], 100_000, current_drawdown=-0.05,
        )
        assert result['approved_count'] == 1


# ── position concentration ──────────────────────────────────────────

class TestPositionLimits:
    def test_blocks_exceeding_position_limit(self, gate):
        # Existing 4% position + 2% order = 6% > 5% limit
        positions = [_position('XLK', weight=0.04)]
        order = _order('XLK', 'buy', 2000)  # 2% of 100k
        result = gate.evaluate([order], positions, 100_000)
        assert result['blocked_count'] == 1
        assert 'position_limit' in result['blocked'][0]['block_reason']

    def test_allows_within_position_limit(self, gate):
        positions = [_position('XLK', weight=0.02)]
        order = _order('XLK', 'buy', 2000)  # 2% + 2% = 4% < 5%
        result = gate.evaluate([order], positions, 100_000)
        assert result['approved_count'] == 1


# ── sector concentration ────────────────────────────────────────────

class TestSectorLimits:
    def test_blocks_exceeding_sector_limit(self, gate):
        # 23% in Tech + 5% order = 28% > 25% limit
        positions = [
            _position('XLK', weight=0.12, sector='Technology'),
            _position('AAPL', weight=0.11, sector='Technology'),
        ]
        order = _order('MSFT', 'buy', 5000)  # 5% of 100k
        sector_map = {'XLK': 'Technology', 'AAPL': 'Technology', 'MSFT': 'Technology'}
        result = gate.evaluate([order], positions, 100_000, sector_map=sector_map)
        assert result['blocked_count'] == 1
        assert 'sector_limit' in result['blocked'][0]['block_reason']

    def test_allows_different_sector(self, gate):
        positions = [_position('XLK', weight=0.20, sector='Technology')]
        order = _order('XLV', 'buy', 3000)
        sector_map = {'XLK': 'Technology', 'XLV': 'Health Care'}
        result = gate.evaluate([order], positions, 100_000, sector_map=sector_map)
        assert result['approved_count'] == 1


# ── sells always allowed ────────────────────────────────────────────

class TestSellsAllowed:
    def test_sells_always_pass(self, gate):
        result = gate.evaluate(
            [_order('XLK', 'sell', 50000)], [], 100_000,
        )
        assert result['approved_count'] == 1


# ── short constraints ──────────────────────────────────────────────

class TestShortConstraints:
    def test_short_blocked_by_default(self, gate):
        result = gate.evaluate(
            [_order('XLK', 'short', 2000)], [], 100_000, regime='risk_off',
        )
        assert result['blocked_count'] == 1
        assert 'short_selling_disabled' in result['blocked'][0]['block_reason']

    def test_short_blocked_outside_risk_off(self, redis):
        # Without config_loader, _get_bool defaults to False, so shorts always
        # blocked. Test that the block reason indicates short_selling_disabled.
        gate = PreTradeGate(redis_client=redis)
        result = gate.evaluate(
            [_order('XLK', 'short', 2000)], [], 100_000, regime='trending',
        )
        assert result['blocked_count'] == 1
        assert 'short' in result['blocked'][0]['block_reason']


# ── liquidity shock ─────────────────────────────────────────────────

class TestLiquidityShock:
    def test_liquidity_shock_blocks_buy(self, redis, gate):
        redis.set('liquidity_shock:XLK', '1')
        result = gate.evaluate([_order('XLK')], [], 100_000)
        assert result['blocked_count'] == 1
        assert 'liquidity_shock' in result['blocked'][0]['block_reason']


# ── order size limits ───────────────────────────────────────────────

class TestOrderSizeLimits:
    def test_below_min_notional_blocked(self, gate):
        result = gate.evaluate([_order(notional=50)], [], 100_000)
        assert result['blocked_count'] == 1
        assert 'below_min_notional' in result['blocked'][0]['block_reason']

    def test_above_max_notional_blocked(self, gate):
        result = gate.evaluate([_order('XLK', 'buy', 60000)], [], 100_000)
        assert result['blocked_count'] == 1
        # Could be position_limit or above_max_notional depending on eval order
        reason = result['blocked'][0]['block_reason']
        assert 'max_notional' in reason or 'position_limit' in reason


# ── publishing ──────────────────────────────────────────────────────

class TestPreTradePublishing:
    def test_approved_orders_pushed_to_queue(self, redis, gate):
        gate.evaluate([_order()], [], 100_000)
        queued = redis.rpop('channel:orders_approved')
        assert queued is not None
        data = json.loads(queued)
        assert data['symbol'] == 'XLK'
        assert 'approved_at' in data

    def test_blocked_orders_logged_to_alerts(self, redis, gate):
        # Block via liquidity shock (per-order block, not global kill switch)
        # so _publish_decisions runs and logs the alert
        redis.set('liquidity_shock:XLK', '1')
        gate.evaluate([_order('XLK')], [], 100_000)
        alert = redis.rpop('channel:risk_alerts')
        assert alert is not None
        data = json.loads(alert)
        assert data['type'] == 'order_blocked'


# ── batch evaluation ────────────────────────────────────────────────

class TestBatchEvaluation:
    def test_mixed_batch(self, redis, gate):
        redis.set('liquidity_shock:XLE', '1')
        orders = [
            _order('XLK', 'buy', 2000),   # ok
            _order('XLE', 'buy', 2000),    # blocked — liquidity shock
            _order('XLV', 'sell', 3000),   # ok — sell always passes
        ]
        result = gate.evaluate(orders, [], 100_000)
        assert result['approved_count'] == 2
        assert result['blocked_count'] == 1
        assert result['blocked'][0]['symbol'] == 'XLE'
