from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import structlog

from app.extensions import db
from app.models.price_bars import PriceBar

logger = structlog.get_logger()


class FactorBase(ABC):
    """Abstract base class for all factor computations.

    Every factor must implement compute(symbols) returning {symbol: score}
    where scores are in [0, 1].
    """

    name: str = ''
    weight: float = 0.0
    update_frequency: str = 'daily'  # 'daily' or 'intraday'

    def __init__(self, redis_client=None, db_session=None, config_loader=None):
        self._redis = redis_client
        self._db_session = db_session or db.session
        self._config = config_loader
        self._log = logger.bind(factor=self.name)

    @abstractmethod
    def compute(self, symbols: list[str]) -> dict[str, float]:
        """Compute factor scores for given symbols.

        Returns:
            dict mapping symbol -> score in [0, 1]
        """

    def _get_daily_closes(
        self, symbol: str, lookback: int = 252,
    ) -> pd.Series:
        """Fetch daily close prices for a symbol.

        Returns a Series indexed by timestamp with close prices.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback * 1.5))

        bars = (
            PriceBar.query.filter(
                PriceBar.symbol == symbol,
                PriceBar.timeframe == '1d',
                PriceBar.timestamp >= cutoff,
            )
            .order_by(PriceBar.timestamp.asc())
            .all()
        )

        if not bars:
            return pd.Series(dtype=float)

        data = {bar.timestamp: float(bar.close) for bar in bars}
        series = pd.Series(data).sort_index()
        return series.tail(lookback)

    def _get_multi_closes(
        self, symbols: list[str], lookback: int = 252,
    ) -> pd.DataFrame:
        """Fetch daily closes for multiple symbols as a DataFrame.

        Returns DataFrame with timestamps as index, symbols as columns.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback * 1.5))

        bars = (
            PriceBar.query.filter(
                PriceBar.symbol.in_(symbols),
                PriceBar.timeframe == '1d',
                PriceBar.timestamp >= cutoff,
            )
            .order_by(PriceBar.timestamp.asc())
            .all()
        )

        if not bars:
            return pd.DataFrame()

        records = [
            {'timestamp': bar.timestamp, 'symbol': bar.symbol, 'close': float(bar.close)}
            for bar in bars
        ]
        df = pd.DataFrame(records)
        pivot = df.pivot_table(index='timestamp', columns='symbol', values='close')
        pivot = pivot.sort_index()

        # Keep only last `lookback` rows
        return pivot.tail(lookback)

    def _get_daily_ohlcv(
        self, symbol: str, lookback: int = 252,
    ) -> pd.DataFrame:
        """Fetch daily OHLCV bars for a symbol."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback * 1.5))

        bars = (
            PriceBar.query.filter(
                PriceBar.symbol == symbol,
                PriceBar.timeframe == '1d',
                PriceBar.timestamp >= cutoff,
            )
            .order_by(PriceBar.timestamp.asc())
            .all()
        )

        if not bars:
            return pd.DataFrame()

        records = [{
            'timestamp': bar.timestamp,
            'open': float(bar.open),
            'high': float(bar.high),
            'low': float(bar.low),
            'close': float(bar.close),
            'volume': int(bar.volume),
        } for bar in bars]

        df = pd.DataFrame(records).set_index('timestamp').sort_index()
        return df.tail(lookback)
