from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import structlog

from app.portfolio.candidate_selector import CandidateSelector
from app.portfolio.constraints import PortfolioConstraints
from app.portfolio.constructor import PortfolioConstructor
from app.portfolio.exposure import ExposureAnalyzer
from app.portfolio.rebalancer import RebalanceEngine
from app.portfolio.sizer import PositionSizer

logger = structlog.get_logger()


class PortfolioManager:
    """Top-level Portfolio Construction Engine.

    Orchestrates the full pipeline:
      1. Select candidates from signals (regime-aware)
      2. Build target weights (PyPortfolioOpt)
      3. Apply volatility targeting
      4. Generate rebalance orders (sells-first)
      5. Push to channel:orders_proposed for Risk Engine pre-trade gate

    Also provides exposure reporting for the dashboard.
    """

    def __init__(self, redis_client=None, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='portfolio_manager')

        self.selector = CandidateSelector(config_loader)
        self.constructor = PortfolioConstructor(config_loader)
        self.sizer = PositionSizer()
        self.rebalancer = RebalanceEngine(redis_client, config_loader)
        self.exposure = ExposureAnalyzer()

    def run(
        self,
        signals: dict[str, dict],
        current_positions: dict[str, float],
        portfolio_value: float,
        prices_df: pd.DataFrame,
        regime: str = 'consolidation',
        sector_map: dict[str, str] | None = None,
        constraints: PortfolioConstraints | None = None,
    ) -> dict:
        """Execute the full portfolio construction pipeline.

        Args:
            signals: {symbol: signal_dict} from SignalGenerator.run().
            current_positions: {symbol: current_weight}.
            portfolio_value: total portfolio NAV.
            prices_df: daily close prices DataFrame.
            regime: current market regime.
            sector_map: {symbol: sector_name}.
            constraints: portfolio constraints (loaded from config if None).

        Returns:
            dict with target_weights, orders, exposure_report, metadata.
        """
        if sector_map is None:
            sector_map = {}

        if constraints is None:
            if self._config is not None:
                constraints = PortfolioConstraints.from_config(self._config)
            else:
                constraints = PortfolioConstraints()

        now = datetime.now(timezone.utc)

        # Apply regime-based constraint overrides
        constraints = self._apply_regime_overrides(constraints, regime)

        # 1. Select candidates
        candidates = self.selector.select(
            signals, constraints, regime, sector_map,
        )

        if not candidates:
            self._log.info('no_candidates_selected', regime=regime)
            return self._empty_result(current_positions, portfolio_value, now)

        # 2. Build target weights via PyPortfolioOpt
        target_weights = self.constructor.build_target_weights(
            candidates=candidates,
            prices_df=prices_df,
            sector_map=sector_map,
            constraints=constraints,
        )

        if not target_weights:
            self._log.warning('optimization_produced_no_weights')
            return self._empty_result(current_positions, portfolio_value, now)

        # 3. Apply volatility targeting
        vol_enabled = True
        if self._config is not None:
            vol_enabled = self._config.get_bool(
                'risk', 'vol_scaling_enabled', True,
            )

        if vol_enabled:
            self.sizer.vol_target = constraints.vol_target
            target_weights = self.sizer.apply_vol_target(
                target_weights, prices_df,
            )

        # 4. Generate rebalance orders
        orders = self.rebalancer.run_rebalance_cycle(
            target_weights=target_weights,
            current_positions=current_positions,
            portfolio_value=portfolio_value,
            regime=regime,
            constraints=constraints,
        )

        # 5. Compute exposure report
        exposure_report = self.exposure.full_report(
            weights=target_weights,
            sector_map=sector_map,
            prices_df=prices_df,
            portfolio_value=portfolio_value,
        )

        # Estimate expected vol
        expected_vol = self.sizer.estimate_portfolio_vol(
            target_weights, prices_df,
        )

        result = {
            'target_weights': target_weights,
            'orders': orders,
            'exposure': exposure_report,
            'expected_vol': round(expected_vol, 4) if expected_vol else None,
            'candidates': candidates,
            'regime': regime,
            'n_orders': len(orders),
            'n_sells': sum(1 for o in orders if o['side'] == 'sell'),
            'n_buys': sum(1 for o in orders if o['side'] == 'buy'),
            'timestamp': now.isoformat(),
        }

        # Cache result for dashboard
        self._cache_result(result)

        self._log.info(
            'portfolio_construction_complete',
            candidates=len(candidates),
            positions=len(target_weights),
            orders=len(orders),
            expected_vol=result['expected_vol'],
            regime=regime,
        )

        return result

    # ── regime overrides ────────────────────────────────────────────

    @staticmethod
    def _apply_regime_overrides(
        constraints: PortfolioConstraints,
        regime: str,
    ) -> PortfolioConstraints:
        """Adjust constraints for current regime.

        risk_off:
          - max_positions: min(current, 5)
          - cash_buffer: max(current, 0.20)
        """
        if regime == 'risk_off':
            return PortfolioConstraints(
                max_positions=min(constraints.max_positions, 5),
                max_position_size=constraints.max_position_size,
                min_position_size=constraints.min_position_size,
                max_sector_exposure=constraints.max_sector_exposure,
                turnover_limit_pct=constraints.turnover_limit_pct,
                cash_buffer_pct=max(constraints.cash_buffer_pct, 0.20),
                objective=constraints.objective,
                vol_target=constraints.vol_target,
            )
        return constraints

    # ── helpers ─────────────────────────────────────────────────────

    def _empty_result(
        self,
        current_positions: dict[str, float],
        portfolio_value: float,
        now: datetime,
    ) -> dict:
        return {
            'target_weights': {},
            'orders': [],
            'exposure': None,
            'expected_vol': None,
            'candidates': [],
            'regime': 'unknown',
            'n_orders': 0,
            'n_sells': 0,
            'n_buys': 0,
            'timestamp': now.isoformat(),
        }

    def _cache_result(self, result: dict) -> None:
        if self._redis is None:
            return
        try:
            cache = {
                'target_weights': result['target_weights'],
                'expected_vol': result['expected_vol'],
                'regime': result['regime'],
                'n_positions': len(result['target_weights']),
                'n_orders': result['n_orders'],
                'timestamp': result['timestamp'],
            }
            self._redis.set('cache:portfolio:latest', json.dumps(cache), ex=300)

            # Pub/sub for dashboard (lossy)
            self._redis.publish('channel:portfolio', json.dumps(cache))

        except Exception as exc:
            self._log.error('portfolio_cache_failed', error=str(exc))
