"""Pipeline orchestrator — chains signals → portfolio → risk → execution.

Runs as a single function call (sync via API or async via Celery).
Passes data directly between stages rather than through Redis queues.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import structlog

logger = structlog.get_logger()


class PipelineOrchestrator:
    """Chains the full trading pipeline end-to-end.

    Stages:
        1. Load signals (from cache or DB)
        2. Load current positions
        3. Build portfolio targets (PortfolioManager)
        4. Run pre-trade risk gate (RiskManager)
        5. Execute approved orders (ExecutionEngine)
        6. Record results
    """

    def __init__(self, redis_client, db_session, config_loader=None, broker=None):
        self._redis = redis_client
        self._db = db_session
        self._config = config_loader
        self._broker = broker
        self._log = logger.bind(component='pipeline_orchestrator')

    def run(self) -> dict:
        """Execute the full trading pipeline.

        Returns summary dict with counts and status at each stage.
        """
        now = datetime.now(timezone.utc)
        self._log.info('pipeline_start')

        # 1. Load signals (only for active-set ETFs)
        active_symbols = self._load_active_set()
        if not active_symbols:
            self._log.warning('pipeline_empty_active_set')
            return self._result(now, stage='active_set', reason='no_etfs_in_active_set')

        signals = self._load_signals()
        if not signals:
            self._log.info('pipeline_no_signals')
            return self._result(now, stage='signals', reason='no_signals')

        # Filter signals to active set only
        signals = {s: v for s, v in signals.items() if s in active_symbols}

        # 2. Determine regime from signals
        regime = self._get_regime(signals)

        # 3. Load current positions
        positions_weights, positions_list = self._load_positions()

        # 4. Load portfolio value
        portfolio_value = self._get_portfolio_value(positions_list)

        # 5. Load prices for optimization
        symbols = list(signals.keys())
        prices_df = self._load_prices_df(symbols)
        if prices_df.empty:
            self._log.warning('pipeline_no_price_data')
            return self._result(now, stage='prices', reason='no_price_data')

        # 6. Build sector map
        sector_map = self._build_sector_map()

        # 7. Get current drawdown
        current_drawdown = self._get_current_drawdown()

        # 8. Run portfolio construction
        from app.portfolio.portfolio_manager import PortfolioManager
        pm = PortfolioManager(self._redis, self._config)
        portfolio_result = pm.run(
            signals=signals,
            current_positions=positions_weights,
            portfolio_value=portfolio_value,
            prices_df=prices_df,
            regime=regime,
            sector_map=sector_map,
        )

        orders = portfolio_result.get('orders', [])
        if not orders:
            self._log.info('pipeline_no_orders', regime=regime)
            return self._result(
                now, stage='portfolio', reason='no_orders',
                n_signals=len(signals),
                n_candidates=len(portfolio_result.get('candidates', [])),
                regime=regime,
            )

        # 9. Run pre-trade risk gate
        from app.risk.risk_manager import RiskManager
        rm = RiskManager(self._redis, self._config)
        gate_result = rm.pre_trade(
            proposed_orders=orders,
            current_positions=positions_list,
            portfolio_value=portfolio_value,
            current_drawdown=current_drawdown,
            regime=regime,
            sector_map=sector_map,
        )

        approved = gate_result.get('approved', [])
        blocked = gate_result.get('blocked', [])

        if not approved:
            self._log.info(
                'pipeline_all_blocked',
                blocked=len(blocked),
                reasons=[o.get('block_reason') for o in blocked[:5]],
            )
            return self._result(
                now, stage='risk_gate', reason='all_blocked',
                n_signals=len(signals),
                n_orders=len(orders),
                n_blocked=len(blocked),
                regime=regime,
            )

        # 10. Execute approved orders
        from app.execution.execution_engine import ExecutionEngine
        engine = ExecutionEngine(
            broker=self._broker,
            redis_client=self._redis,
            config_loader=self._config,
            db_session=self._db,
        )
        exec_results = engine.execute_orders(approved)

        n_filled = sum(1 for r in exec_results if r['status'] == 'filled')
        n_skipped = sum(1 for r in exec_results if r['status'] == 'skipped')

        # 11. Publish pipeline summary
        summary = {
            'n_signals': len(signals),
            'n_candidates': len(portfolio_result.get('candidates', [])),
            'n_orders': len(orders),
            'n_approved': len(approved),
            'n_blocked': len(blocked),
            'n_filled': n_filled,
            'n_skipped': n_skipped,
            'regime': regime,
            'portfolio_value': portfolio_value,
            'timestamp': now.isoformat(),
        }
        self._publish_summary(summary)

        self._log.info('pipeline_complete', **summary)
        return summary

    # ── data loaders ─────────────────────────────────────────────

    def _load_signals(self) -> dict[str, dict]:
        """Load latest signals from Redis cache or DB."""
        # Try Redis cache first
        if self._redis:
            raw = self._redis.get('cache:signals:latest')
            if raw:
                try:
                    groups = json.loads(raw)
                    # Flatten grouped signals into {symbol: signal_dict}
                    signals = {}
                    for group_name, entries in groups.items():
                        for entry in entries:
                            sym = entry.get('symbol')
                            if sym:
                                signals[sym] = entry
                    if signals:
                        return signals
                except (json.JSONDecodeError, TypeError):
                    pass

        # Fall back to latest signals from DB
        from app.models.signals import Signal
        from sqlalchemy import func

        latest_time = self._db.query(func.max(Signal.signal_time)).scalar()
        if not latest_time:
            return {}

        rows = Signal.query.filter_by(signal_time=latest_time).all()
        return {
            row.symbol: {
                'symbol': row.symbol,
                'composite_score': float(row.composite_score or 0),
                'signal_type': row.signal_type or 'neutral',
                'regime': row.regime or 'unknown',
                'regime_confidence': float(row.regime_confidence or 0),
            }
            for row in rows
        }

    def _load_positions(self) -> tuple[dict[str, float], list[dict]]:
        """Load current open positions from DB.

        Returns:
            (weights_dict, positions_list)
        """
        from app.models.positions import Position

        positions = Position.query.filter_by(status='open').all()
        weights = {p.symbol: float(p.weight or 0) for p in positions}
        pos_list = [
            {
                'symbol': p.symbol,
                'weight': float(p.weight or 0),
                'notional': float(p.notional or 0),
                'sector': p.sector,
                'status': p.status,
            }
            for p in positions
        ]
        return weights, pos_list

    def _load_prices_df(self, symbols: list[str], days: int = 252) -> pd.DataFrame:
        """Build prices DataFrame from price_bars table."""
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
            return pd.DataFrame()
        return pd.DataFrame(frames).dropna(how='all')

    def _get_portfolio_value(self, positions: list[dict]) -> float:
        """Compute portfolio value from positions + initial capital.

        Aggregates PnL in SQL to avoid loading the full position history
        into memory — critical after months of trading with many closed positions.
        """
        from app.extensions import db as _db
        from sqlalchemy import func, text

        # Get initial capital from config
        initial_capital = 100_000.0
        if self._redis:
            raw = self._redis.hget('config:portfolio', 'initial_capital')
            if raw:
                try:
                    initial_capital = float(raw)
                except (ValueError, TypeError):
                    pass

        # Aggregate realized PnL for all closed positions in one SQL query
        realized_row = _db.session.execute(
            text("SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status = 'closed'")
        ).fetchone()
        realized = float(realized_row[0]) if realized_row else 0.0

        # Aggregate unrealized PnL for open positions in one SQL query
        unrealized_row = _db.session.execute(
            text("SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions WHERE status = 'open'")
        ).fetchone()
        unrealized = float(unrealized_row[0]) if unrealized_row else 0.0

        return initial_capital + realized + unrealized

    def _get_regime(self, signals: dict[str, dict]) -> str:
        """Extract regime from signals or Redis cache."""
        # Check Redis first
        if self._redis:
            raw = self._redis.get('cache:regime:current')
            if raw:
                try:
                    data = json.loads(raw)
                    return data.get('regime', 'consolidation')
                except (json.JSONDecodeError, TypeError):
                    pass

        # Fall back to first signal's regime
        for sig in signals.values():
            regime = sig.get('regime')
            if regime and regime != 'unknown':
                return regime
        return 'consolidation'

    def _get_current_drawdown(self) -> float:
        """Get latest drawdown from performance_metrics."""
        from app.models.performance import PerformanceMetric

        latest = (
            PerformanceMetric.query
            .order_by(PerformanceMetric.date.desc())
            .first()
        )
        if latest and latest.drawdown is not None:
            return float(latest.drawdown)
        return 0.0

    def _load_active_set(self) -> set[str]:
        """Load symbols with in_active_set=True (the trading subset)."""
        from app.models.etf_universe import ETFUniverse

        etfs = ETFUniverse.query.filter_by(
            is_active=True, in_active_set=True,
        ).all()
        symbols = {e.symbol for e in etfs}
        self._log.info('active_set_loaded', count=len(symbols))
        return symbols

    def _build_sector_map(self) -> dict[str, str]:
        """Load {symbol: sector} from full active universe (for constraints)."""
        from app.models.etf_universe import ETFUniverse

        etfs = ETFUniverse.query.filter_by(is_active=True).all()
        return {e.symbol: e.sector for e in etfs if e.sector}

    # ── helpers ───────────────────────────────────────────────────

    def _publish_summary(self, summary: dict) -> None:
        if self._redis:
            try:
                self._redis.set(
                    'cache:pipeline:latest',
                    json.dumps(summary),
                    ex=3600,
                )
                self._redis.publish('channel:portfolio', json.dumps(summary))
            except Exception as exc:
                self._log.error('pipeline_publish_failed', error=str(exc))

    @staticmethod
    def _result(timestamp, stage: str, reason: str, **kwargs) -> dict:
        return {
            'status': 'stopped',
            'stage': stage,
            'reason': reason,
            'timestamp': timestamp.isoformat(),
            **kwargs,
        }
