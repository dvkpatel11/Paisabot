from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.base import FactorBase
from app.factors.dispersion import SECTOR_ETFS


class BreadthFactor(FactorBase):
    """F06 — Breadth Score (weight: 0.15).

    breadth_score = 0.30 * pct_above_SMA50
                  + 0.30 * pct_above_SMA200
                  + 0.20 * ad_ema_score
                  + 0.20 * sector_participation

    Measures market health via price-breadth indicators.
    Deterioration: 5d change < -0.15 triggers warning.
    """

    name = 'breadth_score'
    weight = 0.15

    def compute(self, symbols: list[str]) -> dict[str, float]:
        # Need sector data for sector_participation component
        all_symbols = list(set(symbols + SECTOR_ETFS))
        closes_df = self._get_multi_closes(all_symbols, lookback=220)

        if closes_df.empty:
            self._log.warning('no_data_for_breadth')
            return {s: 0.5 for s in symbols}

        # Pre-compute sector participation (market-level)
        sector_part = self._compute_sector_participation(closes_df)

        results = {}
        for symbol in symbols:
            closes = closes_df.get(symbol)
            if closes is None or len(closes.dropna()) < 50:
                results[symbol] = 0.5
                continue

            closes = closes.dropna()

            # 1. % above SMA-50
            sma50_score = self._above_sma(closes, 50)

            # 2. % above SMA-200
            sma200_score = self._above_sma(closes, 200)

            # 3. A/D EMA score
            ad_score = self._compute_ad_ema_score(closes)

            # Combine
            score = (
                0.30 * sma50_score
                + 0.30 * sma200_score
                + 0.20 * ad_score
                + 0.20 * sector_part
            )
            results[symbol] = max(0.0, min(1.0, score))

        # Check deterioration
        self._check_deterioration(results)

        return results

    @staticmethod
    def _above_sma(closes: pd.Series, period: int) -> float:
        """1.0 if current close > SMA(period), else 0.0."""
        if len(closes) < period:
            return 0.5  # insufficient data
        sma = closes.rolling(window=period).mean().iloc[-1]
        return 1.0 if closes.iloc[-1] > sma else 0.0

    @staticmethod
    def _compute_ad_ema_score(closes: pd.Series) -> float:
        """Advance/Decline EMA score.

        Computes daily changes (+1/-1), takes 10-day EMA,
        normalizes to [0, 1].
        """
        daily_changes = closes.diff().apply(lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0))
        if len(daily_changes) < 10:
            return 0.5

        ema = daily_changes.ewm(span=10).mean().iloc[-1]
        # Normalize: ema ranges roughly [-1, 1], map to [0, 1]
        score = np.clip((ema + 0.01) / 0.02, 0.0, 1.0)
        return float(score)

    def _compute_sector_participation(self, closes_df: pd.DataFrame) -> float:
        """Fraction of sectors with positive 5-day returns."""
        available = [s for s in SECTOR_ETFS if s in closes_df.columns]
        if len(available) < 5:
            return 0.5

        positive_count = 0
        for sector in available:
            closes = closes_df[sector].dropna()
            if len(closes) < 6:
                continue
            ret_5d = (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]
            if ret_5d > 0:
                positive_count += 1

        return positive_count / len(available)

    def _check_deterioration(self, results: dict[str, float]) -> None:
        """Flag breadth deterioration if 5d change < -0.15."""
        if self._redis is None:
            return

        for symbol, score in results.items():
            key = f'breadth:history:{symbol}'
            try:
                # Push current score to Redis list (keep last 10)
                self._redis.lpush(key, str(score))
                self._redis.ltrim(key, 0, 9)

                # Check 5-day deterioration
                history = self._redis.lrange(key, 0, 5)
                if len(history) >= 5:
                    current = float(history[0])
                    five_ago = float(history[4])
                    change = current - five_ago
                    if change < -0.15:
                        self._log.warning(
                            'breadth_deterioration',
                            symbol=symbol,
                            change=round(change, 4),
                        )
                        self._redis.set(
                            f'breadth_warning:{symbol}', '1', ex=86400,
                        )
            except Exception:
                pass
