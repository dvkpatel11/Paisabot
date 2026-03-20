from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

from app.portfolio.constraints import PortfolioConstraints

logger = structlog.get_logger()


class PortfolioConstructor:
    """Build target portfolio weights using PyPortfolioOpt.

    Supports objectives: max_sharpe, min_vol, equal_weight, hrp.
    Enforces position-level and sector-level constraints.
    """

    def __init__(self, config_loader=None):
        self._config = config_loader
        self._log = logger.bind(component='portfolio_constructor')

    def build_target_weights(
        self,
        candidates: list[str],
        prices_df: pd.DataFrame,
        sector_map: dict[str, str] | None = None,
        constraints: PortfolioConstraints | None = None,
        objective: str | None = None,
    ) -> dict[str, float]:
        """Compute target weights for candidate ETFs.

        Args:
            candidates: symbols to include.
            prices_df: daily close prices (columns = symbols).
            sector_map: {symbol: sector_name} for sector constraints.
            constraints: portfolio constraints; uses defaults if None.
            objective: override constraints.objective if provided.

        Returns:
            {symbol: weight} summing to ~(1 - cash_buffer).
        """
        if constraints is None:
            constraints = PortfolioConstraints()
        if sector_map is None:
            sector_map = {}

        obj = objective or constraints.objective
        investable = 1.0 - constraints.cash_buffer_pct

        if not candidates:
            return {}

        if len(candidates) == 1:
            w = min(investable, constraints.max_position_size)
            return {candidates[0]: round(w, 6)}

        # Filter prices to candidates with enough data
        available = [c for c in candidates if c in prices_df.columns]
        if len(available) < 2:
            if available:
                w = min(investable, constraints.max_position_size)
                return {available[0]: round(w, 6)}
            return {}

        prices_subset = prices_df[available].dropna()
        if len(prices_subset) < 30:
            self._log.warning('insufficient_price_data', rows=len(prices_subset))
            return self._equal_weight(available, constraints)

        if obj == 'equal_weight':
            return self._equal_weight(available, constraints)

        if obj == 'hrp':
            return self._hrp_weights(available, prices_subset, constraints, sector_map)

        # max_sharpe or min_vol via EfficientFrontier
        return self._efficient_frontier(
            available, prices_subset, sector_map, constraints, obj,
        )

    def _efficient_frontier(
        self,
        candidates: list[str],
        prices_subset: pd.DataFrame,
        sector_map: dict[str, str],
        constraints: PortfolioConstraints,
        objective: str,
    ) -> dict[str, float]:
        from pypfopt import EfficientFrontier, expected_returns, risk_models

        investable = 1.0 - constraints.cash_buffer_pct

        try:
            mu = expected_returns.mean_historical_return(prices_subset, frequency=252)
            S = risk_models.CovarianceShrinkage(prices_subset).ledoit_wolf()

            ef = EfficientFrontier(mu, S)

            # Position bounds (EF weights sum to 1.0; investable scaling
            # happens after optimisation, so constraints use raw fractions)
            ef.add_constraint(lambda w: w <= constraints.max_position_size)
            ef.add_constraint(lambda w: w >= constraints.min_position_size)

            # Sector constraints
            self._add_sector_constraints(
                ef, candidates, sector_map,
                constraints.max_sector_exposure,
            )

            if objective == 'min_vol':
                ef.min_volatility()
            else:
                ef.max_sharpe(risk_free_rate=0.05)

            cleaned = ef.clean_weights()
            result = {
                sym: round(w * investable, 6)
                for sym, w in cleaned.items()
                if w > 0.001
            }

            self._log.info(
                'weights_built',
                objective=objective,
                n_positions=len(result),
                total_weight=round(sum(result.values()), 4),
            )
            return result

        except Exception as exc:
            self._log.warning(
                'optimization_failed',
                error=str(exc),
                fallback='equal_weight',
            )
            return self._equal_weight(candidates, constraints)

    def _hrp_weights(
        self,
        candidates: list[str],
        prices_subset: pd.DataFrame,
        constraints: PortfolioConstraints,
        sector_map: dict[str, str] | None = None,
    ) -> dict[str, float]:
        from pypfopt import HRPOpt

        investable = 1.0 - constraints.cash_buffer_pct

        try:
            returns = prices_subset.pct_change().dropna()
            if len(returns) < 30:
                return self._equal_weight(candidates, constraints)

            hrp = HRPOpt(returns)
            raw = hrp.optimize()

            # Step 1: cap individual positions
            result = {}
            for sym, w in raw.items():
                scaled = w * investable
                if scaled > constraints.max_position_size:
                    scaled = constraints.max_position_size
                if scaled > 0.001:
                    result[sym] = scaled

            # Step 2: enforce sector limits and trim breaching symbols
            if sector_map and constraints.max_sector_exposure:
                sector_totals: dict[str, float] = {}
                for sym, w in result.items():
                    sec = sector_map.get(sym, 'Unknown')
                    sector_totals[sec] = sector_totals.get(sec, 0.0) + w

                for sec, total in sector_totals.items():
                    if total > constraints.max_sector_exposure:
                        # Scale all symbols in this sector proportionally
                        scale = constraints.max_sector_exposure / total
                        for sym in list(result):
                            if sector_map.get(sym, 'Unknown') == sec:
                                result[sym] *= scale

            # Step 3: re-normalize so weights sum to investable fraction
            total = sum(result.values())
            if total > 0:
                result = {
                    sym: round(w / total * investable, 6)
                    for sym, w in result.items()
                }

            return result

        except Exception as exc:
            self._log.warning('hrp_failed', error=str(exc))
            return self._equal_weight(candidates, constraints)

    def _equal_weight(
        self,
        candidates: list[str],
        constraints: PortfolioConstraints,
    ) -> dict[str, float]:
        investable = 1.0 - constraints.cash_buffer_pct
        n = len(candidates)
        if n == 0:
            return {}
        per_position = min(investable / n, constraints.max_position_size)
        return {sym: round(per_position, 6) for sym in candidates}

    @staticmethod
    def _add_sector_constraints(
        ef,
        candidates: list[str],
        sector_map: dict[str, str],
        max_sector_weight: float,
    ) -> None:
        sectors: dict[str, list[int]] = {}
        for i, sym in enumerate(candidates):
            sec = sector_map.get(sym, 'Unknown')
            sectors.setdefault(sec, []).append(i)

        for sec, indices in sectors.items():
            if len(indices) > 1:
                ef.add_constraint(
                    lambda w, idx=indices: sum(w[i] for i in idx) <= max_sector_weight,
                )
