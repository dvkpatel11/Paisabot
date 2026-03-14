from __future__ import annotations

import time
import threading
from datetime import date, datetime, timezone

import pandas as pd
import structlog

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestBarRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame

from app.data.base import DataProvider

logger = structlog.get_logger()


class RateLimiter:
    """Simple token-bucket rate limiter for Alpaca free tier (200 req/min)."""

    def __init__(self, max_calls: int = 190, period: float = 60.0):
        self._max_calls = max_calls
        self._period = period
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            # Purge old calls outside the window
            self._calls = [t for t in self._calls if now - t < self._period]
            if len(self._calls) >= self._max_calls:
                sleep_until = self._calls[0] + self._period
                sleep_time = sleep_until - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self._calls.append(time.monotonic())


class AlpacaDataProvider(DataProvider):
    """Market data provider using Alpaca-py SDK."""

    def __init__(self, api_key: str, secret_key: str):
        self.client = StockHistoricalDataClient(api_key, secret_key)
        self._limiter = RateLimiter()
        self._log = logger.bind(provider='alpaca')

    def get_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        self._limiter.wait()
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=datetime.combine(start_date, datetime.min.time()).replace(
                tzinfo=timezone.utc
            ),
            end=datetime.combine(end_date, datetime.min.time()).replace(
                tzinfo=timezone.utc
            ),
        )
        bars = self.client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            self._log.warning('no_bars_returned', symbol=symbol,
                              start=str(start_date), end=str(end_date))
            return pd.DataFrame(
                columns=['timestamp', 'open', 'high', 'low', 'close',
                         'volume', 'vwap', 'trade_count']
            )
        return self._normalize_bars_df(df, symbol)

    def get_intraday_bars(
        self, symbol: str, timeframe: TimeFrame = TimeFrame.Minute,
        limit: int = 500,
    ) -> pd.DataFrame:
        self._limiter.wait()
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            limit=limit,
        )
        bars = self.client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            return pd.DataFrame(
                columns=['timestamp', 'open', 'high', 'low', 'close',
                         'volume', 'vwap', 'trade_count']
            )
        return self._normalize_bars_df(df, symbol)

    def get_latest_bar(self, symbol: str) -> dict | None:
        self._limiter.wait()
        request = StockLatestBarRequest(symbol_or_symbols=symbol)
        bars = self.client.get_stock_latest_bar(request)
        bar = bars.get(symbol)
        if bar is None:
            return None
        return {
            'symbol': symbol,
            'timestamp': bar.timestamp,
            'open': float(bar.open),
            'high': float(bar.high),
            'low': float(bar.low),
            'close': float(bar.close),
            'volume': int(bar.volume),
            'vwap': float(bar.vwap) if bar.vwap else None,
            'trade_count': bar.trade_count,
        }

    def get_latest_quote(self, symbol: str) -> dict | None:
        self._limiter.wait()
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self.client.get_stock_latest_quote(request)
        quote = quotes.get(symbol)
        if quote is None:
            return None
        bid = float(quote.bid_price) if quote.bid_price else 0.0
        ask = float(quote.ask_price) if quote.ask_price else 0.0
        mid = (bid + ask) / 2 if bid and ask else 0.0
        spread_bps = ((ask - bid) / mid * 10_000) if mid > 0 else 0.0
        return {
            'symbol': symbol,
            'bid': bid,
            'ask': ask,
            'mid': mid,
            'spread_bps': spread_bps,
            'timestamp': quote.timestamp,
        }

    def get_multi_bars(
        self, symbols: list[str], start_date: date, end_date: date
    ) -> dict[str, pd.DataFrame]:
        self._limiter.wait()
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.combine(start_date, datetime.min.time()).replace(
                tzinfo=timezone.utc
            ),
            end=datetime.combine(end_date, datetime.min.time()).replace(
                tzinfo=timezone.utc
            ),
        )
        bars = self.client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            return {s: pd.DataFrame() for s in symbols}

        result = {}
        for symbol in symbols:
            try:
                sym_df = df.loc[symbol].copy() if symbol in df.index.get_level_values(0) else pd.DataFrame()
                if not sym_df.empty:
                    result[symbol] = self._normalize_bars_df(sym_df, symbol, multi=True)
                else:
                    result[symbol] = pd.DataFrame()
            except KeyError:
                result[symbol] = pd.DataFrame()
        return result

    def _normalize_bars_df(
        self, df: pd.DataFrame, symbol: str, multi: bool = False
    ) -> pd.DataFrame:
        """Normalize Alpaca bar DataFrame to standard format."""
        df = df.reset_index()
        # Multi-symbol responses have 'symbol' in index
        if 'symbol' in df.columns:
            df = df.drop(columns=['symbol'])

        rename_map = {}
        if 'timestamp' not in df.columns:
            # Alpaca uses 'timestamp' as index name in some versions
            for col in df.columns:
                if 'time' in col.lower():
                    rename_map[col] = 'timestamp'
                    break

        df = df.rename(columns=rename_map)

        # Ensure standard column names
        col_map = {
            'open': 'open', 'high': 'high', 'low': 'low',
            'close': 'close', 'volume': 'volume',
            'vwap': 'vwap', 'trade_count': 'trade_count',
        }
        for expected in col_map:
            if expected not in df.columns:
                df[expected] = None

        # Ensure timestamp is tz-aware UTC
        if 'timestamp' in df.columns and not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

        df = df.sort_values('timestamp').reset_index(drop=True)
        return df[['timestamp', 'open', 'high', 'low', 'close',
                    'volume', 'vwap', 'trade_count']]
