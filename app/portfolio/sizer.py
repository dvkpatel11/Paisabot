from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class PositionSizer:
    """Apply volatility targeting to portfolio weights.

    Scales weights down proportionally when estimated portfolio volatility
    exceeds the target. Never leverages (scale factor capped at 1.0).
    """

    def __init__(self, vol_target: float = 0.12, lookback: int = 60):
        self.vol_target = vol_target
        self.lookback = lookback
        self._log = logger.bind(component='position_sizer')

    def apply_vol_target(
        self,
        weights: dict[str, float],
        prices_df: pd.DataFrame,
    ) -> dict[str, float]:
        """Scale weights so portfolio vol ≈ vol_target.

        Args:
            weights: {symbol: weight} from PortfolioConstructor.
            prices_df: daily close prices (must contain weighted symbols).

        Returns:
            Adjusted weights dict. Sum may be < original if vol was high.
        """
        if not weights:
            return weights

        available = [s for s in weights if s in prices_df.columns]
        if len(available) < 2:
            return weights

        returns_df = prices_df[available].pct_change().dropna()
        if len(returns_df) < self.lookback:
            self._log.warning(
                'insufficient_returns_data',
                rows=len(returns_df),
                required=self.lookback,
            )
            return weights

        # Use recent window for covariance
        recent = returns_df.iloc[-self.lookback:]
        cov_matrix = recent.cov().values

        w_arr = np.array([weights[sym] for sym in available])
        portfolio_var = w_arr @ cov_matrix @ w_arr
        portfolio_vol = float(np.sqrt(portfolio_var * 252))

        if portfolio_vol <= 0 or np.isnan(portfolio_vol):
            return weights

        if portfolio_vol > self.vol_target:
            scale = self.vol_target / portfolio_vol
            scale = min(scale, 1.0)  # never leverage

            self._log.info(
                'vol_scaled',
                portfolio_vol=round(portfolio_vol, 4),
                target=self.vol_target,
                scale_factor=round(scale, 4),
            )

            return {
                sym: round(w * scale, 6) for sym, w in weights.items()
            }

        return weights

    def estimate_portfolio_vol(
        self,
        weights: dict[str, float],
        prices_df: pd.DataFrame,
    ) -> float | None:
        """Estimate annualized portfolio volatility."""
        available = [s for s in weights if s in prices_df.columns]
        if len(available) < 2:
            return None

        returns_df = prices_df[available].pct_change().dropna()
        if len(returns_df) < self.lookback:
            return None

        recent = returns_df.iloc[-self.lookback:]
        cov_matrix = recent.cov().values
        w_arr = np.array([weights[sym] for sym in available])

        portfolio_var = w_arr @ cov_matrix @ w_arr
        return float(np.sqrt(portfolio_var * 252))
