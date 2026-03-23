from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from app.execution.broker_base import BrokerBase, BrokerOrder
from app.execution.cost_model import TransactionCostModel
from app.execution.fill_monitor import FillMonitor
from app.execution.slippage_tracker import SlippageTracker

logger = structlog.get_logger()


class OrderManager:
    """Core execution orchestrator.

    Dequeues approved orders from Redis, checks operational mode and
    kill switches, estimates slippage, submits to the broker, monitors
    fills, and publishes results.

    This is the only module that gates behavior on operational_mode:
    - research:   simulate fills via cost model (no broker)
    - simulation: skip execution, mark-to-market only
    - live:       submit real orders to the broker
    """

    # Default ADV for ETFs when not available (conservative estimate)
    DEFAULT_ADV_USD = 50_000_000

    def __init__(
        self,
        broker: BrokerBase | None = None,
        redis_client=None,
        config_loader=None,
    ):
        self._broker = broker
        self._redis = redis_client
        self._config = config_loader
        self._slippage = SlippageTracker(config_loader)
        self._cost_model = TransactionCostModel(self._slippage, config_loader)
        self._fill_monitor = (
            FillMonitor(broker) if broker is not None else None
        )
        self._log = logger.bind(component='order_manager')

    # ── main entry point ───────────────────────────────────────────

    def execute_order(self, order: dict) -> dict:
        """Execute a single approved order through the full pipeline.

        Args:
            order: dict with symbol, side, notional, and optional metadata.

        Returns:
            Execution result dict with status, fill details, slippage.
        """
        symbol = order['symbol']
        side = order['side']
        notional = order['notional']
        now = datetime.now(timezone.utc)

        self._log.info(
            'executing_order',
            symbol=symbol,
            side=side,
            notional=notional,
        )

        # 1. Check kill switches
        if self._is_kill_switch_active():
            self._log.warning(
                'order_blocked_kill_switch',
                symbol=symbol,
            )
            return self._result(
                order, 'blocked', reason='kill_switch_active', timestamp=now,
            )

        # 2. Check operational mode
        mode = self._get_operational_mode()

        if mode == 'research':
            return self._simulate_fill(order, now)

        if mode == 'simulation':
            return self._result(
                order, 'skipped', reason='simulation_mode', timestamp=now,
            )

        # 3. Live mode — need a broker
        if self._broker is None:
            self._log.error('no_broker_configured')
            return self._result(
                order, 'error', reason='no_broker', timestamp=now,
            )

        # 4. Get mid price
        try:
            quote = self._broker.get_latest_quote(symbol)
            mid_price = quote['mid']
        except Exception as exc:
            self._log.error('quote_fetch_failed', symbol=symbol, error=str(exc))
            return self._result(
                order, 'error', reason='quote_failed', timestamp=now,
            )

        # 5. Pre-trade slippage check
        daily_volume = self._get_daily_volume(symbol)
        volatility = self._get_volatility(symbol)
        exec_window = self._get_exec_window()

        slippage_check = self._slippage.check_pretrade(
            notional, mid_price, daily_volume, volatility, exec_window,
        )

        if not slippage_check['acceptable']:
            self._log.warning(
                'order_aborted',
                symbol=symbol,
                reason='slippage_too_high',
                estimated_bps=slippage_check['estimated_bps'],
            )
            return self._result(
                order, 'aborted',
                reason='slippage_too_high',
                estimated_slippage_bps=slippage_check['estimated_bps'],
                timestamp=now,
            )

        # 6. Convert notional to shares
        submit_price = quote['ask'] if side == 'buy' else quote['bid']
        qty = self._notional_to_qty(notional, submit_price)

        if qty <= 0:
            return self._result(
                order, 'skipped', reason='zero_quantity', timestamp=now,
            )

        # 7. Determine order type and TIF
        order_type = self._get_order_type()
        limit_price = None
        if order_type == 'limit':
            offset_bps = self._get_limit_offset_bps()
            if side == 'buy':
                limit_price = round(mid_price * (1 + offset_bps / 10_000), 2)
            else:
                limit_price = round(mid_price * (1 - offset_bps / 10_000), 2)

        tif = self._get_time_in_force()

        # 8. Submit to broker
        try:
            broker_order = self._broker.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                order_type=order_type,
                time_in_force=tif,
                limit_price=limit_price,
            )
        except Exception as exc:
            self._log.error(
                'order_submission_failed',
                symbol=symbol,
                error=str(exc),
            )
            return self._result(
                order, 'error', reason='submission_failed',
                error=str(exc), timestamp=now,
            )

        self._log.info(
            'order_submitted',
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            broker_order_id=broker_order.order_id,
            mid_price=mid_price,
            estimated_slippage_bps=slippage_check['estimated_bps'],
        )

        # 9. Wait for fill
        final_order = self._fill_monitor.wait_for_fill(broker_order.order_id)

        # 10. Detect partial fill and measure post-trade slippage
        actual_slippage_bps = None
        effective_status = final_order.status
        filled_qty = final_order.filled_qty or 0

        # An order cancelled after timeout may have partially filled at the broker.
        # Promote to 'partial_fill' so position tracking records the actual shares held.
        if effective_status in ('cancelled', 'expired') and filled_qty > 0:
            effective_status = 'partial_fill'
            self._log.warning(
                'order_partial_fill_detected',
                symbol=symbol,
                broker_order_id=broker_order.order_id,
                filled_qty=filled_qty,
                requested_qty=qty,
                pct_filled=round(filled_qty / qty, 4) if qty else 0,
            )

        if effective_status in ('filled', 'partial_fill') and final_order.filled_avg_price:
            actual_slippage_bps = self._slippage.measure_posttrade(
                final_order.filled_avg_price, mid_price, side,
            )

            if abs(actual_slippage_bps) > slippage_check['threshold_bps']:
                self._log.warning(
                    'slippage_alert',
                    symbol=symbol,
                    actual_bps=actual_slippage_bps,
                    threshold_bps=slippage_check['threshold_bps'],
                )

        # 11. Build result
        result = self._result(
            order,
            status=effective_status,
            broker_order_id=final_order.order_id,
            fill_price=final_order.filled_avg_price,
            filled_qty=filled_qty,
            filled_at=final_order.filled_at,
            mid_at_submission=mid_price,
            estimated_slippage_bps=slippage_check['estimated_bps'],
            actual_slippage_bps=actual_slippage_bps,
            timestamp=now,
        )

        # 12. Publish fill event
        if effective_status in ('filled', 'partial_fill'):
            self._publish_fill(result)

        return result

    def execute_batch(self, orders: list[dict]) -> list[dict]:
        """Execute a batch of orders sequentially (sells first).

        Args:
            orders: list of order dicts (pre-sorted sells-first by rebalancer).

        Returns:
            list of execution result dicts.
        """
        results = []
        for order in orders:
            result = self.execute_order(order)
            results.append(result)
        return results

    def dequeue_and_execute(self, timeout: int = 1) -> list[dict] | None:
        """Pop from channel:orders_approved and execute.

        Returns None if no orders in queue.
        """
        if self._redis is None:
            return None

        raw = self._redis.brpop('channel:orders_approved', timeout=timeout)
        if raw is None:
            return None

        payload = json.loads(raw[1])
        orders = payload if isinstance(payload, list) else payload.get('orders', [payload])

        return self.execute_batch(orders)

    # ── simulated fills (research mode) ────────────────────────────

    def _simulate_fill(self, order: dict, now: datetime) -> dict:
        """Simulate a fill for research/backtesting mode.

        Uses :class:`TransactionCostModel` (half-spread + Almgren-Chriss
        market impact) to produce a realistic fill price from the mid price
        stored in Redis (or the order's ``ref_price``).
        """
        symbol = order['symbol']
        side = order['side']
        notional = order['notional']

        # Fetch mid price: try Redis cache, then order metadata, then error
        mid_price = self._get_cached_mid(symbol)
        if mid_price is None:
            mid_price = order.get('ref_price')
        if mid_price is None or mid_price <= 0:
            self._log.warning(
                'simulate_fill_no_price',
                symbol=symbol,
                reason='no mid price available',
            )
            return self._result(
                order, 'error', reason='no_price_for_simulation',
                timestamp=now,
            )

        daily_volume = self._get_daily_volume(symbol)
        volatility = self._get_volatility(symbol)
        exec_window = self._get_exec_window()
        spread_bps = self._get_config_float('universe', 'max_spread_bps', 1.0)

        breakdown = self._cost_model.estimate(
            symbol=symbol,
            side=side,
            notional=notional,
            mid_price=mid_price,
            daily_volume_usd=daily_volume,
            volatility=volatility,
            spread_bps=spread_bps,
            execution_window_min=exec_window,
        )

        result = self._result(
            order,
            status='filled',
            reason='simulated',
            operational_mode='research',
            fill_price=breakdown.fill_price,
            filled_qty=breakdown.filled_qty,
            filled_at=now.isoformat(),
            mid_at_submission=mid_price,
            estimated_slippage_bps=breakdown.total_bps,
            actual_slippage_bps=breakdown.total_bps,
            cost_breakdown={
                'half_spread_bps': breakdown.half_spread_bps,
                'market_impact_bps': breakdown.market_impact_bps,
                'total_bps': breakdown.total_bps,
            },
            timestamp=now,
        )

        self._publish_fill(result)
        self._log.info(
            'simulated_fill',
            symbol=symbol,
            side=side,
            notional=notional,
            fill_price=breakdown.fill_price,
            filled_qty=breakdown.filled_qty,
            slippage_bps=round(breakdown.total_bps, 2),
        )

        return result

    def _get_cached_mid(self, symbol: str) -> float | None:
        """Fetch cached mid price from Redis."""
        if self._redis is None:
            return None
        val = self._redis.hget('cache:mid_prices', symbol)
        if val:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
        return None

    # ── config helpers ─────────────────────────────────────────────

    def _is_kill_switch_active(self) -> bool:
        if self._redis is None:
            return False
        for switch in ('trading', 'rebalance', 'all'):
            val = self._redis.get(f'kill_switch:{switch}')
            if val in ('1', b'1'):
                return True
        return False

    def _get_operational_mode(self) -> str:
        if self._config is not None:
            return self._config.get('system', 'operational_mode', 'simulation')
        if self._redis is not None:
            val = self._redis.hget('config:system', 'operational_mode')
            if val:
                return val.decode() if isinstance(val, bytes) else val
        return 'simulation'

    def _get_daily_volume(self, symbol: str) -> float:
        """Get estimated daily volume in USD for a symbol."""
        if self._redis is not None:
            val = self._redis.hget(f'cache:adv', symbol)
            if val:
                return float(val.decode() if isinstance(val, bytes) else val)
        return self.DEFAULT_ADV_USD

    def _get_volatility(self, symbol: str) -> float:
        """Get realized volatility for a symbol (annualized)."""
        if self._redis is not None:
            val = self._redis.hget('cache:volatility', symbol)
            if val:
                return float(val.decode() if isinstance(val, bytes) else val)
        return 0.20  # 20% annualized default

    def _get_exec_window(self) -> int:
        return self._get_config_int('execution', 'execution_window_minutes', 30)

    def _get_order_type(self) -> str:
        if self._config is not None:
            return self._config.get('execution', 'order_type', 'market')
        return 'market'

    def _get_limit_offset_bps(self) -> float:
        return self._get_config_float('execution', 'limit_slippage_bps', 5.0)

    def _get_time_in_force(self) -> str:
        return 'day'

    def _get_use_fractional(self) -> bool:
        if self._config is not None:
            return self._config.get_bool(
                'execution', 'use_fractional_shares', True,
            )
        return True

    def _get_config_float(self, cat: str, key: str, default: float) -> float:
        if self._config is not None:
            return self._config.get_float(cat, key, default)
        return default

    def _get_config_int(self, cat: str, key: str, default: int) -> int:
        if self._config is not None:
            return self._config.get_int(cat, key, default)
        return default

    def _notional_to_qty(self, notional: float, price: float) -> float:
        if price <= 0:
            return 0.0
        qty = notional / price
        if not self._get_use_fractional():
            qty = int(qty)
        return round(qty, 6)

    # ── publishing ─────────────────────────────────────────────────

    def _publish_fill(self, result: dict) -> None:
        if self._redis is None:
            return
        try:
            event = {
                'symbol': result['symbol'],
                'side': result['side'],
                'notional': result['notional'],
                'status': result['status'],
                'fill_price': result.get('fill_price'),
                'filled_qty': result.get('filled_qty'),
                'actual_slippage_bps': result.get('actual_slippage_bps'),
                'timestamp': result.get('timestamp'),
                'asset_class': result.get('asset_class', 'etf'),
            }
            payload = json.dumps(event, default=str)
            self._redis.publish('channel:fills', payload)
            self._redis.publish('channel:trades', payload)
        except Exception as exc:
            self._log.error('fill_publish_failed', error=str(exc))

    # ── result builder ─────────────────────────────────────────────

    @staticmethod
    def _result(order: dict, status: str, **kwargs) -> dict:
        result = {
            'symbol': order['symbol'],
            'side': order['side'],
            'notional': order['notional'],
            'status': status,
            'asset_class': order.get('asset_class', 'etf'),
            'account_id': order.get('account_id'),
        }
        result.update(kwargs)
        if 'timestamp' in result and hasattr(result['timestamp'], 'isoformat'):
            result['timestamp'] = result['timestamp'].isoformat()
        return result
