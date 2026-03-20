"""Celery tasks for continuous risk monitoring."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from celery_worker import celery

logger = structlog.get_logger()


def _is_market_hours() -> bool:
    """Check if current time is within US market hours (9:30-16:00 ET)."""
    now_et = datetime.now(ZoneInfo('America/New_York'))
    if now_et.weekday() >= 5:  # Weekend
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


@celery.task(
    name='app.risk.run_continuous_monitor',
    bind=True,
    max_retries=3,
    default_retry_delay=30,  # seconds between retries on transient errors
)
def run_continuous_monitor(self):
    """Run all continuous risk monitors.

    Scheduled every 5 minutes. Skips outside market hours.
    """
    if not _is_market_hours():
        return {'status': 'skipped', 'reason': 'market_closed'}

    from app import create_app
    app = create_app()
    with app.app_context():
        from app.extensions import db as _db, redis_client
        from app.utils.config_loader import ConfigLoader
        from app.risk.risk_manager import RiskManager
        from app.execution.position_tracker import PositionTracker
        from app.models.positions import Position
        from app.models.performance import PerformanceMetric
        from app.models.price_bars import PriceBar
        import pandas as pd
        from sqlalchemy import desc

        try:
            config = ConfigLoader(redis_client, _db.session)
            risk_mgr = RiskManager(redis_client, config)
            pos_tracker = PositionTracker(_db.session, redis_client)

            # 1. Load open positions
            positions = Position.query.filter_by(status='open').all()
            if not positions:
                return {'status': 'ok', 'reason': 'no_positions'}

            symbols = [p.symbol for p in positions]
            positions_list = [
                {
                    'symbol': p.symbol,
                    'direction': p.direction,
                    'quantity': float(p.quantity or 0),
                    'entry_price': float(p.entry_price or 0),
                    'current_price': float(p.current_price or 0),
                    'notional': float(p.notional or 0),
                    'weight': float(p.weight or 0),
                    'high_watermark': float(p.high_watermark or 0),
                    'sector': p.sector,
                    'status': p.status,
                }
                for p in positions
            ]

            # 2. Get current prices from Redis cache or latest bars
            current_prices = {}
            for sym in symbols:
                price = None
                if redis_client:
                    raw = redis_client.hget('cache:prices:latest', sym)
                    if raw:
                        try:
                            price = float(raw)
                        except (ValueError, TypeError):
                            pass
                if price is None:
                    # Fall back to latest bar close
                    bar = (
                        PriceBar.query
                        .filter_by(symbol=sym, timeframe='1d')
                        .order_by(desc(PriceBar.timestamp))
                        .first()
                    )
                    if bar:
                        price = float(bar.close)
                if price:
                    current_prices[sym] = price

            # 3. Mark to market
            initial_capital = 100_000.0
            raw_cap = redis_client.hget('config:portfolio', 'initial_capital') if redis_client else None
            if raw_cap:
                try:
                    initial_capital = float(raw_cap)
                except (ValueError, TypeError):
                    pass

            # Aggregate in SQL — avoid loading entire position history into memory
            from sqlalchemy import text as _text
            realized_row = _db.session.execute(
                _text("SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status = 'closed'")
            ).fetchone()
            realized = float(realized_row[0]) if realized_row else 0.0
            unrealized = sum(float(p.unrealized_pnl or 0) for p in positions)
            portfolio_value = initial_capital + realized + unrealized

            pos_tracker.mark_to_market(current_prices, portfolio_value)

            # 4. Build portfolio_values and returns Series from performance_metrics
            metrics = (
                PerformanceMetric.query
                .order_by(PerformanceMetric.date.asc())
                .all()
            )
            portfolio_values = pd.Series(
                {m.date: float(m.portfolio_value) for m in metrics if m.portfolio_value},
                dtype=float,
            )
            portfolio_returns = pd.Series(
                {m.date: float(m.daily_return) for m in metrics if m.daily_return is not None},
                dtype=float,
            )

            # Need at least a few data points for risk monitoring
            if len(portfolio_values) < 2:
                return {'status': 'ok', 'reason': 'insufficient_history'}

            # 5. Build prices_df for correlation check
            frames = {}
            for sym in symbols:
                bars = (
                    PriceBar.query
                    .filter_by(symbol=sym, timeframe='1d')
                    .order_by(desc(PriceBar.timestamp))
                    .limit(60)
                    .all()
                )
                if bars:
                    frames[sym] = pd.Series(
                        {b.timestamp: float(b.close) for b in bars},
                    ).sort_index()
            prices_df = pd.DataFrame(frames).dropna(how='all') if frames else pd.DataFrame()

            # 6. Get ADVs for liquidity check
            current_advs = {}
            for sym in symbols:
                if redis_client:
                    raw = redis_client.get(f'etf:{sym}:adv_30d_m')
                    if raw:
                        try:
                            current_advs[sym] = float(raw)
                        except (ValueError, TypeError):
                            pass

            # 7. Run continuous monitor
            results = risk_mgr.monitor(
                portfolio_values=portfolio_values,
                portfolio_returns=portfolio_returns,
                positions=positions_list,
                current_prices=current_prices,
                prices_df=prices_df,
                portfolio_value=portfolio_value,
                current_advs=current_advs if current_advs else None,
                db_session=_db.session,
            )

            overall = results.get('overall_status', 'ok')

            # 8. Act on halt status
            if overall == 'halt':
                redis_client.set('kill_switch:trading', '1')
                logger.critical('risk_halt_triggered', results=results)

            # 9. Process stop-loss exits (full liquidation)
            stop_exits = results.get('stop_loss', {}).get('exits', [])
            for exit_order in stop_exits:
                redis_client.lpush(
                    'channel:orders_approved',
                    json.dumps({
                        'symbol': exit_order['symbol'],
                        'side': 'sell',
                        'notional': float(exit_order.get('notional', 0)),
                        'reason': 'stop_loss',
                    }),
                )

            # 9b. Process soft-stop reductions (50% position trim)
            stop_reductions = results.get('stop_loss', {}).get('reductions', [])
            for reduction in stop_reductions:
                reduce_notional = float(reduction.get('notional', 0)) * 0.50
                if reduce_notional > 0:
                    redis_client.lpush(
                        'channel:orders_approved',
                        json.dumps({
                            'symbol': reduction['symbol'],
                            'side': 'sell',
                            'notional': reduce_notional,
                            'reason': 'soft_stop_reduction',
                        }),
                    )

            # 9c. Process vol-scaling orders
            vol_scaling = results.get('vol_scaling', {})
            if vol_scaling.get('action') not in ('no_change', None):
                scale = vol_scaling.get('scale_factor', 1.0)
                for pos in positions_list:
                    if pos.get('status') == 'open' and pos.get('notional', 0) > 0:
                        trim_notional = pos['notional'] * (1.0 - scale)
                        if trim_notional > 0:
                            redis_client.lpush(
                                'channel:orders_approved',
                                json.dumps({
                                    'symbol': pos['symbol'],
                                    'side': 'sell',
                                    'notional': round(trim_notional, 2),
                                    'reason': vol_scaling['action'],
                                }),
                            )

            # 10. Cache risk state for dashboard
            redis_client.set(
                'cache:risk_state',
                json.dumps({
                    'overall_status': overall,
                    'drawdown': results.get('drawdown', {}).get('status', 'ok'),
                    'stop_exits': len(stop_exits),
                    'timestamp': datetime.now(ZoneInfo('UTC')).isoformat(),
                }),
                ex=600,
            )

            logger.info(
                'continuous_monitor_complete',
                overall=overall,
                stop_exits=len(stop_exits),
            )

            return {
                'status': overall,
                'drawdown': results.get('drawdown', {}).get('status', 'ok'),
                'stop_exits': len(stop_exits),
            }

        except Exception as exc:
            logger.error('continuous_monitor_failed', error=str(exc))
            raise self.retry(exc=exc)
