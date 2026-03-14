from __future__ import annotations

import time

import structlog

from app.execution.broker_base import BrokerBase, BrokerOrder

logger = structlog.get_logger()


class FillMonitor:
    """Polls broker for order fill status.

    Alpaca doesn't push fills; we poll order status at a configurable
    interval until filled, cancelled, expired, or timeout.
    """

    TERMINAL_STATUSES = frozenset({'filled', 'cancelled', 'expired', 'rejected'})

    def __init__(
        self,
        broker: BrokerBase,
        poll_interval: float = 0.5,
        max_wait_sec: float = 60.0,
    ):
        self._broker = broker
        self._poll_interval = poll_interval
        self._max_wait_sec = max_wait_sec
        self._log = logger.bind(component='fill_monitor')

    def wait_for_fill(self, order_id: str) -> BrokerOrder:
        """Block until order reaches a terminal status or times out.

        On timeout, attempts to cancel the order.

        Returns:
            BrokerOrder with final status.
        """
        start = time.monotonic()
        last_status = None

        while True:
            elapsed = time.monotonic() - start

            order = self._broker.get_order(order_id)
            status = order.status

            if status != last_status:
                self._log.info(
                    'order_status_update',
                    order_id=order_id,
                    symbol=order.symbol,
                    status=status,
                    elapsed_sec=round(elapsed, 1),
                )
                last_status = status

            if status in self.TERMINAL_STATUSES:
                if status == 'filled':
                    self._log.info(
                        'order_filled',
                        order_id=order_id,
                        symbol=order.symbol,
                        fill_price=order.filled_avg_price,
                        filled_qty=order.filled_qty,
                        elapsed_sec=round(elapsed, 1),
                    )
                elif status in ('cancelled', 'expired', 'rejected'):
                    self._log.warning(
                        f'order_{status}',
                        order_id=order_id,
                        symbol=order.symbol,
                    )
                return order

            if elapsed >= self._max_wait_sec:
                self._log.warning(
                    'order_timeout',
                    order_id=order_id,
                    symbol=order.symbol,
                    max_wait_sec=self._max_wait_sec,
                )
                self._broker.cancel_order(order_id)
                # Fetch final status after cancellation
                order = self._broker.get_order(order_id)
                return order

            time.sleep(self._poll_interval)

    def check_status(self, order_id: str) -> BrokerOrder:
        """Non-blocking single status check."""
        return self._broker.get_order(order_id)

    def is_terminal(self, status: str) -> bool:
        return status in self.TERMINAL_STATUSES
