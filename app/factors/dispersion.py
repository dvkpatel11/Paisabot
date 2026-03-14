from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.base import FactorBase
from scipy.special import expit

# Standard sector ETFs for dispersion computation
SECTOR_ETFS = [
    'XLK', 'XLE', 'XLF', 'XLV', 'XLI',
    'XLC', 'XLY', 'XLP', 'XLU', 'XLRE', 'XLB',
]

MIN_SECTORS = 8


class DispersionFactor(FactorBase):
    """F04 — Dispersion Score (weight: 0.15).

    dispersion_score = sigmoid(z_score(cross_sectional_std(sector_5d_returns)))

    Higher dispersion = sectors differentiating = rotation opportunity.
    Crisis gate: if volatility_score < 0.35, cap at 0.50.

    Market-level factor: same score for all symbols.
    """

    name = 'dispersion_score'
    weight = 0.15

    def compute(self, symbols: list[str]) -> dict[str, float]:
        closes_df = self._get_multi_closes(SECTOR_ETFS, lookback=280)
        if closes_df.empty:
            self._log.warning('no_sector_data')
            return {s: 0.5 for s in symbols}

        # Filter to sectors we have data for
        available = [s for s in SECTOR_ETFS if s in closes_df.columns]
        if len(available) < MIN_SECTORS:
            self._log.warning(
                'insufficient_sectors',
                available=len(available),
                required=MIN_SECTORS,
            )
            return {s: 0.5 for s in symbols}

        sector_closes = closes_df[available]

        # Compute 5-day returns for each sector
        returns_5d = sector_closes.pct_change(periods=5).dropna()
        if len(returns_5d) < 30:
            return {s: 0.5 for s in symbols}

        # Cross-sectional std of 5-day returns for each date
        daily_dispersion = returns_5d.std(axis=1)

        # Current dispersion
        current_disp = float(daily_dispersion.iloc[-1])

        # Build 252-day history of dispersion values
        disp_history = daily_dispersion.tail(252).values

        if len(disp_history) < 30:
            return {s: 0.5 for s in symbols}

        # Z-score → sigmoid
        mean_disp = float(np.mean(disp_history))
        std_disp = float(np.std(disp_history))

        if std_disp < 1e-10:
            score = 0.5
        else:
            z = (current_disp - mean_disp) / std_disp
            score = float(expit(z))

        # Crisis gate: check vol regime
        score = self._apply_crisis_gate(score)

        score = max(0.0, min(1.0, score))

        # Market-level factor: same score for all symbols
        return {s: score for s in symbols}

    def _apply_crisis_gate(self, score: float) -> float:
        """Cap dispersion score at 0.50 if volatility regime is in crisis.

        Crisis = volatility_score < 0.35 (fear-driven dispersion, not opportunity).
        """
        if self._redis is None:
            return score

        # Check if any symbol's vol score indicates crisis
        # Use SPY as proxy for overall market vol regime
        try:
            vol_score_raw = self._redis.get('factor:SPY:volatility_regime')
            if vol_score_raw is not None:
                vol_score = float(vol_score_raw)
                if vol_score < 0.35:
                    self._log.info(
                        'crisis_gate_active',
                        vol_score=vol_score,
                        original_score=score,
                        capped=min(score, 0.50),
                    )
                    return min(score, 0.50)
        except (ValueError, TypeError):
            pass

        return score
