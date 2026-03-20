from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import structlog

from app.risk.pre_trade_gate import PreTradeGate
from app.risk.continuous_monitor import ContinuousMonitor

logger = structlog.get_logger()


class RiskManager:
    """Top-level Risk Engine API.

    Provides two entry points matching the architecture spec:
      1. pre_trade()  — evaluate proposed orders before execution
      2. monitor()    — run all continuous monitors on live portfolio

    Also provides helper methods for the dashboard/API layer.
    """

    def __init__(self, redis_client, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='risk_manager')

        self.gate = PreTradeGate(redis_client, config_loader)
        self.monitor_ = ContinuousMonitor(redis_client, config_loader)

    # ── pre-trade ───────────────────────────────────────────────────

    def pre_trade(
        self,
        proposed_orders: list[dict],
        current_positions: list[dict],
        portfolio_value: float,
        current_drawdown: float = 0.0,
        regime: str = 'consolidation',
        sector_map: dict[str, str] | None = None,
    ) -> dict:
        """Evaluate proposed orders through the pre-trade gate.

        Returns gate result with approved/blocked orders.
        """
        self._log.info(
            'pre_trade_start',
            order_count=len(proposed_orders),
            portfolio_value=portfolio_value,
            regime=regime,
        )

        result = self.gate.evaluate(
            proposed_orders=proposed_orders,
            current_positions=current_positions,
            portfolio_value=portfolio_value,
            current_drawdown=current_drawdown,
            regime=regime,
            sector_map=sector_map,
        )

        self._log.info(
            'pre_trade_complete',
            approved=result['approved_count'],
            blocked=result['blocked_count'],
        )

        return result

    # ── continuous monitoring ───────────────────────────────────────

    def monitor(
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
        """Run all continuous risk monitors.

        Returns aggregated results with overall status.
        """
        return self.monitor_.run(
            portfolio_values=portfolio_values,
            portfolio_returns=portfolio_returns,
            positions=positions,
            current_prices=current_prices,
            prices_df=prices_df,
            portfolio_value=portfolio_value,
            current_advs=current_advs,
            db_session=db_session,
        )

    # ── convenience methods ─────────────────────────────────────────

    def get_risk_state(self) -> dict | None:
        """Read cached risk state from Redis (for dashboard/API)."""
        if self._redis is None:
            return None
        raw = self._redis.get('cache:risk_state')
        if raw is None:
            return None
        return json.loads(raw)

    def force_liquidate(self, positions: list[dict]) -> list[dict]:
        """Generate sell orders for all open positions (force liquidate).

        Does NOT submit orders — returns the order list for the execution
        engine to process after admin confirms.
        """
        self._log.critical(
            'force_liquidate_requested',
            position_count=len(positions),
        )

        orders = []
        for pos in positions:
            if pos.get('status', 'open') != 'open':
                continue
            orders.append({
                'symbol': pos['symbol'],
                'side': 'sell',
                'notional': float(pos.get('notional', 0)),
                'reason': 'force_liquidate',
                'timestamp': datetime.now(timezone.utc).isoformat(),
            })

        if self._redis is not None:
            self._redis.lpush(
                'channel:risk_alerts',
                json.dumps({
                    'type': 'force_liquidate',
                    'level': 'critical',
                    'order_count': len(orders),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                }),
            )

        return orders

    def check_vol_scaling(
        self,
        portfolio_returns: pd.Series,
        vol_target: float = 0.12,
    ) -> dict:
        """Check if vol-triggered position reduction is needed."""
        return self.monitor_.check_vol_scaling(portfolio_returns, vol_target)

    def check_reentry(self, portfolio_returns: pd.Series) -> dict:
        """Check if conditions are met for re-entry after halt."""
        return self.monitor_.check_reentry_eligibility(portfolio_returns)
