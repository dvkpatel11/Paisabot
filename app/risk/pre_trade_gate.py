from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from app.risk.liquidity_monitor import LiquidityMonitor

logger = structlog.get_logger()


class PreTradeGate:
    """Pre-trade approval gate for proposed orders.

    Runs before any order list enters the Execution Engine.
    Checks kill switches, drawdown headroom, position concentration,
    sector exposure, liquidity, and regime constraints.

    Consumes from channel:orders_proposed (list queue).
    Approved orders pushed to channel:orders_approved (list queue).
    """

    def __init__(
        self,
        redis_client,
        config_loader=None,
        liquidity_monitor: LiquidityMonitor | None = None,
        asset_class: str = 'etf',
    ):
        self._redis = redis_client
        self._config = config_loader
        self._asset_class = asset_class
        self._liquidity = liquidity_monitor or LiquidityMonitor(
            redis_client, config_loader, asset_class=asset_class,
        )
        self._log = logger.bind(component='pre_trade_gate', asset_class=asset_class)

    # ── kill switch checks ──────────────────────────────────────────

    def _is_kill_switch_active(self, switch: str) -> bool:
        if self._config is not None:
            return self._config.is_kill_switch_active(switch)
        if self._redis is not None:
            val = self._redis.get(f'kill_switch:{switch}')
            return val in ('1', b'1')
        return False

    # ── config helpers ──────────────────────────────────────────────

    def _get_float(self, category: str, key: str, default: float) -> float:
        if self._config is not None:
            return self._config.get_float(category, key, default)
        return default

    def _get_bool(self, category: str, key: str, default: bool) -> bool:
        if self._config is not None:
            return self._config.get_bool(category, key, default)
        return default

    # ── main gate ───────────────────────────────────────────────────

    def evaluate(
        self,
        proposed_orders: list[dict],
        current_positions: list[dict],
        portfolio_value: float,
        current_drawdown: float = 0.0,
        regime: str = 'consolidation',
        sector_map: dict[str, str] | None = None,
    ) -> dict:
        """Evaluate a batch of proposed orders.

        Args:
            proposed_orders: list of order dicts with keys:
                symbol, side ('buy'|'sell'), notional, weight (optional)
            current_positions: list of position dicts with keys:
                symbol, weight, sector, status
            portfolio_value: current portfolio NAV
            current_drawdown: current portfolio drawdown (negative decimal)
            regime: current market regime
            sector_map: {symbol: sector} mapping

        Returns:
            dict with approved (list), blocked (list), summary.
        """
        if sector_map is None:
            sector_map = {}

        approved = []
        blocked = []

        # 1. Global kill switch checks
        global_block = self._check_global_kill_switches()
        if global_block:
            for order in proposed_orders:
                blocked.append({**order, 'block_reason': global_block})
            return self._make_result(approved, blocked)

        # 2. Drawdown headroom check
        max_dd = self._get_float('risk', 'max_drawdown', -0.15)
        dd_headroom = current_drawdown - max_dd  # negative means close to limit
        if dd_headroom < 0.02:  # within 2% of halt
            for order in proposed_orders:
                if order.get('side') == 'buy':
                    blocked.append({
                        **order,
                        'block_reason': f'drawdown_headroom {dd_headroom:.2%} too narrow',
                    })
                else:
                    approved.append(order)
            return self._make_result(approved, blocked)

        # 3. Per-order checks
        current_weights = self._compute_current_weights(current_positions)
        current_sectors = self._compute_sector_exposure(current_positions, sector_map)

        for order in proposed_orders:
            reason = self._check_single_order(
                order, current_weights, current_sectors,
                portfolio_value, regime, sector_map,
            )
            if reason is None:
                approved.append(order)
            else:
                blocked.append({**order, 'block_reason': reason})

        result = self._make_result(approved, blocked)
        self._publish_decisions(result)
        return result

    # ── global checks ───────────────────────────────────────────────

    def _check_global_kill_switches(self) -> str | None:
        """Return block reason if any global kill switch is active."""
        if self._is_kill_switch_active('all'):
            return 'kill_switch:all active'
        if self._is_kill_switch_active('trading'):
            return 'kill_switch:trading active'
        if self._is_kill_switch_active('rebalance'):
            return 'kill_switch:rebalance active'
        if self._is_kill_switch_active('maintenance'):
            return 'kill_switch:maintenance active'
        return None

    # ── per-order checks ────────────────────────────────────────────

    def _check_single_order(
        self,
        order: dict,
        current_weights: dict[str, float],
        current_sectors: dict[str, float],
        portfolio_value: float,
        regime: str,
        sector_map: dict[str, str],
    ) -> str | None:
        """Return block reason or None if order is approved."""
        symbol = order['symbol']
        side = order.get('side', 'buy')
        notional = float(order.get('notional', 0))

        # Sells are always allowed (de-risking)
        if side == 'sell':
            return None

        # Short orders only in risk_off with config enabled
        if side == 'short':
            allow_short = self._get_bool('execution', 'allow_short', False)
            if not allow_short:
                return 'short_selling_disabled'
            if regime != 'risk_off':
                return f'short_blocked_in_{regime}_regime'

        # Liquidity shock check
        if self._liquidity.is_shocked(symbol):
            return 'liquidity_shock_active'

        # Earnings blackout check (stocks only — ETFs don't have earnings)
        if self._asset_class == 'stock' and self._is_in_earnings_blackout(symbol):
            return 'earnings_blackout_zone'

        # Position concentration check — enforce limit exactly, no hidden tolerance.
        max_pos = self._get_float('portfolio', 'max_position_size', 0.05)
        order_weight = notional / portfolio_value if portfolio_value > 0 else 0
        new_weight = current_weights.get(symbol, 0.0) + order_weight
        if new_weight > max_pos:
            return f'position_limit {new_weight:.2%} > {max_pos:.2%}'

        # Sector concentration check — enforce limit exactly.
        max_sector = self._get_float('portfolio', 'max_sector_exposure', 0.25)
        sector = sector_map.get(symbol, 'Unknown')
        new_sector_exp = current_sectors.get(sector, 0.0) + order_weight
        if new_sector_exp > max_sector:
            return f'sector_limit {sector} {new_sector_exp:.2%} > {max_sector:.2%}'

        # Min order size
        min_notional = self._get_float('execution', 'min_order_notional', 100.0)
        if notional < min_notional:
            return f'below_min_notional ${notional:.0f} < ${min_notional:.0f}'

        # Max order size
        max_notional = self._get_float('execution', 'max_order_notional', 50000.0)
        if notional > max_notional:
            return f'above_max_notional ${notional:.0f} > ${max_notional:.0f}'

        return None

    # ── earnings blackout ──────────────────────────────────────────

    def _is_in_earnings_blackout(self, symbol: str) -> bool:
        """Check if a stock is within the earnings blackout zone (≤3 days).

        Returns False for ETFs (no earnings dates) or when earnings date
        is unknown. Only blocks trades for stocks with a known imminent
        earnings report.
        """
        if self._redis is None:
            return False

        try:
            raw = self._redis.get(f'earnings:{symbol}:days_to')
            if raw is None:
                return False
            val = raw.decode() if isinstance(raw, bytes) else raw
            days = int(val)
            return days <= 3
        except (ValueError, TypeError):
            return False

    # ── weight computation ──────────────────────────────────────────

    @staticmethod
    def _compute_current_weights(positions: list[dict]) -> dict[str, float]:
        weights = {}
        for pos in positions:
            if pos.get('status', 'open') == 'open':
                weights[pos['symbol']] = float(pos.get('weight', 0.0))
        return weights

    @staticmethod
    def _compute_sector_exposure(
        positions: list[dict],
        sector_map: dict[str, str],
    ) -> dict[str, float]:
        sectors: dict[str, float] = {}
        for pos in positions:
            if pos.get('status', 'open') != 'open':
                continue
            sector = sector_map.get(pos['symbol'], pos.get('sector', 'Unknown'))
            weight = float(pos.get('weight', 0.0))
            sectors[sector] = sectors.get(sector, 0.0) + weight
        return sectors

    # ── result + publish ────────────────────────────────────────────

    def _make_result(self, approved: list, blocked: list) -> dict:
        result = {
            'approved': approved,
            'blocked': blocked,
            'approved_count': len(approved),
            'blocked_count': len(blocked),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        self._log.info(
            'pre_trade_gate_evaluated',
            approved=len(approved),
            blocked=len(blocked),
            block_reasons=[b.get('block_reason') for b in blocked],
        )
        return result

    def _publish_decisions(self, result: dict) -> None:
        """Push approved orders to channel:orders_approved queue."""
        if self._redis is None:
            return

        for order in result['approved']:
            try:
                self._redis.lpush(
                    'channel:orders_approved',
                    json.dumps({
                        **order,
                        'approved_at': result['timestamp'],
                    }),
                )
            except Exception as exc:
                self._log.error('approved_order_publish_failed', error=str(exc))

        # Log blocked orders to risk alerts
        for order in result['blocked']:
            try:
                self._redis.lpush(
                    'channel:risk_alerts',
                    json.dumps({
                        'type': 'order_blocked',
                        'level': 'info',
                        'symbol': order.get('symbol'),
                        'reason': order.get('block_reason'),
                        'timestamp': result['timestamp'],
                    }),
                )
            except Exception as exc:
                self._log.error('blocked_alert_publish_failed', error=str(exc))
