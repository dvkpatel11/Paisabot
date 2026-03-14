from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.base import FactorBase
from app.utils.normalization import cross_sectional_percentile_rank


class TrendFactor(FactorBase):
    """F01 — Trend Score (weight: 0.25).

    trend_score = 0.40 * MA_alignment + 0.35 * momentum_pct + 0.25 * RS_pct

    Components:
    - MA Alignment: EMA-20/50/200 stacking (bullish=1.0, bearish=0.0, mixed=0.5)
    - Momentum: Jegadeesh-Titman 12-1 (252d return minus 21d return), cross-sectional percentile
    - Relative Strength: 63d excess return vs SPY, cross-sectional percentile
    """

    name = 'trend_score'
    weight = 0.25

    def compute(self, symbols: list[str]) -> dict[str, float]:
        closes_df = self._get_multi_closes(symbols + ['SPY'], lookback=280)
        if closes_df.empty or len(closes_df) < 50:
            self._log.warning('insufficient_data', rows=len(closes_df))
            return {s: 0.5 for s in symbols}

        results = {}
        # Pre-compute momentum and RS values for cross-sectional ranking
        momentum_raw = {}
        rs_raw = {}

        spy_closes = closes_df.get('SPY')
        spy_return_63d = self._pct_change(spy_closes, 63) if spy_closes is not None else 0.0

        for symbol in symbols:
            closes = closes_df.get(symbol)
            if closes is None or len(closes.dropna()) < 50:
                results[symbol] = 0.5
                continue

            # MA Alignment
            ma_score = self._compute_ma_alignment(closes)

            # Raw momentum (12-1): 252d return minus 21d return
            mom_12m = self._pct_change(closes, 252)
            mom_1m = self._pct_change(closes, 21)
            if mom_12m is not None and mom_1m is not None:
                momentum_raw[symbol] = mom_12m - mom_1m
            else:
                momentum_raw[symbol] = 0.0

            # Raw relative strength vs SPY (63d excess return)
            sym_return_63d = self._pct_change(closes, 63)
            if sym_return_63d is not None and spy_return_63d is not None and spy_return_63d != 0:
                rs_raw[symbol] = sym_return_63d - spy_return_63d
            else:
                rs_raw[symbol] = 0.0

            results[symbol] = ma_score  # placeholder, will combine below

        # Cross-sectional percentile rank for momentum and RS
        momentum_pct = cross_sectional_percentile_rank(momentum_raw)
        rs_pct = cross_sectional_percentile_rank(rs_raw)

        # Combine components
        for symbol in symbols:
            if symbol not in results:
                continue
            ma_score = results[symbol]
            mom_score = momentum_pct.get(symbol, 0.5)
            rs_score = rs_pct.get(symbol, 0.5)

            trend = 0.40 * ma_score + 0.35 * mom_score + 0.25 * rs_score
            results[symbol] = max(0.0, min(1.0, trend))

        return results

    def _compute_ma_alignment(self, closes: pd.Series) -> float:
        """Score EMA-20/50/200 alignment.

        Returns 1.0 (bullish: 20>50>200), 0.0 (bearish: 20<50<200), 0.5 (mixed).
        """
        closes = closes.dropna()
        if len(closes) < 200:
            # Not enough data for EMA-200, use available EMAs
            if len(closes) < 50:
                return 0.5
            ema20 = closes.ewm(span=20).mean().iloc[-1]
            ema50 = closes.ewm(span=50).mean().iloc[-1]
            return 1.0 if ema20 > ema50 else 0.0

        ema20 = closes.ewm(span=20).mean().iloc[-1]
        ema50 = closes.ewm(span=50).mean().iloc[-1]
        ema200 = closes.ewm(span=200).mean().iloc[-1]

        if ema20 > ema50 > ema200:
            return 1.0
        elif ema20 < ema50 < ema200:
            return 0.0
        else:
            return 0.5

    @staticmethod
    def _pct_change(series: pd.Series, periods: int) -> float | None:
        """Compute percentage change over N periods."""
        series = series.dropna()
        if len(series) <= periods:
            return None
        current = series.iloc[-1]
        prior = series.iloc[-periods - 1]
        if prior == 0:
            return None
        return (current - prior) / prior

    def compute_sector_momentum_rank(
        self, symbols: list[str],
    ) -> dict[str, float]:
        """F09 — Sector Momentum Rank (supplementary).

        63-day return cross-sectionally ranked.
        """
        closes_df = self._get_multi_closes(symbols, lookback=80)
        if closes_df.empty:
            return {s: 0.5 for s in symbols}

        returns_raw = {}
        for symbol in symbols:
            closes = closes_df.get(symbol)
            if closes is not None and len(closes.dropna()) > 63:
                ret = self._pct_change(closes, 63)
                returns_raw[symbol] = ret if ret is not None else 0.0
            else:
                returns_raw[symbol] = 0.0

        return cross_sectional_percentile_rank(returns_raw)

    def compute_beta_adjusted(
        self, symbols: list[str],
    ) -> dict[str, float]:
        """F10 — Beta-Adjusted Momentum (supplementary).

        63-day return adjusted for SPY beta, cross-sectionally ranked.
        """
        closes_df = self._get_multi_closes(symbols + ['SPY'], lookback=280)
        if closes_df.empty:
            return {s: 0.5 for s in symbols}

        spy_returns = closes_df.get('SPY')
        if spy_returns is None:
            return {s: 0.5 for s in symbols}
        spy_returns = spy_returns.pct_change().dropna()

        adjusted_raw = {}
        for symbol in symbols:
            closes = closes_df.get(symbol)
            if closes is None or len(closes.dropna()) < 63:
                adjusted_raw[symbol] = 0.0
                continue

            sym_returns = closes.pct_change().dropna()
            # Align series
            aligned = pd.DataFrame({'sym': sym_returns, 'spy': spy_returns}).dropna()
            if len(aligned) < 30:
                adjusted_raw[symbol] = 0.0
                continue

            # OLS beta
            cov = aligned['sym'].cov(aligned['spy'])
            var = aligned['spy'].var()
            beta = cov / var if var > 0 else 1.0

            # Beta-adjusted return
            sym_ret = self._pct_change(closes, 63) or 0.0
            spy_ret = self._pct_change(closes_df['SPY'], 63) or 0.0
            adjusted_raw[symbol] = sym_ret - beta * spy_ret

        return cross_sectional_percentile_rank(adjusted_raw)
