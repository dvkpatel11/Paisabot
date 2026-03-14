from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class BrokerOrder:
    """Standardized order representation across brokers."""

    order_id: str
    symbol: str
    side: str           # 'buy' | 'sell'
    qty: float
    order_type: str     # 'market' | 'limit'
    status: str         # 'pending' | 'filled' | 'cancelled' | 'expired' | 'rejected'
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    filled_at: str | None = None
    limit_price: float | None = None
    time_in_force: str = 'day'


@dataclass
class BrokerAccount:
    """Standardized account info."""

    equity: float
    buying_power: float
    cash: float
    currency: str = 'USD'


class BrokerBase(abc.ABC):
    """Abstract broker interface.

    All broker implementations (Alpaca, MT5, simulated) must implement
    these methods so the execution engine is broker-agnostic.
    """

    @abc.abstractmethod
    def connect(self) -> bool:
        """Establish connection to the broker. Returns True on success."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Clean up broker connection."""

    @abc.abstractmethod
    def get_account(self) -> BrokerAccount:
        """Fetch current account info."""

    @abc.abstractmethod
    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
        limit_price: float | None = None,
    ) -> BrokerOrder:
        """Submit an order. Returns the broker's order object."""

    @abc.abstractmethod
    def get_order(self, order_id: str) -> BrokerOrder:
        """Get current status of an order by ID."""

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancellation succeeded."""

    @abc.abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        """Get latest bid/ask/mid for a symbol.

        Returns dict with keys: bid, ask, mid, timestamp.
        """

    @abc.abstractmethod
    def get_positions(self) -> list[dict]:
        """Get all open positions from the broker.

        Returns list of dicts with: symbol, qty, market_value, avg_entry_price.
        """

    @property
    @abc.abstractmethod
    def broker_name(self) -> str:
        """Identifier string, e.g. 'alpaca_paper', 'alpaca_live', 'mt5'."""
