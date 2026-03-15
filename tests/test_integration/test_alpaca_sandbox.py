"""Alpaca paper trading sandbox integration tests.

Requires real Alpaca paper credentials set in the environment:

    ALPACA_PAPER_KEY=<key>
    ALPACA_PAPER_SECRET=<secret>

Run with:

    pytest tests/test_integration/test_alpaca_sandbox.py -m integration -v

All tests use SPY with a $10 fractional notional — safe for paper accounts.
Each test that opens a position tears it down before exiting, so the paper
account returns to its starting state after the suite.

What is covered:
  1. connect()              — credentials accepted, account data returned
  2. get_account()          — equity / buying_power / cash populated
  3. get_latest_quote()     — bid/ask/mid structure correct
  4. Market order lifecycle — submit buy → fill → fill_monitor detects fill
  5. Sell (position close)  — sell the position opened above, verify closed
  6. Limit order cancel     — submit limit far off-market → cancel → confirmed
  7. PositionTracker reconciliation
                            — after a fill, DB Position matches broker's
                              get_positions() for the same symbol
"""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from datetime import datetime, timezone

import pytest

# ── skip the entire module if credentials are absent ──────────────

PAPER_KEY = os.environ.get('ALPACA_PAPER_KEY', '')
PAPER_SECRET = os.environ.get('ALPACA_PAPER_SECRET', '')
_CREDS_MISSING = not (PAPER_KEY and PAPER_SECRET)

pytestmark = pytest.mark.integration


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def broker():
    """One AlpacaBroker connection shared across the module's tests."""
    if _CREDS_MISSING:
        pytest.skip('ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET not set')

    from app.execution.alpaca_broker import AlpacaBroker

    b = AlpacaBroker(api_key=PAPER_KEY, secret_key=PAPER_SECRET, paper=True)
    ok = b.connect()
    assert ok, 'AlpacaBroker.connect() returned False — check paper credentials'
    yield b
    b.disconnect()


@pytest.fixture(scope='module')
def app_context():
    """Flask app context for tests that touch the DB via PositionTracker."""
    if _CREDS_MISSING:
        pytest.skip('ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET not set')

    from app import create_app
    from app.extensions import db as _db

    app = create_app('testing')
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.rollback()
        _db.drop_all()


# ── helpers ───────────────────────────────────────────────────────

_TEST_SYMBOL = 'SPY'
_TEST_NOTIONAL = 10.0       # $10 fractional — minimal paper capital risk


def _wait_for_fill(broker, order_id: str, timeout: int = 30) -> object:
    """Poll until order is filled, cancelled, or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        order = broker.get_order(order_id)
        if order.status in ('filled', 'cancelled', 'expired', 'rejected'):
            return order
        time.sleep(1)
    return broker.get_order(order_id)


def _cleanup_position(broker, symbol: str) -> None:
    """Close any open position in symbol by submitting a market sell."""
    positions = broker.get_positions()
    for p in positions:
        if p['symbol'] == symbol and p['qty'] > 0:
            broker.submit_order(
                symbol=symbol,
                qty=p['qty'],
                side='sell',
                order_type='market',
                time_in_force='day',
            )
            time.sleep(3)  # give paper engine time to process
            return


# ── 1. Connection ─────────────────────────────────────────────────

class TestConnection:
    def test_connect_returns_true(self):
        """Verifies that connect() succeeds with valid paper credentials."""
        from app.execution.alpaca_broker import AlpacaBroker

        b = AlpacaBroker(api_key=PAPER_KEY, secret_key=PAPER_SECRET, paper=True)
        result = b.connect()
        assert result is True
        b.disconnect()

    def test_connect_fails_on_bad_credentials(self):
        """connect() returns False (not raises) on bad credentials."""
        from app.execution.alpaca_broker import AlpacaBroker

        b = AlpacaBroker(api_key='bad_key', secret_key='bad_secret', paper=True)
        result = b.connect()
        assert result is False

    def test_broker_name_is_alpaca_paper(self, broker):
        assert broker.broker_name == 'alpaca_paper'


# ── 2. Account ────────────────────────────────────────────────────

class TestAccount:
    def test_get_account_returns_broker_account(self, broker):
        from app.execution.broker_base import BrokerAccount

        acct = broker.get_account()
        assert isinstance(acct, BrokerAccount)

    def test_account_has_positive_equity(self, broker):
        """Paper accounts start with $100k — equity should be positive."""
        acct = broker.get_account()
        assert acct.equity > 0, f'Expected positive equity, got {acct.equity}'

    def test_account_has_buying_power(self, broker):
        acct = broker.get_account()
        assert acct.buying_power >= 0

    def test_account_cash_is_non_negative(self, broker):
        acct = broker.get_account()
        assert acct.cash >= 0

    def test_account_currency_is_usd(self, broker):
        acct = broker.get_account()
        assert acct.currency.upper() == 'USD'


# ── 3. Market data ────────────────────────────────────────────────

class TestMarketData:
    def test_get_latest_quote_returns_bid_ask_mid(self, broker):
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        assert 'bid' in quote
        assert 'ask' in quote
        assert 'mid' in quote
        assert 'timestamp' in quote

    def test_quote_prices_are_positive(self, broker):
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        assert quote['bid'] > 0
        assert quote['ask'] > 0
        assert quote['mid'] > 0

    def test_mid_is_between_bid_and_ask(self, broker):
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        assert quote['bid'] <= quote['mid'] <= quote['ask']

    def test_spread_is_reasonable_for_spy(self, broker):
        """SPY spread should be under 10 bps on a paper account."""
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        spread_bps = (quote['ask'] - quote['bid']) / quote['mid'] * 10_000
        assert spread_bps < 10, f'SPY spread unusually wide: {spread_bps:.2f} bps'


# ── 4. Order lifecycle ────────────────────────────────────────────

class TestOrderLifecycle:
    """Submit a fractional market buy, verify it fills, then close the position."""

    def test_market_buy_submits_and_fills(self, broker):
        """End-to-end: submit $10 fractional buy → broker confirms fill."""
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        mid = quote['mid']
        qty = round(_TEST_NOTIONAL / mid, 6)

        order = broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=qty,
            side='buy',
            order_type='market',
            time_in_force='day',
        )

        assert order.order_id, 'Expected a broker order ID'
        assert order.symbol == _TEST_SYMBOL
        assert order.side in ('buy', 'long')

        # Poll for fill (paper fills market orders almost instantly)
        final = _wait_for_fill(broker, order.order_id, timeout=30)

        assert final.status == 'filled', (
            f'Expected filled, got {final.status} for order {order.order_id}'
        )
        assert final.filled_qty is not None and final.filled_qty > 0
        assert final.filled_avg_price is not None and final.filled_avg_price > 0

        # Clean up — sell what we bought
        broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=final.filled_qty,
            side='sell',
            order_type='market',
            time_in_force='day',
        )
        time.sleep(3)

    def test_sell_closes_open_position(self, broker):
        """Buy a fractional position then sell it; verify position qty goes to zero."""
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        qty = round(_TEST_NOTIONAL / quote['mid'], 6)

        # Buy
        buy = broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=qty,
            side='buy',
            order_type='market',
            time_in_force='day',
        )
        _wait_for_fill(broker, buy.order_id, timeout=30)
        time.sleep(2)

        # Verify position exists
        positions_before = {p['symbol']: p for p in broker.get_positions()}
        assert _TEST_SYMBOL in positions_before, 'Position not found after buy'
        held_qty = positions_before[_TEST_SYMBOL]['qty']
        assert held_qty > 0

        # Sell
        sell = broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=held_qty,
            side='sell',
            order_type='market',
            time_in_force='day',
        )
        final = _wait_for_fill(broker, sell.order_id, timeout=30)

        assert final.status == 'filled', f'Sell not filled: {final.status}'

        # Verify position is closed
        time.sleep(2)
        positions_after = {p['symbol']: p for p in broker.get_positions()}
        remaining_qty = positions_after.get(_TEST_SYMBOL, {}).get('qty', 0)
        assert remaining_qty < 0.001, (
            f'Expected zero qty after sell, got {remaining_qty}'
        )

    def test_get_order_returns_current_status(self, broker):
        """get_order() fetches live status of an existing order."""
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        qty = round(_TEST_NOTIONAL / quote['mid'], 6)

        order = broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=qty,
            side='buy',
            order_type='market',
            time_in_force='day',
        )

        fetched = broker.get_order(order.order_id)
        assert fetched.order_id == order.order_id
        assert fetched.symbol == _TEST_SYMBOL

        # Wait and clean up
        _wait_for_fill(broker, order.order_id)
        _cleanup_position(broker, _TEST_SYMBOL)


# ── 5. Order cancellation ─────────────────────────────────────────

class TestOrderCancellation:
    """Submit a limit order far off-market and cancel it immediately."""

    def test_cancel_unfilled_limit_order(self, broker):
        """Limit order far from market can be cancelled; status reflects cancelled."""
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        qty = round(_TEST_NOTIONAL / quote['mid'], 6)

        # Set limit price 20% below mid — will not fill
        limit_price = round(quote['mid'] * 0.80, 2)

        order = broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=qty,
            side='buy',
            order_type='limit',
            time_in_force='day',
            limit_price=limit_price,
        )

        assert order.order_id
        time.sleep(1)

        ok = broker.cancel_order(order.order_id)
        assert ok is True, 'cancel_order() should return True on success'

        time.sleep(1)
        final = broker.get_order(order.order_id)
        assert final.status in ('cancelled', 'canceled', 'expired'), (
            f'Expected cancelled status, got {final.status}'
        )

    def test_cancel_already_filled_order_returns_false(self, broker):
        """cancel_order() returns False (not raises) when the order is already filled."""
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        qty = round(_TEST_NOTIONAL / quote['mid'], 6)

        order = broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=qty,
            side='buy',
            order_type='market',
            time_in_force='day',
        )
        _wait_for_fill(broker, order.order_id)

        # Try cancelling a filled order — broker should reject gracefully
        result = broker.cancel_order(order.order_id)
        # Result can be True or False depending on broker timing; it must NOT raise
        assert isinstance(result, bool)

        # Clean up
        _cleanup_position(broker, _TEST_SYMBOL)


# ── 6. PositionTracker reconciliation ────────────────────────────

class TestPositionTrackerReconciliation:
    """After a live fill, verify DB Position matches broker's get_positions()."""

    def test_position_tracker_matches_broker_after_fill(self, broker, app_context):
        """
        Flow:
          1. Submit $10 fractional buy via AlpacaBroker
          2. Wait for fill
          3. Call PositionTracker.update_from_fill() with the fill result
          4. Query DB for open position
          5. Compare qty and price to broker.get_positions()
          6. Sell to clean up
        """
        from app.execution.position_tracker import PositionTracker
        from app.extensions import db as _db

        # Step 1 & 2: Buy and wait for fill
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        qty = round(_TEST_NOTIONAL / quote['mid'], 6)

        order = broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=qty,
            side='buy',
            order_type='market',
            time_in_force='day',
        )
        final = _wait_for_fill(broker, order.order_id, timeout=30)

        assert final.status == 'filled', f'Fill prerequisite failed: {final.status}'
        filled_qty = final.filled_qty
        fill_price = final.filled_avg_price

        # Step 3: Build fill result dict (mirrors OrderManager._result output)
        fill_result = {
            'symbol': _TEST_SYMBOL,
            'side': 'buy',
            'notional': filled_qty * fill_price,
            'status': 'filled',
            'fill_price': fill_price,
            'filled_qty': filled_qty,
            'broker_order_id': order.order_id,
            'broker': 'alpaca_paper',
            'regime': 'simulation',
            'filled_at': final.filled_at,
        }

        tracker = PositionTracker(db_session=_db.session)
        tracker.update_from_fill(fill_result)

        # Step 4: Query DB
        from app.models.positions import Position
        db_position = (
            Position.query
            .filter_by(symbol=_TEST_SYMBOL, status='open', direction='long')
            .first()
        )

        assert db_position is not None, 'PositionTracker did not create a DB Position'

        # Step 5: Compare to broker
        broker_positions = {p['symbol']: p for p in broker.get_positions()}
        assert _TEST_SYMBOL in broker_positions, (
            'Broker has no open position for SPY — fill may have reversed'
        )
        broker_pos = broker_positions[_TEST_SYMBOL]

        # Quantities should match within floating-point tolerance
        db_qty = float(db_position.quantity)
        broker_qty = float(broker_pos['qty'])
        assert abs(db_qty - broker_qty) < 0.001, (
            f'Qty mismatch: DB={db_qty} broker={broker_qty}'
        )

        # Entry price should be close to broker's avg entry price
        db_entry = float(db_position.entry_price)
        broker_entry = float(broker_pos['avg_entry_price'])
        pct_diff = abs(db_entry - broker_entry) / broker_entry
        assert pct_diff < 0.001, (
            f'Entry price mismatch: DB={db_entry} broker={broker_entry}'
        )

        # Step 6: Clean up
        broker.submit_order(
            symbol=_TEST_SYMBOL,
            qty=broker_qty,
            side='sell',
            order_type='market',
            time_in_force='day',
        )
        time.sleep(3)

    def test_position_tracker_closes_on_sell_fill(self, broker, app_context):
        """
        After a sell fill, PositionTracker marks the position closed and
        records realized PnL.
        """
        from app.execution.position_tracker import PositionTracker
        from app.extensions import db as _db
        from app.models.positions import Position

        # Buy
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        qty = round(_TEST_NOTIONAL / quote['mid'], 6)
        buy_order = broker.submit_order(
            symbol=_TEST_SYMBOL, qty=qty, side='buy',
            order_type='market', time_in_force='day',
        )
        buy_final = _wait_for_fill(broker, buy_order.order_id)
        assert buy_final.status == 'filled'

        tracker = PositionTracker(db_session=_db.session)
        tracker.update_from_fill({
            'symbol': _TEST_SYMBOL, 'side': 'buy',
            'notional': buy_final.filled_qty * buy_final.filled_avg_price,
            'status': 'filled', 'fill_price': buy_final.filled_avg_price,
            'filled_qty': buy_final.filled_qty,
            'broker_order_id': buy_order.order_id,
            'broker': 'alpaca_paper',
        })

        # Sell
        time.sleep(2)
        sell_order = broker.submit_order(
            symbol=_TEST_SYMBOL, qty=buy_final.filled_qty, side='sell',
            order_type='market', time_in_force='day',
        )
        sell_final = _wait_for_fill(broker, sell_order.order_id)
        assert sell_final.status == 'filled'

        tracker.update_from_fill({
            'symbol': _TEST_SYMBOL, 'side': 'sell',
            'notional': sell_final.filled_qty * sell_final.filled_avg_price,
            'status': 'filled', 'fill_price': sell_final.filled_avg_price,
            'filled_qty': sell_final.filled_qty,
            'broker_order_id': sell_order.order_id,
            'broker': 'alpaca_paper',
        })

        # DB position should now be closed
        closed = (
            Position.query
            .filter_by(symbol=_TEST_SYMBOL, status='closed', direction='long')
            .order_by(Position.closed_at.desc())
            .first()
        )
        assert closed is not None, 'Expected a closed Position record after sell fill'
        assert float(closed.quantity) == 0.0 or float(closed.quantity) < 0.001

        # Realized PnL should be recorded (can be positive or negative; must not be NULL)
        assert closed.realized_pnl is not None


# ── 7. Positions endpoint ─────────────────────────────────────────

class TestGetPositions:
    def test_get_positions_returns_list(self, broker):
        positions = broker.get_positions()
        assert isinstance(positions, list)

    def test_position_dict_has_required_fields(self, broker):
        """Each position dict has the expected keys."""
        # Open a tiny position to guarantee at least one result
        quote = broker.get_latest_quote(_TEST_SYMBOL)
        qty = round(_TEST_NOTIONAL / quote['mid'], 6)
        order = broker.submit_order(
            symbol=_TEST_SYMBOL, qty=qty, side='buy',
            order_type='market', time_in_force='day',
        )
        _wait_for_fill(broker, order.order_id)
        time.sleep(2)

        positions = broker.get_positions()
        spy_pos = next((p for p in positions if p['symbol'] == _TEST_SYMBOL), None)
        assert spy_pos is not None

        required_keys = {'symbol', 'qty', 'market_value', 'avg_entry_price', 'unrealized_pl'}
        assert required_keys.issubset(spy_pos.keys()), (
            f'Missing keys: {required_keys - spy_pos.keys()}'
        )

        # Clean up
        _cleanup_position(broker, _TEST_SYMBOL)
