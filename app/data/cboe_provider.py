"""CBOE Put/Call ratio provider.

Fetches daily aggregate equity put/call ratio from CBOE's free data.
Falls back to scraping the CBOE website totals page if the CSV endpoint changes.
Caches both the current 10-day MA and 252-day history in Redis.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from io import StringIO

import numpy as np
import pandas as pd
import requests
import structlog

logger = structlog.get_logger()

# CBOE publishes daily volume data; the equity P/C ratio is derived
# from total put volume / total call volume.
CBOE_OPTIONS_URL = (
    'https://www.cboe.com/market_statistics/equity_put_call_ratio/'
)
CBOE_TOTALS_CSV_URL = (
    'https://cdn.cboe.com/data/us/options/market_statistics'
    '/daily_volume/equity_put_call_ratio.csv'
)

# Redis key patterns
PC_RATIO_KEY = 'options:equity:pc_ratio'        # current 10d MA
PC_HISTORY_KEY = 'options:equity:pc_history'     # 252-day array
PC_RAW_KEY = 'options:equity:pc_raw_history'     # raw daily values for MA
PC_LATEST_DATE_KEY = 'options:equity:latest_date'
REDIS_TTL = 86400  # 24 hours


class CBOEPutCallProvider:
    """Fetch and cache CBOE equity put/call ratio data.

    The CBOE publishes aggregate equity put/call ratios daily.
    We cache:
      - 10-day moving average (what the sentiment factor consumes)
      - 252-day history of the 10-day MA (for percentile ranking)
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._log = logger.bind(provider='cboe_pc')

    def refresh(self) -> dict:
        """Fetch latest CBOE data, compute 10-day MA, update Redis caches.

        Returns dict with status info for logging/monitoring.
        """
        df = self._fetch_history()
        if df is None or df.empty:
            self._log.warning('cboe_no_data')
            return {'status': 'no_data'}

        # Compute 10-day moving average of the P/C ratio
        df = df.sort_values('date').reset_index(drop=True)
        df['pc_ma10'] = df['pc_ratio'].rolling(window=10, min_periods=5).mean()
        df = df.dropna(subset=['pc_ma10'])

        if df.empty:
            self._log.warning('cboe_insufficient_data_for_ma')
            return {'status': 'insufficient_data'}

        latest_date = df['date'].iloc[-1]
        latest_ma10 = round(float(df['pc_ma10'].iloc[-1]), 4)

        # Build 252-day history of the 10-day MA for percentile ranking
        history_252 = df['pc_ma10'].tail(252).round(4).tolist()

        self._log.info(
            'cboe_pc_refreshed',
            latest_date=str(latest_date),
            latest_ma10=latest_ma10,
            history_len=len(history_252),
            latest_raw=round(float(df['pc_ratio'].iloc[-1]), 4),
        )

        # Cache to Redis
        if self._redis is not None:
            self._cache_results(latest_ma10, history_252, str(latest_date))
            # Also set per-symbol keys that the sentiment factor reads
            self._set_symbol_keys(latest_ma10, history_252)

        return {
            'status': 'ok',
            'latest_date': str(latest_date),
            'pc_ratio_ma10': latest_ma10,
            'history_len': len(history_252),
        }

    def get_current_ratio(self) -> float | None:
        """Return the cached 10-day MA put/call ratio."""
        if self._redis is None:
            return None
        try:
            val = self._redis.get(PC_RATIO_KEY)
            if val is None:
                return None
            return float(val)
        except (ValueError, TypeError):
            return None

    def get_history(self) -> list[float] | None:
        """Return the cached 252-day history of 10-day MA P/C ratios."""
        if self._redis is None:
            return None
        try:
            raw = self._redis.get(PC_HISTORY_KEY)
            if raw is None:
                return None
            return [float(v) for v in json.loads(raw)]
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def _fetch_history(self) -> pd.DataFrame | None:
        """Fetch P/C ratio history from CBOE CSV endpoint.

        Returns DataFrame with columns: ['date', 'pc_ratio'].
        Tries the CDN CSV first, then falls back to pandas_datareader FRED
        series EQUITYPC if that fails.
        """
        df = self._try_cboe_csv()
        if df is not None and not df.empty:
            return df

        # Fallback: FRED publishes CBOE equity P/C as series EQUITYPC
        df = self._try_fred_fallback()
        if df is not None and not df.empty:
            return df

        self._log.error('cboe_all_sources_failed')
        return None

    def _try_cboe_csv(self) -> pd.DataFrame | None:
        """Try fetching from CBOE's CDN CSV endpoint."""
        try:
            resp = requests.get(
                CBOE_TOTALS_CSV_URL,
                timeout=30,
                headers={
                    'User-Agent': 'paisabot/1.0 (research)',
                    'Accept': 'text/csv',
                },
            )
            resp.raise_for_status()

            df = pd.read_csv(StringIO(resp.text))

            # CBOE CSV format varies; try common column patterns
            date_col = None
            ratio_col = None
            for col in df.columns:
                cl = col.lower().strip()
                if cl in ('date', 'trade_date', 'trade date'):
                    date_col = col
                elif 'ratio' in cl or 'p/c' in cl or 'put' in cl:
                    ratio_col = col

            if date_col is None or ratio_col is None:
                # Try positional: first col = date, last col = ratio
                if len(df.columns) >= 2:
                    date_col = df.columns[0]
                    ratio_col = df.columns[-1]
                else:
                    self._log.warning(
                        'cboe_csv_unknown_columns',
                        columns=list(df.columns),
                    )
                    return None

            result = pd.DataFrame({
                'date': pd.to_datetime(df[date_col], errors='coerce'),
                'pc_ratio': pd.to_numeric(df[ratio_col], errors='coerce'),
            })
            result = result.dropna()
            result['date'] = result['date'].dt.date

            self._log.info('cboe_csv_fetched', rows=len(result))
            return result

        except Exception as exc:
            self._log.warning('cboe_csv_failed', error=str(exc))
            return None

    def _try_fred_fallback(self) -> pd.DataFrame | None:
        """Fallback: fetch CBOE equity P/C ratio from FRED (series EQUITYPC)."""
        try:
            from pandas_datareader import data as pdr

            end = date.today()
            start = end - timedelta(days=400)  # ~1 year + buffer
            df = pdr.DataReader('EQUITYPC', 'fred', start, end)
            df = df.reset_index()
            df.columns = ['date', 'pc_ratio']
            df['pc_ratio'] = pd.to_numeric(df['pc_ratio'], errors='coerce')
            df = df.dropna(subset=['pc_ratio'])
            df['date'] = pd.to_datetime(df['date']).dt.date

            self._log.info('fred_pc_fetched', rows=len(df))
            return df

        except Exception as exc:
            self._log.warning('fred_pc_fallback_failed', error=str(exc))
            return None

    def _cache_results(
        self, latest_ma10: float, history: list[float], latest_date: str,
    ) -> None:
        """Write computed values to Redis."""
        pipe = self._redis.pipeline()
        pipe.set(PC_RATIO_KEY, str(latest_ma10), ex=REDIS_TTL)
        pipe.set(PC_HISTORY_KEY, json.dumps(history), ex=REDIS_TTL)
        pipe.set(PC_LATEST_DATE_KEY, latest_date, ex=REDIS_TTL)
        pipe.execute()

    def _set_symbol_keys(
        self, latest_ma10: float, history: list[float],
    ) -> None:
        """Set per-symbol Redis keys that the sentiment factor reads.

        The equity P/C ratio is market-wide, so we set it under a
        generic 'equity' key. The sentiment factor's _compute_options_score
        reads `options:{symbol}:pc_ratio` — we populate both an 'equity'
        key and per-ETF keys (since the aggregate ratio applies to all).
        """
        from app.models.etf_universe import ETFUniverse

        pipe = self._redis.pipeline()

        # Set the aggregate key
        pipe.set('options:equity:pc_ratio', str(latest_ma10), ex=REDIS_TTL)
        pipe.set(
            'options:equity:pc_history',
            json.dumps(history),
            ex=REDIS_TTL,
        )

        # Set per-symbol keys so the sentiment factor picks it up
        try:
            etfs = ETFUniverse.query.filter_by(is_active=True).all()
            for etf in etfs:
                pipe.set(
                    f'options:{etf.symbol}:pc_ratio',
                    str(latest_ma10),
                    ex=REDIS_TTL,
                )
                pipe.set(
                    f'options:{etf.symbol}:pc_history',
                    json.dumps(history),
                    ex=REDIS_TTL,
                )
        except Exception as exc:
            # DB might not be available (e.g. during testing)
            self._log.warning(
                'symbol_keys_skip_db', error=str(exc),
            )

        pipe.execute()
