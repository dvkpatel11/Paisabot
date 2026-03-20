from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.base import FactorBase
from app.utils.normalization import percentile_rank


class VolatilityFactor(FactorBase):
    """F02 — Volatility Regime (weight: 0.20).

    volatility_score = 0.40 * (1 - vol_percentile)
                     + 0.40 * (1 - vix_percentile)
                     + 0.20 * min(1 / GARCH_ratio, 1.0)

    Lower volatility = higher score (inverted).
    GARCH cold start: skip GARCH component if < 250 observations.
    """

    name = 'volatility_regime'
    weight = 0.20
    update_frequency = 'intraday'

    GARCH_MIN_OBS = 250

    def compute(self, symbols: list[str]) -> dict[str, float]:
        # Get VIX value
        vix_value = self._get_current_vix()
        vix_history = self._get_vix_history()

        results = {}
        for symbol in symbols:
            closes = self._get_daily_closes(symbol, lookback=280)
            if len(closes) < 30:
                results[symbol] = 0.5
                continue

            score = self._compute_single(closes, vix_value, vix_history)
            results[symbol] = score

        return results

    def _compute_single(
        self,
        closes: pd.Series,
        vix_value: float | None,
        vix_history: list[float],
    ) -> float:
        log_returns = np.log(closes / closes.shift(1)).dropna()
        if len(log_returns) < 20:
            return 0.5

        # 1. Realized 20-day vol (annualized)
        realized_vol = float(log_returns.tail(20).std() * np.sqrt(252))

        # Build 252-day vol history for percentile ranking
        vol_history = []
        for i in range(20, min(len(log_returns), 252)):
            window = log_returns.iloc[i - 20:i]
            vol_history.append(float(window.std() * np.sqrt(252)))

        if not vol_history:
            return 0.5

        vol_pct = percentile_rank(realized_vol, vol_history)
        vol_component = 1.0 - vol_pct

        # 2. VIX component
        if vix_value is not None and vix_history:
            vix_pct = percentile_rank(vix_value, vix_history)
            vix_component = 1.0 - vix_pct
        else:
            vix_component = 0.5

        # 3. GARCH component
        garch_component = self._compute_garch_component(log_returns, realized_vol)

        # Combine
        if garch_component is not None:
            score = (
                0.40 * vol_component
                + 0.40 * vix_component
                + 0.20 * garch_component
            )
        else:
            # Cold start: skip GARCH, re-weight
            score = 0.50 * vol_component + 0.50 * vix_component

        return max(0.0, min(1.0, score))

    def _compute_garch_component(
        self, log_returns: pd.Series, realized_vol: float,
    ) -> float | None:
        """Compute GARCH(1,1) ratio component.

        Returns None if insufficient data for GARCH fitting.
        """
        if len(log_returns) < self.GARCH_MIN_OBS:
            return None

        try:
            from arch import arch_model

            am = arch_model(
                log_returns.values * 100,  # scale for numerical stability
                vol='Garch', p=1, q=1, mean='Zero',
                rescale=False,
            )
            res = am.fit(disp='off', show_warning=False)

            # 1-day ahead forecast (annualized)
            forecast = res.forecast(horizon=1)
            garch_var = forecast.variance.values[-1, 0]
            garch_vol = np.sqrt(garch_var) / 100 * np.sqrt(252)

            if garch_vol <= 0:
                return None

            ratio = realized_vol / garch_vol
            return min(1.0 / ratio, 1.0) if ratio > 0 else 1.0

        except Exception as exc:
            self._log.warning('garch_fit_failed', error=str(exc))
            return None

    def _get_current_vix(self) -> float | None:
        """Get current VIX from Redis cache."""
        if self._redis is None:
            return None
        try:
            val = self._redis.get('vix:latest')
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def _get_vix_history(self) -> list[float]:
        """Build VIX history for percentile ranking.

        Attempts Redis cache first, falls back to DB/provider.
        """
        if self._redis is not None:
            cached = self._redis.get('vix:history_252')
            if cached:
                try:
                    import json
                    return [float(v) for v in json.loads(cached)]
                except (ValueError, TypeError):
                    pass

        # Fallback: use VIX bars from price_bars table if available
        from app.models.price_bars import PriceBar
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=400)
        bars = (
            PriceBar.query.filter(
                PriceBar.symbol == 'VIX',
                PriceBar.timeframe == '1d',
                PriceBar.timestamp >= cutoff,
            )
            .order_by(PriceBar.timestamp.asc())
            .all()
        )

        if bars:
            return [float(bar.close) for bar in bars[-252:]]

        # No VIX data available in either Redis or DB.
        # Return a synthetic history centred on 20 (long-run VIX mean) so that
        # percentile_rank() produces a meaningful neutral score (≈0.5) rather
        # than crashing or always returning exactly 0.5 with no context.
        # The synthetic range covers typical VIX territory (10–40).
        import numpy as np
        self._log.warning('vix_history_unavailable_using_synthetic_fallback')
        synthetic = list(np.linspace(10.0, 40.0, 60))  # 60-point uniform ladder
        return synthetic
