from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.base import FactorBase


class LiquidityFactor(FactorBase):
    """F07 — Liquidity Score (weight: 0.10).

    liquidity_score = 0.50 * adv_score + 0.50 * spread_score

    adv_score = min(ADV_30d / $20M, 1.0)
    spread_score = max(0, 1 - spread_est_bps / 10)

    Hard filter: ADV < $20M or spread > 10bps blocks all signals.
    """

    name = 'liquidity_score'
    weight = 0.10

    ADV_THRESHOLD = 20_000_000  # $20M
    SPREAD_THRESHOLD_BPS = 10.0

    def compute(self, symbols: list[str]) -> dict[str, float]:
        results = {}
        for symbol in symbols:
            ohlcv = self._get_daily_ohlcv(symbol, lookback=40)
            if ohlcv.empty or len(ohlcv) < 5:
                results[symbol] = 0.5
                continue

            adv_30d = self._compute_adv(ohlcv)
            spread_bps = self._estimate_spread(ohlcv)

            adv_score = min(adv_30d / self.ADV_THRESHOLD, 1.0)
            spread_score = max(0.0, 1.0 - spread_bps / self.SPREAD_THRESHOLD_BPS)

            score = 0.50 * adv_score + 0.50 * spread_score

            # Cache ADV and spread for other factors (slippage, signal filter)
            if self._redis is not None:
                self._redis.set(
                    f'etf:{symbol}:adv_30d_m',
                    str(round(adv_30d / 1_000_000, 2)),
                    ex=86400,
                )
                self._redis.set(
                    f'etf:{symbol}:spread_bps',
                    str(round(spread_bps, 2)),
                    ex=86400,
                )

            results[symbol] = max(0.0, min(1.0, score))

        return results

    def is_tradable(self, symbol: str) -> bool:
        """Hard filter check: is the symbol liquid enough to trade?

        Returns False if ADV < $20M or spread > 10bps.
        """
        ohlcv = self._get_daily_ohlcv(symbol, lookback=40)
        if ohlcv.empty:
            return False

        adv = self._compute_adv(ohlcv)
        spread = self._estimate_spread(ohlcv)

        if adv < self.ADV_THRESHOLD:
            self._log.info(
                'liquidity_filter_blocked',
                symbol=symbol,
                reason='adv_too_low',
                adv=adv,
            )
            return False

        if spread > self.SPREAD_THRESHOLD_BPS:
            self._log.info(
                'liquidity_filter_blocked',
                symbol=symbol,
                reason='spread_too_wide',
                spread_bps=spread,
            )
            return False

        return True

    @staticmethod
    def _compute_adv(ohlcv: pd.DataFrame) -> float:
        """30-day average daily dollar volume."""
        dollar_volume = ohlcv['close'] * ohlcv['volume']
        adv = dollar_volume.tail(30).mean()
        return float(adv) if not np.isnan(adv) else 0.0

    @staticmethod
    def _estimate_spread(ohlcv: pd.DataFrame) -> float:
        """Estimate spread using Roll (1984) OHLCV estimator.

        spread_est_bps = 2 * sqrt(max(-cov(r_t, r_{t-1}), 0)) * 10000
        """
        if len(ohlcv) < 22:
            return 5.0  # conservative default

        log_returns = np.log(ohlcv['close'] / ohlcv['close'].shift(1)).dropna()
        if len(log_returns) < 20:
            return 5.0

        # Rolling 20-day covariance of returns with lagged returns
        r = log_returns.values
        r_lag = log_returns.shift(1).dropna().values
        r_trimmed = r[1:]  # align lengths

        n = min(len(r_trimmed), len(r_lag), 20)
        if n < 10:
            return 5.0

        cov = np.cov(r_trimmed[-n:], r_lag[-n:])[0, 1]
        spread_est = 2.0 * np.sqrt(max(-cov, 0.0)) * 10_000
        return float(spread_est)
