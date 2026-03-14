from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import structlog

from app.extensions import db
from app.factors.factor_registry import FactorRegistry
from app.models.signals import Signal
from app.signals.composite_scorer import CompositeScorer
from app.signals.regime_detector import RegimeTracker, classify_regime
from app.signals.signal_filter import SignalFilter

logger = structlog.get_logger()


def classify_signal(composite_score: float, regime: str = 'consolidation') -> str:
    """Classify a composite score into a signal type.

    Thresholds:
        ≥0.65 → long (≥0.70 in risk_off)
        0.40–0.64 → neutral
        <0.40 → avoid
    """
    long_threshold = 0.70 if regime == 'risk_off' else 0.65

    if composite_score >= long_threshold:
        return 'long'
    elif composite_score >= 0.40:
        return 'neutral'
    else:
        return 'avoid'


class SignalGenerator:
    """Orchestrate the full signal generation pipeline.

    Flow: compute factors → classify regime → rank → filter → classify signals.
    """

    def __init__(
        self,
        redis_client=None,
        db_session=None,
        config_loader=None,
    ):
        self._redis = redis_client
        self._db_session = db_session
        self._config = config_loader
        self._log = logger.bind(component='signal_generator')

        self.factors = FactorRegistry(
            redis_client=redis_client,
            db_session=db_session,
            config_loader=config_loader,
        )
        self.scorer = CompositeScorer(
            redis_client=redis_client,
            config_loader=config_loader,
        )
        self.regime = RegimeTracker(redis_client=redis_client)
        self.filter = SignalFilter(
            redis_client=redis_client,
            config_loader=config_loader,
        )

    def run(self, universe: list[str]) -> dict:
        """Run the full signal generation pipeline.

        Args:
            universe: list of symbol strings to score.

        Returns:
            dict of {symbol: signal_dict}
        """
        self._log.info('signal_run_start', universe_size=len(universe))
        now = datetime.now(timezone.utc)

        # 1. Compute all factor scores
        all_scores = self.factors.compute_all(universe)

        # 2. Aggregate market factors and classify regime
        market_factors = self._aggregate_market_factors(all_scores, universe)
        raw_regime, raw_confidence = classify_regime(market_factors)
        effective_regime = self.regime.update(
            raw_regime, raw_confidence, market_factors,
        )
        effective_confidence = (
            raw_confidence if effective_regime == raw_regime
            else self.regime.current_confidence
        )

        # 3. Rank universe by composite score
        ranked_df = self.scorer.rank_universe(all_scores)
        if ranked_df.empty:
            self._log.warning('empty_ranked_universe')
            return {}

        # 4. Filter and classify signals
        signals = {}
        for symbol in ranked_df.index:
            composite = float(ranked_df.loc[symbol, 'composite'])
            rank = int(ranked_df.loc[symbol, 'rank'])

            # Get liquidity data for filter
            adv_m = self._get_cached_float(f'etf:{symbol}:adv_30d_m')
            spread_bps = self._get_cached_float(f'etf:{symbol}:spread_bps')

            tradable, reason = self.filter.is_tradable(symbol, adv_m, spread_bps)

            if tradable:
                signal_type = classify_signal(composite, effective_regime)
            else:
                signal_type = 'blocked'

            signals[symbol] = {
                'composite_score': composite,
                'signal_type': signal_type,
                'rank': rank,
                'regime': effective_regime,
                'regime_confidence': effective_confidence,
                'tradable': tradable,
                'block_reason': reason if not tradable else None,
                'factors': all_scores.get(symbol, {}),
                'calc_time': now.isoformat(),
            }

        # 5. Publish and persist
        self._publish(signals, effective_regime, effective_confidence, now)
        self._persist(signals, effective_regime, effective_confidence, now)

        # Log summary
        longs = sum(1 for s in signals.values() if s['signal_type'] == 'long')
        avoids = sum(1 for s in signals.values() if s['signal_type'] == 'avoid')
        blocked = sum(1 for s in signals.values() if s['signal_type'] == 'blocked')

        self._log.info(
            'signal_run_complete',
            regime=effective_regime,
            confidence=effective_confidence,
            longs=longs,
            avoids=avoids,
            blocked=blocked,
            total=len(signals),
        )

        return signals

    def _aggregate_market_factors(
        self,
        all_scores: dict[str, dict[str, float]],
        universe: list[str],
    ) -> dict[str, float]:
        """Compute mean factor scores across universe for regime detection."""
        if not all_scores:
            return {}

        df = pd.DataFrame(all_scores).T
        return df.mean().to_dict()

    def _get_cached_float(self, key: str) -> float | None:
        """Read a float from Redis cache."""
        if self._redis is None:
            return None
        try:
            val = self._redis.get(key)
            if val is None:
                return None
            decoded = val.decode() if isinstance(val, bytes) else val
            return float(decoded)
        except (ValueError, TypeError):
            return None

    def _publish(
        self,
        signals: dict,
        regime: str,
        confidence: float,
        timestamp: datetime,
    ) -> None:
        """Publish signals to Redis cache and pub/sub."""
        if self._redis is None:
            return

        try:
            payload = json.dumps({
                'signals': {
                    sym: {k: v for k, v in sig.items() if k != 'factors'}
                    for sym, sig in signals.items()
                },
                'regime': regime,
                'confidence': confidence,
                'timestamp': timestamp.isoformat(),
            })

            # Cache with 5min TTL
            self._redis.set('cache:latest_scores', payload, ex=300)

            # Pub/sub (lossy, for dashboard)
            self._redis.publish('channel:signals', payload)

        except Exception as exc:
            self._log.warning('publish_signals_failed', error=str(exc))

    def _persist(
        self,
        signals: dict,
        regime: str,
        confidence: float,
        timestamp: datetime,
    ) -> None:
        """Persist signals to PostgreSQL."""
        try:
            rows = []
            for symbol, sig in signals.items():
                factors = sig.get('factors', {})
                row = Signal(
                    symbol=symbol,
                    signal_time=timestamp,
                    composite_score=sig['composite_score'],
                    trend_score=factors.get('trend_score'),
                    volatility_score=factors.get('volatility_regime'),
                    sentiment_score=factors.get('sentiment_score'),
                    breadth_score=factors.get('breadth_score'),
                    dispersion_score=factors.get('dispersion_score'),
                    liquidity_score=factors.get('liquidity_score'),
                    regime=regime,
                    regime_confidence=confidence,
                    signal_type=sig['signal_type'],
                    block_reason=sig.get('block_reason'),
                )
                rows.append(row)

            db.session.add_all(rows)
            db.session.commit()

        except Exception as exc:
            self._log.error('persist_signals_failed', error=str(exc))
            try:
                db.session.rollback()
            except Exception:
                pass
