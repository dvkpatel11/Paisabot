"""F13 — Earnings Score (stock-only factor).

earnings_score = 0.35 * surprise_score
               + 0.30 * proximity_score
               + 0.35 * estimate_revision_score

Components:
  - Surprise: cross-sectional percentile of 3-quarter average earnings surprise
    Consistently beating estimates → higher score
  - Proximity: distance to next earnings date, converted to risk score
    Far from earnings = low risk = higher score (inverted)
    Pre-earnings blackout zone (≤3 days) → 0.0
  - Estimate Revision: most recent surprise as proxy for estimate momentum
    Positive surprises suggest upward revisions → higher score

Data source: Redis earnings:{symbol}:* keys + StockUniverse columns.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import structlog

from app.factors.base import FactorBase
from app.utils.normalization import cross_sectional_percentile_rank

logger = structlog.get_logger()


# ── proximity scoring ────────────────────────────────────────────
# Days to earnings → score mapping
# Blackout zone (≤3 days): score = 0.0 (max risk)
# 4-7 days:  linearly ramp from 0.1 to 0.4
# 8-30 days: linearly ramp from 0.4 to 0.8
# 31+ days:  score = 1.0 (earnings risk is distant)
BLACKOUT_DAYS = 3
PROXIMITY_THRESHOLDS = [
    (BLACKOUT_DAYS, 0.0),  # ≤3 days: blackout
    (7, 0.4),              # 4-7 days: caution
    (30, 0.8),             # 8-30 days: moderate
    (999, 1.0),            # 31+ days: safe
]


def _days_to_proximity_score(days: int | None) -> float:
    """Convert days-to-earnings into a [0,1] safety score.

    Lower days = more risk = lower score.
    None (no known date) → 0.7 (moderate, not penalizing unknowns too much).
    """
    if days is None:
        return 0.7

    if days <= BLACKOUT_DAYS:
        return 0.0

    prev_days, prev_score = BLACKOUT_DAYS, 0.0
    for threshold_days, threshold_score in PROXIMITY_THRESHOLDS:
        if days <= threshold_days:
            # Linear interpolation
            frac = (days - prev_days) / max(threshold_days - prev_days, 1)
            return prev_score + frac * (threshold_score - prev_score)
        prev_days, prev_score = threshold_days, threshold_score

    return 1.0


class EarningsFactor(FactorBase):
    """Stock earnings factor — surprise history, proximity risk, revisions."""

    name = 'earnings_score'
    weight = 0.15

    def compute(self, symbols: list[str]) -> dict[str, float]:
        earnings_data = self._load_earnings_data(symbols)

        if not earnings_data:
            self._log.warning('no_earnings_data')
            return {s: 0.5 for s in symbols}

        # Compute raw values for cross-sectional ranking
        surprise_raw = {}
        revision_raw = {}
        proximity_scores = {}

        today = date.today()

        for symbol in symbols:
            data = earnings_data.get(symbol, {})

            # Surprise: 3-quarter average
            surprise_3q = data.get('earnings_surprise_3q_avg')
            if surprise_3q is not None:
                surprise_raw[symbol] = surprise_3q

            # Latest surprise as revision proxy
            last_surprise = data.get('last_earnings_surprise')
            if last_surprise is not None:
                revision_raw[symbol] = last_surprise

            # Proximity: days to next earnings
            days_to = data.get('days_to_earnings')
            if days_to is None:
                next_date_str = data.get('next_earnings_date')
                if next_date_str:
                    try:
                        next_date = (
                            date.fromisoformat(next_date_str)
                            if isinstance(next_date_str, str)
                            else next_date_str
                        )
                        days_to = (next_date - today).days
                    except (ValueError, TypeError):
                        days_to = None

            proximity_scores[symbol] = _days_to_proximity_score(days_to)

        # Cross-sectional percentile rank surprise and revision
        surprise_pct = cross_sectional_percentile_rank(surprise_raw)
        revision_pct = cross_sectional_percentile_rank(revision_raw)

        # Combine
        results = {}
        for symbol in symbols:
            surprise = surprise_pct.get(symbol, 0.5)
            proximity = proximity_scores.get(symbol, 0.7)
            revision = revision_pct.get(symbol, 0.5)

            score = (
                0.35 * surprise
                + 0.30 * proximity
                + 0.35 * revision
            )
            results[symbol] = max(0.0, min(1.0, score))

        return results

    # ── data loading ─────────────────────────────────────────────

    def _load_earnings_data(self, symbols: list[str]) -> dict[str, dict]:
        """Load earnings data from Redis, fall back to DB.

        Returns {symbol: {earnings_surprise_3q_avg, last_earnings_surprise,
                          days_to_earnings, next_earnings_date}}.
        """
        results = {}

        # Redis fast path
        if self._redis is not None:
            for symbol in symbols:
                data = {}

                # Days to earnings (cached by fundamentals_ingestion)
                days_raw = self._redis.get(f'earnings:{symbol}:days_to')
                if days_raw is not None:
                    try:
                        val = days_raw.decode() if isinstance(days_raw, bytes) else days_raw
                        data['days_to_earnings'] = int(val)
                    except (ValueError, TypeError):
                        pass

                next_date = self._redis.get(f'earnings:{symbol}:next_date')
                if next_date is not None:
                    val = next_date.decode() if isinstance(next_date, bytes) else next_date
                    data['next_earnings_date'] = val

                # Fundamentals hash has surprise data
                fund = self._redis.hgetall(f'fundamentals:{symbol}')
                if fund:
                    for key in ['last_earnings_surprise', 'earnings_surprise_3q_avg']:
                        raw = fund.get(key) or fund.get(key.encode())
                        if raw is not None:
                            try:
                                val = raw.decode() if isinstance(raw, bytes) else raw
                                data[key] = float(val)
                            except (ValueError, TypeError):
                                pass

                if data:
                    results[symbol] = data

        # DB fallback for missing symbols
        missing = [s for s in symbols if s not in results]
        if missing:
            db_data = self._load_from_db(missing)
            results.update(db_data)

        return results

    def _load_from_db(self, symbols: list[str]) -> dict[str, dict]:
        """Load earnings data from StockUniverse table."""
        try:
            from app.models.stock_universe import StockUniverse

            stocks = StockUniverse.query.filter(
                StockUniverse.symbol.in_(symbols),
            ).all()

            today = date.today()
            results = {}
            for stock in stocks:
                data = {}

                if stock.next_earnings_date:
                    data['next_earnings_date'] = stock.next_earnings_date
                    data['days_to_earnings'] = (stock.next_earnings_date - today).days

                if stock.last_earnings_surprise is not None:
                    data['last_earnings_surprise'] = float(stock.last_earnings_surprise)

                if stock.earnings_surprise_3q_avg is not None:
                    data['earnings_surprise_3q_avg'] = float(stock.earnings_surprise_3q_avg)

                if data:
                    results[stock.symbol] = data

            return results

        except Exception as exc:
            self._log.error('db_earnings_load_failed', error=str(exc))
            return {}

    def is_in_blackout(self, symbol: str) -> bool:
        """Check if a symbol is within the earnings blackout zone.

        Used by PreTradeGate to block trades near earnings.
        """
        days = None

        if self._redis is not None:
            raw = self._redis.get(f'earnings:{symbol}:days_to')
            if raw is not None:
                try:
                    val = raw.decode() if isinstance(raw, bytes) else raw
                    days = int(val)
                except (ValueError, TypeError):
                    pass

        if days is None:
            try:
                from app.models.stock_universe import StockUniverse
                stock = StockUniverse.query.filter_by(symbol=symbol).first()
                if stock and stock.next_earnings_date:
                    days = (stock.next_earnings_date - date.today()).days
            except Exception:
                pass

        if days is None:
            return False

        return days <= BLACKOUT_DAYS
