from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import structlog

from app.risk.drawdown_monitor import DrawdownMonitor
from app.risk.stop_loss_engine import StopLossEngine
from app.risk.var_monitor import VaRMonitor
from app.risk.correlation_monitor import CorrelationMonitor
from app.risk.liquidity_monitor import LiquidityMonitor

logger = structlog.get_logger()


class ContinuousMonitor:
    """Orchestrator for all continuous risk monitors.

    Runs every tick / every 1 min in production. Aggregates results from:
      - DrawdownMonitor (portfolio-level)
      - StopLossEngine (position-level)
      - VaRMonitor (portfolio-level)
      - CorrelationMonitor (portfolio-level)
      - LiquidityMonitor (per-symbol)

    Publishes aggregated risk state to Redis for dashboard consumption.
    """

    def __init__(self, redis_client, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='continuous_monitor')

        self.drawdown = DrawdownMonitor(redis_client, config_loader)
        self.stop_loss = StopLossEngine(redis_client, config_loader)
        self.var = VaRMonitor(redis_client, config_loader)
        self.correlation = CorrelationMonitor(redis_client, config_loader)
        self.liquidity = LiquidityMonitor(redis_client, config_loader)

    def run(
        self,
        portfolio_values: pd.Series,
        portfolio_returns: pd.Series,
        positions: list[dict],
        current_prices: dict[str, float],
        prices_df: pd.DataFrame,
        portfolio_value: float = 100_000.0,
        current_advs: dict[str, float] | None = None,
        db_session=None,
    ) -> dict:
        """Run all continuous monitors and return aggregated results.

        Args:
            portfolio_values: time-indexed NAV series.
            portfolio_returns: time-indexed daily return series.
            positions: list of position dicts (from Position model).
            current_prices: {symbol: latest_price}.
            prices_df: DataFrame with daily closes for correlation.
            portfolio_value: current portfolio NAV.
            current_advs: {symbol: current_session_adv} for liquidity.

        Returns:
            dict with sub-results for each monitor + overall status.
        """
        now = datetime.now(timezone.utc)
        results = {}

        # 1. Drawdown check
        results['drawdown'] = self.drawdown.check(portfolio_values)

        # 2. Stop-loss scan
        results['stop_loss'] = self.stop_loss.scan_all_positions(
            positions, current_prices, db_session=db_session,
        )

        # 3. VaR computation
        results['var'] = self.var.compute(portfolio_returns, portfolio_value)

        # 4. Correlation check
        held_symbols = [
            p['symbol'] for p in positions
            if p.get('status', 'open') == 'open'
        ]
        results['correlation'] = self.correlation.check(held_symbols, prices_df)

        # 5. Liquidity scan
        results['liquidity'] = self.liquidity.scan_universe(
            held_symbols, current_advs,
        )

        # 6. Volatility scaling check
        results['vol_scaling'] = self.check_vol_scaling(portfolio_returns)

        # Determine overall status
        overall = self._aggregate_status(results)
        results['overall_status'] = overall
        results['timestamp'] = now.isoformat()

        # Cache for dashboard
        self._cache_risk_state(results)

        # Publish to dashboard (pub/sub, lossy)
        self._publish_risk_state(results)

        self._log.info(
            'continuous_monitor_complete',
            overall=overall,
            drawdown_status=results['drawdown']['status'],
            stop_exits=len(results['stop_loss']['exits']),
            var_status=results['var']['status'],
            corr_status=results['correlation']['status'],
            liquidity_shocked=len(results['liquidity']['shocked']),
            vol_scaling=results['vol_scaling']['action'],
        )

        return results

    # ── vol-triggered position reduction ────────────────────────────

    def check_vol_scaling(
        self,
        portfolio_returns: pd.Series,
        vol_target: float = 0.12,
    ) -> dict:
        """Check if portfolio vol exceeds 1.5x target and needs scaling.

        Returns:
            dict with realized_vol, target, scale_factor, action.
        """
        if len(portfolio_returns) < 20:
            return {
                'realized_vol': 0.0,
                'target': vol_target,
                'scale_factor': 1.0,
                'action': 'no_change',
            }

        realized = float(portfolio_returns.tail(20).std() * (252 ** 0.5))
        threshold = vol_target * 1.5

        if realized > threshold:
            scale = 0.50  # reduce all positions to 50%
            action = 'scale_down_50pct'
        elif realized > vol_target:
            scale = vol_target / realized
            action = 'scale_to_target'
        else:
            scale = 1.0
            action = 'no_change'

        return {
            'realized_vol': round(realized, 6),
            'target': vol_target,
            'scale_factor': round(scale, 4),
            'action': action,
        }

    # ── re-entry after halt ─────────────────────────────────────────

    def check_reentry_eligibility(
        self,
        portfolio_returns: pd.Series,
    ) -> dict:
        """Check if conditions are met for re-entry after a trading halt.

        Requirements (from risk_framework.md):
          1. 5 consecutive risk_ok days
          2. Start at 50% sizing for 10 days
          3. Rolling 30-day Sharpe > 0.5 before full sizing
          4. Admin must manually clear kill_switch:trading
        """
        is_halted = False
        if self._config is not None:
            is_halted = self._config.is_kill_switch_active('trading')
        elif self._redis is not None:
            val = self._redis.get('kill_switch:trading')
            is_halted = val in ('1', b'1')

        if not is_halted:
            return {
                'is_halted': False,
                'eligible': True,
                'sizing_pct': 1.0,
                'reason': 'not_halted',
            }

        if len(portfolio_returns) < 30:
            return {
                'is_halted': True,
                'eligible': False,
                'sizing_pct': 0.0,
                'reason': 'insufficient_data_for_reentry',
            }

        # Check rolling 30-day Sharpe
        recent_30 = portfolio_returns.tail(30)
        sharpe_30d = float(
            (recent_30.mean() / recent_30.std()) * (252 ** 0.5)
        ) if recent_30.std() > 0 else 0.0

        # Check 5 consecutive positive/neutral days
        recent_5 = portfolio_returns.tail(5)
        consecutive_ok = all(r >= -0.005 for r in recent_5)

        if not consecutive_ok:
            return {
                'is_halted': True,
                'eligible': False,
                'sizing_pct': 0.0,
                'sharpe_30d': round(sharpe_30d, 3),
                'reason': 'need_5_consecutive_ok_days',
            }

        if sharpe_30d < 0.5:
            return {
                'is_halted': True,
                'eligible': False,
                'sizing_pct': 0.0,
                'sharpe_30d': round(sharpe_30d, 3),
                'reason': f'sharpe_30d {sharpe_30d:.2f} < 0.5',
            }

        # Eligible for re-entry at reduced sizing
        return {
            'is_halted': True,
            'eligible': True,
            'sizing_pct': 0.50,  # start at 50%, admin decides when to go full
            'sharpe_30d': round(sharpe_30d, 3),
            'reason': 'eligible_at_50pct_sizing',
        }

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _aggregate_status(results: dict) -> str:
        """Determine worst-case overall status."""
        if results['drawdown']['status'] == 'halt':
            return 'halt'
        if results['stop_loss']['exits']:
            return 'critical'
        if results['drawdown']['status'] == 'critical':
            return 'critical'
        if results['var']['status'] == 'breach':
            return 'warning'
        if results['correlation']['status'] in ('warn', 'force_diversify'):
            return 'warning'
        if results['drawdown']['status'] == 'warn':
            return 'warning'
        if results['liquidity']['shocked']:
            return 'warning'
        return 'ok'

    def _cache_risk_state(self, results: dict) -> None:
        """Cache aggregated risk state in Redis (5-min TTL)."""
        if self._redis is None:
            return
        try:
            # Serialize, stripping non-JSON-serializable items
            cache_data = {
                'overall_status': results['overall_status'],
                'drawdown': results['drawdown'],
                'var': results['var'],
                'correlation': {
                    'avg_corr': results['correlation']['avg_corr'],
                    'status': results['correlation']['status'],
                    'consecutive_breach_days': results['correlation']['consecutive_breach_days'],
                },
                'stop_loss': {
                    'exit_count': len(results['stop_loss']['exits']),
                    'reduction_count': len(results['stop_loss']['reductions']),
                },
                'liquidity': {
                    'shocked_count': len(results['liquidity']['shocked']),
                },
                'timestamp': results['timestamp'],
            }
            self._redis.set(
                'cache:risk_state', json.dumps(cache_data), ex=300,
            )
        except Exception as exc:
            self._log.error('risk_state_cache_failed', error=str(exc))

    def _publish_risk_state(self, results: dict) -> None:
        """Publish risk state to pub/sub for dashboard (lossy)."""
        if self._redis is None:
            return
        try:
            self._redis.publish('channel:risk_alerts', json.dumps({
                'type': 'risk_state_update',
                'overall_status': results['overall_status'],
                'timestamp': results['timestamp'],
            }))
        except Exception as exc:
            self._log.error('risk_state_publish_failed', error=str(exc))
