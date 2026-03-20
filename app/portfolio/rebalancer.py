from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from app.portfolio.constraints import PortfolioConstraints

logger = structlog.get_logger()


class RebalanceEngine:
    """Generate rebalance orders from target vs current weights.

    Enforces turnover limits, skips micro-trades, sells before buys.
    """

    MIN_TRADE_THRESHOLD = 0.005  # 50 bps; skip micro-trades

    def __init__(self, redis_client=None, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='rebalancer')

    def generate_orders(
        self,
        target_weights: dict[str, float],
        current_positions: dict[str, float],
        portfolio_value: float,
        constraints: PortfolioConstraints | None = None,
    ) -> list[dict]:
        """Compute order list to move from current to target weights.

        Args:
            target_weights: {symbol: target_weight}.
            current_positions: {symbol: current_weight}.
            portfolio_value: total portfolio value in dollars.
            constraints: portfolio constraints for turnover limit.

        Returns:
            List of order dicts sorted sells-first, each with:
            symbol, side, notional, target_weight, current_weight, delta_weight.
        """
        if constraints is None:
            constraints = PortfolioConstraints()

        all_symbols = set(target_weights) | set(current_positions)

        # Check turnover
        total_turnover = sum(
            abs(target_weights.get(sym, 0) - current_positions.get(sym, 0))
            for sym in all_symbols
        ) / 2  # one-way turnover

        effective_targets = dict(target_weights)

        if total_turnover > constraints.turnover_limit_pct:
            self._log.info(
                'turnover_exceeds_limit',
                turnover=round(total_turnover, 4),
                limit=constraints.turnover_limit_pct,
            )
            # Scale back: move halfway toward target
            effective_targets = {
                sym: current_positions.get(sym, 0)
                + 0.5 * (target_weights.get(sym, 0) - current_positions.get(sym, 0))
                for sym in all_symbols
            }

        orders = []
        for sym in all_symbols:
            target = effective_targets.get(sym, 0.0)
            current = current_positions.get(sym, 0.0)
            delta = target - current

            if abs(delta) < self.MIN_TRADE_THRESHOLD:
                continue

            notional = abs(delta) * portfolio_value

            orders.append({
                'symbol': sym,
                'side': 'buy' if delta > 0 else 'sell',
                'notional': round(notional, 2),
                'target_weight': round(target, 6),
                'current_weight': round(current, 6),
                'delta_weight': round(delta, 6),
                'ref_price': self._get_ref_price(sym),
            })

        # Sells first to free up cash
        orders.sort(key=lambda o: (0 if o['side'] == 'sell' else 1))

        self._log.info(
            'orders_generated',
            n_orders=len(orders),
            sells=sum(1 for o in orders if o['side'] == 'sell'),
            buys=sum(1 for o in orders if o['side'] == 'buy'),
            turnover=round(total_turnover, 4),
        )

        return orders

    # ── helpers ────────────────────────────────────────────────────

    def _get_ref_price(self, symbol: str) -> float | None:
        """Look up the latest mid/close price for *symbol*.

        Used as ``ref_price`` on order dicts so that simulated fills in
        research mode have a price source even when the Redis mid-price
        cache is empty.

        Lookup order:
          1. Redis ``cache:mid_prices`` hash (set by market data layer).
          2. ``None`` — the execution layer already handles this gracefully.
        """
        if self._redis is None:
            return None
        try:
            val = self._redis.hget('cache:mid_prices', symbol)
            if val is not None:
                return float(val.decode() if isinstance(val, bytes) else val)
        except (ValueError, TypeError):
            pass
        return None

    def run_rebalance_cycle(
        self,
        target_weights: dict[str, float],
        current_positions: dict[str, float],
        portfolio_value: float,
        regime: str = 'consolidation',
        constraints: PortfolioConstraints | None = None,
    ) -> list[dict]:
        """Full rebalance orchestration: generate orders → push to queue.

        Returns the generated order list.
        """
        orders = self.generate_orders(
            target_weights, current_positions, portfolio_value, constraints,
        )

        if not orders:
            self._log.info('no_rebalance_needed')
            return orders

        # Push orders to Redis queue for risk engine
        if self._redis is not None:
            payload = json.dumps({
                'orders': orders,
                'portfolio_value': portfolio_value,
                'regime': regime,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            })
            self._redis.lpush('channel:orders_proposed', payload)

            # Cache portfolio state
            self._redis.set('cache:portfolio:current', json.dumps({
                'weights': target_weights,
                'portfolio_value': portfolio_value,
                'regime': regime,
                'rebalance_time': datetime.now(timezone.utc).isoformat(),
            }), ex=300)

        return orders
