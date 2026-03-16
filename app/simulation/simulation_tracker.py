"""Simulation Tracker — paper-trade tracking with hypothetical positions.

Upgrades the old simulation mode (which just returned ``status: 'skipped'``)
into a full paper-trading service that:
- Runs orders through the TransactionCostModel for realistic fills.
- Tracks hypothetical positions and paper PnL over time.
- Builds an equity curve without touching the live broker.

Operates as an independent async service — not gated by the global
``operational_mode`` setting.  Can run alongside live trading.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import structlog

from app.execution.cost_model import TransactionCostModel
from app.execution.slippage_tracker import SlippageTracker

logger = structlog.get_logger()


@dataclass
class SimulatedPosition:
    """A hypothetical position tracked by the simulation."""

    symbol: str
    side: str                    # 'long' or 'short'
    entry_price: float
    quantity: float
    notional: float
    entry_time: str
    current_price: float | None = None
    unrealized_pnl: float = 0.0
    cost_bps: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def mark_to_market(self, price: float) -> float:
        """Update current price and compute unrealized PnL."""
        self.current_price = price
        if self.side == 'long':
            self.unrealized_pnl = (price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.quantity
        return self.unrealized_pnl


@dataclass
class SimulationSnapshot:
    """Point-in-time snapshot for equity curve."""

    timestamp: str
    portfolio_value: float
    cash: float
    positions_value: float
    unrealized_pnl: float
    realized_pnl: float
    n_positions: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimulationState:
    """Full state of a simulation session."""

    session_id: str
    initial_capital: float
    cash: float
    positions: dict[str, SimulatedPosition] = field(default_factory=dict)
    closed_trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    realized_pnl: float = 0.0
    started_at: str = ''

    def to_dict(self) -> dict:
        d = {
            'session_id': self.session_id,
            'initial_capital': self.initial_capital,
            'cash': self.cash,
            'positions': {k: v.to_dict() for k, v in self.positions.items()},
            'closed_trades': self.closed_trades,
            'equity_curve': self.equity_curve,
            'realized_pnl': self.realized_pnl,
            'started_at': self.started_at,
            'n_positions': len(self.positions),
            'portfolio_value': self.portfolio_value,
            'unrealized_pnl': self.unrealized_pnl,
        }
        return d

    @property
    def portfolio_value(self) -> float:
        positions_val = sum(
            (p.current_price or p.entry_price) * p.quantity
            for p in self.positions.values()
        )
        return self.cash + positions_val

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())


class SimulationTracker:
    """Paper-trade tracking service with cost-model fills.

    Unlike the pipeline's ``simulation`` mode (which returns ``skipped``),
    this service:
    - Simulates realistic fills via ``TransactionCostModel``.
    - Maintains hypothetical positions with mark-to-market PnL.
    - Tracks an equity curve over time.
    - Persists state in Redis so it survives restarts.

    Works independently of the global ``operational_mode`` — can run
    alongside live trading for A/B comparison.

    Tooltip:
        Paper-trade any strategy with realistic cost-model fills.
        Tracks hypothetical positions, PnL, and equity curve over time
        without placing real orders.  Run alongside live trading for
        strategy comparison.
    """

    SERVICE_NAME = 'simulation'
    TOOLTIP = (
        'Paper-trade any strategy with realistic cost-model fills. '
        'Tracks hypothetical positions, PnL, and equity curve over time '
        'without placing real orders.  Run alongside live trading for '
        'strategy comparison.'
    )

    # Default ADV for ETFs when not available
    DEFAULT_ADV_USD = 50_000_000

    def __init__(
        self,
        redis_client=None,
        config_loader=None,
    ):
        self._redis = redis_client
        self._config = config_loader
        self._slippage = SlippageTracker(config_loader)
        self._cost_model = TransactionCostModel(self._slippage, config_loader)
        self._log = logger.bind(component='simulation_tracker')

    # ── session management ────────────────────────────────────────

    def create_session(
        self, initial_capital: float = 100_000.0,
    ) -> SimulationState:
        """Create a new simulation session."""
        now = datetime.now(timezone.utc)
        session_id = f'sim_{now.strftime("%Y%m%d_%H%M%S")}'

        state = SimulationState(
            session_id=session_id,
            initial_capital=initial_capital,
            cash=initial_capital,
            started_at=now.isoformat(),
        )

        self._save_state(state)
        self._log.info('simulation_session_created', session_id=session_id)
        return state

    def load_session(self, session_id: str) -> SimulationState | None:
        """Load a simulation session from Redis."""
        if self._redis is None:
            return None

        raw = self._redis.get(f'sim:state:{session_id}')
        if raw is None:
            return None

        data = json.loads(raw)
        positions = {
            k: SimulatedPosition(**v) for k, v in data.get('positions', {}).items()
        }
        return SimulationState(
            session_id=data['session_id'],
            initial_capital=data['initial_capital'],
            cash=data['cash'],
            positions=positions,
            closed_trades=data.get('closed_trades', []),
            equity_curve=data.get('equity_curve', []),
            realized_pnl=data.get('realized_pnl', 0.0),
            started_at=data.get('started_at', ''),
        )

    def get_active_session(self) -> SimulationState | None:
        """Get the most recent simulation session."""
        if self._redis is None:
            return None
        session_id = self._redis.get('sim:active_session')
        if session_id:
            sid = session_id.decode() if isinstance(session_id, bytes) else session_id
            return self.load_session(sid)
        return None

    # ── order execution (simulated) ───────────────────────────────

    def execute_order(
        self,
        state: SimulationState,
        order: dict,
    ) -> dict:
        """Execute a simulated order against the paper portfolio.

        Uses TransactionCostModel for realistic fill pricing.

        Args:
            state: current simulation state.
            order: dict with symbol, side, notional, ref_price (optional).

        Returns:
            Execution result dict with fill details and cost breakdown.
        """
        symbol = order['symbol']
        side = order['side']
        notional = order['notional']
        now = datetime.now(timezone.utc)

        # Get mid price
        mid_price = self._get_mid_price(symbol, order)
        if mid_price is None or mid_price <= 0:
            return {
                'status': 'error',
                'reason': 'no_price',
                'symbol': symbol,
                'side': side,
            }

        # Check cash for buys
        if side == 'buy' and notional > state.cash:
            return {
                'status': 'error',
                'reason': 'insufficient_cash',
                'symbol': symbol,
                'available': state.cash,
                'required': notional,
            }

        # Run cost model
        daily_volume = self._get_daily_volume(symbol)
        volatility = self._get_volatility(symbol)
        spread_bps = self._get_spread_bps()
        exec_window = self._get_exec_window()

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

        # Update portfolio state
        if side == 'buy':
            actual_cost = breakdown.fill_price * breakdown.filled_qty
            state.cash -= actual_cost

            if symbol in state.positions:
                pos = state.positions[symbol]
                # Average up
                total_qty = pos.quantity + breakdown.filled_qty
                avg_price = (
                    (pos.entry_price * pos.quantity + breakdown.fill_price * breakdown.filled_qty)
                    / total_qty
                )
                pos.entry_price = avg_price
                pos.quantity = total_qty
                pos.notional = avg_price * total_qty
                pos.current_price = mid_price
            else:
                state.positions[symbol] = SimulatedPosition(
                    symbol=symbol,
                    side='long',
                    entry_price=breakdown.fill_price,
                    quantity=breakdown.filled_qty,
                    notional=notional,
                    entry_time=now.isoformat(),
                    current_price=mid_price,
                    cost_bps=breakdown.total_bps,
                )

        elif side == 'sell':
            if symbol in state.positions:
                pos = state.positions[symbol]
                sell_qty = min(breakdown.filled_qty, pos.quantity)
                realized = (breakdown.fill_price - pos.entry_price) * sell_qty
                state.realized_pnl += realized
                state.cash += breakdown.fill_price * sell_qty

                pos.quantity -= sell_qty
                if pos.quantity <= 0.0001:
                    # Close position
                    state.closed_trades.append({
                        'symbol': symbol,
                        'entry_price': pos.entry_price,
                        'exit_price': breakdown.fill_price,
                        'quantity': sell_qty,
                        'realized_pnl': round(realized, 2),
                        'cost_bps': breakdown.total_bps,
                        'closed_at': now.isoformat(),
                    })
                    del state.positions[symbol]
                else:
                    pos.notional = pos.entry_price * pos.quantity
            else:
                # Short sell (simplified — track as negative position)
                state.cash += breakdown.fill_price * breakdown.filled_qty
                state.positions[symbol] = SimulatedPosition(
                    symbol=symbol,
                    side='short',
                    entry_price=breakdown.fill_price,
                    quantity=breakdown.filled_qty,
                    notional=notional,
                    entry_time=now.isoformat(),
                    current_price=mid_price,
                    cost_bps=breakdown.total_bps,
                )

        # Record equity point
        snapshot = SimulationSnapshot(
            timestamp=now.isoformat(),
            portfolio_value=state.portfolio_value,
            cash=state.cash,
            positions_value=state.portfolio_value - state.cash,
            unrealized_pnl=state.unrealized_pnl,
            realized_pnl=state.realized_pnl,
            n_positions=len(state.positions),
        )
        state.equity_curve.append(snapshot.to_dict())

        # Persist
        self._save_state(state)

        result = {
            'status': 'filled',
            'reason': 'simulated',
            'operational_mode': 'simulation',
            'symbol': symbol,
            'side': side,
            'notional': notional,
            'fill_price': breakdown.fill_price,
            'filled_qty': breakdown.filled_qty,
            'mid_at_submission': mid_price,
            'cost_breakdown': {
                'half_spread_bps': breakdown.half_spread_bps,
                'market_impact_bps': breakdown.market_impact_bps,
                'total_bps': breakdown.total_bps,
            },
            'portfolio_value': state.portfolio_value,
            'cash_remaining': state.cash,
            'timestamp': now.isoformat(),
        }

        # Publish for dashboard
        self._publish_fill(result, state.session_id)

        return result

    def execute_batch(
        self,
        state: SimulationState,
        orders: list[dict],
    ) -> list[dict]:
        """Execute a batch of orders (sells first)."""
        # Sort sells first
        sorted_orders = sorted(
            orders,
            key=lambda o: (0 if o['side'] == 'sell' else 1),
        )
        return [self.execute_order(state, o) for o in sorted_orders]

    # ── mark-to-market ────────────────────────────────────────────

    def mark_to_market(self, state: SimulationState) -> SimulationState:
        """Update all positions with current prices."""
        for symbol, pos in state.positions.items():
            price = self._get_mid_price(symbol, {})
            if price and price > 0:
                pos.mark_to_market(price)

        # Record equity point
        now = datetime.now(timezone.utc)
        snapshot = SimulationSnapshot(
            timestamp=now.isoformat(),
            portfolio_value=state.portfolio_value,
            cash=state.cash,
            positions_value=state.portfolio_value - state.cash,
            unrealized_pnl=state.unrealized_pnl,
            realized_pnl=state.realized_pnl,
            n_positions=len(state.positions),
        )
        state.equity_curve.append(snapshot.to_dict())
        self._save_state(state)

        return state

    # ── persistence ───────────────────────────────────────────────

    def _save_state(self, state: SimulationState) -> None:
        """Persist simulation state to Redis."""
        if self._redis is None:
            return
        try:
            data = {
                'session_id': state.session_id,
                'initial_capital': state.initial_capital,
                'cash': state.cash,
                'positions': {k: v.to_dict() for k, v in state.positions.items()},
                'closed_trades': state.closed_trades,
                'equity_curve': state.equity_curve[-500:],  # keep last 500 points
                'realized_pnl': state.realized_pnl,
                'started_at': state.started_at,
            }
            self._redis.set(
                f'sim:state:{state.session_id}',
                json.dumps(data, default=str),
            )
            self._redis.set('sim:active_session', state.session_id)
        except Exception as exc:
            self._log.error('simulation_save_failed', error=str(exc))

    # ── price helpers ─────────────────────────────────────────────

    def _get_mid_price(self, symbol: str, order: dict) -> float | None:
        """Get mid price: Redis cache > order ref_price."""
        if self._redis is not None:
            val = self._redis.hget('cache:mid_prices', symbol)
            if val:
                try:
                    return float(val.decode() if isinstance(val, bytes) else val)
                except (ValueError, TypeError):
                    pass
        return order.get('ref_price')

    def _get_daily_volume(self, symbol: str) -> float:
        if self._redis is not None:
            val = self._redis.hget('cache:adv', symbol)
            if val:
                return float(val.decode() if isinstance(val, bytes) else val)
        return self.DEFAULT_ADV_USD

    def _get_volatility(self, symbol: str) -> float:
        if self._redis is not None:
            val = self._redis.hget('cache:volatility', symbol)
            if val:
                return float(val.decode() if isinstance(val, bytes) else val)
        return 0.20

    def _get_spread_bps(self) -> float:
        if self._config is not None:
            return self._config.get_float('universe', 'max_spread_bps', 1.0)
        return 1.0

    def _get_exec_window(self) -> int:
        if self._config is not None:
            return self._config.get_int('execution', 'execution_window_minutes', 30)
        return 30

    # ── publishing ────────────────────────────────────────────────

    def _publish_fill(self, result: dict, session_id: str) -> None:
        """Publish simulated fill event for dashboard."""
        if self._redis is None:
            return
        try:
            event = {
                'session_id': session_id,
                'symbol': result['symbol'],
                'side': result['side'],
                'status': result['status'],
                'fill_price': result.get('fill_price'),
                'filled_qty': result.get('filled_qty'),
                'portfolio_value': result.get('portfolio_value'),
                'timestamp': result.get('timestamp'),
            }
            self._redis.publish(
                'channel:simulation',
                json.dumps(event, default=str),
            )
        except Exception as exc:
            self._log.warning('simulation_publish_failed', error=str(exc))
