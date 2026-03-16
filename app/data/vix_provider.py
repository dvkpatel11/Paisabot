from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import structlog

logger = structlog.get_logger()


class VIXProvider:
    """Fetch VIX data from FRED (VIXCLS series).

    Uses pandas_datareader to pull the CBOE VIX close from FRED.
    Caches the latest value and 252-day history in Redis.
    """

    FRED_SERIES = 'VIXCLS'
    REDIS_KEY = 'vix:latest'
    REDIS_HISTORY_KEY = 'vix:history_252'
    REDIS_TTL = 86400  # 24 hours

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._log = logger.bind(provider='vix_fred')

    def get_latest_vix(self) -> float | None:
        """Return the most recent VIX close (T-1).

        Checks Redis cache first. On miss, fetches from FRED and caches.
        Returns None if data is unavailable.
        """
        # Try Redis cache first
        if self._redis is not None:
            cached = self._redis.get(self.REDIS_KEY)
            if cached is not None:
                try:
                    return float(cached)
                except (ValueError, TypeError):
                    pass

        vix_value = self._fetch_from_fred()
        if vix_value is not None and self._redis is not None:
            self._redis.set(self.REDIS_KEY, str(vix_value), ex=self.REDIS_TTL)

        return vix_value

    def get_vix_history_cached(self) -> list[float] | None:
        """Return the cached 252-day VIX history from Redis.

        Used by the volatility factor for percentile ranking.
        Returns None if cache is empty.
        """
        if self._redis is None:
            return None
        try:
            raw = self._redis.get(self.REDIS_HISTORY_KEY)
            if raw is None:
                return None
            return [float(v) for v in json.loads(raw)]
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def get_vix_history(
        self, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """Fetch historical VIX closes from FRED.

        Returns DataFrame with columns: ['date', 'vix_close'].
        """
        try:
            from pandas_datareader import data as pdr

            df = pdr.DataReader(
                self.FRED_SERIES, 'fred', start_date, end_date,
            )
            df = df.reset_index()
            df.columns = ['date', 'vix_close']
            df['vix_close'] = pd.to_numeric(df['vix_close'], errors='coerce')
            df = df.dropna(subset=['vix_close'])
            self._log.info(
                'vix_history_fetched',
                start=str(start_date),
                end=str(end_date),
                rows=len(df),
            )
            return df
        except Exception as exc:
            self._log.error('vix_history_fetch_failed', error=str(exc))
            return pd.DataFrame(columns=['date', 'vix_close'])

    def refresh(self) -> dict:
        """Full refresh: fetch latest VIX + 252-day history, cache both.

        Called by the refresh_vix Celery task. Returns status dict.
        """
        # Fetch ~400 days of history (buffer for weekends/holidays)
        end = date.today()
        start = end - timedelta(days=400)
        df = self.get_vix_history(start, end)

        if df.empty:
            self._log.warning('vix_refresh_no_data')
            return {'status': 'no_data', 'vix': None}

        latest_value = float(df['vix_close'].iloc[-1])
        latest_date = df['date'].iloc[-1]

        # Build 252-day history for percentile ranking
        history_252 = df['vix_close'].tail(252).round(2).tolist()

        if self._redis is not None:
            pipe = self._redis.pipeline()
            pipe.set(self.REDIS_KEY, str(latest_value), ex=self.REDIS_TTL)
            pipe.set(
                self.REDIS_HISTORY_KEY,
                json.dumps(history_252),
                ex=self.REDIS_TTL,
            )
            pipe.execute()

        self._log.info(
            'vix_refreshed',
            value=latest_value,
            date=str(latest_date),
            history_len=len(history_252),
        )

        return {
            'status': 'ok',
            'vix': latest_value,
            'date': str(latest_date),
            'history_len': len(history_252),
        }

    def _fetch_from_fred(self) -> float | None:
        """Pull the latest VIX close from FRED (T-1 value)."""
        try:
            from pandas_datareader import data as pdr

            end = date.today()
            start = end - timedelta(days=10)  # buffer for weekends/holidays
            df = pdr.DataReader(self.FRED_SERIES, 'fred', start, end)
            df = df.dropna()
            if df.empty:
                self._log.warning('vix_no_data_from_fred')
                return None

            latest_value = float(df.iloc[-1].values[0])
            latest_date = df.index[-1]
            self._log.info(
                'vix_fetched',
                value=latest_value,
                date=str(latest_date.date()),
            )
            return latest_value
        except Exception as exc:
            self._log.error('vix_fetch_failed', error=str(exc))
            return None
