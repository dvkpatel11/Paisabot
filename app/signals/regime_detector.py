from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import structlog

from app.utils.normalization import percentile_rank

logger = structlog.get_logger()

REGIMES = ('trending', 'rotation', 'risk_off', 'consolidation')


def classify_regime(
    market_factors: dict[str, float],
) -> tuple[str, float]:
    """Classify current market regime from aggregated factor scores.

    Args:
        market_factors: mean factor scores across universe.
            Keys: trend_score, volatility_regime, breadth_score,
                  dispersion_score, correlation_index

    Returns:
        (regime_name, confidence) where confidence is in [0, 1].
    """
    b = market_factors.get('breadth_score', 0.5)
    t = market_factors.get('trend_score', 0.5)
    v = market_factors.get('volatility_regime', 0.5)
    d = market_factors.get('dispersion_score', 0.5)
    c = market_factors.get('correlation_index', 0.5)

    # Score each regime
    trending_score = 0.35 * b + 0.35 * t + 0.30 * v
    rotation_score = 0.40 * d + 0.30 * c + 0.30 * (0.5 - abs(t - 0.5))
    risk_off_score = 0.35 * (1 - b) + 0.35 * (1 - t) + 0.30 * (1 - v)
    consolidation_score = 1.0 - max(trending_score, rotation_score, risk_off_score)

    scores = {
        'trending': trending_score,
        'rotation': rotation_score,
        'risk_off': risk_off_score,
        'consolidation': consolidation_score,
    }

    best = max(scores, key=scores.get)
    confidence = scores[best]

    # Low confidence defaults to consolidation
    if confidence < 0.55:
        return 'consolidation', 0.50

    return best, round(confidence, 4)


class RegimeTracker:
    """Track regime persistence and enforce transition rules.

    Requires 3 consecutive days of same regime before switching.
    Exiting risk_off requires ≥0.65 confidence.
    """

    MIN_CONSECUTIVE = 3

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._log = logger.bind(component='regime_tracker')

        self.current_regime: str = 'consolidation'
        self.current_confidence: float = 0.50
        self.pending_regime: str | None = None
        self.pending_count: int = 0

        # Try to restore state from Redis
        self._restore_state()

    def update(
        self,
        new_regime: str,
        confidence: float,
        market_factors: dict[str, float] | None = None,
    ) -> str:
        """Update regime with persistence enforcement.

        Returns the effective regime (may differ from new_regime
        if persistence threshold not yet met).
        """
        if new_regime == self.current_regime:
            # Same regime — reset pending
            self.current_confidence = confidence
            self.pending_regime = None
            self.pending_count = 0
            self._save_state()
            return self.current_regime

        if new_regime == self.pending_regime:
            self.pending_count += 1
        else:
            self.pending_regime = new_regime
            self.pending_count = 1

        # Check if enough consecutive days
        required = self.MIN_CONSECUTIVE
        if self.current_regime == 'risk_off':
            # Higher bar to exit risk_off
            required = self.MIN_CONSECUTIVE + 1
            if confidence < 0.65:
                self._log.info(
                    'risk_off_exit_blocked',
                    pending=new_regime,
                    confidence=confidence,
                    reason='confidence_below_0.65',
                )
                self._save_state()
                return self.current_regime

        if self.pending_count >= required:
            old_regime = self.current_regime
            self.current_regime = new_regime
            self.current_confidence = confidence
            self.pending_regime = None
            self.pending_count = 0

            self._log.info(
                'regime_changed',
                from_regime=old_regime,
                to_regime=new_regime,
                confidence=confidence,
            )
            self._record_change(old_regime, new_regime, confidence, market_factors)
            self._save_state()
            return self.current_regime

        self._log.info(
            'regime_pending',
            current=self.current_regime,
            pending=new_regime,
            count=self.pending_count,
            required=required,
        )
        self._save_state()
        return self.current_regime

    def _record_change(
        self,
        from_regime: str,
        to_regime: str,
        confidence: float,
        market_factors: dict[str, float] | None,
    ) -> None:
        """Push regime change to Redis queue."""
        if self._redis is None:
            return

        import json
        msg = json.dumps({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'from_regime': from_regime,
            'to_regime': to_regime,
            'confidence': confidence,
            'factors': market_factors,
        })
        self._redis.lpush('channel:regime_change', msg)      # reliable queue
        self._redis.publish('channel:regime_change', msg)   # real-time dashboard

    def _save_state(self) -> None:
        """Persist tracker state to Redis."""
        if self._redis is None:
            return
        import json
        state = {
            'current_regime': self.current_regime,
            'current_confidence': self.current_confidence,
            'pending_regime': self.pending_regime,
            'pending_count': self.pending_count,
        }
        self._redis.set('regime:tracker_state', json.dumps(state))

    def _restore_state(self) -> None:
        """Restore tracker state from Redis."""
        if self._redis is None:
            return
        try:
            import json
            raw = self._redis.get('regime:tracker_state')
            if raw:
                data = raw.decode() if isinstance(raw, bytes) else raw
                state = json.loads(data)
                self.current_regime = state.get('current_regime', 'consolidation')
                self.current_confidence = state.get('current_confidence', 0.50)
                self.pending_regime = state.get('pending_regime')
                self.pending_count = state.get('pending_count', 0)
        except Exception:
            pass


def detect_correlation_collapse(
    corr_history: pd.Series,
    threshold_zscore: float = -2.0,
) -> bool:
    """Detect correlation collapse (z-score < threshold).

    Input: daily series of 60-day rolling avg pairwise sector correlation.
    Returns True when correlation drops 2+ std below 252-day mean.
    """
    if len(corr_history) < 60:
        return False

    rolling_mean = corr_history.rolling(252, min_periods=60).mean()
    rolling_std = corr_history.rolling(252, min_periods=60).std()

    current = corr_history.iloc[-1]
    mean_val = rolling_mean.iloc[-1]
    std_val = rolling_std.iloc[-1]

    if pd.isna(std_val) or std_val < 1e-10:
        return False

    zscore = (current - mean_val) / std_val
    return float(zscore) < threshold_zscore


def momentum_divergence_score(
    sector_closes: pd.DataFrame,
    lookback_days: int = 63,
) -> float:
    """Compute momentum divergence between top-3 and bottom-3 sectors.

    Returns percentile rank of current divergence vs 252-day history.
    High score = strong historically significant leadership divergence.
    """
    if sector_closes.empty or len(sector_closes) < lookback_days + 30:
        return 0.5

    returns = sector_closes.pct_change(lookback_days).dropna()
    if len(returns) < 30:
        return 0.5

    # Build divergence history
    divergence_history = []
    for i in range(len(returns)):
        row = returns.iloc[i].dropna()
        if len(row) < 6:
            continue
        top3 = row.nlargest(3).mean()
        bottom3 = row.nsmallest(3).mean()
        divergence_history.append(top3 - bottom3)

    if len(divergence_history) < 30:
        return 0.5

    current_row = returns.iloc[-1].dropna()
    if len(current_row) < 6:
        return 0.5

    current_div = current_row.nlargest(3).mean() - current_row.nsmallest(3).mean()
    return percentile_rank(current_div, divergence_history)
