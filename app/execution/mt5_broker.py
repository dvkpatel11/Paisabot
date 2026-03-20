"""MetaTrader 5 broker integration.

Implements BrokerBase for MT5-connected brokers (CMC Markets, Admiral Markets,
IC Markets, etc.) trading ETF CFDs.  All MT5 API calls are thread-safe via an
internal RLock and include automatic reconnection on disconnect.

Requirements:
    - Windows x86-64 only (MT5 Python package uses IPC, not network)
    - MT5 terminal must be running on the same machine
    - ``pip install MetaTrader5``
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import structlog

from app.execution.broker_base import BrokerAccount, BrokerBase, BrokerOrder

logger = structlog.get_logger()

# Magic number to tag Paisabot orders in MT5 (visible in journal/history)
PAISABOT_MAGIC = 100001

# Reconnect settings
MAX_RECONNECT_RETRIES = 10
RECONNECT_BASE_DELAY = 5.0  # seconds


def _notional_to_lots(notional_usd: float, price: float, symbol_info) -> float:
    """Convert dollar notional to MT5 lots respecting volume_step and limits.

    Args:
        notional_usd: Dollar amount to trade.
        price: Current ask (buy) or bid (sell).
        symbol_info: MT5 SymbolInfo namedtuple (from ``mt5.symbol_info()``).

    Returns:
        Volume in lots, clamped to [volume_min, volume_max] and rounded to
        the nearest volume_step.
    """
    contract_size = symbol_info.trade_contract_size  # e.g. 1 or 100
    if price <= 0 or contract_size <= 0:
        return 0.0
    raw_lots = notional_usd / (price * contract_size)
    step = symbol_info.volume_step
    lots = round(round(raw_lots / step) * step, 8)
    lots = max(symbol_info.volume_min, min(symbol_info.volume_max, lots))
    return lots


class MT5Broker(BrokerBase):
    """MetaTrader 5 broker via the ``MetaTrader5`` Python package.

    Designed as a drop-in replacement for :class:`AlpacaBroker`.  Since the
    MT5 Python API is synchronous and uses Windows IPC, every call is wrapped
    in a thread lock and the terminal must be running locally.

    Args:
        login: Integer MT5 account number.
        password: Account password.
        server: Broker server name (e.g. ``"CMCMarkets-Demo"``).
        terminal_path: Full path to ``terminal64.exe``.
        deviation: Max price deviation in points for market orders (slippage
            tolerance). Default ``30``.
    """

    def __init__(
        self,
        login: int,
        password: str,
        server: str,
        terminal_path: str | None = None,
        deviation: int = 30,
    ):
        self._login = login
        self._password = password
        self._server = server
        self._path = terminal_path
        self._deviation = deviation
        self._lock = threading.RLock()
        self._connected = False
        self._log = logger.bind(component='mt5_broker')

    # ── connection ──────────────────────────────────────────────────

    def connect(self) -> bool:
        with self._lock:
            try:
                import MetaTrader5 as mt5
            except ImportError:
                self._log.error(
                    'mt5_import_failed',
                    hint='pip install MetaTrader5 (Windows only)',
                )
                return False

            kwargs: dict = {
                'login': self._login,
                'password': self._password,
                'server': self._server,
                'timeout': 60_000,
            }
            if self._path:
                kwargs['path'] = self._path

            ok = mt5.initialize(**kwargs)
            if not ok:
                err = mt5.last_error()
                self._log.error('mt5_connect_failed', error=err)
                self._connected = False
                return False

            acct = mt5.account_info()
            self._log.info(
                'broker_connected',
                broker=self.broker_name,
                login=acct.login,
                equity=acct.equity,
                server=acct.server,
            )
            self._connected = True
            return True

    def disconnect(self) -> None:
        with self._lock:
            try:
                import MetaTrader5 as mt5
                mt5.shutdown()
            except Exception:
                pass
            self._connected = False
            self._log.info('broker_disconnected', broker=self.broker_name)

    def _ensure_connected(self) -> bool:
        """Check terminal connectivity, reconnect if needed."""
        import MetaTrader5 as mt5

        info = mt5.terminal_info()
        if info is not None and info.connected:
            return True

        self._log.warning('mt5_connection_lost', attempting_reconnect=True)
        return self._reconnect()

    def _reconnect(self) -> bool:
        import MetaTrader5 as mt5

        mt5.shutdown()
        for attempt in range(MAX_RECONNECT_RETRIES):
            self._log.warning(
                'mt5_reconnect_attempt',
                attempt=attempt + 1,
                max=MAX_RECONNECT_RETRIES,
            )
            kwargs: dict = {
                'login': self._login,
                'password': self._password,
                'server': self._server,
                'timeout': 30_000,
            }
            if self._path:
                kwargs['path'] = self._path

            ok = mt5.initialize(**kwargs)
            if ok:
                self._log.info('mt5_reconnected')
                self._connected = True
                return True
            time.sleep(RECONNECT_BASE_DELAY * (1.5 ** attempt))

        self._log.error('mt5_reconnect_exhausted')
        self._connected = False
        return False

    def _safe_call(self, fn, *args, **kwargs):
        """Thread-safe MT5 call with auto-reconnect on disconnect errors."""
        import MetaTrader5 as mt5

        with self._lock:
            self._ensure_connected()
            result = fn(*args, **kwargs)
            if result is None:
                err = mt5.last_error()
                # -10004 = NOT_CONNECTED, -10006 = DISCONNECTED
                if err[0] in (-10004, -10006):
                    if self._reconnect():
                        result = fn(*args, **kwargs)
            return result

    # ── account ─────────────────────────────────────────────────────

    def get_account(self) -> BrokerAccount:
        import MetaTrader5 as mt5

        info = self._safe_call(mt5.account_info)
        if info is None:
            raise ConnectionError(f'MT5 account_info failed: {mt5.last_error()}')

        return BrokerAccount(
            equity=info.equity,
            buying_power=info.margin_free,
            cash=info.balance,
            currency=info.currency,
        )

    # ── orders ──────────────────────────────────────────────────────

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
        limit_price: float | None = None,
    ) -> BrokerOrder:
        import MetaTrader5 as mt5

        with self._lock:
            self._ensure_connected()

            # Ensure symbol is in MarketWatch
            mt5.symbol_select(symbol, True)

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise ValueError(
                    f'No tick data for {symbol}: {mt5.last_error()}'
                )

            sym_info = mt5.symbol_info(symbol)
            if sym_info is None:
                raise ValueError(
                    f'Symbol info unavailable for {symbol}: {mt5.last_error()}'
                )

            # Determine filling mode from symbol capabilities
            filling_type = self._get_filling_type(sym_info)

            # Build order request
            if order_type == 'limit' and limit_price is not None:
                request = self._build_limit_request(
                    symbol, qty, side, limit_price, filling_type, time_in_force,
                )
            else:
                price = tick.ask if side == 'buy' else tick.bid
                request = self._build_market_request(
                    symbol, qty, side, price, filling_type,
                )

            # Check order before sending (dry-run validation)
            check = mt5.order_check(request)
            if check is None or check.retcode != 0:
                err_msg = check.comment if check else str(mt5.last_error())
                self._log.warning(
                    'order_check_failed',
                    symbol=symbol,
                    error=err_msg,
                )

            result = mt5.order_send(request)
            if result is None:
                raise RuntimeError(f'order_send returned None: {mt5.last_error()}')

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                self._log.error(
                    'order_rejected',
                    symbol=symbol,
                    retcode=result.retcode,
                    comment=result.comment,
                )
                return BrokerOrder(
                    order_id=str(result.order),
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    order_type=order_type,
                    status='rejected',
                    limit_price=limit_price,
                    time_in_force=time_in_force,
                )

            self._log.info(
                'order_submitted',
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=order_type,
                ticket=result.order,
                fill_price=result.price,
                fill_volume=result.volume,
            )

            # MT5 market orders fill immediately in most cases
            now_str = datetime.now(timezone.utc).isoformat()
            return BrokerOrder(
                order_id=str(result.order),
                symbol=symbol,
                side=side,
                qty=result.volume,
                order_type=order_type,
                status='filled' if result.retcode == mt5.TRADE_RETCODE_DONE else 'pending',
                filled_qty=result.volume,
                filled_avg_price=result.price,
                filled_at=now_str,
                limit_price=limit_price,
                time_in_force=time_in_force,
            )

    def get_order(self, order_id: str) -> BrokerOrder:
        import MetaTrader5 as mt5

        ticket = int(order_id)

        # Check pending orders first
        orders = self._safe_call(mt5.orders_get, ticket=ticket)
        if orders:
            o = orders[0]
            return BrokerOrder(
                order_id=str(o.ticket),
                symbol=o.symbol,
                side='buy' if o.type in (0, 2, 4) else 'sell',
                qty=o.volume_initial,
                order_type='limit' if o.type in (2, 3) else 'market',
                status='pending',
                filled_qty=o.volume_initial - o.volume_current,
            )

        # Check deal history (filled orders)
        deals = self._safe_call(
            mt5.history_deals_get, ticket=ticket,
        )
        if deals:
            d = deals[0]
            return BrokerOrder(
                order_id=str(d.order),
                symbol=d.symbol,
                side='buy' if d.type == 0 else 'sell',
                qty=d.volume,
                order_type='market',
                status='filled',
                filled_qty=d.volume,
                filled_avg_price=d.price,
                filled_at=datetime.fromtimestamp(
                    d.time, tz=timezone.utc
                ).isoformat(),
            )

        raise ValueError(f'Order {order_id} not found in MT5')

    def cancel_order(self, order_id: str) -> bool:
        import MetaTrader5 as mt5

        with self._lock:
            self._ensure_connected()
            request = {
                'action': mt5.TRADE_ACTION_REMOVE,
                'order': int(order_id),
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                self._log.info('order_cancelled', order_id=order_id)
                return True
            self._log.error(
                'order_cancel_failed',
                order_id=order_id,
                retcode=result.retcode if result else None,
            )
            return False

    # ── market data ─────────────────────────────────────────────────

    def get_latest_quote(self, symbol: str) -> dict:
        import MetaTrader5 as mt5

        with self._lock:
            self._ensure_connected()
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)

        if tick is None:
            raise ValueError(f'No tick data for {symbol}: {mt5.last_error()}')

        bid = tick.bid
        ask = tick.ask
        return {
            'bid': bid,
            'ask': ask,
            'mid': (bid + ask) / 2,
            'timestamp': datetime.fromtimestamp(
                tick.time, tz=timezone.utc
            ).isoformat(),
        }

    # ── positions ───────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        import MetaTrader5 as mt5

        positions = self._safe_call(mt5.positions_get)
        if not positions:
            return []

        return [
            {
                'symbol': p.symbol,
                'qty': p.volume,
                'market_value': p.volume * p.price_current * (
                    mt5.symbol_info(p.symbol).trade_contract_size
                    if mt5.symbol_info(p.symbol) else 1
                ),
                'avg_entry_price': p.price_open,
                'unrealized_pl': p.profit,
                'ticket': p.ticket,
                'direction': 'long' if p.type == 0 else 'short',
                'magic': p.magic,
            }
            for p in positions
        ]

    # ── properties ──────────────────────────────────────────────────

    @property
    def broker_name(self) -> str:
        return 'mt5'

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_filling_type(sym_info):
        """Determine the best filling mode for a symbol.

        Checks the symbol's ``filling_mode`` bitmask and returns the first
        supported type: IOC > FOK > RETURN.
        """
        import MetaTrader5 as mt5

        fm = sym_info.filling_mode
        if fm & 2:  # IOC
            return mt5.ORDER_FILLING_IOC
        if fm & 1:  # FOK
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _build_market_request(
        self, symbol: str, qty: float, side: str, price: float,
        filling_type: int,
    ) -> dict:
        import MetaTrader5 as mt5

        return {
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': symbol,
            'volume': qty,
            'type': mt5.ORDER_TYPE_BUY if side == 'buy' else mt5.ORDER_TYPE_SELL,
            'price': price,
            'deviation': self._deviation,
            'magic': PAISABOT_MAGIC,
            'comment': f'paisabot-{side}',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': filling_type,
        }

    def _build_limit_request(
        self, symbol: str, qty: float, side: str, limit_price: float,
        filling_type: int, time_in_force: str,
    ) -> dict:
        import MetaTrader5 as mt5

        tif_map = {
            'day': mt5.ORDER_TIME_DAY,
            'gtc': mt5.ORDER_TIME_GTC,
        }
        return {
            'action': mt5.TRADE_ACTION_PENDING,
            'symbol': symbol,
            'volume': qty,
            'type': (
                mt5.ORDER_TYPE_BUY_LIMIT if side == 'buy'
                else mt5.ORDER_TYPE_SELL_LIMIT
            ),
            'price': limit_price,
            'deviation': self._deviation,
            'magic': PAISABOT_MAGIC,
            'comment': f'paisabot-limit-{side}',
            'type_time': tif_map.get(time_in_force, mt5.ORDER_TIME_DAY),
            'type_filling': filling_type,
        }

    @staticmethod
    def notional_to_lots(
        notional_usd: float, price: float, symbol_info,
    ) -> float:
        """Public wrapper for lot conversion — useful for external callers."""
        return _notional_to_lots(notional_usd, price, symbol_info)
