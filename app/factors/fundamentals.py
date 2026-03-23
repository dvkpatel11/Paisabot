"""F12 — Fundamentals Score (stock-only factor).

fundamentals_score = 0.25 * valuation_score
                   + 0.25 * quality_score
                   + 0.25 * growth_score
                   + 0.25 * financial_health_score

Components:
  - Valuation: cross-sectional percentile of (1/PE + 1/PB + 1/PS) composite
    Lower valuation multiples → higher score
  - Quality: percentile of (ROE + profit_margin) composite
    Higher quality → higher score
  - Growth: percentile of (rev_growth_yoy + earnings_growth_yoy) composite
    Higher growth → higher score
  - Financial Health: percentile of (1/debt_to_equity + dividend_yield) composite
    Lower leverage + income → higher score

All sub-scores are cross-sectionally percentile-ranked to [0,1].
Data source: Redis fundamentals:{symbol} hash (populated by FMP ingestion).
Fallback: StockUniverse DB columns.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import structlog

from app.factors.base import FactorBase
from app.utils.normalization import cross_sectional_percentile_rank

logger = structlog.get_logger()


class FundamentalsFactor(FactorBase):
    """Stock fundamentals factor — valuation, quality, growth, health."""

    name = 'fundamentals_score'
    weight = 0.25

    def compute(self, symbols: list[str]) -> dict[str, float]:
        # Load fundamental data for all symbols
        fund_data = self._load_fundamentals(symbols)

        if not fund_data:
            self._log.warning('no_fundamentals_data')
            return {s: 0.5 for s in symbols}

        # Compute raw sub-component values per symbol
        valuation_raw = {}
        quality_raw = {}
        growth_raw = {}
        health_raw = {}

        for symbol in symbols:
            data = fund_data.get(symbol)
            if not data:
                continue

            # Valuation: inverse of multiples (lower = better)
            val = self._valuation_composite(data)
            if val is not None:
                valuation_raw[symbol] = val

            # Quality: ROE + profit margin
            qual = self._quality_composite(data)
            if qual is not None:
                quality_raw[symbol] = qual

            # Growth: revenue + earnings growth
            grw = self._growth_composite(data)
            if grw is not None:
                growth_raw[symbol] = grw

            # Financial health: low leverage + dividend income
            hlth = self._health_composite(data)
            if hlth is not None:
                health_raw[symbol] = hlth

        # Cross-sectional percentile rank each component
        val_pct = cross_sectional_percentile_rank(valuation_raw)
        qual_pct = cross_sectional_percentile_rank(quality_raw)
        grw_pct = cross_sectional_percentile_rank(growth_raw)
        hlth_pct = cross_sectional_percentile_rank(health_raw)

        # Combine with equal weights
        results = {}
        for symbol in symbols:
            components = []
            if symbol in val_pct:
                components.append(val_pct[symbol])
            if symbol in qual_pct:
                components.append(qual_pct[symbol])
            if symbol in grw_pct:
                components.append(grw_pct[symbol])
            if symbol in hlth_pct:
                components.append(hlth_pct[symbol])

            if components:
                score = sum(components) / len(components)
                results[symbol] = max(0.0, min(1.0, score))
            else:
                results[symbol] = 0.5

        return results

    # ── sub-component calculations ───────────────────────────────

    @staticmethod
    def _valuation_composite(data: dict) -> float | None:
        """Composite valuation: inverse of PE + PB + PS.

        Higher value = cheaper stock = better.
        """
        pe = data.get('pe_ratio')
        pb = data.get('pb_ratio')
        ps = data.get('ps_ratio')

        terms = []
        if pe and pe > 0:
            # Cap PE at 100 to prevent near-zero inversions from dominating
            terms.append(1.0 / min(pe, 100.0))
        if pb and pb > 0:
            terms.append(1.0 / min(pb, 50.0))
        if ps and ps > 0:
            terms.append(1.0 / min(ps, 50.0))

        if not terms:
            return None
        return sum(terms)

    @staticmethod
    def _quality_composite(data: dict) -> float | None:
        """Quality: ROE + profit margin."""
        roe = data.get('roe')
        margin = data.get('profit_margin')

        terms = []
        # Cap outliers: ROE > 1.0 (100%) is unusual, margin > 0.5 (50%) is extreme
        if roe is not None:
            terms.append(min(max(roe, -0.5), 1.0))
        if margin is not None:
            terms.append(min(max(margin, -0.5), 0.5))

        if not terms:
            return None
        return sum(terms)

    @staticmethod
    def _growth_composite(data: dict) -> float | None:
        """Growth: revenue growth + earnings growth (YoY)."""
        rev = data.get('revenue_growth_yoy')
        earn = data.get('earnings_growth_yoy')

        terms = []
        # Cap at ±200% to limit outlier impact
        if rev is not None:
            terms.append(min(max(rev, -2.0), 2.0))
        if earn is not None:
            terms.append(min(max(earn, -2.0), 2.0))

        if not terms:
            return None
        return sum(terms)

    @staticmethod
    def _health_composite(data: dict) -> float | None:
        """Financial health: inverse of debt/equity + dividend yield."""
        de = data.get('debt_to_equity')
        div = data.get('dividend_yield')

        terms = []
        if de is not None and de >= 0:
            # Lower debt = better. Invert, cap at D/E=10
            terms.append(1.0 / (1.0 + min(de, 10.0)))
        if div is not None and div >= 0:
            terms.append(min(div, 0.10))  # cap at 10% yield

        if not terms:
            return None
        return sum(terms)

    # ── data loading ─────────────────────────────────────────────

    def _load_fundamentals(self, symbols: list[str]) -> dict[str, dict]:
        """Load fundamental data from Redis cache, fall back to DB.

        Returns {symbol: {pe_ratio, pb_ratio, roe, ...}}.
        """
        results = {}

        # Try Redis first (fast path)
        if self._redis is not None:
            for symbol in symbols:
                cached = self._redis.hgetall(f'fundamentals:{symbol}')
                if cached:
                    parsed = {}
                    for k, v in cached.items():
                        key = k.decode() if isinstance(k, bytes) else k
                        val = v.decode() if isinstance(v, bytes) else v
                        try:
                            parsed[key] = float(val)
                        except (ValueError, TypeError):
                            pass
                    if parsed:
                        results[symbol] = parsed

        # Fall back to DB for any symbols not in Redis
        missing = [s for s in symbols if s not in results]
        if missing:
            db_data = self._load_from_db(missing)
            results.update(db_data)

        return results

    def _load_from_db(self, symbols: list[str]) -> dict[str, dict]:
        """Load fundamentals from StockUniverse table."""
        try:
            from app.models.stock_universe import StockUniverse

            stocks = StockUniverse.query.filter(
                StockUniverse.symbol.in_(symbols),
            ).all()

            results = {}
            for stock in stocks:
                data = {}
                for field in [
                    'pe_ratio', 'forward_pe', 'pb_ratio', 'ps_ratio',
                    'roe', 'debt_to_equity', 'profit_margin', 'dividend_yield',
                    'revenue_growth_yoy', 'earnings_growth_yoy',
                ]:
                    val = getattr(stock, field, None)
                    if val is not None:
                        data[field] = float(val) if isinstance(val, Decimal) else val
                if data:
                    results[stock.symbol] = data

            return results

        except Exception as exc:
            self._log.error('db_fundamentals_load_failed', error=str(exc))
            return {}
