"""Pipeline orchestrator — stage helpers for the Celery task chain.

Each public method corresponds to one pipeline stage and returns a
serializable dict that the next stage in the chain receives as input.

The monolithic ``run()`` method is kept for testing and manual invocation
but delegates to the same stage methods used by the Celery chain.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import structlog

logger = structlog.get_logger()


class PipelineOrchestrator:
    """Stage-based pipeline that chains signals -> portfolio -> risk -> execution.

    Stages:
        1. load_data     — fetch signals, positions, prices, regime, drawdown
        2. portfolio      — run portfolio construction, produce orders
        3. risk_gate      — pre-trade risk filter
        4. execute        — submit approved orders to broker
        5. record         — publish results to Redis / dashboard
    """

    def __init__(self, redis_client, db_session, config_loader=None, broker=None):
        self._redis = redis_client
        self._db = db_session
        self._config = config_loader
        self._broker = broker
        self._log = logger.bind(component='pipeline_orchestrator')

    # ── stage 1 ──────────────────────────────────────────────────────

    def load_data(self) -> dict:
        """Stage 1: gather all inputs needed by downstream stages.

        Returns a fully-serializable dict (JSON-safe types only) so it
        can be passed through the Celery chain.
        """
        now = datetime.now(timezone.utc)
        self._log.info('stage_load_data_start')

        active_symbols = self._load_active_set()
        if not active_symbols:
            self._log.warning('pipeline_empty_active_set')
            return self._stopped(now, 'load_data', 'no_etfs_in_active_set')

        signals = self._load_signals()
        if not signals:
            self._log.info('pipeline_no_signals')
            return self._stopped(now, 'load_data', 'no_signals')

        # Filter to active set
        signals = {s: v for s, v in signals.items() if s in active_symbols}
        if not signals:
            return self._stopped(now, 'load_data', 'no_active_signals')

        regime = self._get_regime(signals)
        positions_weights, positions_list = self._load_positions()
        portfolio_value = self._get_portfolio_value(positions_list)

        symbols = list(signals.keys())
        prices_df = self._load_prices_df(symbols)
        if prices_df.empty:
            self._log.warning('pipeline_no_price_data')
            return self._stopped(now, 'load_data', 'no_price_data')

        sector_map = self._build_sector_map()
        current_drawdown = self._get_current_drawdown()

        # Serialize prices_df to JSON-safe format (list of {date: price} per symbol)
        prices_serialized = {
            col: {str(idx): float(val) for idx, val in prices_df[col].dropna().items()}
            for col in prices_df.columns
        }

        self._log.info(
            'stage_load_data_complete',
            n_signals=len(signals),
            n_positions=len(positions_list),
            regime=regime,
        )

        return {
            'status': 'continue',
            'stage': 'load_data',
            'timestamp': now.isoformat(),
            'signals': signals,
            'positions_weights': positions_weights,
            'positions_list': positions_list,
            'portfolio_value': portfolio_value,
            'prices_serialized': prices_serialized,
            'regime': regime,
            'sector_map': sector_map,
            'current_drawdown': current_drawdown,
        }

    # ── stage 2 ──────────────────────────────────────────────────────

    def portfolio(self, pipeline_data: dict) -> dict:
        """Stage 2: run portfolio construction, produce rebalance orders."""
        if pipeline_data.get('status') == 'stopped':
            return pipeline_data

        self._log.info('stage_portfolio_start')

        # Reconstruct prices DataFrame from serialized form
        prices_df = pd.DataFrame({
            sym: pd.Series({k: v for k, v in vals.items()})
            for sym, vals in pipeline_data['prices_serialized'].items()
        })

        from app.portfolio.portfolio_manager import PortfolioManager
        pm = PortfolioManager(self._redis, self._config)
        portfolio_result = pm.run(
            signals=pipeline_data['signals'],
            current_positions=pipeline_data['positions_weights'],
            portfolio_value=pipeline_data['portfolio_value'],
            prices_df=prices_df,
            regime=pipeline_data['regime'],
            sector_map=pipeline_data['sector_map'],
        )

        orders = portfolio_result.get('orders', [])
        if not orders:
            self._log.info('pipeline_no_orders', regime=pipeline_data['regime'])
            return self._stopped(
                datetime.now(timezone.utc), 'portfolio', 'no_orders',
                n_signals=len(pipeline_data['signals']),
                n_candidates=len(portfolio_result.get('candidates', [])),
                regime=pipeline_data['regime'],
            )

        self._log.info('stage_portfolio_complete', n_orders=len(orders))

        pipeline_data['orders'] = orders
        pipeline_data['n_candidates'] = len(portfolio_result.get('candidates', []))
        pipeline_data['stage'] = 'portfolio'
        return pipeline_data

    # ── stage 3 ──────────────────────────────────────────────────────

    def risk_gate(self, pipeline_data: dict) -> dict:
        """Stage 3: run pre-trade risk gate on proposed orders."""
        if pipeline_data.get('status') == 'stopped':
            return pipeline_data

        self._log.info('stage_risk_gate_start', n_orders=len(pipeline_data['orders']))

        from app.risk.risk_manager import RiskManager
        rm = RiskManager(self._redis, self._config)
        gate_result = rm.pre_trade(
            proposed_orders=pipeline_data['orders'],
            current_positions=pipeline_data['positions_list'],
            portfolio_value=pipeline_data['portfolio_value'],
            current_drawdown=pipeline_data['current_drawdown'],
            regime=pipeline_data['regime'],
            sector_map=pipeline_data['sector_map'],
        )

        approved = gate_result.get('approved', [])
        blocked = gate_result.get('blocked', [])

        if not approved:
            self._log.info(
                'pipeline_all_blocked',
                blocked=len(blocked),
                reasons=[o.get('block_reason') for o in blocked[:5]],
            )
            return self._stopped(
                datetime.now(timezone.utc), 'risk_gate', 'all_blocked',
                n_signals=len(pipeline_data['signals']),
                n_orders=len(pipeline_data['orders']),
                n_blocked=len(blocked),
                regime=pipeline_data['regime'],
            )

        self._log.info(
            'stage_risk_gate_complete',
            n_approved=len(approved),
            n_blocked=len(blocked),
        )

        pipeline_data['approved'] = approved
        pipeline_data['blocked'] = blocked
        pipeline_data['stage'] = 'risk_gate'
        return pipeline_data

    # ── stage 4 ──────────────────────────────────────────────────────

    def execute(self, pipeline_data: dict) -> dict:
        """Stage 4: execute approved orders via broker.

        This stage has NO retry — retrying risks double-fills.
        On failure the error callback sets kill_switch:rebalance.
        """
        if pipeline_data.get('status') == 'stopped':
            return pipeline_data

        approved = pipeline_data.get('approved', [])
        self._log.info('stage_execute_start', n_approved=len(approved))

        from app.execution.execution_engine import ExecutionEngine
        engine = ExecutionEngine(
            broker=self._broker,
            redis_client=self._redis,
            config_loader=self._config,
            db_session=self._db,
        )
        exec_results = engine.execute_orders(approved)

        n_filled = sum(1 for r in exec_results if r.get('status') == 'filled')
        n_errors = sum(1 for r in exec_results if r.get('status') == 'error')

        self._log.info(
            'stage_execute_complete',
            n_filled=n_filled,
            n_errors=n_errors,
        )

        pipeline_data['exec_results'] = exec_results
        pipeline_data['n_filled'] = n_filled
        pipeline_data['n_errors'] = n_errors
        pipeline_data['stage'] = 'execute'
        return pipeline_data

    # ── stage 5 ──────────────────────────────────────────────────────

    def record(self, pipeline_data: dict) -> dict:
        """Stage 5: publish pipeline summary to Redis + dashboard."""
        self._log.info('stage_record_start')

        summary = {
            'status': pipeline_data.get('status', 'complete'),
            'stage': pipeline_data.get('stage', 'record'),
            'n_signals': len(pipeline_data.get('signals', {})),
            'n_candidates': pipeline_data.get('n_candidates', 0),
            'n_orders': len(pipeline_data.get('orders', [])),
            'n_approved': len(pipeline_data.get('approved', [])),
            'n_blocked': len(pipeline_data.get('blocked', [])),
            'n_filled': pipeline_data.get('n_filled', 0),
            'n_errors': pipeline_data.get('n_errors', 0),
            'regime': pipeline_data.get('regime', 'unknown'),
            'portfolio_value': pipeline_data.get('portfolio_value', 0),
            'timestamp': pipeline_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
        }

        # Preserve reason for stopped pipelines
        if pipeline_data.get('reason'):
            summary['reason'] = pipeline_data['reason']

        # Override status for completed pipelines
        if summary['status'] != 'stopped':
            summary['status'] = 'complete'

        self._publish_summary(summary)
        self._log.info('pipeline_complete', **summary)
        return summary

    # ── monolithic run (kept for testing / manual use) ───────────────

    def run(self) -> dict:
        """Execute the full pipeline synchronously (delegates to stage methods).

        Useful for testing and manual ``flask shell`` invocation.
        """
        data = self.load_data()
        data = self.portfolio(data)
        data = self.risk_gate(data)
        data = self.execute(data)
        return self.record(data)

    # ── data loaders (private) ────────────────────────────────────────

    def _load_signals(self) -> dict[str, dict]:
        """Load latest signals from Redis cache or DB."""
        if self._redis:
            raw = self._redis.get('cache:signals:latest')
            if raw:
                try:
                    groups = json.loads(raw)
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
        from app.extensions import db as _db
        from sqlalchemy import text

        initial_capital = 100_000.0
        if self._redis:
            raw = self._redis.hget('config:portfolio', 'initial_capital')
            if raw:
                try:
                    initial_capital = float(raw)
                except (ValueError, TypeError):
                    pass

        realized_row = _db.session.execute(
            text("SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status = 'closed'")
        ).fetchone()
        realized = float(realized_row[0]) if realized_row else 0.0

        unrealized_row = _db.session.execute(
            text("SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions WHERE status = 'open'")
        ).fetchone()
        unrealized = float(unrealized_row[0]) if unrealized_row else 0.0

        return initial_capital + realized + unrealized

    def _get_regime(self, signals: dict[str, dict]) -> str:
        if self._redis:
            raw = self._redis.get('cache:regime:current')
            if raw:
                try:
                    data = json.loads(raw)
                    return data.get('regime', 'consolidation')
                except (json.JSONDecodeError, TypeError):
                    pass

        for sig in signals.values():
            regime = sig.get('regime')
            if regime and regime != 'unknown':
                return regime
        return 'consolidation'

    def _get_current_drawdown(self) -> float:
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
        from app.models.etf_universe import ETFUniverse

        etfs = ETFUniverse.query.filter_by(
            is_active=True, in_active_set=True,
        ).all()
        symbols = {e.symbol for e in etfs}
        self._log.info('active_set_loaded', count=len(symbols))
        return symbols

    def _build_sector_map(self) -> dict[str, str]:
        from app.models.etf_universe import ETFUniverse

        etfs = ETFUniverse.query.filter_by(is_active=True).all()
        return {e.symbol: e.sector for e in etfs if e.sector}

    # ── helpers ───────────────────────────────────────────────────────

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
    def _stopped(timestamp, stage: str, reason: str, **kwargs) -> dict:
        return {
            'status': 'stopped',
            'stage': stage,
            'reason': reason,
            'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
            **kwargs,
        }
