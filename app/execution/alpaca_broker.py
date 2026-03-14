from __future__ import annotations

import structlog

from app.execution.broker_base import BrokerAccount, BrokerBase, BrokerOrder

logger = structlog.get_logger()


class AlpacaBroker(BrokerBase):
    """Alpaca Markets broker integration via alpaca-py.

    Wraps the TradingClient for order submission, fill polling,
    and account/position queries.  Supports both paper and live accounts.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
    ):
        self._api_key = api_key
        self._secret_key = secret_key
        self._paper = paper
        self._client = None
        self._data_client = None
        self._log = logger.bind(component='alpaca_broker')

    # ── connection ─────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            from alpaca.trading.client import TradingClient
            self._client = TradingClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                paper=self._paper,
            )
            # Verify credentials by fetching account
            acct = self._client.get_account()
            self._log.info(
                'broker_connected',
                broker=self.broker_name,
                equity=float(acct.equity),
            )
            return True
        except Exception as exc:
            self._log.error('broker_connect_failed', error=str(exc))
            return False

    def disconnect(self) -> None:
        self._client = None
        self._data_client = None
        self._log.info('broker_disconnected')

    # ── account ────────────────────────────────────────────────────

    def get_account(self) -> BrokerAccount:
        acct = self._client.get_account()
        return BrokerAccount(
            equity=float(acct.equity),
            buying_power=float(acct.buying_power),
            cash=float(acct.cash),
            currency=acct.currency,
        )

    # ── orders ─────────────────────────────────────────────────────

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
        limit_price: float | None = None,
    ) -> BrokerOrder:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        tif_map = {
            'day': TimeInForce.DAY,
            'gtc': TimeInForce.GTC,
            'opg': TimeInForce.OPG,
            'ioc': TimeInForce.IOC,
        }
        side_enum = OrderSide.BUY if side == 'buy' else OrderSide.SELL
        tif_enum = tif_map.get(time_in_force, TimeInForce.DAY)

        if order_type == 'limit' and limit_price is not None:
            request = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif_enum,
                limit_price=limit_price,
            )
        else:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif_enum,
            )

        order = self._client.submit_order(order_data=request)

        self._log.info(
            'order_submitted',
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            broker_order_id=str(order.id),
        )

        return self._to_broker_order(order)

    def get_order(self, order_id: str) -> BrokerOrder:
        order = self._client.get_order_by_id(order_id)
        return self._to_broker_order(order)

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(order_id)
            self._log.info('order_cancelled', order_id=order_id)
            return True
        except Exception as exc:
            self._log.error(
                'order_cancel_failed',
                order_id=order_id,
                error=str(exc),
            )
            return False

    # ── market data ────────────────────────────────────────────────

    def get_latest_quote(self, symbol: str) -> dict:
        from alpaca.data.requests import StockLatestQuoteRequest
        from alpaca.data.historical import StockHistoricalDataClient

        if self._data_client is None:
            self._data_client = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )

        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self._data_client.get_stock_latest_quote(request)
        quote = quotes[symbol]

        bid = float(quote.bid_price)
        ask = float(quote.ask_price)
        return {
            'bid': bid,
            'ask': ask,
            'mid': (bid + ask) / 2,
            'timestamp': str(quote.timestamp),
        }

    # ── positions ──────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {
                'symbol': p.symbol,
                'qty': float(p.qty),
                'market_value': float(p.market_value),
                'avg_entry_price': float(p.avg_entry_price),
                'unrealized_pl': float(p.unrealized_pl),
            }
            for p in positions
        ]

    # ── properties ─────────────────────────────────────────────────

    @property
    def broker_name(self) -> str:
        return 'alpaca_paper' if self._paper else 'alpaca_live'

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _to_broker_order(order) -> BrokerOrder:
        filled_qty = float(order.filled_qty) if order.filled_qty else 0.0
        filled_avg = (
            float(order.filled_avg_price) if order.filled_avg_price else None
        )
        filled_at = str(order.filled_at) if order.filled_at else None

        return BrokerOrder(
            order_id=str(order.id),
            symbol=order.symbol,
            side=order.side.value if hasattr(order.side, 'value') else str(order.side),
            qty=float(order.qty) if order.qty else 0.0,
            order_type=order.order_type.value if hasattr(order.order_type, 'value') else str(order.order_type),
            status=order.status.value if hasattr(order.status, 'value') else str(order.status),
            filled_qty=filled_qty,
            filled_avg_price=filled_avg,
            filled_at=filled_at,
            limit_price=float(order.limit_price) if order.limit_price else None,
            time_in_force=order.time_in_force.value if hasattr(order.time_in_force, 'value') else str(order.time_in_force),
        )
