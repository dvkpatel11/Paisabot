from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataProvider(ABC):
    """Abstract base class for all market data providers."""

    @abstractmethod
    def get_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """Fetch daily OHLCV bars.

        Returns DataFrame with columns:
            timestamp (datetime, UTC), open, high, low, close, volume
        Sorted by timestamp ascending.
        """
        ...

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> dict | None:
        """Return the most recent bar as a dict with OHLCV fields."""
        ...

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict | None:
        """Return latest quote: {bid, ask, mid, spread_bps, timestamp}."""
        ...

    @abstractmethod
    def get_multi_bars(
        self, symbols: list[str], start_date: date, end_date: date
    ) -> dict[str, pd.DataFrame]:
        """Fetch daily bars for multiple symbols at once.

        Returns {symbol: DataFrame} mapping.
        """
        ...
