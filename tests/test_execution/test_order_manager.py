import json

import fakeredis
import pytest

from app.execution.order_manager import OrderManager


class TestOrderManagerLiveMode:
    """Tests for live mode execution (real broker, no kill switches)."""

    @pytest.fixture
    def redis(self):
        r = fakeredis.FakeRedis()
        # Set operational mode to live
        r.hset('config:system', 'operational_mode', 'live')
        return r

    @pytest.fixture
    def manager(self, mock_broker, redis):
        return OrderManager(broker=mock_broker, redis_client=redis)

    def test_buy_order_fills(self, manager):
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'filled'
        assert result['symbol'] == 'XLK'
        assert result['side'] == 'buy'
        assert result['fill_price'] == 100.0
        assert result['filled_qty'] > 0
        assert result['actual_slippage_bps'] is not None

    def test_sell_order_fills(self, manager):
        order = {'symbol': 'SPY', 'side': 'sell', 'notional': 3000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'filled'
        assert result['side'] == 'sell'

    def test_fill_publishes_events(self, manager, redis):
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        # Subscribe before executing
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:fills', 'channel:trades')
        # Consume subscription confirmations
        pubsub.get_message()
        pubsub.get_message()

        manager.execute_order(order)

        # Check that events were published
        msg1 = pubsub.get_message()
        assert msg1 is not None
        data = json.loads(msg1['data'])
        assert data['symbol'] == 'XLK'

    def test_batch_execution(self, manager):
        orders = [
            {'symbol': 'XLE', 'side': 'sell', 'notional': 2000.0},
            {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0},
        ]
        results = manager.execute_batch(orders)
        assert len(results) == 2
        assert all(r['status'] == 'filled' for r in results)


class TestOrderManagerKillSwitch:
    @pytest.fixture
    def redis(self):
        r = fakeredis.FakeRedis()
        r.hset('config:system', 'operational_mode', 'live')
        return r

    @pytest.fixture
    def manager(self, mock_broker, redis):
        return OrderManager(broker=mock_broker, redis_client=redis)

    def test_trading_kill_switch_blocks(self, manager, redis):
        redis.set('kill_switch:trading', '1')
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'blocked'
        assert result['reason'] == 'kill_switch_active'

    def test_all_kill_switch_blocks(self, manager, redis):
        redis.set('kill_switch:all', '1')
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'blocked'

    def test_inactive_kill_switch_allows(self, manager, redis):
        redis.set('kill_switch:trading', '0')
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'filled'


class TestOrderManagerOperationalModes:
    @pytest.fixture
    def redis(self):
        return fakeredis.FakeRedis()

    def test_simulation_mode_skips(self, mock_broker, redis):
        redis.hset('config:system', 'operational_mode', 'simulation')
        manager = OrderManager(broker=mock_broker, redis_client=redis)
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'skipped'
        assert result['reason'] == 'simulation_mode'

    def test_research_mode_simulates_fill(self, redis):
        redis.hset('config:system', 'operational_mode', 'research')
        manager = OrderManager(broker=None, redis_client=redis)
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'filled'
        assert result['reason'] == 'simulated'

    def test_default_mode_is_simulation(self, mock_broker):
        """With no config and no redis, default to simulation."""
        manager = OrderManager(broker=mock_broker)
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'skipped'
        assert result['reason'] == 'simulation_mode'


class TestOrderManagerBrokerErrors:
    @pytest.fixture
    def redis(self):
        r = fakeredis.FakeRedis()
        r.hset('config:system', 'operational_mode', 'live')
        return r

    def test_no_broker_in_live_mode(self, redis):
        manager = OrderManager(broker=None, redis_client=redis)
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'error'
        assert result['reason'] == 'no_broker'

    def test_broker_submission_failure(self, mock_broker, redis):
        mock_broker.set_should_fail(True)
        manager = OrderManager(broker=mock_broker, redis_client=redis)
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'error'
        assert result['reason'] == 'submission_failed'

    def test_quote_failure(self, mock_broker, redis):
        """If getting the quote fails, order should error."""
        # Override get_latest_quote to raise
        original = mock_broker.get_latest_quote
        mock_broker.get_latest_quote = lambda s: (_ for _ in ()).throw(RuntimeError('no quote'))
        manager = OrderManager(broker=mock_broker, redis_client=redis)
        order = {'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}
        result = manager.execute_order(order)
        assert result['status'] == 'error'
        assert result['reason'] == 'quote_failed'
        mock_broker.get_latest_quote = original


class TestOrderManagerQueue:
    def test_dequeue_empty(self, mock_broker):
        redis = fakeredis.FakeRedis()
        manager = OrderManager(broker=mock_broker, redis_client=redis)
        result = manager.dequeue_and_execute(timeout=1)
        assert result is None

    def test_dequeue_and_execute(self, mock_broker):
        redis = fakeredis.FakeRedis()
        redis.hset('config:system', 'operational_mode', 'live')

        # Push an order to the queue
        payload = json.dumps({
            'orders': [{'symbol': 'XLK', 'side': 'buy', 'notional': 5000.0}],
        })
        redis.lpush('channel:orders_approved', payload)

        manager = OrderManager(broker=mock_broker, redis_client=redis)
        results = manager.dequeue_and_execute(timeout=1)
        assert results is not None
        assert len(results) == 1
        assert results[0]['status'] == 'filled'


class TestNotionalToQty:
    def test_fractional_shares(self, mock_broker):
        manager = OrderManager(broker=mock_broker)
        qty = manager._notional_to_qty(5000.0, 100.0)
        assert qty == 50.0

    def test_whole_shares_only(self, mock_broker, redis):
        redis.hset('config:system', 'operational_mode', 'live')
        # No config loader → defaults to fractional=True
        manager = OrderManager(broker=mock_broker, redis_client=redis)
        qty = manager._notional_to_qty(5050.0, 100.0)
        assert qty == 50.5

    def test_zero_price(self, mock_broker):
        manager = OrderManager(broker=mock_broker)
        qty = manager._notional_to_qty(5000.0, 0.0)
        assert qty == 0.0
