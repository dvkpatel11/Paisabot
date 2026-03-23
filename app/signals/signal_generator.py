from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import structlog

from app.extensions import db
from app.factors.factor_registry import FactorRegistry
from app.models.signals import Signal
from app.signals.composite_scorer import CompositeScorer
from app.signals.regime_detector import (
    RegimeTracker,
    classify_regime,
    detect_correlation_collapse,
    momentum_divergence_score,
)
from app.signals.signal_filter import SignalFilter

logger = structlog.get_logger()


def classify_signal(
    composite_score: float,
    regime: str = 'consolidation',
    allow_short: bool = False,
    trend_score: float | None = None,
) -> str:
    """Classify a composite score into a signal type.

    Thresholds:
        ≥0.65 → long  (≥0.70 in risk_off)
        0.40–0.64 → neutral
        <0.40 → avoid  (or 'short' when all conditions met)

    Short conditions (all must be true):
        1. regime == 'risk_off'
        2. allow_short == True  (config:execution.allow_short)
        3. composite_score < 0.40
        4. trend_score < 0.30  (strong downtrend confirmation)
    """
    long_threshold = 0.70 if regime == 'risk_off' else 0.65

    if composite_score >= long_threshold:
        return 'long'
    elif composite_score >= 0.40:
        return 'neutral'
    else:
        # Short candidate — only in risk_off with explicit config opt-in
        if (
            allow_short
            and regime == 'risk_off'
            and trend_score is not None
            and trend_score < 0.30
        ):
            return 'short'
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
        asset_class: str = 'etf',
    ):
        self._redis = redis_client
        self._db_session = db_session
        self._config = config_loader
        self._asset_class = asset_class
        self._log = logger.bind(
            component='signal_generator', asset_class=asset_class,
        )

        self.factors = FactorRegistry(
            redis_client=redis_client,
            db_session=db_session,
            config_loader=config_loader,
            asset_class=asset_class,
        )
        self.scorer = CompositeScorer(
            redis_client=redis_client,
            config_loader=config_loader,
            asset_class=asset_class,
        )
        self.regime = RegimeTracker(redis_client=redis_client)
        self.filter = SignalFilter(
            redis_client=redis_client,
            config_loader=config_loader,
            asset_class=asset_class,
        )
        self._account_id_cache: int | None = None

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

        # 2b. Check correlation collapse — override to rotation if detected
        corr_history = self._get_correlation_history()
        collapse_detected = False
        if corr_history is not None and len(corr_history) >= 60:
            collapse_detected = detect_correlation_collapse(corr_history)
            if collapse_detected:
                raw_regime = 'rotation'
                raw_confidence = max(raw_confidence, 0.75)
                self._log.warning(
                    'correlation_collapse_detected',
                    override_regime='rotation',
                )

        # 2c. Compute momentum divergence for rotation confidence
        mom_divergence = 0.5
        sector_closes = self._get_sector_closes(universe)
        if sector_closes is not None and not sector_closes.empty:
            mom_divergence = momentum_divergence_score(sector_closes)

        effective_regime = self.regime.update(
            raw_regime, raw_confidence, market_factors,
        )
        effective_confidence = (
            raw_confidence if effective_regime == raw_regime
            else self.regime.current_confidence
        )

        # Adjust confidence for rotation regime using divergence signal
        if effective_regime == 'rotation':
            effective_confidence = round(
                effective_confidence * 0.7 + mom_divergence * 0.3, 4,
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

            # Get liquidity data for filter (key prefix varies by asset class)
            liq_prefix = 'etf' if self._asset_class == 'etf' else 'stock'
            adv_m = self._get_cached_float(f'{liq_prefix}:{symbol}:adv_30d_m')
            spread_bps = self._get_cached_float(f'{liq_prefix}:{symbol}:spread_bps')

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

    def _get_correlation_history(self) -> pd.Series | None:
        """Load daily avg-correlation series from Redis or DB for collapse detection."""
        if self._redis is None:
            return None
        try:
            raw = self._redis.get('cache:corr_history')
            if raw is None:
                return None
            data = json.loads(raw)
            if not data:
                return None
            return pd.Series(data, dtype=float).sort_index()
        except Exception:
            return None

    def _get_sector_closes(self, universe: list[str]) -> pd.DataFrame | None:
        """Load sector-level close prices for momentum divergence calculation."""
        try:
            from app.models.price_bars import PriceBar
            from sqlalchemy import desc

            # Use sector ETFs from the universe (XL* tickers)
            sector_syms = [s for s in universe if s.startswith('XL')]
            if len(sector_syms) < 6:
                return None

            frames = {}
            for sym in sector_syms:
                bars = (
                    PriceBar.query
                    .filter_by(symbol=sym, timeframe='1d')
                    .order_by(desc(PriceBar.timestamp))
                    .limit(300)
                    .all()
                )
                if bars:
                    frames[sym] = pd.Series(
                        {b.timestamp: float(b.close) for b in bars},
                    ).sort_index()

            if len(frames) < 6:
                return None
            return pd.DataFrame(frames).dropna(how='all')
        except Exception:
            return None

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

            # Cache with 5min TTL — namespaced by asset class
            cache_key = (
                'cache:signals:latest'
                if self._asset_class == 'etf'
                else f'cache:signals:{self._asset_class}:latest'
            )
            self._redis.set(cache_key, payload, ex=300)

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
                    # dispersion_score removed from active composite
                    liquidity_score=factors.get('liquidity_score'),
                    regime=regime,
                    regime_confidence=confidence,
                    signal_type=sig['signal_type'],
                    block_reason=sig.get('block_reason'),
                    asset_class=self._asset_class,
                    account_id=self._get_account_id(),
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

    def _get_account_id(self) -> int | None:
        """Resolve account_id for this asset class (cached)."""
        if self._account_id_cache is not None:
            return self._account_id_cache
        try:
            from app.models.account import Account
            acct = Account.query.filter_by(
                asset_class=self._asset_class, is_active=True,
            ).first()
            if acct:
                self._account_id_cache = acct.id
                return acct.id
        except Exception:
            pass
        return None
