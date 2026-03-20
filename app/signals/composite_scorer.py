from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()

# Dispersion factor removed — its 15% redistributed:
#   Trend:      0.25 → 0.30  (+5%)
#   Volatility: 0.20 → 0.25  (+5%)
#   Sentiment:  0.15 → 0.15  (unchanged)
#   Breadth:    0.15 → 0.15  (unchanged)
#   Liquidity:  0.10 → 0.15  (+5%)
DEFAULT_WEIGHTS = {
    'trend_score': 0.30,
    'volatility_regime': 0.25,
    'sentiment_score': 0.15,
    'breadth_score': 0.15,
    'liquidity_score': 0.15,
}

# Map config key names (weight_trend) to factor names (trend_score)
WEIGHT_KEY_MAP = {
    'weight_trend': 'trend_score',
    'weight_volatility': 'volatility_regime',
    'weight_sentiment': 'sentiment_score',
    'weight_breadth': 'breadth_score',
    'weight_liquidity': 'liquidity_score',
}


class CompositeScorer:
    """Compute weighted composite scores from factor scores.

    Loads weights from Redis config:weights hash. Falls back to defaults.
    """

    def __init__(self, redis_client=None, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='composite_scorer')

    def load_weights(self) -> dict[str, float]:
        """Load factor weights from Redis, normalize to sum to 1.0."""
        weights = dict(DEFAULT_WEIGHTS)

        if self._redis is not None:
            try:
                raw = self._redis.hgetall('config:weights')
                if raw:
                    loaded = {}
                    for k, v in raw.items():
                        key = k.decode() if isinstance(k, bytes) else k
                        val = v.decode() if isinstance(v, bytes) else v
                        factor = WEIGHT_KEY_MAP.get(key)
                        if factor:
                            loaded[factor] = float(val)
                    if loaded:
                        weights = loaded
            except Exception as exc:
                self._log.warning('weight_load_failed', error=str(exc))

        # Normalize to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def compute(
        self, symbol: str, scores: dict[str, float],
        weights: dict[str, float] | None = None,
    ) -> float:
        """Compute composite score for a single symbol.

        Returns value in [0.0, 1.0].
        """
        if weights is None:
            weights = self.load_weights()

        score = sum(
            weights.get(k, 0.0) * scores.get(k, 0.5)
            for k in weights
        )
        return round(float(np.clip(score, 0.0, 1.0)), 4)

    def rank_universe(
        self, all_factor_scores: dict[str, dict[str, float]],
        weights: dict[str, float] | None = None,
    ) -> pd.DataFrame:
        """Score and rank entire universe.

        Returns DataFrame sorted by composite descending, with rank column.
        """
        if weights is None:
            weights = self.load_weights()

        rows = []
        for symbol, scores in all_factor_scores.items():
            composite = self.compute(symbol, scores, weights)
            row = {'symbol': symbol, 'composite': composite}
            row.update(scores)
            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index('symbol')
        df = df.sort_values('composite', ascending=False)
        df['rank'] = range(1, len(df) + 1)

        self._log.info(
            'universe_ranked',
            count=len(df),
            top=df.index[0] if len(df) > 0 else None,
            top_score=float(df['composite'].iloc[0]) if len(df) > 0 else None,
        )

        return df
