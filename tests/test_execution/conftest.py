"""Shared fixtures for execution engine tests."""
import uuid

import fakeredis
import pytest

from app.execution.broker_base import BrokerAccount, BrokerBase, BrokerOrder


class MockBroker(BrokerBase):
    """In-memory broker for unit testing.

    All orders fill immediately at the configured price.
    """

    def __init__(self, fill_price=100.0, should_fail=False):
        self._fill_price = fill_price
        self._should_fail = should_fail
        self._connected = False
        self._orders: dict[str, BrokerOrder] = {}
        self._positions: list[dict] = []
        self._quotes: dict[str, dict] = {}

    def connect(self) -> bool:
        if self._should_fail:
            return False
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            equity=100_000.0,
            buying_power=50_000.0,
            cash=30_000.0,
        )

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
        limit_price: float | None = None,
    ) -> BrokerOrder:
        if self._should_fail:
            raise RuntimeError('broker_submission_error')

        order_id = str(uuid.uuid4())
        order = BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            status='filled',
            filled_qty=qty,
            filled_avg_price=self._fill_price,
            filled_at='2026-03-14T14:30:00Z',
            limit_price=limit_price,
            time_in_force=time_in_force,
        )
        self._orders[order_id] = order
        return order

    def get_order(self, order_id: str) -> BrokerOrder:
        return self._orders[order_id]

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = 'cancelled'
            return True
        return False

    def get_latest_quote(self, symbol: str) -> dict:
        if symbol in self._quotes:
            return self._quotes[symbol]
        p = self._fill_price
        return {
            'bid': p * 0.999,
            'ask': p * 1.001,
            'mid': p,
            'timestamp': '2026-03-14T14:30:00Z',
        }

    def get_positions(self) -> list[dict]:
        return self._positions

    @property
    def broker_name(self) -> str:
        return 'mock_paper'

    # ── test helpers ───────────────────────────────────────────────

    def set_fill_price(self, price: float):
        self._fill_price = price

    def set_quotes(self, quotes: dict[str, dict]):
        self._quotes = quotes

    def set_positions(self, positions: list[dict]):
        self._positions = positions

    def set_should_fail(self, fail: bool):
        self._should_fail = fail

    def make_pending_order(self, symbol='XLK', side='buy', qty=10.0):
        """Create an order that stays in 'pending' status (for fill_monitor timeout tests)."""
        order_id = str(uuid.uuid4())
        order = BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            order_type='market',
            status='pending',
        )
        self._orders[order_id] = order
        return order


@pytest.fixture
def mock_broker():
    return MockBroker(fill_price=100.0)


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()
