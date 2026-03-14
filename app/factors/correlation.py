from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.base import FactorBase
from app.utils.normalization import percentile_rank

# Same sector ETFs used for correlation matrix
SECTOR_ETFS = [
    'XLK', 'XLE', 'XLF', 'XLV', 'XLI',
    'XLC', 'XLY', 'XLP', 'XLU', 'XLRE', 'XLB',
]


class CorrelationFactor(FactorBase):
    """F05 — Correlation Index (used for regime detection).

    correlation_score = 1 - percentile_rank(avg_corr_60d, 252d_history)

    Inverted: low correlation = favorable = high score.
    Collapse detection: z-score < -2.0 sets rotation regime flag.

    Market-level factor: same score for all symbols.
    """

    name = 'correlation_index'
    weight = 0.0  # Not in composite; used for regime detection

    CORR_WINDOW = 60
    COLLAPSE_Z_THRESHOLD = -2.0
    RISK_OFF_CORR_THRESHOLD = 0.85
    RISK_OFF_CONSECUTIVE_DAYS = 3

    def compute(self, symbols: list[str]) -> dict[str, float]:
        closes_df = self._get_multi_closes(SECTOR_ETFS, lookback=350)
        if closes_df.empty:
            self._log.warning('no_sector_data')
            return {s: 0.5 for s in symbols}

        available = [s for s in SECTOR_ETFS if s in closes_df.columns]
        if len(available) < 5:
            return {s: 0.5 for s in symbols}

        sector_closes = closes_df[available]

        # Log returns
        log_returns = np.log(sector_closes / sector_closes.shift(1)).dropna()
        if len(log_returns) < self.CORR_WINDOW + 10:
            return {s: 0.5 for s in symbols}

        # Compute rolling avg pairwise correlation
        avg_corr_history = self._compute_rolling_avg_corr(log_returns)

        if len(avg_corr_history) < 30:
            return {s: 0.5 for s in symbols}

        current_corr = avg_corr_history[-1]

        # Percentile rank (inverted)
        history_252 = avg_corr_history[-252:] if len(avg_corr_history) >= 252 else avg_corr_history
        corr_pct = percentile_rank(current_corr, list(history_252))
        score = 1.0 - corr_pct

        # Detect correlation collapse (rotation signal)
        self._detect_collapse(avg_corr_history)

        # Detect risk-off (sustained high correlation)
        self._detect_risk_off(avg_corr_history)

        score = max(0.0, min(1.0, score))
        return {s: score for s in symbols}

    def _compute_rolling_avg_corr(
        self, log_returns: pd.DataFrame,
    ) -> list[float]:
        """Compute rolling average pairwise correlation.

        For each day, compute the 60-day correlation matrix and
        take the mean of the upper triangle.
        """
        n_cols = log_returns.shape[1]
        avg_corrs = []

        for i in range(self.CORR_WINDOW, len(log_returns)):
            window = log_returns.iloc[i - self.CORR_WINDOW:i]
            corr_matrix = window.corr().values

            # Upper triangle (excluding diagonal)
            mask = np.triu_indices(n_cols, k=1)
            upper_vals = corr_matrix[mask]

            # Filter NaN
            valid = upper_vals[~np.isnan(upper_vals)]
            if len(valid) > 0:
                avg_corrs.append(float(np.mean(valid)))

        return avg_corrs

    def _detect_collapse(self, avg_corr_history: list[float]) -> bool:
        """Detect correlation collapse (z-score < -2.0).

        Sets Redis flag for regime detector.
        """
        if len(avg_corr_history) < 60:
            return False

        history = np.array(avg_corr_history[-252:] if len(avg_corr_history) >= 252 else avg_corr_history)
        mean_corr = float(np.mean(history))
        std_corr = float(np.std(history))

        if std_corr < 1e-10:
            return False

        current = avg_corr_history[-1]
        z = (current - mean_corr) / std_corr

        collapse = z < self.COLLAPSE_Z_THRESHOLD

        if collapse and self._redis is not None:
            self._redis.set('correlation_collapse', '1', ex=86400)
            self._log.info(
                'correlation_collapse_detected',
                z_score=round(z, 3),
                avg_corr=round(current, 4),
            )

        return collapse

    def _detect_risk_off(self, avg_corr_history: list[float]) -> bool:
        """Detect sustained high correlation (risk-off signal).

        avg_corr > 0.85 for 3+ consecutive days.
        """
        if len(avg_corr_history) < self.RISK_OFF_CONSECUTIVE_DAYS:
            return False

        recent = avg_corr_history[-self.RISK_OFF_CONSECUTIVE_DAYS:]
        risk_off = all(c > self.RISK_OFF_CORR_THRESHOLD for c in recent)

        if risk_off and self._redis is not None:
            self._redis.set('correlation_risk_off', '1', ex=86400)
            self._log.warning(
                'correlation_risk_off',
                avg_corr_3d=[round(c, 4) for c in recent],
            )

        return risk_off

    def detect_correlation_collapse(self) -> bool:
        """Public check: is correlation collapse currently flagged?"""
        if self._redis is None:
            return False
        return self._redis.get('correlation_collapse') == '1'

    def get_current_avg_correlation(self) -> float | None:
        """Get the most recent average pairwise correlation."""
        closes_df = self._get_multi_closes(SECTOR_ETFS, lookback=70)
        if closes_df.empty:
            return None

        available = [s for s in SECTOR_ETFS if s in closes_df.columns]
        if len(available) < 5:
            return None

        log_returns = np.log(closes_df[available] / closes_df[available].shift(1)).dropna()
        if len(log_returns) < self.CORR_WINDOW:
            return None

        window = log_returns.tail(self.CORR_WINDOW)
        corr_matrix = window.corr().values
        mask = np.triu_indices(len(available), k=1)
        upper_vals = corr_matrix[mask]
        valid = upper_vals[~np.isnan(upper_vals)]

        return float(np.mean(valid)) if len(valid) > 0 else None
