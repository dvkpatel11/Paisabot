from __future__ import annotations

from datetime import datetime, timezone

import structlog

from app.factors.base import FactorBase
from app.factors.trend import TrendFactor
from app.factors.volatility import VolatilityFactor
from app.factors.correlation import CorrelationFactor
from app.factors.breadth import BreadthFactor
from app.factors.liquidity import LiquidityFactor
from app.factors.slippage import SlippageFactor
from app.factors.sentiment import SentimentFactor

logger = structlog.get_logger()


class FactorRegistry:
    """Registry for all factor computations.

    Manages factor lifecycle, compute_all orchestration,
    and output to Redis + PostgreSQL.

    Note: Dispersion factor (F04) removed from active composite.
    Class remains in app/factors/dispersion.py for research use.
    """

    AVAILABLE_FACTORS: dict[str, type[FactorBase]] = {
        'trend_score': TrendFactor,
        'volatility_regime': VolatilityFactor,
        'sentiment_score': SentimentFactor,
        'correlation_index': CorrelationFactor,
        'breadth_score': BreadthFactor,
        'liquidity_score': LiquidityFactor,
        'slippage_estimator': SlippageFactor,
    }

    def __init__(self, redis_client=None, db_session=None, config_loader=None):
        self._redis = redis_client
        self._db_session = db_session
        self._config = config_loader
        self._log = logger.bind(component='factor_registry')

        # Determine enabled factors from config
        enabled = 'all'
        if config_loader:
            enabled = config_loader.get('factors', 'enabled_factors', default='all')

        if enabled == 'all':
            names = list(self.AVAILABLE_FACTORS.keys())
        else:
            names = [n.strip() for n in enabled.split(',')]

        self.factors: dict[str, FactorBase] = {}
        for name in names:
            cls = self.AVAILABLE_FACTORS.get(name)
            if cls:
                self.factors[name] = cls(
                    redis_client=redis_client,
                    db_session=db_session,
                    config_loader=config_loader,
                )

        self._log.info('registry_initialized', factors=list(self.factors.keys()))

    def compute_all(
        self, symbols: list[str],
    ) -> dict[str, dict[str, float]]:
        """Compute all enabled factors for given symbols.

        Returns:
            {symbol: {factor_name: score}} where scores are in [0, 1]
        """
        results: dict[str, dict[str, float]] = {sym: {} for sym in symbols}

        for name, factor in self.factors.items():
            try:
                scores = factor.compute(symbols)
                for sym in symbols:
                    results[sym][name] = scores.get(sym, 0.5)

                self._log.info(
                    'factor_computed',
                    factor=name,
                    symbols=len(symbols),
                )

            except Exception as exc:
                self._log.error(
                    'factor_compute_failed',
                    factor=name,
                    error=str(exc),
                )
                # Default to 0.5 (neutral) on failure
                for sym in symbols:
                    results[sym][name] = 0.5

        # Cache results
        self._cache_results(results)
        self._persist_results(results)
        self._publish_results(results)

        return results

    def compute_single(
        self, factor_name: str, symbols: list[str],
    ) -> dict[str, float]:
        """Compute a single factor for given symbols."""
        factor = self.factors.get(factor_name)
        if factor is None:
            self._log.warning('unknown_factor', factor=factor_name)
            return {s: 0.5 for s in symbols}

        return factor.compute(symbols)

    def _cache_results(self, results: dict[str, dict[str, float]]) -> None:
        """Cache factor scores in Redis."""
        if self._redis is None:
            return

        import json

        for symbol, scores in results.items():
            # Per-symbol composite cache (15min TTL)
            key = f'scores:{symbol}'
            self._redis.set(key, json.dumps(scores), ex=900)

            # Per-factor cache (1h TTL)
            for factor_name, score in scores.items():
                fkey = f'factor:{symbol}:{factor_name}'
                self._redis.set(fkey, str(score), ex=3600)

    def _persist_results(self, results: dict[str, dict[str, float]]) -> None:
        """Persist factor scores to PostgreSQL."""
        try:
            from app.models.factor_scores import FactorScore
            from app.extensions import db

            now = datetime.now(timezone.utc)
            for symbol, scores in results.items():
                record = FactorScore(
                    symbol=symbol,
                    calc_time=now,
                    trend_score=scores.get('trend_score'),
                    volatility_score=scores.get('volatility_regime'),
                    sentiment_score=scores.get('sentiment_score'),
                    dispersion_score=scores.get('dispersion_score'),
                    correlation_score=scores.get('correlation_index'),
                    breadth_score=scores.get('breadth_score'),
                    liquidity_score=scores.get('liquidity_score'),
                    slippage_score=scores.get('slippage_estimator'),
                )
                db.session.add(record)

            db.session.commit()
        except Exception as exc:
            self._log.error('persist_scores_failed', error=str(exc))

    def _publish_results(self, results: dict[str, dict[str, float]]) -> None:
        """Publish factor scores to Redis pub/sub for dashboard."""
        if self._redis is None:
            return

        try:
            import json
            self._redis.publish(
                'channel:factor_scores',
                json.dumps({
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'scores': results,
                }),
            )
        except Exception as exc:
            self._log.warning('publish_scores_failed', error=str(exc))

    def get_enabled_factors(self) -> list[str]:
        """Return list of enabled factor names."""
        return list(self.factors.keys())
