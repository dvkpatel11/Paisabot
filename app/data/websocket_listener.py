"""Real-time market data via Alpaca WebSocket streaming.

Subscribes to minute bars for active ETFs and publishes to Redis
for dashboard consumption and risk monitoring.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()


class AlpacaWebSocketListener:
    """WebSocket consumer for real-time bars via alpaca-py StockDataStream.

    Subscribes to minute bars for all active ETFs.
    On each bar: update Redis price cache + publish to channel:bars.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        redis_client,
    ):
        self._api_key = api_key
        self._secret_key = secret_key
        self._redis = redis_client
        self._stream = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._symbols: list[str] = []
        self._log = logger.bind(component='ws_listener')

    def start(self, symbols: list[str]) -> None:
        """Subscribe to bars for given symbols and start streaming.

        Runs the WebSocket connection in a daemon thread.
        """
        if self._running:
            self._log.warning('ws_already_running')
            return

        try:
            from alpaca.data.live import StockDataStream
        except ImportError:
            self._log.error('alpaca_streaming_not_available')
            return

        self._symbols = symbols
        self._stream = StockDataStream(self._api_key, self._secret_key)

        def on_bar(bar):
            self._handle_bar(bar)

        self._stream.subscribe_bars(on_bar, *symbols)

        self._running = True
        self._thread = threading.Thread(
            target=self._run_stream,
            daemon=True,
            name='alpaca-ws',
        )
        self._thread.start()
        self._log.info('ws_started', symbols=len(symbols))

    def add_symbol(self, symbol: str) -> None:
        """Dynamically add a symbol to the live subscription."""
        if symbol in self._symbols:
            return
        self._symbols.append(symbol)
        if self._stream:
            try:
                def on_bar(bar):
                    self._handle_bar(bar)
                self._stream.subscribe_bars(on_bar, symbol)
                self._log.info('ws_symbol_added', symbol=symbol)
            except Exception as exc:
                self._log.error('ws_symbol_add_failed', symbol=symbol, error=str(exc))

    def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
        self._stream = None
        self._thread = None
        self._log.info('ws_stopped')

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _run_stream(self) -> None:
        """Run the blocking stream loop with exponential-backoff reconnection."""
        import time

        attempt = 0
        max_backoff = 30

        while self._running:
            try:
                self._log.info('ws_stream_connecting', attempt=attempt)
                self._stream.run()
            except Exception as exc:
                if not self._running:
                    break
                wait = min(2 ** attempt, max_backoff)
                self._log.error(
                    'ws_stream_disconnected',
                    error=str(exc),
                    reconnect_in=wait,
                    attempt=attempt,
                )
                time.sleep(wait)
                attempt += 1

                # Rebuild stream for reconnection
                try:
                    from alpaca.data.live import StockDataStream
                    self._stream = StockDataStream(self._api_key, self._secret_key)

                    def on_bar(bar):
                        self._handle_bar(bar)

                    self._stream.subscribe_bars(on_bar, *self._symbols)
                except Exception as rebuild_exc:
                    self._log.error('ws_stream_rebuild_failed', error=str(rebuild_exc))
            else:
                # Clean exit (no exception) — reset attempts
                attempt = 0

        self._running = False

    def _handle_bar(self, bar) -> None:
        """Process incoming bar: Redis cache + pub/sub."""
        try:
            symbol = bar.symbol
            close_price = float(bar.close)
            timestamp = bar.timestamp.isoformat() if bar.timestamp else datetime.now(timezone.utc).isoformat()

            bar_data = {
                'symbol': symbol,
                'timestamp': timestamp,
                'open': float(bar.open),
                'high': float(bar.high),
                'low': float(bar.low),
                'close': close_price,
                'volume': int(bar.volume),
                'vwap': float(bar.vwap) if hasattr(bar, 'vwap') and bar.vwap else None,
            }

            # 1. Update latest price cache (for risk monitoring)
            self._redis.hset('cache:prices:latest', symbol, str(close_price))

            # 2. Publish to channel:bars (lossy, for dashboard)
            self._redis.publish('channel:bars', json.dumps(bar_data))

        except Exception as exc:
            self._log.error('ws_bar_handle_error', error=str(exc))
