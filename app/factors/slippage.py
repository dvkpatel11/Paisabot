from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.base import FactorBase


class SlippageFactor(FactorBase):
    """F08 — Slippage Estimator (pre-trade gate).

    Almgren-Chriss simplified model:
        participation_rate = order_notional / ADV_30d
        linear_impact = spread_bps / 2
        market_impact = alpha * daily_vol% * 100 * sqrt(participation_rate)
        total_slippage = linear_impact + market_impact

    slippage_score = max(0, 1 - total_slippage_bps / 10)

    Also provides estimate_for_order() for real-time pre-trade checks.
    """

    name = 'slippage_estimator'
    weight = 0.0  # Not in composite; used as pre-trade gate

    ALPHA = 0.10  # Market impact coefficient (calibrated for liquid ETFs)
    MAX_SLIPPAGE_BPS = 10.0  # Score = 0 at this level

    def compute(self, symbols: list[str]) -> dict[str, float]:
        """Compute slippage score assuming a standard order size.

        Uses median position size from config or defaults to $50,000.
        """
        default_notional = 50_000.0
        if self._config:
            try:
                default_notional = self._config.get_float(
                    'execution', 'default_order_notional',
                ) or default_notional
            except Exception:
                pass

        results = {}
        for symbol in symbols:
            ohlcv = self._get_daily_ohlcv(symbol, lookback=40)
            if ohlcv.empty or len(ohlcv) < 20:
                results[symbol] = 0.5
                continue

            total_bps = self._estimate_slippage(ohlcv, default_notional)
            score = max(0.0, 1.0 - total_bps / self.MAX_SLIPPAGE_BPS)
            results[symbol] = max(0.0, min(1.0, score))

        return results

    def estimate_for_order(
        self,
        symbol: str,
        order_notional: float,
    ) -> float:
        """Estimate slippage in basis points for a specific order.

        Used as pre-trade gate: block if > execution.max_slippage_bps (default 8).
        """
        ohlcv = self._get_daily_ohlcv(symbol, lookback=40)
        if ohlcv.empty or len(ohlcv) < 20:
            return 10.0  # conservative: assume high slippage

        return self._estimate_slippage(ohlcv, order_notional)

    def _estimate_slippage(
        self, ohlcv: pd.DataFrame, order_notional: float,
    ) -> float:
        """Core Almgren-Chriss slippage estimation."""
        # ADV (30-day average dollar volume)
        dollar_volume = ohlcv['close'] * ohlcv['volume']
        adv = float(dollar_volume.tail(30).mean())
        if adv <= 0:
            return self.MAX_SLIPPAGE_BPS

        # Participation rate
        participation = order_notional / adv

        # Spread estimate (Roll 1984)
        spread_bps = self._estimate_spread_bps(ohlcv)

        # Realized 20-day daily volatility
        log_returns = np.log(ohlcv['close'] / ohlcv['close'].shift(1)).dropna()
        daily_vol = float(log_returns.tail(20).std()) if len(log_returns) >= 20 else 0.01

        # Almgren-Chriss components
        linear_impact = spread_bps / 2.0
        market_impact = self.ALPHA * daily_vol * 100.0 * np.sqrt(participation)
        total = linear_impact + market_impact

        return float(total)

    @staticmethod
    def _estimate_spread_bps(ohlcv: pd.DataFrame) -> float:
        """Roll (1984) spread estimator."""
        if len(ohlcv) < 22:
            return 3.0

        log_returns = np.log(ohlcv['close'] / ohlcv['close'].shift(1)).dropna()
        if len(log_returns) < 20:
            return 3.0

        r = log_returns.values
        n = min(len(r) - 1, 20)
        if n < 10:
            return 3.0

        cov = np.cov(r[-n - 1:-1], r[-n:])[0, 1]
        spread = 2.0 * np.sqrt(max(-cov, 0.0)) * 10_000
        return float(spread)
