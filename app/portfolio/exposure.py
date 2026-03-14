from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class ExposureAnalyzer:
    """Compute and report portfolio exposure metrics.

    Sector exposure, concentration, beta, and expected volatility.
    Used by the dashboard and risk engine.
    """

    def __init__(self):
        self._log = logger.bind(component='exposure_analyzer')

    def sector_exposure(
        self,
        weights: dict[str, float],
        sector_map: dict[str, str],
    ) -> dict[str, float]:
        """Compute sector-level weight totals.

        Returns:
            {sector: total_weight} sorted descending.
        """
        sectors: dict[str, float] = {}
        for sym, w in weights.items():
            sector = sector_map.get(sym, 'Unknown')
            sectors[sector] = sectors.get(sector, 0.0) + w

        return dict(sorted(sectors.items(), key=lambda x: -x[1]))

    def concentration_metrics(
        self,
        weights: dict[str, float],
    ) -> dict:
        """Compute concentration metrics (HHI, top-N share).

        Returns:
            dict with hhi, top1_pct, top3_pct, top5_pct, n_positions.
        """
        if not weights:
            return {
                'hhi': 0.0, 'top1_pct': 0.0, 'top3_pct': 0.0,
                'top5_pct': 0.0, 'n_positions': 0,
            }

        sorted_w = sorted(weights.values(), reverse=True)
        total = sum(sorted_w)
        if total <= 0:
            return {
                'hhi': 0.0, 'top1_pct': 0.0, 'top3_pct': 0.0,
                'top5_pct': 0.0, 'n_positions': 0,
            }

        # Normalize weights for HHI
        normalized = [w / total for w in sorted_w]
        hhi = sum(w ** 2 for w in normalized)

        return {
            'hhi': round(hhi, 4),
            'top1_pct': round(sorted_w[0] / total, 4) if len(sorted_w) >= 1 else 0.0,
            'top3_pct': round(sum(sorted_w[:3]) / total, 4) if len(sorted_w) >= 3 else round(total / total, 4),
            'top5_pct': round(sum(sorted_w[:5]) / total, 4) if len(sorted_w) >= 5 else round(total / total, 4),
            'n_positions': len(sorted_w),
        }

    def portfolio_beta(
        self,
        weights: dict[str, float],
        prices_df: pd.DataFrame,
        benchmark: str = 'SPY',
        lookback: int = 60,
    ) -> float | None:
        """Compute weighted portfolio beta vs benchmark.

        Returns None if insufficient data.
        """
        if benchmark not in prices_df.columns:
            return None

        available = [s for s in weights if s in prices_df.columns and s != benchmark]
        if not available:
            return None

        returns = prices_df[available + [benchmark]].pct_change().dropna()
        if len(returns) < lookback:
            return None

        recent = returns.tail(lookback)
        bench_ret = recent[benchmark]
        bench_var = bench_ret.var()
        if bench_var <= 0:
            return None

        # Weighted portfolio return
        port_ret = sum(
            weights[sym] * recent[sym] for sym in available
        )

        cov = port_ret.cov(bench_ret)
        beta = cov / bench_var

        return round(float(beta), 4)

    def full_report(
        self,
        weights: dict[str, float],
        sector_map: dict[str, str],
        prices_df: pd.DataFrame | None = None,
        portfolio_value: float = 100_000.0,
    ) -> dict:
        """Generate a full exposure report for the dashboard.

        Returns:
            dict with sector_exposures, concentration, beta,
            cash_pct, invested_pct, portfolio_value.
        """
        sectors = self.sector_exposure(weights, sector_map)
        concentration = self.concentration_metrics(weights)

        total_invested = sum(weights.values())
        cash_pct = 1.0 - total_invested

        report = {
            'sector_exposures': sectors,
            'concentration': concentration,
            'invested_pct': round(total_invested, 4),
            'cash_pct': round(cash_pct, 4),
            'portfolio_value': portfolio_value,
        }

        if prices_df is not None:
            beta = self.portfolio_beta(weights, prices_df)
            report['beta'] = beta

        self._log.info(
            'exposure_report',
            n_positions=concentration['n_positions'],
            hhi=concentration['hhi'],
            cash_pct=round(cash_pct, 4),
        )

        return report
