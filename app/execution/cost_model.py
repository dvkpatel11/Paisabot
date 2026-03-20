from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.execution.slippage_tracker import SlippageTracker

logger = structlog.get_logger()


@dataclass
class CostBreakdown:
    """Itemised transaction cost estimate for a single order."""

    half_spread_bps: float
    market_impact_bps: float
    total_bps: float
    fill_price: float
    filled_qty: float


class TransactionCostModel:
    """Estimate realistic transaction costs for simulated fills.

    Components:
      1. Half-spread — half of the quoted bid-ask spread (or config default).
      2. Market impact — Almgren-Chriss temporary + permanent impact via
         :class:`SlippageTracker`.

    The model is intentionally conservative for liquid US equity ETFs and
    delegates the market-microstructure maths to :class:`SlippageTracker`
    so there is a single calibration point.
    """

    def __init__(
        self,
        slippage_tracker: SlippageTracker,
        config_loader=None,
    ):
        self._slippage = slippage_tracker
        self._config = config_loader
        self._log = logger.bind(component='cost_model')

    # ── public API ───────────────────────────────────────────────

    def estimate(
        self,
        symbol: str,
        side: str,
        notional: float,
        mid_price: float,
        daily_volume_usd: float,
        volatility: float,
        spread_bps: float = 1.0,
        execution_window_min: int = 30,
    ) -> CostBreakdown:
        """Return a :class:`CostBreakdown` for a proposed order.

        Args:
            symbol: Ticker (used only for logging).
            side: ``'buy'`` or ``'sell'``.
            notional: Order size in USD.
            mid_price: Current mid-market price.
            daily_volume_usd: 30-day average daily dollar volume.
            volatility: Annualised realised volatility (decimal).
            spread_bps: Quoted spread in basis points (default 1.0).
            execution_window_min: Assumed execution window in minutes.

        Returns:
            :class:`CostBreakdown` with itemised costs and simulated fill.
        """
        # 1. Half-spread
        half_spread_bps = spread_bps / 2.0

        # 2. Market impact (Almgren-Chriss)
        market_impact_bps = self._slippage.estimate_pretrade(
            notional,
            mid_price,
            daily_volume_usd,
            volatility,
            execution_window_min,
        )

        # 3. Total cost
        total_bps = half_spread_bps + market_impact_bps

        # 4. Fill price (adverse direction)
        fill_price = self._compute_fill_price(mid_price, side, total_bps)

        # 5. Filled quantity
        filled_qty = round(notional / fill_price, 6) if fill_price > 0 else 0.0

        breakdown = CostBreakdown(
            half_spread_bps=round(half_spread_bps, 4),
            market_impact_bps=round(market_impact_bps, 4),
            total_bps=round(total_bps, 4),
            fill_price=fill_price,
            filled_qty=filled_qty,
        )

        self._log.debug(
            'cost_estimate',
            symbol=symbol,
            side=side,
            notional=notional,
            half_spread_bps=breakdown.half_spread_bps,
            market_impact_bps=breakdown.market_impact_bps,
            total_bps=breakdown.total_bps,
            fill_price=breakdown.fill_price,
        )

        return breakdown

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _compute_fill_price(
        mid_price: float,
        side: str,
        total_bps: float,
    ) -> float:
        """Apply adverse cost to mid price.

        Buys fill above mid; sells fill below mid.
        """
        direction = 1.0 if side == 'buy' else -1.0
        return round(mid_price * (1 + direction * total_bps / 10_000), 4)
