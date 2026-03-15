"""Tests for MT5Broker — all MT5 API calls are mocked."""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Create a fake MetaTrader5 module so tests run on any OS (MT5 is Windows-only)
_fake_mt5 = types.ModuleType('MetaTrader5')
_fake_mt5.TRADE_ACTION_DEAL = 1
_fake_mt5.TRADE_ACTION_PENDING = 5
_fake_mt5.TRADE_ACTION_REMOVE = 8
_fake_mt5.ORDER_TYPE_BUY = 0
_fake_mt5.ORDER_TYPE_SELL = 1
_fake_mt5.ORDER_TYPE_BUY_LIMIT = 2
_fake_mt5.ORDER_TYPE_SELL_LIMIT = 3
_fake_mt5.ORDER_TIME_GTC = 0
_fake_mt5.ORDER_TIME_DAY = 1
_fake_mt5.ORDER_FILLING_FOK = 1
_fake_mt5.ORDER_FILLING_IOC = 2
_fake_mt5.ORDER_FILLING_RETURN = 3
_fake_mt5.TRADE_RETCODE_DONE = 10009
_fake_mt5.TIMEFRAME_M1 = 1
_fake_mt5.TIMEFRAME_D1 = 16408

# Stub functions (replaced per-test via MagicMock)
_fake_mt5.initialize = MagicMock(return_value=True)
_fake_mt5.shutdown = MagicMock()
_fake_mt5.login = MagicMock(return_value=True)
_fake_mt5.last_error = MagicMock(return_value=(0, 'OK'))
_fake_mt5.account_info = MagicMock()
_fake_mt5.terminal_info = MagicMock()
_fake_mt5.symbol_info = MagicMock()
_fake_mt5.symbol_info_tick = MagicMock()
_fake_mt5.symbol_select = MagicMock(return_value=True)
_fake_mt5.order_send = MagicMock()
_fake_mt5.order_check = MagicMock()
_fake_mt5.orders_get = MagicMock(return_value=None)
_fake_mt5.positions_get = MagicMock(return_value=None)
_fake_mt5.history_deals_get = MagicMock(return_value=None)

sys.modules['MetaTrader5'] = _fake_mt5

from app.execution.mt5_broker import MT5Broker, _notional_to_lots  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────

def _make_account_info(**overrides):
    defaults = {
        'login': 12345678,
        'equity': 50000.0,
        'balance': 48000.0,
        'margin_free': 45000.0,
        'leverage': 20,
        'currency': 'USD',
        'server': 'CMCMarkets-Demo',
    }
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def _make_tick(bid=529.50, ask=530.00, time_val=1710000000):
    obj = MagicMock()
    obj.bid = bid
    obj.ask = ask
    obj.time = time_val
    return obj


def _make_symbol_info(
    contract_size=1, volume_step=0.01, volume_min=0.01, volume_max=100.0,
    filling_mode=2,
):
    obj = MagicMock()
    obj.trade_contract_size = contract_size
    obj.volume_step = volume_step
    obj.volume_min = volume_min
    obj.volume_max = volume_max
    obj.filling_mode = filling_mode
    return obj


def _make_order_result(retcode=10009, order=123456, volume=1.0, price=530.0, comment=''):
    obj = MagicMock()
    obj.retcode = retcode
    obj.order = order
    obj.volume = volume
    obj.price = price
    obj.comment = comment
    return obj


def _make_terminal_info(connected=True):
    obj = MagicMock()
    obj.connected = connected
    return obj


def _make_position(ticket=1001, symbol='SPY.US', volume=0.5, price_open=525.0,
                   price_current=530.0, profit=250.0, type_val=0, magic=100001):
    obj = MagicMock()
    obj.ticket = ticket
    obj.symbol = symbol
    obj.volume = volume
    obj.price_open = price_open
    obj.price_current = price_current
    obj.profit = profit
    obj.type = type_val
    obj.magic = magic
    return obj


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_mt5_mocks():
    """Reset all MT5 mocks before each test."""
    _fake_mt5.initialize.reset_mock()
    _fake_mt5.initialize.return_value = True
    _fake_mt5.shutdown.reset_mock()
    _fake_mt5.last_error.return_value = (0, 'OK')
    _fake_mt5.account_info.return_value = _make_account_info()
    _fake_mt5.terminal_info.return_value = _make_terminal_info(connected=True)
    _fake_mt5.symbol_info.return_value = _make_symbol_info()
    _fake_mt5.symbol_info_tick.return_value = _make_tick()
    _fake_mt5.symbol_select.return_value = True
    _fake_mt5.order_send.return_value = _make_order_result()
    _fake_mt5.order_check.return_value = MagicMock(retcode=0)
    _fake_mt5.orders_get.return_value = None
    _fake_mt5.positions_get.return_value = None
    _fake_mt5.history_deals_get.return_value = None
    yield


@pytest.fixture
def broker():
    return MT5Broker(
        login=12345678,
        password='testpass',
        server='CMCMarkets-Demo',
        terminal_path=r'C:\Program Files\MT5\terminal64.exe',
    )


# ── Connection tests ────────────────────────────────────────────────

class TestMT5Connection:
    def test_connect_success(self, broker):
        assert broker.connect() is True
        assert broker.is_connected is True
        _fake_mt5.initialize.assert_called_once()

    def test_connect_failure(self, broker):
        _fake_mt5.initialize.return_value = False
        _fake_mt5.last_error.return_value = (-10004, 'NOT_CONNECTED')
        assert broker.connect() is False
        assert broker.is_connected is False

    def test_disconnect(self, broker):
        broker.connect()
        broker.disconnect()
        assert broker.is_connected is False
        _fake_mt5.shutdown.assert_called()

    def test_broker_name(self, broker):
        assert broker.broker_name == 'mt5'


# ── Account tests ───────────────────────────────────────────────────

class TestMT5Account:
    def test_get_account(self, broker):
        broker.connect()
        acct = broker.get_account()
        assert acct.equity == 50000.0
        assert acct.buying_power == 45000.0
        assert acct.cash == 48000.0
        assert acct.currency == 'USD'

    @patch('app.execution.mt5_broker.time.sleep')
    @patch('app.execution.mt5_broker.MAX_RECONNECT_RETRIES', 1)
    def test_get_account_connection_error(self, mock_sleep, broker):
        broker.connect()
        _fake_mt5.account_info.return_value = None
        _fake_mt5.last_error.return_value = (-10004, 'NOT_CONNECTED')
        _fake_mt5.terminal_info.return_value = _make_terminal_info(connected=False)
        # Reconnect also fails
        _fake_mt5.initialize.return_value = False
        with pytest.raises(ConnectionError):
            broker.get_account()


# ── Order submission tests ──────────────────────────────────────────

class TestMT5Orders:
    def test_market_buy(self, broker):
        broker.connect()
        order = broker.submit_order('SPY.US', 1.0, 'buy')
        assert order.status == 'filled'
        assert order.symbol == 'SPY.US'
        assert order.side == 'buy'
        assert order.filled_avg_price == 530.0
        assert order.order_id == '123456'

    def test_market_sell(self, broker):
        broker.connect()
        order = broker.submit_order('SPY.US', 0.5, 'sell')
        assert order.status == 'filled'
        assert order.side == 'sell'

    def test_limit_buy(self, broker):
        broker.connect()
        order = broker.submit_order(
            'QQQ.US', 2.0, 'buy',
            order_type='limit', limit_price=450.0,
        )
        assert order.status == 'filled'
        assert order.limit_price == 450.0

    def test_order_rejected(self, broker):
        broker.connect()
        _fake_mt5.order_send.return_value = _make_order_result(
            retcode=10006, comment='Not enough money',
        )
        order = broker.submit_order('SPY.US', 100.0, 'buy')
        assert order.status == 'rejected'

    def test_no_tick_data(self, broker):
        broker.connect()
        _fake_mt5.symbol_info_tick.return_value = None
        with pytest.raises(ValueError, match='No tick data'):
            broker.submit_order('INVALID', 1.0, 'buy')

    def test_no_symbol_info(self, broker):
        broker.connect()
        _fake_mt5.symbol_info.return_value = None
        with pytest.raises(ValueError, match='Symbol info unavailable'):
            broker.submit_order('INVALID', 1.0, 'buy')

    def test_order_send_returns_none(self, broker):
        broker.connect()
        _fake_mt5.order_send.return_value = None
        with pytest.raises(RuntimeError, match='order_send returned None'):
            broker.submit_order('SPY.US', 1.0, 'buy')


# ── Get order tests ─────────────────────────────────────────────────

class TestMT5GetOrder:
    def test_get_pending_order(self, broker):
        broker.connect()
        pending = MagicMock()
        pending.ticket = 999
        pending.symbol = 'SPY.US'
        pending.type = 2  # BUY_LIMIT
        pending.volume_initial = 1.0
        pending.volume_current = 1.0
        _fake_mt5.orders_get.return_value = (pending,)

        order = broker.get_order('999')
        assert order.status == 'pending'
        assert order.symbol == 'SPY.US'
        assert order.side == 'buy'

    def test_get_filled_deal(self, broker):
        broker.connect()
        _fake_mt5.orders_get.return_value = None
        deal = MagicMock()
        deal.order = 888
        deal.symbol = 'QQQ.US'
        deal.type = 0  # BUY
        deal.volume = 2.0
        deal.price = 450.0
        deal.time = 1710000000
        _fake_mt5.history_deals_get.return_value = (deal,)

        order = broker.get_order('888')
        assert order.status == 'filled'
        assert order.filled_avg_price == 450.0

    def test_order_not_found(self, broker):
        broker.connect()
        _fake_mt5.orders_get.return_value = None
        _fake_mt5.history_deals_get.return_value = None
        with pytest.raises(ValueError, match='not found'):
            broker.get_order('777')


# ── Cancel order tests ──────────────────────────────────────────────

class TestMT5CancelOrder:
    def test_cancel_success(self, broker):
        broker.connect()
        _fake_mt5.order_send.return_value = _make_order_result(retcode=10009)
        assert broker.cancel_order('999') is True

    def test_cancel_failure(self, broker):
        broker.connect()
        _fake_mt5.order_send.return_value = _make_order_result(retcode=10013)
        assert broker.cancel_order('999') is False


# ── Quote tests ─────────────────────────────────────────────────────

class TestMT5Quotes:
    def test_get_latest_quote(self, broker):
        broker.connect()
        quote = broker.get_latest_quote('SPY.US')
        assert quote['bid'] == 529.50
        assert quote['ask'] == 530.00
        assert quote['mid'] == pytest.approx(529.75)
        assert 'timestamp' in quote

    def test_quote_no_tick(self, broker):
        broker.connect()
        _fake_mt5.symbol_info_tick.return_value = None
        with pytest.raises(ValueError, match='No tick data'):
            broker.get_latest_quote('INVALID')


# ── Position tests ──────────────────────────────────────────────────

class TestMT5Positions:
    def test_get_positions_empty(self, broker):
        broker.connect()
        _fake_mt5.positions_get.return_value = None
        assert broker.get_positions() == []

    def test_get_positions(self, broker):
        broker.connect()
        pos = _make_position()
        _fake_mt5.positions_get.return_value = (pos,)

        positions = broker.get_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p['symbol'] == 'SPY.US'
        assert p['qty'] == 0.5
        assert p['avg_entry_price'] == 525.0
        assert p['unrealized_pl'] == 250.0
        assert p['direction'] == 'long'
        assert p['ticket'] == 1001

    def test_short_position_direction(self, broker):
        broker.connect()
        pos = _make_position(type_val=1)
        _fake_mt5.positions_get.return_value = (pos,)

        positions = broker.get_positions()
        assert positions[0]['direction'] == 'short'


# ── Lot conversion tests ───────────────────────────────────────────

class TestNotionalToLots:
    def test_basic_conversion(self):
        sym_info = _make_symbol_info(contract_size=1, volume_step=0.01)
        lots = _notional_to_lots(5300.0, 530.0, sym_info)
        assert lots == pytest.approx(10.0)

    def test_contract_size_100(self):
        sym_info = _make_symbol_info(contract_size=100, volume_step=0.01)
        lots = _notional_to_lots(10000.0, 530.0, sym_info)
        # 10000 / (530 * 100) = 0.1887 → rounded to 0.19
        assert lots == pytest.approx(0.19)

    def test_clamps_to_min(self):
        sym_info = _make_symbol_info(
            contract_size=1, volume_step=0.01, volume_min=0.1,
        )
        lots = _notional_to_lots(1.0, 530.0, sym_info)
        assert lots >= 0.1

    def test_clamps_to_max(self):
        sym_info = _make_symbol_info(
            contract_size=1, volume_step=0.01, volume_max=10.0,
        )
        lots = _notional_to_lots(999999.0, 530.0, sym_info)
        assert lots <= 10.0

    def test_zero_price(self):
        sym_info = _make_symbol_info()
        assert _notional_to_lots(5000.0, 0.0, sym_info) == 0.0


# ── Filling type tests ─────────────────────────────────────────────

class TestFillingType:
    def test_ioc_preferred(self):
        sym = _make_symbol_info(filling_mode=2)
        assert MT5Broker._get_filling_type(sym) == _fake_mt5.ORDER_FILLING_IOC

    def test_fok_fallback(self):
        sym = _make_symbol_info(filling_mode=1)
        assert MT5Broker._get_filling_type(sym) == _fake_mt5.ORDER_FILLING_FOK

    def test_return_fallback(self):
        sym = _make_symbol_info(filling_mode=0)
        assert MT5Broker._get_filling_type(sym) == _fake_mt5.ORDER_FILLING_RETURN


# ── Reconnect tests ────────────────────────────────────────────────

class TestMT5Reconnect:
    @patch('app.execution.mt5_broker.time.sleep')
    def test_auto_reconnect_on_disconnect(self, mock_sleep, broker):
        broker.connect()
        # Simulate disconnected terminal
        _fake_mt5.terminal_info.return_value = _make_terminal_info(connected=False)
        # Re-initialize succeeds
        _fake_mt5.initialize.return_value = True

        quote = broker.get_latest_quote('SPY.US')
        assert quote['bid'] == 529.50
        # initialize should have been called again for reconnect
        assert _fake_mt5.initialize.call_count >= 2
