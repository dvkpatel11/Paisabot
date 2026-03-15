from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import jsonify, request

from app.api import api_bp
from app.extensions import db, redis_client


# ── health ─────────────────────────────────────────────────────────

@api_bp.route('/health', methods=['GET'])
def health():
    """System health check with component status."""
    components = {}
    try:
        redis_client.ping()
        components['redis'] = 'ok'
    except Exception:
        components['redis'] = 'error'

    try:
        db.session.execute(db.text('SELECT 1'))
        components['database'] = 'ok'
    except Exception:
        components['database'] = 'error'

    # Check kill switches
    kill_switches = {}
    for switch in ('trading', 'rebalance', 'all', 'force_liquidate'):
        val = redis_client.get(f'kill_switch:{switch}')
        kill_switches[switch] = val == b'1'

    overall = 'ok' if all(v == 'ok' for v in components.values()) else 'degraded'

    return jsonify({
        'status': overall,
        'components': components,
        'kill_switches': kill_switches,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })


# ── scores ─────────────────────────────────────────────────────────

@api_bp.route('/scores', methods=['GET'])
def get_scores():
    """Get latest composite scores for all ETFs.

    Query params:
        preview_weights: JSON dict of {factor: weight} — re-score without saving.
    """
    cached = redis_client.get('cache:scores:latest')
    if cached:
        scores = json.loads(cached)
    else:
        scores = _load_scores_from_db()

    preview = request.args.get('preview_weights')
    if preview:
        try:
            weights = json.loads(preview)
            scores = _apply_preview_weights(scores, weights)
        except (json.JSONDecodeError, ValueError):
            return jsonify({'error': 'invalid preview_weights'}), 400

    return jsonify(scores)


def _load_scores_from_db():
    from app.models.factor_scores import FactorScore
    from sqlalchemy import func

    subq = db.session.query(
        FactorScore.symbol,
        func.max(FactorScore.calc_time).label('latest'),
    ).group_by(FactorScore.symbol).subquery()

    rows = db.session.query(FactorScore).join(
        subq,
        (FactorScore.symbol == subq.c.symbol)
        & (FactorScore.calc_time == subq.c.latest),
    ).all()

    result = {}
    for r in rows:
        result[r.symbol] = {
            'trend': _to_float(r.trend_score),
            'volatility': _to_float(r.volatility_score),
            'sentiment': _to_float(r.sentiment_score),
            'dispersion': _to_float(r.dispersion_score),
            'correlation': _to_float(r.correlation_score),
            'breadth': _to_float(r.breadth_score),
            'liquidity': _to_float(r.liquidity_score),
            'slippage': _to_float(r.slippage_score),
            'calc_time': r.calc_time.isoformat() if r.calc_time else None,
        }
    return result


def _apply_preview_weights(scores: dict, weights: dict) -> dict:
    """Re-compute composite scores with alternative weights (no DB write)."""
    from app.signals.composite_scorer import CompositeScorer
    default_weights = CompositeScorer.DEFAULT_WEIGHTS

    # Merge preview into defaults
    merged = {**default_weights, **weights}
    total = sum(merged.values())
    if total <= 0:
        return scores

    # Normalize
    merged = {k: v / total for k, v in merged.items()}

    result = {}
    for sym, factors in scores.items():
        composite = sum(
            factors.get(f, 0) * merged.get(f, 0)
            for f in merged
        )
        result[sym] = {**factors, 'composite_score': round(composite, 4)}

    return result


# ── signals ────────────────────────────────────────────────────────

@api_bp.route('/signals', methods=['GET'])
def get_signals():
    """Get latest signals grouped by type."""
    cached = redis_client.get('cache:signals:latest')
    if cached:
        return jsonify(json.loads(cached))

    from app.models.signals import Signal
    from sqlalchemy import func

    subq = db.session.query(
        Signal.symbol,
        func.max(Signal.signal_time).label('latest'),
    ).group_by(Signal.symbol).subquery()

    rows = db.session.query(Signal).join(
        subq,
        (Signal.symbol == subq.c.symbol)
        & (Signal.signal_time == subq.c.latest),
    ).all()

    groups = {'long': [], 'neutral': [], 'avoid': []}
    for r in rows:
        entry = {
            'symbol': r.symbol,
            'composite_score': _to_float(r.composite_score),
            'signal_type': r.signal_type,
            'regime': r.regime,
            'regime_confidence': _to_float(r.regime_confidence),
        }
        bucket = groups.get(r.signal_type, groups['neutral'])
        bucket.append(entry)

    # Sort by composite score descending
    for bucket in groups.values():
        bucket.sort(key=lambda x: x['composite_score'], reverse=True)

    return jsonify(groups)


# ── regime ─────────────────────────────────────────────────────────

@api_bp.route('/regime', methods=['GET'])
def get_regime():
    """Get current market regime and recent history."""
    cached = redis_client.get('cache:regime:current')
    regime_data = json.loads(cached) if cached else {'regime': 'unknown', 'confidence': 0}

    # Recent regime history from signals table
    from app.models.signals import Signal
    history = db.session.query(
        Signal.signal_time, Signal.regime, Signal.regime_confidence,
    ).group_by(
        Signal.signal_time, Signal.regime, Signal.regime_confidence,
    ).order_by(
        Signal.signal_time.desc(),
    ).limit(30).all()

    regime_data['history'] = [
        {
            'time': h.signal_time.isoformat(),
            'regime': h.regime,
            'confidence': _to_float(h.regime_confidence),
        }
        for h in history
    ]

    return jsonify(regime_data)


# ── portfolio ──────────────────────────────────────────────────────

@api_bp.route('/portfolio', methods=['GET'])
def get_portfolio():
    """Get current portfolio state: positions, weights, PnL."""
    cached = redis_client.get('cache:portfolio:latest')
    portfolio = json.loads(cached) if cached else {}

    # Open positions from DB
    from app.models.positions import Position
    positions = Position.query.filter_by(status='open').all()

    pos_list = [
        {
            'symbol': p.symbol,
            'direction': p.direction,
            'entry_price': _to_float(p.entry_price),
            'current_price': _to_float(p.current_price),
            'quantity': _to_float(p.quantity),
            'weight': _to_float(p.weight),
            'unrealized_pnl': _to_float(p.unrealized_pnl),
            'sector': p.sector,
            'high_watermark': _to_float(p.high_watermark),
        }
        for p in positions
    ]

    portfolio['positions'] = pos_list
    return jsonify(portfolio)


# ── risk ───────────────────────────────────────────────────────────

@api_bp.route('/risk', methods=['GET'])
def get_risk():
    """Get current risk state."""
    cached = redis_client.get('cache:risk_state')
    if cached:
        risk_data = json.loads(cached)
    else:
        risk_data = {}

    # Add kill switch states
    kill_switches = {}
    for switch in ('trading', 'rebalance', 'all', 'force_liquidate'):
        val = redis_client.get(f'kill_switch:{switch}')
        kill_switches[switch] = val == b'1'

    risk_data['kill_switches'] = kill_switches

    # Latest performance metrics
    from app.models.performance import PerformanceMetric
    latest = PerformanceMetric.query.order_by(
        PerformanceMetric.date.desc(),
    ).first()

    if latest:
        risk_data['latest_metrics'] = {
            'date': latest.date.isoformat(),
            'drawdown': _to_float(latest.drawdown),
            'sharpe_30d': _to_float(latest.sharpe_30d),
            'volatility_30d': _to_float(latest.volatility_30d),
            'var_95': _to_float(latest.var_95),
        }

    return jsonify(risk_data)


# ── trades ─────────────────────────────────────────────────────────

@api_bp.route('/trades', methods=['GET'])
def get_trades():
    """Get recent trade execution log.

    Query params:
        limit: number of trades (default 50).
        symbol: filter by symbol.
    """
    from app.models.trades import Trade

    limit = request.args.get('limit', 50, type=int)
    limit = min(limit, 200)

    query = Trade.query.order_by(Trade.trade_time.desc())

    symbol = request.args.get('symbol')
    if symbol:
        query = query.filter_by(symbol=symbol.upper())

    trades = query.limit(limit).all()

    return jsonify([
        {
            'symbol': t.symbol,
            'side': t.side,
            'order_type': t.order_type,
            'requested_notional': _to_float(t.requested_notional),
            'filled_notional': _to_float(t.filled_notional),
            'fill_price': _to_float(t.fill_price),
            'slippage_bps': _to_float(t.slippage_bps),
            'status': t.status,
            'trade_time': t.trade_time.isoformat() if t.trade_time else None,
            'regime': t.regime,
        }
        for t in trades
    ])


# ── factors (per symbol) ──────────────────────────────────────────

@api_bp.route('/factors/<symbol>', methods=['GET'])
def get_factors(symbol: str):
    """Get historical factor scores for a single ETF.

    Query params:
        days: lookback in days (default 30, max 365).
    """
    from app.models.factor_scores import FactorScore

    days = request.args.get('days', 30, type=int)
    days = min(days, 365)

    rows = FactorScore.query.filter_by(
        symbol=symbol.upper(),
    ).order_by(
        FactorScore.calc_time.desc(),
    ).limit(days).all()

    rows.reverse()  # chronological

    factors = {
        'dates': [],
        'trend': [],
        'volatility': [],
        'sentiment': [],
        'dispersion': [],
        'correlation': [],
        'breadth': [],
        'liquidity': [],
        'slippage': [],
    }

    for r in rows:
        factors['dates'].append(r.calc_time.isoformat() if r.calc_time else None)
        factors['trend'].append(_to_float(r.trend_score))
        factors['volatility'].append(_to_float(r.volatility_score))
        factors['sentiment'].append(_to_float(r.sentiment_score))
        factors['dispersion'].append(_to_float(r.dispersion_score))
        factors['correlation'].append(_to_float(r.correlation_score))
        factors['breadth'].append(_to_float(r.breadth_score))
        factors['liquidity'].append(_to_float(r.liquidity_score))
        factors['slippage'].append(_to_float(r.slippage_score))

    return jsonify({'symbol': symbol.upper(), 'factors': factors})


# ── config ─────────────────────────────────────────────────────────

@api_bp.route('/config', methods=['GET'])
def get_all_config():
    """Get all config grouped by category."""
    from app.models.system_config import SystemConfig
    rows = SystemConfig.query.order_by(
        SystemConfig.category, SystemConfig.key,
    ).all()

    result = {}
    for r in rows:
        if r.category not in result:
            result[r.category] = {}
        result[r.category][r.key] = {
            'value': r.value,
            'type': r.value_type,
            'description': r.description,
            'updated_at': r.updated_at.isoformat() if r.updated_at else None,
            'updated_by': r.updated_by,
        }

    return jsonify(result)


@api_bp.route('/config/<category>', methods=['GET'])
def get_config_category(category: str):
    """Get config for a single category."""
    from app.models.system_config import SystemConfig
    rows = SystemConfig.query.filter_by(category=category).all()

    result = {}
    for r in rows:
        result[r.key] = {
            'value': r.value,
            'type': r.value_type,
            'description': r.description,
        }

    return jsonify(result)


@api_bp.route('/config/<category>', methods=['PATCH'])
def update_config(category: str):
    """Update config keys within a category.

    Body: {key: value, ...}
    Writes to PostgreSQL + syncs to Redis.
    """
    from app.models.system_config import SystemConfig

    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    updated_by = request.headers.get('X-Updated-By', 'api')
    changes = []

    for key, value in data.items():
        row = SystemConfig.query.filter_by(
            category=category, key=key,
        ).first()

        old_value = row.value if row else None

        if row:
            row.value = str(value)
            row.updated_by = updated_by
        else:
            row = SystemConfig(
                category=category,
                key=key,
                value=str(value),
                updated_by=updated_by,
            )
            db.session.add(row)

        # Sync to Redis
        redis_client.hset(f'config:{category}', key, str(value))

        changes.append({
            'category': category,
            'key': key,
            'old_value': old_value,
            'new_value': str(value),
            'updated_by': updated_by,
        })

    db.session.commit()

    # Broadcast config change via pub/sub
    for change in changes:
        change['timestamp'] = datetime.now(timezone.utc).isoformat()
        redis_client.publish('channel:config_change', json.dumps(change))

    return jsonify({'updated': len(changes), 'changes': changes})


@api_bp.route('/config/weights', methods=['PATCH'])
def update_weights():
    """Update factor weights with validation (must sum to 1.0)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    total = sum(float(v) for v in data.values())
    if abs(total - 1.0) > 0.01:
        return jsonify({
            'error': f'weights must sum to 1.0, got {total:.4f}',
        }), 400

    # Delegate to the generic config update
    from app.models.system_config import SystemConfig

    updated_by = request.headers.get('X-Updated-By', 'api')
    for key, value in data.items():
        row = SystemConfig.query.filter_by(
            category='weights', key=key,
        ).first()
        if row:
            row.value = str(value)
            row.updated_by = updated_by
        else:
            row = SystemConfig(
                category='weights', key=key,
                value=str(value), updated_by=updated_by,
            )
            db.session.add(row)

        redis_client.hset('config:weights', key, str(value))

    db.session.commit()
    return jsonify({'status': 'ok', 'weights': data})


@api_bp.route('/config/mode', methods=['PATCH'])
def update_mode():
    """Change operational mode with transition guards."""
    data = request.get_json()
    mode = data.get('mode') if data else None

    valid_modes = ('research', 'simulation', 'live')
    if mode not in valid_modes:
        return jsonify({'error': f'mode must be one of {valid_modes}'}), 400

    # Get current mode
    current = redis_client.hget('config:system', 'operational_mode')
    current = current.decode() if isinstance(current, bytes) else current

    # Guard: live→simulation sets kill switch
    if current == 'live' and mode == 'simulation':
        redis_client.set('kill_switch:rebalance', '1')

    # Guard: simulation→live requires explicit confirmation
    if current == 'simulation' and mode == 'live':
        confirmation = data.get('confirm')
        if confirmation != 'I_CONFIRM_LIVE_TRADING':
            return jsonify({
                'error': 'switching to live requires confirm="I_CONFIRM_LIVE_TRADING"',
            }), 400

    from app.models.system_config import SystemConfig
    row = SystemConfig.query.filter_by(
        category='system', key='operational_mode',
    ).first()
    if row:
        row.value = mode
    else:
        row = SystemConfig(
            category='system', key='operational_mode',
            value=mode, updated_by='api',
        )
        db.session.add(row)

    db.session.commit()
    redis_client.hset('config:system', 'operational_mode', mode)

    redis_client.publish('channel:config_change', json.dumps({
        'category': 'system',
        'key': 'operational_mode',
        'old_value': current,
        'new_value': mode,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }))

    return jsonify({'mode': mode, 'previous': current})


@api_bp.route('/config/audit', methods=['GET'])
def get_config_audit():
    """Get config change audit trail from system_config updated_at."""
    from app.models.system_config import SystemConfig

    limit = request.args.get('limit', 100, type=int)
    rows = SystemConfig.query.order_by(
        SystemConfig.updated_at.desc(),
    ).limit(limit).all()

    return jsonify([
        {
            'category': r.category,
            'key': r.key,
            'value': r.value,
            'updated_at': r.updated_at.isoformat() if r.updated_at else None,
            'updated_by': r.updated_by,
        }
        for r in rows
    ])


# ── control ────────────────────────────────────────────────────────

@api_bp.route('/control/<switch>', methods=['PATCH'])
def toggle_kill_switch(switch: str):
    """Toggle a kill switch on or off.

    Body: {"active": true|false}
    """
    valid_switches = ('trading', 'rebalance', 'all', 'force_liquidate', 'sentiment', 'maintenance')
    if switch not in valid_switches:
        return jsonify({'error': f'invalid switch, must be one of {valid_switches}'}), 400

    data = request.get_json()
    active = data.get('active', True) if data else True

    redis_client.set(f'kill_switch:{switch}', '1' if active else '0')

    redis_client.publish('channel:risk_alerts', json.dumps({
        'type': 'kill_switch',
        'switch': switch,
        'active': active,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }))

    return jsonify({'switch': switch, 'active': active})


@api_bp.route('/control/force_liquidate', methods=['POST'])
def force_liquidate():
    """Emergency liquidation of all positions.

    Body: {"confirm": "LIQUIDATE_ALL"}
    """
    data = request.get_json()
    confirmation = data.get('confirm') if data else None

    if confirmation != 'LIQUIDATE_ALL':
        return jsonify({
            'error': 'requires confirm="LIQUIDATE_ALL"',
        }), 400

    redis_client.set('kill_switch:force_liquidate', '1')
    redis_client.set('kill_switch:trading', '1')

    redis_client.publish('channel:risk_alerts', json.dumps({
        'type': 'force_liquidate',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }))

    return jsonify({'status': 'liquidation_triggered'})


# ── universe ───────────────────────────────────────────────────────

@api_bp.route('/universe', methods=['GET'])
def get_universe():
    """Get ETF universe with metadata."""
    from app.models.etf_universe import ETFUniverse

    etfs = ETFUniverse.query.filter_by(is_active=True).order_by(
        ETFUniverse.symbol,
    ).all()

    return jsonify([
        {
            'symbol': e.symbol,
            'name': e.name,
            'sector': e.sector,
            'aum_bn': _to_float(e.aum_bn),
            'avg_daily_vol_m': _to_float(e.avg_daily_vol_m),
            'spread_bps': _to_float(e.spread_est_bps),
            'liquidity_score': _to_float(e.liquidity_score),
            'options': e.options_market,
        }
        for e in etfs
    ])


# ── backtest ───────────────────────────────────────────────────────

@api_bp.route('/backtest/results', methods=['GET'])
def get_backtest_results():
    """Get performance metrics for tearsheet display."""
    from app.models.performance import PerformanceMetric

    rows = PerformanceMetric.query.order_by(
        PerformanceMetric.date,
    ).all()

    return jsonify({
        'dates': [r.date.isoformat() for r in rows],
        'portfolio_value': [_to_float(r.portfolio_value) for r in rows],
        'daily_return': [_to_float(r.daily_return) for r in rows],
        'cumulative_return': [_to_float(r.cumulative_return) for r in rows],
        'drawdown': [_to_float(r.drawdown) for r in rows],
        'sharpe_30d': [_to_float(r.sharpe_30d) for r in rows],
        'volatility_30d': [_to_float(r.volatility_30d) for r in rows],
    })


# ── pipelines ─────────────────────────────────────────────────────

@api_bp.route('/pipelines/status', methods=['GET'])
def get_pipeline_status():
    """Get real-time status of all 7 pipeline modules.

    Returns per-module: status (ok/degraded/error/idle), last_activity,
    items_processed, compute_time_ms, and queue depths for list-based channels.
    """
    now = datetime.now(timezone.utc)

    # Module definitions with their Redis cache keys and channels
    modules = [
        {
            'id': 'market_data',
            'name': 'Market Data Layer',
            'index': 1,
            'cache_key': 'cache:pipeline:market_data',
            'channel': 'channel:bars',
        },
        {
            'id': 'factor_engine',
            'name': 'Factor Engine',
            'index': 2,
            'cache_key': 'cache:pipeline:factor_engine',
            'channel': 'channel:factor_scores',
        },
        {
            'id': 'signal_engine',
            'name': 'Signal Engine',
            'index': 3,
            'cache_key': 'cache:pipeline:signal_engine',
            'channel': 'channel:signals',
        },
        {
            'id': 'portfolio_engine',
            'name': 'Portfolio Construction',
            'index': 4,
            'cache_key': 'cache:pipeline:portfolio_engine',
            'channel': 'channel:orders_proposed',
        },
        {
            'id': 'risk_engine',
            'name': 'Risk Engine',
            'index': 5,
            'cache_key': 'cache:pipeline:risk_engine',
            'channel': 'channel:orders_approved',
        },
        {
            'id': 'execution_engine',
            'name': 'Execution Engine',
            'index': 6,
            'cache_key': 'cache:pipeline:execution_engine',
            'channel': 'channel:trades',
        },
        {
            'id': 'dashboard',
            'name': 'Dashboard & Analytics',
            'index': 7,
            'cache_key': 'cache:pipeline:dashboard',
            'channel': None,
        },
    ]

    result = []
    for mod in modules:
        cached = redis_client.get(mod['cache_key'])
        if cached:
            info = json.loads(cached)
        else:
            info = {}

        # Queue depth for list-based channels
        queue_depth = None
        if mod['channel'] in ('channel:orders_proposed', 'channel:orders_approved'):
            try:
                queue_depth = redis_client.llen(mod['channel'])
            except Exception:
                queue_depth = 0

        # Determine status from cached info
        status = info.get('status', 'idle')
        last_activity = info.get('last_activity')

        # Auto-degrade if last activity too old (>10 min for fast, >60 min for slow)
        if last_activity:
            from datetime import timedelta
            try:
                last_dt = datetime.fromisoformat(last_activity)
                stale_threshold = timedelta(minutes=10) if mod['id'] in ('market_data',) else timedelta(minutes=60)
                if now - last_dt > stale_threshold:
                    status = 'stale'
            except (ValueError, TypeError):
                pass

        result.append({
            'id': mod['id'],
            'name': mod['name'],
            'index': mod['index'],
            'status': status,
            'last_activity': last_activity,
            'items_processed': info.get('items_processed', 0),
            'compute_time_ms': info.get('compute_time_ms'),
            'queue_depth': queue_depth,
            'extra': info.get('extra', {}),
        })

    # Kill switches
    kill_switches = {}
    for switch in ('trading', 'rebalance', 'all'):
        val = redis_client.get(f'kill_switch:{switch}')
        kill_switches[switch] = val == b'1'

    # Operational mode
    mode = redis_client.hget('config:system', 'operational_mode')
    if isinstance(mode, bytes):
        mode = mode.decode()

    return jsonify({
        'modules': result,
        'kill_switches': kill_switches,
        'operational_mode': mode or 'simulation',
        'timestamp': now.isoformat(),
    })


# ── helpers ────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    if val is None:
        return None
    return float(val)
