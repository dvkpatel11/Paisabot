import pytest

from app.execution.broker_base import BrokerOrder
from app.execution.fill_monitor import FillMonitor


class TestFillMonitor:
    @pytest.fixture
    def monitor(self, mock_broker):
        return FillMonitor(mock_broker, poll_interval=0.01, max_wait_sec=0.5)

    def test_immediate_fill(self, mock_broker, monitor):
        """Already-filled order should return immediately."""
        order = mock_broker.submit_order('XLK', 10, 'buy')
        result = monitor.wait_for_fill(order.order_id)
        assert result.status == 'filled'
        assert result.filled_avg_price == 100.0

    def test_cancelled_order(self, mock_broker, monitor):
        """Cancelled order should return with cancelled status."""
        order = mock_broker.submit_order('XLK', 10, 'buy')
        mock_broker._orders[order.order_id].status = 'cancelled'
        result = monitor.wait_for_fill(order.order_id)
        assert result.status == 'cancelled'

    def test_expired_order(self, mock_broker, monitor):
        """Expired order should return with expired status."""
        order = mock_broker.submit_order('XLK', 10, 'buy')
        mock_broker._orders[order.order_id].status = 'expired'
        result = monitor.wait_for_fill(order.order_id)
        assert result.status == 'expired'

    def test_rejected_order(self, mock_broker, monitor):
        order = mock_broker.submit_order('XLK', 10, 'buy')
        mock_broker._orders[order.order_id].status = 'rejected'
        result = monitor.wait_for_fill(order.order_id)
        assert result.status == 'rejected'

    def test_timeout_cancels_order(self, mock_broker, monitor):
        """Order stuck in pending should be cancelled after timeout."""
        order = mock_broker.make_pending_order('XLK', 'buy', 10)
        result = monitor.wait_for_fill(order.order_id)
        # After timeout, monitor calls cancel, so status should be cancelled
        assert result.status == 'cancelled'

    def test_check_status_nonblocking(self, mock_broker, monitor):
        order = mock_broker.submit_order('SPY', 5, 'sell')
        result = monitor.check_status(order.order_id)
        assert result.status == 'filled'

    def test_is_terminal(self, monitor):
        assert monitor.is_terminal('filled') is True
        assert monitor.is_terminal('cancelled') is True
        assert monitor.is_terminal('expired') is True
        assert monitor.is_terminal('rejected') is True
        assert monitor.is_terminal('pending') is False
        assert monitor.is_terminal('new') is False
