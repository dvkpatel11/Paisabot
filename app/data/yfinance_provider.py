"""Yahoo Finance provider — complementary data not covered by Alpaca or FMP.

Provides:
  - Dividend history (ex-date, amount, frequency)
  - Stock split history
  - Options chains (calls/puts with greeks)

Does NOT duplicate: price bars (Alpaca), fundamentals/ratios (FMP),
earnings calendar (FMP), real-time quotes (Alpaca WebSocket).

yfinance is rate-limited by Yahoo (~2000 req/hr).  Batch via Ticker
objects and cache aggressively in Redis.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import structlog

logger = structlog.get_logger()


class YFinanceProvider:
    """Fetch dividends, splits, and options chains from Yahoo Finance."""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._log = logger.bind(provider='yfinance')

    # ── dividends ─────────────────────────────────────────────────

    def get_dividends(self, symbol: str, years: int = 5) -> list[dict]:
        """Fetch dividend history for a symbol.

        Returns list of {ex_date, amount, currency} sorted newest-first.
        """
        cached = self._read_cache(f'yf:dividends:{symbol}')
        if cached is not None:
            return cached

        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            divs = ticker.dividends
            if divs is None or divs.empty:
                self._write_cache(f'yf:dividends:{symbol}', [], ttl=43200)
                return []

            cutoff = datetime.now(timezone.utc).replace(
                year=datetime.now(timezone.utc).year - years,
            )

            results = []
            for dt, amount in divs.items():
                ts = dt.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                results.append({
                    'ex_date': ts.strftime('%Y-%m-%d'),
                    'amount': round(float(amount), 4),
                })

            results.sort(key=lambda x: x['ex_date'], reverse=True)
            self._write_cache(f'yf:dividends:{symbol}', results, ttl=43200)
            self._log.info('dividends_fetched', symbol=symbol, count=len(results))
            return results

        except Exception as exc:
            self._log.error('dividends_fetch_failed', symbol=symbol, error=str(exc))
            return []

    def get_dividend_summary(self, symbol: str) -> dict:
        """Compute dividend summary: yield, frequency, growth, payout streak.

        Returns dict with annual_dividend, frequency, growth_5y, streak_years.
        """
        divs = self.get_dividends(symbol, years=10)
        if not divs:
            return {
                'symbol': symbol,
                'pays_dividend': False,
                'annual_dividend': 0,
                'frequency': None,
                'growth_5y': None,
                'streak_years': 0,
            }

        # Group by year
        by_year: dict[int, float] = {}
        for d in divs:
            yr = int(d['ex_date'][:4])
            by_year[yr] = by_year.get(yr, 0) + d['amount']

        current_year = datetime.now(timezone.utc).year
        # Use most recent complete year (or current if >2 payments)
        recent_year = current_year - 1
        annual = by_year.get(recent_year, by_year.get(current_year, 0))

        # Frequency: count payments in most recent complete year
        count_recent = sum(
            1 for d in divs if d['ex_date'].startswith(str(recent_year))
        )
        freq_map = {1: 'annual', 2: 'semi-annual', 4: 'quarterly', 12: 'monthly'}
        frequency = freq_map.get(count_recent, f'{count_recent}x/year' if count_recent else None)

        # 5-year growth: CAGR
        growth_5y = None
        yr_5_ago = current_year - 5
        if yr_5_ago in by_year and by_year[yr_5_ago] > 0 and recent_year in by_year:
            growth_5y = round(
                (by_year[recent_year] / by_year[yr_5_ago]) ** (1 / 5) - 1, 4,
            )

        # Consecutive years with payments
        streak = 0
        for yr in range(recent_year, recent_year - 30, -1):
            if by_year.get(yr, 0) > 0:
                streak += 1
            else:
                break

        return {
            'symbol': symbol,
            'pays_dividend': True,
            'annual_dividend': round(annual, 4),
            'frequency': frequency,
            'growth_5y': growth_5y,
            'streak_years': streak,
        }

    # ── splits ────────────────────────────────────────────────────

    def get_splits(self, symbol: str) -> list[dict]:
        """Fetch stock split history.

        Returns list of {date, ratio, description} sorted newest-first.
        """
        cached = self._read_cache(f'yf:splits:{symbol}')
        if cached is not None:
            return cached

        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            splits = ticker.splits
            if splits is None or splits.empty:
                self._write_cache(f'yf:splits:{symbol}', [], ttl=86400)
                return []

            results = []
            for dt, ratio in splits.items():
                ratio_f = float(ratio)
                if ratio_f == 0:
                    continue
                results.append({
                    'date': dt.strftime('%Y-%m-%d'),
                    'ratio': ratio_f,
                    'description': self._format_split(ratio_f),
                })

            results.sort(key=lambda x: x['date'], reverse=True)
            self._write_cache(f'yf:splits:{symbol}', results, ttl=86400)
            self._log.info('splits_fetched', symbol=symbol, count=len(results))
            return results

        except Exception as exc:
            self._log.error('splits_fetch_failed', symbol=symbol, error=str(exc))
            return []

    # ── options chain ─────────────────────────────────────────────

    def get_options_expirations(self, symbol: str) -> list[str]:
        """Get available options expiration dates.

        Returns list of date strings sorted ascending.
        """
        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            return list(expirations) if expirations else []
        except Exception as exc:
            self._log.error('options_exp_failed', symbol=symbol, error=str(exc))
            return []

    def get_options_chain(
        self,
        symbol: str,
        expiration: str | None = None,
    ) -> dict:
        """Fetch options chain for a given expiration.

        If expiration is None, uses the nearest expiration.

        Returns {
            expiration, calls: [...], puts: [...],
            put_call_ratio, max_pain, total_oi
        }
        """
        cache_key = f'yf:options:{symbol}:{expiration or "nearest"}'
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            if not expirations:
                return {'symbol': symbol, 'error': 'no_options_available'}

            exp = expiration if expiration and expiration in expirations else expirations[0]
            chain = ticker.option_chain(exp)

            calls = self._serialize_options(chain.calls, 'call')
            puts = self._serialize_options(chain.puts, 'put')

            # Put/call ratio by OI
            total_call_oi = sum(c.get('open_interest', 0) for c in calls)
            total_put_oi = sum(p.get('open_interest', 0) for p in puts)
            pc_ratio = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else None

            result = {
                'symbol': symbol,
                'expiration': exp,
                'calls': calls,
                'puts': puts,
                'total_call_oi': total_call_oi,
                'total_put_oi': total_put_oi,
                'total_oi': total_call_oi + total_put_oi,
                'put_call_ratio': pc_ratio,
            }

            self._write_cache(cache_key, result, ttl=1800)  # 30 min
            self._log.info(
                'options_chain_fetched',
                symbol=symbol,
                expiration=exp,
                calls=len(calls),
                puts=len(puts),
            )
            return result

        except Exception as exc:
            self._log.error('options_chain_failed', symbol=symbol, error=str(exc))
            return {'symbol': symbol, 'error': str(exc)}

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _serialize_options(df, option_type: str) -> list[dict]:
        """Convert yfinance options DataFrame to list of dicts."""
        if df is None or df.empty:
            return []

        results = []
        for _, row in df.iterrows():
            results.append({
                'type': option_type,
                'strike': float(row.get('strike', 0)),
                'last_price': float(row.get('lastPrice', 0)),
                'bid': float(row.get('bid', 0)),
                'ask': float(row.get('ask', 0)),
                'volume': int(row.get('volume', 0) or 0),
                'open_interest': int(row.get('openInterest', 0) or 0),
                'implied_volatility': round(float(row.get('impliedVolatility', 0) or 0), 4),
                'in_the_money': bool(row.get('inTheMoney', False)),
            })
        return results

    @staticmethod
    def _format_split(ratio: float) -> str:
        """Format split ratio as human-readable string."""
        if ratio > 1:
            return f'{ratio:.0f}-for-1 split'
        elif ratio > 0:
            inverse = 1 / ratio
            return f'1-for-{inverse:.0f} reverse split'
        return f'{ratio} ratio'

    def _read_cache(self, key: str):
        """Read JSON from Redis cache."""
        if self._redis is None:
            return None
        try:
            import json
            raw = self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            return None

    def _write_cache(self, key: str, data, ttl: int = 3600) -> None:
        """Write JSON to Redis cache."""
        if self._redis is None:
            return
        try:
            import json
            self._redis.set(key, json.dumps(data, default=str), ex=ttl)
        except Exception as exc:
            self._log.error('cache_write_failed', key=key, error=str(exc))
