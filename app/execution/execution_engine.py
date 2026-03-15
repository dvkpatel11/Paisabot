from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from app.execution.broker_base import BrokerBase
from app.execution.order_manager import OrderManager
from app.execution.position_tracker import PositionTracker
from app.execution.slippage_tracker import SlippageTracker

logger = structlog.get_logger()


class ExecutionEngine:
    """Top-level Execution Engine API.

    Orchestrates the full order lifecycle:
      1. Dequeue approved orders from channel:orders_approved
      2. Gate on operational mode (research/simulation/live)
      3. Pre-trade slippage estimation
      4. Submit to broker
      5. Monitor fills
      6. Record trades to DB
      7. Publish fill events for dashboard

    Usage:
        engine = ExecutionEngine(broker, redis, config, db_session)
        engine.process_approved_orders()     # one-shot
        engine.run_worker(poll_interval=1)   # continuous loop
    """

    def __init__(
        self,
        broker: BrokerBase | None = None,
        redis_client=None,
        config_loader=None,
        db_session=None,
    ):
        self._broker = broker
        self._redis = redis_client
        self._config = config_loader
        self._db = db_session
        self._log = logger.bind(component='execution_engine')

        self.order_manager = OrderManager(
            broker=broker,
            redis_client=redis_client,
            config_loader=config_loader,
        )
        self.slippage_tracker = SlippageTracker(config_loader)
        self.position_tracker = PositionTracker(db_session, redis_client) if db_session else None

    # ── main entry points ──────────────────────────────────────────

    def process_approved_orders(self, timeout: int = 1) -> list[dict]:
        """Dequeue and execute approved orders (one batch).

        Returns list of execution results, or empty list if queue is empty.
        """
        results = self.order_manager.dequeue_and_execute(timeout=timeout)
        if results is None:
            return []

        # Persist trades + update positions
        for result in results:
            self._persist_trade(result)
            self._update_position(result)

        # Cache execution summary
        self._cache_execution_state(results)

        self._log.info(
            'batch_executed',
            n_orders=len(results),
            n_filled=sum(1 for r in results if r['status'] == 'filled'),
            n_blocked=sum(1 for r in results if r['status'] == 'blocked'),
        )

        return results

    def execute_orders(self, orders: list[dict]) -> list[dict]:
        """Execute a list of orders directly (bypasses queue).

        Used for force liquidation or manual order submission.
        """
        results = self.order_manager.execute_batch(orders)

        for result in results:
            self._persist_trade(result)
            self._update_position(result)

        self._cache_execution_state(results)
        return results

    def force_liquidate(self, positions: dict[str, float], portfolio_value: float) -> list[dict]:
        """Generate and execute sell orders for all given positions.

        Args:
            positions: {symbol: weight} of positions to liquidate.
            portfolio_value: total portfolio NAV for notional calculation.

        Returns:
            list of execution results.
        """
        orders = [
            {
                'symbol': sym,
                'side': 'sell',
                'notional': round(weight * portfolio_value, 2),
            }
            for sym, weight in positions.items()
            if weight > 0
        ]

        if not orders:
            return []

        self._log.warning(
            'force_liquidation',
            n_positions=len(orders),
            total_notional=sum(o['notional'] for o in orders),
        )

        results = self.execute_orders(orders)

        # Clear force_liquidate kill switch after execution
        if self._redis is not None:
            self._redis.set('kill_switch:force_liquidate', '0')

        return results

    def get_execution_state(self) -> dict | None:
        """Read cached execution state from Redis."""
        if self._redis is None:
            return None
        raw = self._redis.get('cache:execution:latest')
        if raw:
            return json.loads(raw)
        return None

    def get_broker_positions(self) -> list[dict]:
        """Fetch live positions from the broker for reconciliation."""
        if self._broker is None:
            return []
        try:
            return self._broker.get_positions()
        except Exception as exc:
            self._log.error('broker_position_fetch_failed', error=str(exc))
            return []

    def reconcile_positions(
        self,
        internal_positions: dict[str, float],
        portfolio_value: float,
    ) -> dict:
        """Compare internal position weights with broker positions.

        Returns dict with matched, mismatched, broker_only, internal_only.
        """
        broker_positions = self.get_broker_positions()

        broker_map = {}
        for p in broker_positions:
            weight = p['market_value'] / portfolio_value if portfolio_value > 0 else 0
            broker_map[p['symbol']] = round(weight, 6)

        all_symbols = set(internal_positions) | set(broker_map)
        matched = []
        mismatched = []
        broker_only = []
        internal_only = []

        for sym in all_symbols:
            internal_w = internal_positions.get(sym, 0)
            broker_w = broker_map.get(sym, 0)

            if sym not in internal_positions:
                broker_only.append({'symbol': sym, 'broker_weight': broker_w})
            elif sym not in broker_map:
                internal_only.append({'symbol': sym, 'internal_weight': internal_w})
            elif abs(internal_w - broker_w) < 0.005:  # within 50 bps
                matched.append({'symbol': sym, 'weight': internal_w})
            else:
                mismatched.append({
                    'symbol': sym,
                    'internal_weight': internal_w,
                    'broker_weight': broker_w,
                    'delta': round(internal_w - broker_w, 6),
                })

        result = {
            'matched': matched,
            'mismatched': mismatched,
            'broker_only': broker_only,
            'internal_only': internal_only,
            'in_sync': len(mismatched) == 0 and len(broker_only) == 0 and len(internal_only) == 0,
        }

        if not result['in_sync']:
            self._log.warning(
                'position_reconciliation_mismatch',
                mismatched=len(mismatched),
                broker_only=len(broker_only),
                internal_only=len(internal_only),
            )

        return result

    # ── position tracking ───────────────────────────────────────────

    def _update_position(self, result: dict) -> None:
        """Update position records after a fill."""
        if self.position_tracker is None:
            return
        try:
            self.position_tracker.update_from_fill(result)
        except Exception as exc:
            self._log.error('position_update_failed', error=str(exc))

    # ── persistence ────────────────────────────────────────────────

    def _persist_trade(self, result: dict) -> None:
        """Write trade record to PostgreSQL."""
        if self._db is None:
            return

        try:
            from app.models.trades import Trade

            trade = Trade(
                symbol=result['symbol'],
                broker=self._broker.broker_name if self._broker else 'simulated',
                broker_order_id=result.get('broker_order_id'),
                side=result['side'],
                order_type=result.get('order_type', 'market'),
                requested_notional=result['notional'],
                filled_notional=result.get('filled_notional'),
                filled_quantity=result.get('filled_qty'),
                fill_price=result.get('fill_price'),
                mid_at_submission=result.get('mid_at_submission'),
                slippage_bps=result.get('actual_slippage_bps'),
                estimated_slippage_bps=result.get('estimated_slippage_bps'),
                status=result['status'],
                operational_mode=result.get('operational_mode', 'live'),
                trade_time=datetime.now(timezone.utc),
                fill_time=(
                    datetime.fromisoformat(result['filled_at'])
                    if result.get('filled_at') else None
                ),
                signal_composite=result.get('signal_composite'),
                regime=result.get('regime'),
            )
            self._db.add(trade)
            self._db.commit()

            self._log.info(
                'trade_persisted',
                symbol=result['symbol'],
                status=result['status'],
            )
        except Exception as exc:
            self._log.error('trade_persist_failed', error=str(exc))
            try:
                self._db.rollback()
            except Exception:
                pass

    def _cache_execution_state(self, results: list[dict]) -> None:
        if self._redis is None:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            state = {
                'n_executed': len(results),
                'n_filled': sum(1 for r in results if r['status'] == 'filled'),
                'n_blocked': sum(1 for r in results if r['status'] == 'blocked'),
                'n_aborted': sum(1 for r in results if r['status'] == 'aborted'),
                'n_errors': sum(1 for r in results if r['status'] == 'error'),
                'symbols': [r['symbol'] for r in results],
                'timestamp': now,
            }
            self._redis.set(
                'cache:execution:latest',
                json.dumps(state),
                ex=300,
            )
        except Exception as exc:
            self._log.error('execution_cache_failed', error=str(exc))
