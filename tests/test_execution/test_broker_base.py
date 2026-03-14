import pytest

from app.execution.broker_base import BrokerAccount, BrokerBase, BrokerOrder


class TestBrokerOrder:
    def test_defaults(self):
        order = BrokerOrder(
            order_id='abc',
            symbol='SPY',
            side='buy',
            qty=10.0,
            order_type='market',
            status='pending',
        )
        assert order.filled_qty == 0.0
        assert order.filled_avg_price is None
        assert order.filled_at is None
        assert order.limit_price is None
        assert order.time_in_force == 'day'

    def test_filled_order(self):
        order = BrokerOrder(
            order_id='xyz',
            symbol='QQQ',
            side='sell',
            qty=5.0,
            order_type='limit',
            status='filled',
            filled_qty=5.0,
            filled_avg_price=450.25,
            filled_at='2026-03-14T14:30:00Z',
            limit_price=450.00,
        )
        assert order.status == 'filled'
        assert order.filled_avg_price == 450.25


class TestBrokerAccount:
    def test_defaults(self):
        acct = BrokerAccount(equity=100_000, buying_power=50_000, cash=30_000)
        assert acct.currency == 'USD'


class TestBrokerBaseIsAbstract:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BrokerBase()
