"""Research Runner — standalone scoring and portfolio analysis service.

Accepts arbitrary symbol lists, runs real production factor engine +
composite scorer + portfolio construction, returns scored rankings and
hypothetical allocations.

Operates independently of the pipeline's ``in_active_set`` filter and
the global ``operational_mode`` setting.  Works whether the main app
is running or not — only needs DB and Redis connectivity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

logger = structlog.get_logger()


@dataclass
class ResearchResult:
    """Immutable result envelope for a research run."""

    run_id: str
    symbols: list[str]
    rankings: list[dict]          # sorted by composite desc
    hypothetical_weights: dict[str, float]
    hypothetical_orders: list[dict]
    factor_scores: dict[str, dict[str, float]]
    composite_weights_used: dict[str, float]
    regime: str
    portfolio_value: float
    expected_vol: float | None
    exposure: dict | None
    timestamp: str
    duration_ms: float
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ResearchRunner:
    """Standalone research scoring service.

    Unlike the pipeline, this service:
    - Accepts *any* symbol list (not limited to ``in_active_set``).
    - Does NOT persist trades or positions.
    - Does NOT publish to ``channel:orders_proposed``.
    - Caches results under ``cache:research:*`` for dashboard display.
    - Runs synchronously — no Celery required.

    Tooltip:
        Run production-quality factor scoring and portfolio construction
        on any ETF list.  Results include per-factor scores, composite
        rankings, hypothetical allocations, and cost-model estimates —
        all without touching the live trading pipeline.
    """

    SERVICE_NAME = 'research'
    TOOLTIP = (
        'Run production-quality factor scoring and portfolio construction '
        'on any ETF list.  Results include per-factor scores, composite '
        'rankings, hypothetical allocations, and cost-model estimates \u2014 '
        'all without touching the live trading pipeline.'
    )

    def __init__(
        self,
        redis_client=None,
        db_session=None,
        config_loader=None,
    ):
        self._redis = redis_client
        self._db = db_session
        self._config = config_loader
        self._log = logger.bind(component='research_runner')

    # ── main entry ────────────────────────────────────────────────

    def run(
        self,
        symbols: list[str],
        portfolio_value: float = 100_000.0,
        regime: str | None = None,
        custom_weights: dict[str, float] | None = None,
    ) -> ResearchResult:
        """Score and rank *symbols*, then build a hypothetical portfolio.

        Args:
            symbols: ETF symbols to evaluate (any list, not limited to active set).
            portfolio_value: hypothetical portfolio NAV for position sizing.
            regime: market regime override; auto-detected from Redis if None.
            custom_weights: factor weight overrides; loads from config if None.

        Returns:
            ``ResearchResult`` with rankings, weights, orders, and metadata.
        """
        import time
        t0 = time.monotonic()
        run_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        errors: list[str] = []
        self._log.info('research_run_start', symbols=symbols, run_id=run_id)

        # 1. Compute factors
        factor_scores = self._compute_factors(symbols, errors)

        # 2. Score and rank
        scorer, weights_used = self._get_scorer(custom_weights)
        rankings_df = scorer.rank_universe(factor_scores, weights_used)
        rankings = self._df_to_rankings(rankings_df)

        # 3. Detect regime
        if regime is None:
            regime = self._detect_regime(factor_scores)

        # 4. Build hypothetical portfolio
        prices_df = self._load_prices(symbols, errors)
        sector_map = self._build_sector_map(symbols)

        # Convert factor_scores → signal dicts for portfolio manager
        signals = self._factor_scores_to_signals(
            factor_scores, rankings_df, regime,
        )

        portfolio_result = self._build_portfolio(
            signals=signals,
            prices_df=prices_df,
            portfolio_value=portfolio_value,
            regime=regime,
            sector_map=sector_map,
            errors=errors,
        )

        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        result = ResearchResult(
            run_id=run_id,
            symbols=symbols,
            rankings=rankings,
            hypothetical_weights=portfolio_result.get('target_weights', {}),
            hypothetical_orders=portfolio_result.get('orders', []),
            factor_scores=factor_scores,
            composite_weights_used=weights_used,
            regime=regime,
            portfolio_value=portfolio_value,
            expected_vol=portfolio_result.get('expected_vol'),
            exposure=portfolio_result.get('exposure'),
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
            errors=errors,
        )

        self._cache_result(result)
        self._log.info(
            'research_run_complete',
            run_id=run_id,
            n_symbols=len(symbols),
            n_ranked=len(rankings),
            n_orders=len(result.hypothetical_orders),
            duration_ms=duration_ms,
        )

        return result

    # ── factor computation ────────────────────────────────────────

    def _compute_factors(
        self, symbols: list[str], errors: list[str],
    ) -> dict[str, dict[str, float]]:
        """Run production factor engine on arbitrary symbols."""
        try:
            from app.factors.factor_registry import FactorRegistry

            registry = FactorRegistry(
                redis_client=self._redis,
                db_session=self._db,
                config_loader=self._config,
            )
            return registry.compute_all(symbols)
        except Exception as exc:
            self._log.error('research_factor_compute_failed', error=str(exc))
            errors.append(f'factor_compute: {exc}')
            # Return neutral defaults so ranking still works
            return {sym: {} for sym in symbols}

    # ── scoring ───────────────────────────────────────────────────

    def _get_scorer(
        self, custom_weights: dict[str, float] | None,
    ) -> tuple[Any, dict[str, float]]:
        from app.signals.composite_scorer import CompositeScorer

        scorer = CompositeScorer(
            redis_client=self._redis,
            config_loader=self._config,
        )
        if custom_weights:
            # Normalize
            total = sum(custom_weights.values())
            weights = {k: v / total for k, v in custom_weights.items()} if total > 0 else custom_weights
        else:
            weights = scorer.load_weights()

        return scorer, weights

    def _df_to_rankings(self, df: pd.DataFrame) -> list[dict]:
        if df.empty:
            return []
        records = df.reset_index().to_dict(orient='records')
        # Round floats for JSON
        for rec in records:
            for k, v in rec.items():
                if isinstance(v, float):
                    rec[k] = round(v, 4)
        return records

    # ── regime ────────────────────────────────────────────────────

    def _detect_regime(self, factor_scores: dict[str, dict[str, float]]) -> str:
        """Try Redis cache first, then infer from factor scores."""
        if self._redis:
            raw = self._redis.get('cache:regime:current')
            if raw:
                try:
                    return json.loads(raw).get('regime', 'consolidation')
                except (json.JSONDecodeError, TypeError):
                    pass

        # Simple heuristic: average trend score across universe
        trend_scores = [
            s.get('trend_score', 0.5) for s in factor_scores.values()
        ]
        avg_trend = sum(trend_scores) / len(trend_scores) if trend_scores else 0.5
        if avg_trend > 0.65:
            return 'trending'
        if avg_trend < 0.35:
            return 'risk_off'
        return 'consolidation'

    # ── portfolio construction ────────────────────────────────────

    def _factor_scores_to_signals(
        self,
        factor_scores: dict[str, dict[str, float]],
        rankings_df: pd.DataFrame,
        regime: str,
    ) -> dict[str, dict]:
        """Convert factor scores to signal dicts for PortfolioManager."""
        signals = {}
        for symbol, scores in factor_scores.items():
            composite = float(
                rankings_df.loc[symbol, 'composite']
            ) if symbol in rankings_df.index else 0.5

            signal_type = 'long' if composite >= 0.5 else 'avoid'
            signals[symbol] = {
                'symbol': symbol,
                'composite_score': composite,
                'signal_type': signal_type,
                'regime': regime,
                'regime_confidence': 0.7,
            }
        return signals

    def _build_portfolio(
        self,
        signals: dict[str, dict],
        prices_df: pd.DataFrame,
        portfolio_value: float,
        regime: str,
        sector_map: dict[str, str],
        errors: list[str],
    ) -> dict:
        """Build hypothetical portfolio (no orders pushed to queue)."""
        try:
            from app.portfolio.portfolio_manager import PortfolioManager

            pm = PortfolioManager(self._redis, self._config)

            # Override rebalancer to NOT push to Redis queue
            original_lpush = None
            if pm.rebalancer._redis is not None:
                original_lpush = pm.rebalancer._redis.lpush
                pm.rebalancer._redis.lpush = lambda *a, **kw: None

            try:
                result = pm.run(
                    signals=signals,
                    current_positions={},  # research = fresh portfolio
                    portfolio_value=portfolio_value,
                    prices_df=prices_df,
                    regime=regime,
                    sector_map=sector_map,
                )
            finally:
                if original_lpush is not None:
                    pm.rebalancer._redis.lpush = original_lpush

            return result
        except Exception as exc:
            self._log.error('research_portfolio_failed', error=str(exc))
            errors.append(f'portfolio_construction: {exc}')
            return {'target_weights': {}, 'orders': [], 'expected_vol': None, 'exposure': None}

    # ── data loaders ──────────────────────────────────────────────

    def _load_prices(
        self, symbols: list[str], errors: list[str], days: int = 252,
    ) -> pd.DataFrame:
        """Load price data for symbols from DB."""
        try:
            from app.models.price_bars import PriceBar
            from sqlalchemy import desc

            frames = {}
            for symbol in symbols:
                bars = (
                    PriceBar.query
                    .filter_by(symbol=symbol, timeframe='1d')
                    .order_by(desc(PriceBar.timestamp))
                    .limit(days)
                    .all()
                )
                if bars:
                    frames[symbol] = pd.Series(
                        {b.timestamp: float(b.close) for b in bars},
                        name=symbol,
                    ).sort_index()

            if not frames:
                errors.append('no_price_data')
                return pd.DataFrame()
            return pd.DataFrame(frames).dropna(how='all')
        except Exception as exc:
            self._log.error('research_price_load_failed', error=str(exc))
            errors.append(f'price_load: {exc}')
            return pd.DataFrame()

    def _build_sector_map(self, symbols: list[str]) -> dict[str, str]:
        """Build sector map — works for any symbol, not just active set."""
        try:
            from app.models.etf_universe import ETFUniverse

            etfs = ETFUniverse.query.filter(
                ETFUniverse.symbol.in_(symbols),
            ).all()
            return {e.symbol: e.sector for e in etfs if e.sector}
        except Exception:
            return {}

    # ── caching ───────────────────────────────────────────────────

    def _cache_result(self, result: ResearchResult) -> None:
        """Cache the latest research result for dashboard display."""
        if self._redis is None:
            return
        try:
            self._redis.set(
                'cache:research:latest',
                json.dumps(result.to_dict(), default=str),
                ex=3600,
            )
            # Also publish for real-time dashboard updates
            self._redis.publish(
                'channel:research',
                json.dumps({
                    'run_id': result.run_id,
                    'n_symbols': len(result.symbols),
                    'top_ranked': result.rankings[0] if result.rankings else None,
                    'timestamp': result.timestamp,
                }),
            )
        except Exception as exc:
            self._log.warning('research_cache_failed', error=str(exc))
