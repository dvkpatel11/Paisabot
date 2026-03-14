from __future__ import annotations

import math

import structlog

logger = structlog.get_logger()


class SlippageTracker:
    """Pre-trade slippage estimation (Almgren-Chriss) and post-trade measurement.

    Pre-trade: simplified Almgren-Chriss temporary + permanent impact model
    to gate orders that would be too expensive to execute.

    Post-trade: measures actual slippage as (fill_price - mid_at_submission)
    in basis points, logged for tearsheet analysis.
    """

    # Empirical constants tuned for liquid US equity ETFs
    LAMBDA_COEFF = 0.001   # temporary impact sensitivity
    GAMMA_COEFF = 0.5      # permanent impact sensitivity
    MAX_SLIPPAGE_CAP = 50  # bps — hard cap on estimate

    def __init__(self, config_loader=None):
        self._config = config_loader
        self._log = logger.bind(component='slippage_tracker')

    @property
    def max_slippage_bps(self) -> float:
        if self._config is not None:
            return self._config.get_float('execution', 'max_slippage_bps', 5.0)
        return 5.0

    def estimate_pretrade(
        self,
        notional: float,
        mid_price: float,
        daily_volume_usd: float,
        volatility: float,
        execution_window_min: int = 30,
    ) -> float:
        """Estimate pre-trade market impact using simplified Almgren-Chriss.

        Args:
            notional: Order size in USD.
            mid_price: Current mid-market price.
            daily_volume_usd: Average daily volume in USD.
            volatility: Realized volatility (annualized, as decimal).
            execution_window_min: Execution window in minutes.

        Returns:
            Estimated slippage in basis points.
        """
        if mid_price <= 0 or daily_volume_usd <= 0:
            return 0.0

        shares = notional / mid_price
        # 390 trading minutes per day
        minute_volume_usd = daily_volume_usd / 390
        participation_rate = (shares * mid_price) / max(minute_volume_usd, 1)

        exec_time = max(1, min(execution_window_min, 30))
        # Scale participation by execution window
        participation_rate = participation_rate / exec_time

        temporary = self.LAMBDA_COEFF * volatility * math.sqrt(
            abs(participation_rate),
        )
        permanent = self.GAMMA_COEFF * volatility * participation_rate

        total_bps = (temporary + permanent) * 10_000
        result = min(total_bps, self.MAX_SLIPPAGE_CAP)

        self._log.debug(
            'pretrade_slippage_estimate',
            notional=notional,
            participation_rate=round(participation_rate, 6),
            estimated_bps=round(result, 2),
        )

        return result

    def check_pretrade(
        self,
        notional: float,
        mid_price: float,
        daily_volume_usd: float,
        volatility: float,
        execution_window_min: int = 30,
    ) -> dict:
        """Check if pre-trade slippage is acceptable.

        Returns:
            dict with estimated_bps, threshold_bps, acceptable (bool).
        """
        estimated = self.estimate_pretrade(
            notional, mid_price, daily_volume_usd,
            volatility, execution_window_min,
        )
        threshold = self.max_slippage_bps
        acceptable = estimated <= threshold

        if not acceptable:
            self._log.warning(
                'slippage_alert',
                estimated_bps=round(estimated, 2),
                threshold_bps=threshold,
            )

        return {
            'estimated_bps': round(estimated, 4),
            'threshold_bps': threshold,
            'acceptable': acceptable,
        }

    @staticmethod
    def measure_posttrade(
        fill_price: float,
        mid_at_submission: float,
        side: str,
    ) -> float:
        """Measure actual slippage after a fill.

        For buys: positive means we paid more than mid (adverse).
        For sells: positive means we received less than mid (adverse).

        Returns slippage in basis points (positive = adverse).
        """
        if mid_at_submission <= 0:
            return 0.0

        raw_bps = ((fill_price - mid_at_submission) / mid_at_submission) * 10_000

        # For sells, flip sign so positive = adverse
        if side == 'sell':
            raw_bps = -raw_bps

        return round(raw_bps, 4)
