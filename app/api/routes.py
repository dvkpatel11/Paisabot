from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import jsonify, request

from app.api import api_bp
from app.auth import api_login_required
from app.extensions import db, redis_client


def _get_asset_class() -> str:
    """Extract asset_class from query params, default 'etf'."""
    ac = request.args.get('asset_class', 'etf').lower()
    return ac if ac in ('etf', 'stock') else 'etf'


# ── health ─────────────────────────────────────────────────────────

@api_bp.route('/health', methods=['GET'])
def health():
    """System health check with component status."""
    components = {}
    details = {}

    # Redis
    try:
        redis_client.ping()
        components['redis'] = 'ok'
        try:
            info = redis_client.info('memory')
            used = info.get('used_memory_human', info.get(b'used_memory_human', '?'))
            if isinstance(used, bytes):
                used = used.decode()
            details['redis'] = f'Connected, {used} used'
        except Exception:
            details['redis'] = 'Connected'
    except Exception as exc:
        components['redis'] = 'error'
        details['redis'] = str(exc)

    # Database
    try:
        db.session.execute(db.text('SELECT 1'))
        components['database'] = 'ok'
        details['database'] = 'Connected'
    except Exception as exc:
        components['database'] = 'error'
        details['database'] = str(exc)

    # Check Alpaca — cached 30s to avoid a live network call on every page load
    import os
    alpaca_key = os.environ.get('ALPACA_API_KEY', '')
    if alpaca_key:
        cached_alpaca = redis_client.get('cache:health:alpaca')
        if cached_alpaca:
            components['alpaca'] = cached_alpaca.decode() if isinstance(cached_alpaca, bytes) else cached_alpaca
        else:
            try:
                from alpaca.trading.client import TradingClient
                client = TradingClient(
                    alpaca_key,
                    os.environ.get('ALPACA_SECRET_KEY', ''),
                    paper=os.environ.get('ALPACA_PAPER', 'true').lower() == 'true',
                )
                client.get_account()
                components['alpaca'] = 'ok'
                redis_client.setex('cache:health:alpaca', 30, 'ok')
            except Exception as exc:
                components['alpaca'] = 'error'
                details['alpaca'] = str(exc)
                redis_client.setex('cache:health:alpaca', 30, 'error')
    else:
        details['alpaca'] = 'ALPACA_API_KEY not set in environment'

    # Check kill switches
    kill_switches = {}
    try:
        for switch in ('trading', 'rebalance', 'all', 'force_liquidate'):
            val = redis_client.get(f'kill_switch:{switch}')
            kill_switches[switch] = val in (b'1', '1')
    except Exception:
        pass

    overall = 'ok' if all(v == 'ok' for v in components.values()) else 'degraded'

    try:
        mode = redis_client.hget('config:system', 'operational_mode') or 'simulation'
        if isinstance(mode, bytes):
            mode = mode.decode()
    except Exception:
        mode = 'simulation'

    result = {
        'status': overall,
        'components': components,
        'details': details,
        'kill_switches': kill_switches,
        'operational_mode': mode,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    # Never expose the broker account number on an unauthenticated endpoint.
    # alpaca_account is intentionally omitted here.

    return jsonify(result)


# ── broker account ──────────────────────────────────────────────────

@api_bp.route('/broker/account', methods=['GET'])
@api_login_required
def broker_account():
    """Live Alpaca account info — cached 30 s in Redis."""
    cached = redis_client.get('cache:broker:account')
    if cached:
        return jsonify(json.loads(cached))

    import os
    key = os.environ.get('ALPACA_API_KEY', '')
    if not key:
        return jsonify({'error': 'ALPACA_API_KEY not set'}), 503
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            key,
            os.environ.get('ALPACA_SECRET_KEY', ''),
            paper=os.environ.get('ALPACA_PAPER', 'true').lower() == 'true',
        )
        acct = client.get_account()
        data = {
            'cash':               float(acct.cash),
            'buying_power':       float(acct.buying_power),
            'portfolio_value':    float(acct.portfolio_value),
            'equity':             float(acct.equity),
            'day_trade_count':    int(acct.daytrade_count),
            'pattern_day_trader': acct.pattern_day_trader,
            'trading_blocked':    acct.trading_blocked,
            'account_blocked':    acct.account_blocked,
        }
        redis_client.setex('cache:broker:account', 30, json.dumps(data))
        return jsonify(data)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 503


# ── broker orders ────────────────────────────────────────────────────

@api_bp.route('/broker/orders', methods=['GET'])
@api_login_required
def broker_orders():
    """Open orders from Alpaca — not cached (must be real-time)."""
    import os
    key = os.environ.get('ALPACA_API_KEY', '')
    if not key:
        return jsonify([])
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        client = TradingClient(
            key,
            os.environ.get('ALPACA_SECRET_KEY', ''),
            paper=os.environ.get('ALPACA_PAPER', 'true').lower() == 'true',
        )
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=50))
        return jsonify([{
            'id':           str(o.id),
            'symbol':       o.symbol,
            'side':         o.side.value,
            'type':         o.type.value,
            'qty':          float(o.qty or 0),
            'notional':     float(o.notional) if o.notional else None,
            'limit_price':  float(o.limit_price) if o.limit_price else None,
            'status':       o.status.value,
            'submitted_at': o.submitted_at.isoformat() if o.submitted_at else None,
        } for o in orders])
    except Exception as exc:
        return jsonify({'error': str(exc)}), 503


@api_bp.route('/broker/orders/<order_id>', methods=['DELETE'])
@api_login_required
def cancel_broker_order(order_id):
    """Cancel an open Alpaca order by ID."""
    import os, uuid as _uuid
    key = os.environ.get('ALPACA_API_KEY', '')
    if not key:
        return jsonify({'error': 'ALPACA_API_KEY not set'}), 503
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            key,
            os.environ.get('ALPACA_SECRET_KEY', ''),
            paper=os.environ.get('ALPACA_PAPER', 'true').lower() == 'true',
        )
        client.cancel_order_by_id(_uuid.UUID(order_id))
        return jsonify({'status': 'cancelled'})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


# ── position stop management ─────────────────────────────────────────

@api_bp.route('/positions/<symbol>/stops', methods=['PATCH'])
@api_login_required
def update_position_stops(symbol):
    """Set or clear stop_price / take_profit_price on an open position."""
    from app.models.positions import Position
    body = request.get_json(force=True) or {}
    pos = Position.query.filter_by(symbol=symbol.upper(), status='open').first()
    if not pos:
        return jsonify({'error': f'No open position for {symbol.upper()}'}), 404
    if 'stop_price' in body:
        pos.stop_price = body['stop_price'] or None
    if 'take_profit_price' in body:
        pos.take_profit_price = body['take_profit_price'] or None
    db.session.commit()
    return jsonify({
        'status':            'updated',
        'symbol':            symbol.upper(),
        'stop_price':        float(pos.stop_price) if pos.stop_price else None,
        'take_profit_price': float(pos.take_profit_price) if pos.take_profit_price else None,
    })


# ── scores ─────────────────────────────────────────────────────────

@api_bp.route('/scores', methods=['GET'])
@api_login_required
def get_scores():
    """Get latest composite scores.

    Query params:
        asset_class: 'etf' (default) or 'stock'.
        preview_weights: JSON dict of {factor: weight} — re-score without saving.
    """
    asset_class = _get_asset_class()
    cache_key = f'cache:scores:{asset_class}:latest' if asset_class == 'stock' else 'cache:scores:latest'
    cached = redis_client.get(cache_key)
    if cached:
        scores = json.loads(cached)
    else:
        scores = _load_scores_from_db(asset_class)

    preview = request.args.get('preview_weights')
    if preview:
        try:
            weights = json.loads(preview)
            scores = _apply_preview_weights(scores, weights)
        except (json.JSONDecodeError, ValueError):
            return jsonify({'error': 'invalid preview_weights'}), 400

    return jsonify(scores)


def _load_scores_from_db(asset_class: str = 'etf'):
    from app.models.factor_scores import FactorScore
    from sqlalchemy import func

    subq = db.session.query(
        FactorScore.symbol,
        func.max(FactorScore.calc_time).label('latest'),
    ).filter(
        FactorScore.asset_class == asset_class,
    ).group_by(FactorScore.symbol).subquery()

    rows = db.session.query(FactorScore).join(
        subq,
        (FactorScore.symbol == subq.c.symbol)
        & (FactorScore.calc_time == subq.c.latest),
    ).all()

    result = {}
    for r in rows:
        entry = {
            'trend': _to_float(r.trend_score),
            'volatility': _to_float(r.volatility_score),
            'sentiment': _to_float(r.sentiment_score),
            'liquidity': _to_float(r.liquidity_score),
            'calc_time': r.calc_time.isoformat() if r.calc_time else None,
        }
        if asset_class == 'stock':
            entry['fundamentals'] = _to_float(r.fundamentals_score)
            entry['earnings'] = _to_float(r.earnings_score)
        else:
            entry['dispersion'] = _to_float(r.dispersion_score)
            entry['correlation'] = _to_float(r.correlation_score)
            entry['breadth'] = _to_float(r.breadth_score)
            entry['slippage'] = _to_float(r.slippage_score)
        result[r.symbol] = entry
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
@api_login_required
def get_signals():
    """Get latest signals grouped by type.

    Query params:
        asset_class: 'etf' (default) or 'stock'.
    """
    asset_class = _get_asset_class()
    cache_key = f'cache:signals:{asset_class}:latest'
    cached = redis_client.get(cache_key)
    if cached:
        return jsonify(json.loads(cached))

    from app.models.signals import Signal
    from sqlalchemy import func

    subq = db.session.query(
        Signal.symbol,
        func.max(Signal.signal_time).label('latest'),
    ).filter(
        Signal.asset_class == asset_class,
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

    # Recent regime history from signals table — 90-day window to avoid full-table scan
    from app.models.signals import Signal
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    history = db.session.query(
        Signal.signal_time, Signal.regime, Signal.regime_confidence,
    ).filter(
        Signal.signal_time >= cutoff,
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
@api_login_required
def get_portfolio():
    """Get current portfolio state: positions, weights, PnL.

    Query params:
        asset_class: 'etf' (default) or 'stock'.
    """
    asset_class = _get_asset_class()
    cache_key = f'cache:portfolio:{asset_class}:latest'
    cached = redis_client.get(cache_key)
    portfolio = json.loads(cached) if cached else {}

    # Open positions from DB
    from app.models.positions import Position
    positions = Position.query.filter_by(
        status='open', asset_class=asset_class,
    ).all()

    pos_list = [
        {
            'symbol': p.symbol,
            'direction': p.direction,
            'entry_price':      _to_float(p.entry_price),
            'current_price':    _to_float(p.current_price),
            'quantity':         _to_float(p.quantity),
            'weight':           _to_float(p.weight),
            'unrealized_pnl':   _to_float(p.unrealized_pnl),
            'sector':           p.sector,
            'high_watermark':   _to_float(p.high_watermark),
            'stop_price':       _to_float(p.stop_price),
            'take_profit_price': _to_float(p.take_profit_price),
        }
        for p in positions
    ]

    portfolio['positions'] = pos_list
    return jsonify(portfolio)


# ── risk ───────────────────────────────────────────────────────────

@api_bp.route('/risk', methods=['GET'])
@api_login_required
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
        kill_switches[switch] = val == '1'

    risk_data['kill_switches'] = kill_switches

    # Latest performance metrics
    asset_class = _get_asset_class()
    from app.models.performance import PerformanceMetric
    latest = PerformanceMetric.query.filter_by(
        asset_class=asset_class,
    ).order_by(
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
@api_login_required
def get_trades():
    """Get recent trade execution log.

    Query params:
        limit: number of trades (default 50).
        symbol: filter by symbol.
        asset_class: 'etf' (default) or 'stock'.
    """
    from app.models.trades import Trade

    asset_class = _get_asset_class()
    limit = request.args.get('limit', 50, type=int)
    limit = min(limit, 200)

    query = Trade.query.filter_by(
        asset_class=asset_class,
    ).order_by(Trade.trade_time.desc())

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


@api_bp.route('/trades/manual', methods=['POST'])
@api_login_required
def submit_manual_trade():
    """Submit a manual trade entry.

    Body: {
        "symbol": "SPY",
        "side": "buy",
        "order_type": "market",
        "notional": 5000,
        "broker": "alpaca",
        "limit_price": 450.50  (optional, for limit orders)
    }
    """
    from app.models.trades import Trade
    from app.models.etf_universe import ETFUniverse

    data = request.get_json(silent=True) or {}

    # Validate required fields
    symbol = (data.get('symbol') or '').upper()
    side = data.get('side', '').lower()
    order_type = data.get('order_type', 'market').lower()
    notional = data.get('notional')
    broker = data.get('broker', 'alpaca').lower()

    if not symbol:
        return jsonify({'error': 'symbol is required'}), 400
    if side not in ('buy', 'sell'):
        return jsonify({'error': 'side must be buy or sell'}), 400
    if not notional or float(notional) <= 0:
        return jsonify({'error': 'notional must be positive'}), 400

    # Verify symbol is in universe
    etf = ETFUniverse.query.filter_by(symbol=symbol, is_active=True).first()
    if not etf:
        return jsonify({'error': f'{symbol} not found in active universe'}), 400

    # Check kill switches — fail-safe: block trading if Redis is unreachable.
    # Use strict == '1' so only an explicit '1' value permits trading.
    try:
        kill_rebalance = redis_client.get('kill_switch:rebalance')
    except Exception:
        return jsonify({'error': 'Kill switch status unavailable — trading disabled'}), 503
    if kill_rebalance == '1':
        return jsonify({'error': 'Rebalance kill switch is active — trading disabled'}), 403

    # Run pre-trade risk gate — same checks as algorithmic orders.
    # Manual submissions must not bypass position/sector/drawdown limits.
    try:
        from app.models.positions import Position as _Position
        from app.risk.pre_trade_gate import PreTradeGate

        open_positions = _Position.query.filter_by(status='open').all()
        positions_list = [
            {
                'symbol': p.symbol,
                'weight': float(p.weight or 0),
                'sector': p.sector or 'Unknown',
                'status': p.status,
            }
            for p in open_positions
        ]

        raw_cap = redis_client.hget('config:portfolio', 'initial_capital')
        initial_capital = float(raw_cap) if raw_cap else 100_000.0
        from sqlalchemy import func as _func
        realized = db.session.query(
            _func.coalesce(_func.sum(_Position.realized_pnl), 0)
        ).filter_by(status='closed').scalar() or 0.0
        unrealized = sum(float(p.unrealized_pnl or 0) for p in open_positions)
        portfolio_value = initial_capital + realized + unrealized

        gate = PreTradeGate(redis_client)
        gate_result = gate.evaluate(
            proposed_orders=[{'symbol': symbol, 'side': side, 'notional': float(notional)}],
            current_positions=positions_list,
            portfolio_value=portfolio_value,
        )

        if gate_result.get('blocked'):
            block_reason = gate_result['blocked'][0].get('block_reason', 'risk_gate_blocked')
            return jsonify({'error': f'Risk gate blocked trade: {block_reason}'}), 403
    except Exception as exc:
        # Gate failure is fail-safe: block the trade rather than allow it through
        return jsonify({'error': f'Risk gate check failed: {exc}'}), 503

    # Get operational mode
    mode = redis_client.hget('config:system', 'operational_mode') or 'simulation'

    trade = Trade(
        symbol=symbol,
        broker=broker,
        side=side,
        order_type=order_type,
        requested_notional=float(notional),
        status='pending',
        operational_mode=mode,
        trade_time=datetime.now(timezone.utc),
    )
    db.session.add(trade)
    db.session.commit()

    # Publish to channel for execution engine pickup
    redis_client.publish('channel:trades', json.dumps({
        'trade_id': trade.id,
        'symbol': symbol,
        'side': side,
        'order_type': order_type,
        'notional': float(notional),
        'broker': broker,
        'source': 'manual',
    }))

    return jsonify({
        'trade_id': trade.id,
        'symbol': symbol,
        'side': side,
        'status': 'pending',
        'mode': mode,
    }), 201


# ── factors (per symbol) ──────────────────────────────────────────

@api_bp.route('/factors/<symbol>', methods=['GET'])
@api_login_required
def get_factors(symbol: str):
    """Get historical factor scores for a single symbol.

    Query params:
        days: lookback in days (default 30, max 365).
        asset_class: 'etf' (default) or 'stock'.
    """
    from app.models.factor_scores import FactorScore

    asset_class = _get_asset_class()
    days = request.args.get('days', 30, type=int)
    days = min(days, 365)

    rows = FactorScore.query.filter_by(
        symbol=symbol.upper(),
        asset_class=asset_class,
    ).order_by(
        FactorScore.calc_time.desc(),
    ).limit(days).all()

    rows.reverse()  # chronological

    if asset_class == 'stock':
        factors = {
            'dates': [],
            'trend': [],
            'volatility': [],
            'sentiment': [],
            'liquidity': [],
            'fundamentals': [],
            'earnings': [],
        }
        for r in rows:
            factors['dates'].append(r.calc_time.isoformat() if r.calc_time else None)
            factors['trend'].append(_to_float(r.trend_score))
            factors['volatility'].append(_to_float(r.volatility_score))
            factors['sentiment'].append(_to_float(r.sentiment_score))
            factors['liquidity'].append(_to_float(r.liquidity_score))
            factors['fundamentals'].append(_to_float(r.fundamentals_score))
            factors['earnings'].append(_to_float(r.earnings_score))
    else:
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

    return jsonify({'symbol': symbol.upper(), 'asset_class': asset_class, 'factors': factors})


# ── config ─────────────────────────────────────────────────────────

@api_bp.route('/config', methods=['GET'])
def get_all_config():
    """Get all config grouped by category."""
    from app.models.system_config import SystemConfig
    rows = SystemConfig.query.order_by(
        SystemConfig.category, SystemConfig.key,
    ).all()

    from app.utils.encryption import mask_secret

    result = {}
    for r in rows:
        if r.category not in result:
            result[r.category] = {}
        display_value = mask_secret(r.value) if r.is_secret else r.value
        result[r.category][r.key] = {
            'value': display_value,
            'is_secret': r.is_secret,
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
    from app.utils.encryption import mask_secret

    rows = SystemConfig.query.filter_by(category=category).all()

    result = {}
    for r in rows:
        display_value = mask_secret(r.value) if r.is_secret else r.value
        result[r.key] = {
            'value': display_value,
            'is_secret': r.is_secret,
            'type': r.value_type,
            'description': r.description,
        }

    return jsonify(result)


@api_bp.route('/config/<category>', methods=['PATCH'])
@api_login_required
def update_config(category: str):
    """Update config keys within a category.

    Body: {key: value, ...}
    Writes to PostgreSQL + syncs to Redis.
    Secrets (api_key, secret) are Fernet-encrypted before storage.
    """
    import os
    from app.models.system_config import SystemConfig
    from app.utils.encryption import encrypt_value, mask_secret

    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    fernet_key = os.environ.get('FERNET_KEY', '')
    updated_by = request.headers.get('X-Updated-By', 'api')
    changes = []

    # Keys that should be encrypted at rest
    SECRET_PATTERNS = ('api_key', 'secret', 'password', 'token')

    for key, value in data.items():
        row = SystemConfig.query.filter_by(
            category=category, key=key,
        ).first()

        old_value = row.value if row else None
        is_secret = any(pat in key.lower() for pat in SECRET_PATTERNS)
        store_value = str(value)

        # Encrypt secrets before storing in DB
        if is_secret and fernet_key and store_value:
            store_value = encrypt_value(store_value, fernet_key)

        if row:
            row.value = store_value
            row.is_secret = is_secret
            row.updated_by = updated_by
        else:
            row = SystemConfig(
                category=category,
                key=key,
                value=store_value,
                is_secret=is_secret,
                updated_by=updated_by,
            )
            db.session.add(row)

        # Sync non-secret config to Redis for fast reads.
        # Secrets stay in PostgreSQL only (encrypted); never written to Redis
        # to prevent exposure via Redis dumps, logs, or SUBSCRIBE sniffing.
        if not is_secret:
            redis_client.hset(f'config:{category}', key, str(value))

        # Mask secrets in the response
        display_old = mask_secret(old_value) if is_secret and old_value else old_value
        display_new = mask_secret(str(value)) if is_secret else str(value)

        changes.append({
            'category': category,
            'key': key,
            'old_value': display_old,
            'new_value': display_new,
            'updated_by': updated_by,
        })

    db.session.commit()

    # Broadcast config change via pub/sub
    for change in changes:
        change['timestamp'] = datetime.now(timezone.utc).isoformat()
        redis_client.publish('channel:config_change', json.dumps(change))

    return jsonify({'updated': len(changes), 'changes': changes})


@api_bp.route('/config/weights', methods=['PATCH'])
@api_login_required
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
@api_login_required
def update_mode():
    """Change operational mode with transition guards."""
    data = request.get_json()
    mode = data.get('mode') if data else None

    valid_modes = ('research', 'simulation', 'live')
    if mode not in valid_modes:
        return jsonify({'error': f'mode must be one of {valid_modes}'}), 400

    # Get current mode
    current = redis_client.hget('config:system', 'operational_mode')
    current = current or 'simulation'

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
@api_login_required
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
@api_login_required
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
@api_login_required
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

def _serialize_etf(e, current_price: float | None = None, prev_close: float | None = None) -> dict:
    """Serialize an ETFUniverse row with all tracking columns.

    Args:
        current_price: Latest mid price from Redis cache (None if unavailable).
        prev_close:    Previous trading day's close from price_bars (None if unavailable).
    """
    return {
        'symbol': e.symbol,
        'name': e.name,
        'sector': e.sector,
        'aum_bn': _to_float(e.aum_bn),
        'avg_daily_vol_m': _to_float(e.avg_daily_vol_m),
        'spread_bps': _to_float(e.spread_est_bps),
        'liquidity_score': _to_float(e.liquidity_score),
        'options': e.options_market,
        'in_active_set': bool(e.in_active_set),
        'active_set_reason': e.active_set_reason,
        'notes': e.notes,
        'your_rating': e.your_rating,
        'tags': e.tags,
        'last_signal_type': e.last_signal_type,
        'last_composite_score': _to_float(e.last_composite_score),
        'last_signal_at': e.last_signal_at.isoformat() if e.last_signal_at else None,
        'perf_1w': _to_float(e.perf_1w),
        'perf_1m': _to_float(e.perf_1m),
        'perf_3m': _to_float(e.perf_3m),
        'correlation_to_spy': _to_float(e.correlation_to_spy),
        # Live price fields — populated when Redis cache is warm
        'current_price': current_price,
        'prev_close': prev_close,
    }


@api_bp.route('/universe', methods=['GET'])
def get_universe():
    """Get full ETF watchlist with tracking data.

    Query params:
        active_set=true  — filter to active trading set only

    Includes current_price (from Redis cache:mid_prices) and prev_close
    (from price_bars) so the frontend can display live prices and intraday
    change % without an extra round-trip.
    """
    from app.models.etf_universe import ETFUniverse
    from sqlalchemy import text as _text

    query = ETFUniverse.query.filter_by(is_active=True)

    if request.args.get('active_set', '').lower() == 'true':
        query = query.filter_by(in_active_set=True)

    etfs = query.order_by(ETFUniverse.symbol).all()
    symbols = [e.symbol for e in etfs]

    # Batch-fetch current prices from Redis cache (written by data layer on each bar)
    current_prices: dict[str, float] = {}
    try:
        raw = redis_client.hmget('cache:mid_prices', symbols)
        for sym, val in zip(symbols, raw):
            if val is not None:
                try:
                    current_prices[sym] = float(val)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass  # price display degrades gracefully to '--'

    # Batch-fetch previous trading day's close in a single DB query
    prev_closes: dict[str, float] = {}
    try:
        rows = db.session.execute(_text("""
            SELECT DISTINCT ON (symbol) symbol, close
            FROM price_bars
            WHERE timestamp::date < current_date
            ORDER BY symbol, timestamp DESC
        """)).fetchall()
        prev_closes = {row[0]: float(row[1]) for row in rows}
    except Exception:
        pass  # change % degrades gracefully to '--'

    return jsonify([
        _serialize_etf(
            e,
            current_price=current_prices.get(e.symbol),
            prev_close=prev_closes.get(e.symbol),
        )
        for e in etfs
    ])


@api_bp.route('/universe/<symbol>/active-set', methods=['PATCH'])
@api_login_required
def toggle_active_set(symbol: str):
    """Add or remove an ETF from the active trading set.

    Body: {"in_active_set": true/false, "reason": "optional reason"}

    On activation: dispatches backfill, syncs Redis active-set cache,
    adds to WebSocket subscription, publishes config_change event.
    """
    from app.models.etf_universe import ETFUniverse

    etf = ETFUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not etf:
        return jsonify({'error': f'{symbol} not found in universe'}), 404

    data = request.get_json()
    if data is None or 'in_active_set' not in data:
        return jsonify({'error': 'in_active_set required'}), 400

    activate = bool(data['in_active_set'])
    etf.in_active_set = activate
    etf.active_set_reason = data.get('reason', '')
    etf.active_set_changed_at = datetime.now(timezone.utc)
    db.session.commit()

    sym = etf.symbol
    onboarding = {}

    if activate:
        # 1. Dispatch historical backfill (756 days for factor lookbacks)
        try:
            from app.data.tasks import backfill_bars
            backfill_bars.delay(sym, 756)
            onboarding['backfill'] = 'dispatched'
        except Exception as exc:
            onboarding['backfill'] = f'failed: {exc}'

        # 2. Add to Redis active-set cache
        redis_client.sadd('config:active_symbols', sym)

        # 3. Add to WebSocket subscription if running
        if _ws_listener and _ws_listener.is_running:
            _ws_listener.add_symbol(sym)
            onboarding['websocket'] = 'subscribed'
        else:
            onboarding['websocket'] = 'listener_not_running'
    else:
        # Remove from Redis active-set cache
        redis_client.srem('config:active_symbols', sym)
        onboarding['note'] = 'removed; positions exit at next rebalance'

    # 4. Publish config change event for dashboard
    redis_client.publish('channel:config_change', json.dumps({
        'type': 'active_set_changed',
        'symbol': sym,
        'in_active_set': activate,
        'reason': etf.active_set_reason,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }))

    return jsonify({
        'symbol': sym,
        'in_active_set': etf.in_active_set,
        'reason': etf.active_set_reason,
        'onboarding': onboarding,
    })


@api_bp.route('/universe/<symbol>', methods=['PATCH'])
@api_login_required
def update_universe_etf(symbol: str):
    """Update tracking fields on a watchlist ETF.

    Body: {"notes": "...", "your_rating": 4, "tags": "momentum,defensive"}
    """
    from app.models.etf_universe import ETFUniverse

    etf = ETFUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not etf:
        return jsonify({'error': f'{symbol} not found'}), 404

    data = request.get_json() or {}
    allowed = ('notes', 'your_rating', 'tags')
    for field in allowed:
        if field in data:
            setattr(etf, field, data[field])

    db.session.commit()
    return jsonify(_serialize_etf(etf))


@api_bp.route('/universe', methods=['POST'])
@api_login_required
def create_universe_etf():
    """Add a new ETF to the watchlist.

    Body (required): {"symbol": "XBI", "name": "SPDR S&P Biotech", "sector": "Healthcare"}
    Body (optional): {"aum_bn": 8.5, "avg_daily_vol_m": 450, "spread_est_bps": 3.5,
                      "liquidity_score": 4.2, "options_market": true, "mt5_symbol": "XBI.NYSE",
                      "notes": "...", "your_rating": 3, "tags": "biotech,healthcare"}
    """
    from app.models.etf_universe import ETFUniverse

    data = request.get_json(silent=True) or {}

    symbol = (data.get('symbol') or '').strip().upper()
    if not symbol:
        return jsonify({'error': 'symbol is required'}), 400

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    sector = (data.get('sector') or '').strip()
    if not sector:
        return jsonify({'error': 'sector is required'}), 400

    if ETFUniverse.query.filter_by(symbol=symbol).first():
        return jsonify({'error': f'{symbol} already exists in universe'}), 409

    etf = ETFUniverse(
        symbol=symbol,
        name=name,
        sector=sector,
        aum_bn=data.get('aum_bn'),
        avg_daily_vol_m=data.get('avg_daily_vol_m'),
        spread_est_bps=data.get('spread_est_bps'),
        liquidity_score=data.get('liquidity_score'),
        options_market=data.get('options_market', True),
        mt5_symbol=data.get('mt5_symbol'),
        notes=data.get('notes'),
        your_rating=data.get('your_rating'),
        tags=data.get('tags'),
        is_active=True,
        in_active_set=False,
    )
    db.session.add(etf)
    db.session.commit()
    return jsonify(_serialize_etf(etf)), 201


@api_bp.route('/universe/<symbol>', methods=['DELETE'])
@api_login_required
def delete_universe_etf(symbol: str):
    """Soft-delete an ETF from the watchlist (sets is_active=False).

    Pass ?hard=1 to permanently remove the row (also clears price_bars and
    factor_scores for that symbol).  Hard delete is only permitted in
    research/simulation mode to prevent accidental data loss in live.
    """
    from app.models.etf_universe import ETFUniverse

    etf = ETFUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not etf:
        return jsonify({'error': f'{symbol} not found'}), 404

    hard = request.args.get('hard', '0') == '1'
    if hard:
        # Guard: only allow hard delete outside live mode
        mode = redis_client.hget('config:system', 'operational_mode') or b''
        if isinstance(mode, bytes):
            mode = mode.decode()
        if mode == 'live':
            return jsonify({'error': 'Hard delete is not permitted in live mode'}), 403

        from app.models.price_bars import PriceBar
        from app.models.factor_scores import FactorScore
        PriceBar.query.filter_by(symbol=etf.symbol).delete()
        FactorScore.query.filter_by(symbol=etf.symbol).delete()
        db.session.delete(etf)
        db.session.commit()

        # Remove from Redis active-set cache
        redis_client.srem('config:active_symbols', etf.symbol)
        return jsonify({'deleted': etf.symbol, 'hard': True})

    # Soft delete — deactivate from trading pipeline first
    etf.in_active_set = False
    etf.is_active = False
    db.session.commit()

    # Remove from Redis active-set cache
    redis_client.srem('config:active_symbols', etf.symbol)
    return jsonify({'deleted': etf.symbol, 'hard': False})


# ── backtest ───────────────────────────────────────────────────────

@api_bp.route('/backtest/run', methods=['POST'])
def run_backtest():
    """Run a backtest with given parameters.

    Body: {
        "asset_class": "etf",
        "weights": {"trend": 0.25, "volatility": 0.20, ...},
        "start_date": "2023-01-01",
        "end_date": "2025-12-31",
        "initial_capital": 100000,
        "rebalance_freq": "weekly",
        "max_positions": 10,
        "slippage_bps": 2.0
    }
    """
    from datetime import date as date_cls
    from app.backtesting import VectorizedBacktester

    data = request.get_json(silent=True) or {}
    asset_class = data.get('asset_class', 'etf')

    weights = data.get('weights')
    try:
        start = date_cls.fromisoformat(data.get('start_date', ''))
    except (ValueError, TypeError):
        start = date_cls.today() - __import__('datetime').timedelta(days=756)
    try:
        end = date_cls.fromisoformat(data.get('end_date', ''))
    except (ValueError, TypeError):
        end = date_cls.today()

    backtester = VectorizedBacktester(
        db_session=db.session,
        weights=weights,
        initial_capital=data.get('initial_capital', 100_000),
        rebalance_freq=data.get('rebalance_freq', 'weekly'),
        max_positions=data.get('max_positions', 10 if asset_class == 'etf' else 15),
        slippage_bps=data.get('slippage_bps', 2.0),
        asset_class=asset_class,
    )

    result = backtester.run(start, end)
    return jsonify(result.to_json())


@api_bp.route('/backtest/results', methods=['GET'])
def get_backtest_results():
    """Get performance metrics for tearsheet display.

    Query params:
        asset_class (str): 'etf' or 'stock', default 'etf'
    """
    from app.models.performance import PerformanceMetric

    asset_class = _get_asset_class()

    rows = PerformanceMetric.query.filter_by(
        asset_class=asset_class,
    ).order_by(
        PerformanceMetric.date,
    ).all()

    return jsonify({
        'asset_class': asset_class,
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
        kill_switches[switch] = val == '1'

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


# ── data management ───────────────────────────────────────────────

@api_bp.route('/data/backfill', methods=['POST'])
def trigger_backfill():
    """Trigger historical bar backfill for ETFs.

    Body (optional): {
        "symbols": ["SPY", "QQQ"],
        "days": 756,
        "sync": false  — set true to run synchronously (no Celery needed)
    }
    Defaults to all active ETFs, 756 days, async (Celery).
    """
    import os
    from datetime import timedelta
    data = request.get_json(silent=True) or {}
    days = data.get('days', 756)
    sync = data.get('sync', False)

    if 'symbols' in data and data['symbols']:
        symbols = [s.upper() for s in data['symbols']]
    else:
        from app.models.etf_universe import ETFUniverse
        etfs = ETFUniverse.query.filter_by(is_active=True).all()
        symbols = [e.symbol for e in etfs]

    if sync:
        # Run synchronously — useful when Celery is not running
        from app.data.alpaca_provider import AlpacaDataProvider
        from app.data.ingestion import ingest_daily_bars, update_redis_cache
        from datetime import date as date_cls

        api_key = os.environ.get('ALPACA_API_KEY', '')
        secret_key = os.environ.get('ALPACA_SECRET_KEY', '')
        if not api_key:
            return jsonify({'error': 'ALPACA_API_KEY not set in .env'}), 400

        provider = AlpacaDataProvider(api_key=api_key, secret_key=secret_key)
        end_date = date_cls.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=days)

        results = []
        errors = []
        for symbol in symbols:
            try:
                df = provider.get_daily_bars(symbol, start_date, end_date)
                if df.empty:
                    results.append({'symbol': symbol, 'inserted': 0, 'status': 'no_data'})
                    continue
                inserted = ingest_daily_bars(symbol, df, source='alpaca')
                update_redis_cache(symbol, df, redis_client)
                results.append({'symbol': symbol, 'inserted': inserted, 'status': 'ok'})
            except Exception as exc:
                errors.append({'symbol': symbol, 'error': str(exc)})

        return jsonify({
            'status': 'completed',
            'mode': 'sync',
            'count': len(symbols),
            'days': days,
            'results': results,
            'errors': errors,
        })

    # Async via Celery
    from app.data.tasks import backfill_bars
    task_ids = []
    for symbol in symbols:
        result = backfill_bars.delay(symbol, days)
        task_ids.append({'symbol': symbol, 'task_id': result.id})

    return jsonify({
        'status': 'dispatched',
        'mode': 'async',
        'count': len(symbols),
        'days': days,
        'tasks': task_ids,
    })


@api_bp.route('/data/compute', methods=['POST'])
def trigger_compute():
    """Trigger factor computation and signal generation.

    Body (optional): {"sync": true} to run synchronously (no Celery needed).
    """
    data = request.get_json(silent=True) or {}
    sync = data.get('sync', False)

    if sync:
        from app.factors.factor_registry import FactorRegistry
        from app.signals.signal_generator import SignalGenerator
        from app.models.etf_universe import ETFUniverse

        etfs = ETFUniverse.query.filter_by(
            is_active=True, in_active_set=True,
        ).all()
        symbols = [e.symbol for e in etfs]

        if not symbols:
            return jsonify({'error': 'No ETFs in active set'}), 400

        # Compute factors (persists to DB + Redis)
        registry = FactorRegistry(
            redis_client=redis_client,
            db_session=db.session,
        )
        scores = registry.compute_all(symbols)

        # Cache scores
        redis_client.set(
            'cache:scores:latest',
            json.dumps({
                sym: {k: round(v, 4) for k, v in factors.items()}
                for sym, factors in scores.items()
            }),
            ex=3600,
        )

        # Generate signals (persists to DB + Redis)
        generator = SignalGenerator(
            redis_client=redis_client,
            db_session=db.session,
        )
        signals = generator.run(symbols)

        # Cache regime
        if signals:
            first_sig = next(iter(signals.values()), {})
            redis_client.set(
                'cache:regime:current',
                json.dumps({
                    'regime': first_sig.get('regime', 'unknown'),
                    'confidence': first_sig.get('regime_confidence', 0),
                }),
                ex=3600,
            )

        # Cache signals grouped
        groups = {'long': [], 'neutral': [], 'avoid': []}
        for sym, sig in signals.items():
            entry = {
                'symbol': sym,
                'composite_score': sig.get('composite_score', 0),
                'signal_type': sig.get('signal_type', 'neutral'),
                'regime': sig.get('regime', 'unknown'),
                'regime_confidence': sig.get('regime_confidence', 0),
            }
            bucket = groups.get(sig.get('signal_type', 'neutral'), groups['neutral'])
            bucket.append(entry)
        for bucket in groups.values():
            bucket.sort(key=lambda x: x['composite_score'], reverse=True)
        redis_client.set('cache:signals:latest', json.dumps(groups), ex=3600)

        return jsonify({
            'status': 'completed',
            'mode': 'sync',
            'symbols': len(symbols),
            'signals': len(signals),
        })

    # Async via Celery
    from app.data.tasks import compute_all_factors
    result = compute_all_factors.delay()
    return jsonify({'status': 'dispatched', 'task_id': result.id})


@api_bp.route('/data/status', methods=['GET'])
def get_data_status():
    """Get data pipeline status: bar counts, freshness, factor status."""
    from app.models.price_bars import PriceBar
    from app.models.factor_scores import FactorScore
    from app.models.etf_universe import ETFUniverse
    from sqlalchemy import func

    # Bar counts per symbol
    bar_stats = db.session.query(
        PriceBar.symbol,
        func.count(PriceBar.id).label('count'),
        func.max(PriceBar.timestamp).label('latest'),
        func.min(PriceBar.timestamp).label('earliest'),
    ).filter(
        PriceBar.timeframe == '1d',
    ).group_by(PriceBar.symbol).all()

    bars = {
        row.symbol: {
            'count': row.count,
            'latest': row.latest.isoformat() if row.latest else None,
            'earliest': row.earliest.isoformat() if row.earliest else None,
        }
        for row in bar_stats
    }

    # Factor score freshness
    factor_latest = db.session.query(
        func.max(FactorScore.calc_time),
    ).scalar()

    # Universe symbols
    universe = ETFUniverse.query.filter_by(is_active=True).count()

    return jsonify({
        'universe_count': universe,
        'bars': bars,
        'bars_total_symbols': len(bars),
        'factor_scores_latest': factor_latest.isoformat() if factor_latest else None,
    })


# ── pipeline ──────────────────────────────────────────────────────

@api_bp.route('/pipeline/run', methods=['POST'])
def run_pipeline():
    """Run the full trading pipeline: signals → portfolio → risk → execution.

    Body (optional): {"sync": true} to run synchronously (no Celery needed).
    """
    data = request.get_json(silent=True) or {}
    sync = data.get('sync', False)

    if sync:
        from app.pipeline.orchestrator import PipelineOrchestrator
        from app.utils.config_loader import ConfigLoader

        config = ConfigLoader(redis_client, db.session)
        orchestrator = PipelineOrchestrator(
            redis_client=redis_client,
            db_session=db.session,
            config_loader=config,
        )
        result = orchestrator.run()
        return jsonify(result)

    from app.pipeline.tasks import run_trading_pipeline
    result = run_trading_pipeline.delay()
    return jsonify({'status': 'dispatched', 'task_id': result.id})


@api_bp.route('/pipeline/status', methods=['GET'])
def pipeline_status():
    """Get latest pipeline run result from cache."""
    raw = redis_client.get('cache:pipeline:latest')
    if raw:
        return jsonify(json.loads(raw))
    return jsonify({'status': 'no_runs'})


# ── websocket control ─────────────────────────────────────────────

def _sync_active_set_cache():
    """Rebuild Redis SET config:active_symbols from DB truth."""
    from app.models.etf_universe import ETFUniverse

    etfs = ETFUniverse.query.filter_by(
        is_active=True, in_active_set=True,
    ).all()
    symbols = [e.symbol for e in etfs]

    pipe = redis_client.pipeline()
    pipe.delete('config:active_symbols')
    if symbols:
        pipe.sadd('config:active_symbols', *symbols)
    pipe.execute()
    return symbols


# Global reference to the WebSocket listener instance
_ws_listener = None


@api_bp.route('/data/websocket/start', methods=['POST'])
def start_websocket():
    """Start Alpaca WebSocket streaming for real-time bar data."""
    global _ws_listener

    if _ws_listener and _ws_listener.is_running:
        return jsonify({'status': 'already_running'})

    import os
    from app.data.websocket_listener import AlpacaWebSocketListener
    from app.models.etf_universe import ETFUniverse

    api_key = os.environ.get('ALPACA_API_KEY', '')
    secret_key = os.environ.get('ALPACA_SECRET_KEY', '')

    if not api_key or not secret_key:
        return jsonify({'error': 'Alpaca API keys not configured'}), 400

    etfs = ETFUniverse.query.filter_by(is_active=True).all()
    symbols = [e.symbol for e in etfs]

    # Sync Redis active-set cache on startup
    _sync_active_set_cache()

    _ws_listener = AlpacaWebSocketListener(api_key, secret_key, redis_client)
    _ws_listener.start(symbols)

    return jsonify({'status': 'started', 'symbols': len(symbols)})


@api_bp.route('/data/websocket/stop', methods=['POST'])
def stop_websocket():
    """Stop Alpaca WebSocket streaming."""
    global _ws_listener

    if _ws_listener:
        _ws_listener.stop()
        _ws_listener = None
        return jsonify({'status': 'stopped'})

    return jsonify({'status': 'not_running'})


@api_bp.route('/data/websocket/status', methods=['GET'])
def websocket_status():
    """Get WebSocket listener status."""
    global _ws_listener
    running = _ws_listener.is_running if _ws_listener else False
    return jsonify({'running': running})


# ── pipeline status ─────────────────────────────────────────────────

@api_bp.route('/pipelines/status', methods=['GET'])
@api_login_required
def pipelines_status():
    """Module health via Redis heartbeats + queue depths + today's throughput."""
    now = datetime.now(timezone.utc)

    MODULES = {
        'market_data':      {'key': 'heartbeat:data',       'stale_min': 10},
        'factor_engine':    {'key': 'heartbeat:factors',    'stale_min': 360},
        'signal_engine':    {'key': 'heartbeat:signals',    'stale_min': 360},
        'portfolio_engine': {'key': 'heartbeat:portfolio',  'stale_min': 360},
        'risk_engine':      {'key': 'heartbeat:risk',       'stale_min': 10},
        'execution_engine': {'key': 'heartbeat:execution',  'stale_min': 30},
        'dashboard':        {'key': 'heartbeat:monitoring', 'stale_min': 5},
    }

    modules = {}
    for mod_id, cfg in MODULES.items():
        raw = redis_client.get(cfg['key'])
        if raw is None:
            modules[mod_id] = {'status': 'unknown', 'last_run': None, 'last_duration_ms': None}
            continue
        ts_str = raw.decode() if isinstance(raw, bytes) else raw
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_min = (now - ts).total_seconds() / 60
            status = 'ok' if age_min <= cfg['stale_min'] else 'stale'
        except Exception:
            status = 'error'
        modules[mod_id] = {'status': status, 'last_run': ts_str, 'last_duration_ms': None}

    # Queue depths
    queues = {}
    for q in ('channel:orders_proposed', 'channel:orders_approved', 'channel:risk_alerts'):
        try:
            queues[q.split(':')[1]] = redis_client.llen(q)
        except Exception:
            queues[q.split(':')[1]] = 0

    # Today's throughput
    throughput = {'bars_today': 0, 'signals_today': 0, 'trades_today': 0}
    try:
        from app.models.price_bars import PriceBar
        from app.models.signals import Signal as _Sig
        from app.models.trades import Trade as _Trade
        from sqlalchemy import func as _func3
        today = datetime.now(timezone.utc).date()
        throughput['bars_today'] = db.session.query(
            _func3.count(PriceBar.id)
        ).filter(db.func.date(PriceBar.timestamp) == today).scalar() or 0
        throughput['signals_today'] = db.session.query(
            _func3.count(_Sig.id)
        ).filter(db.func.date(_Sig.signal_time) == today).scalar() or 0
        throughput['trades_today'] = db.session.query(
            _func3.count(_Trade.id)
        ).filter(db.func.date(_Trade.trade_time) == today).scalar() or 0
    except Exception:
        pass

    return jsonify({'modules': modules, 'queues': queues, 'throughput': throughput})


# ── performance recording ─────────────────────────────────────────

@api_bp.route('/data/record-performance', methods=['POST'])
def record_performance():
    """Record daily performance metrics (sync)."""
    from app.risk.performance_recorder import PerformanceRecorder
    recorder = PerformanceRecorder(db.session, redis_client)
    result = recorder.record_daily()
    return jsonify(result)


# ── helpers ────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    if val is None:
        return None
    return float(val)


# ── market data ──────────────────────────────────────────────────

@api_bp.route('/market/vix', methods=['GET'])
@api_login_required
def market_vix():
    """Current VIX level + 30-day sparkline from Redis cache."""
    current_raw = redis_client.get('vix:latest')
    history_raw = redis_client.get('vix:history_252')

    current = float(current_raw) if current_raw else None
    history = []
    if history_raw:
        full = json.loads(history_raw) if isinstance(history_raw, str) else json.loads(history_raw.decode())
        history = full[-30:] if len(full) >= 30 else full

    prev = history[-2] if len(history) >= 2 else None
    delta = round(current - prev, 2) if current is not None and prev is not None else None

    return jsonify({
        'current': current,
        'delta': delta,
        'history_30d': history,
    })


# ── sentiment feed ───────────────────────────────────────────────

@api_bp.route('/sentiment/feed', methods=['GET'])
@api_login_required
def sentiment_feed():
    """Recent sentiment headlines with scores."""
    from app.models.sentiment_raw import SentimentRaw

    symbol = request.args.get('symbol')
    limit = min(int(request.args.get('limit', 20)), 100)

    q = SentimentRaw.query.order_by(SentimentRaw.timestamp.desc())
    if symbol:
        q = q.filter_by(symbol=symbol.upper())
    items = q.limit(limit).all()

    return jsonify([{
        'symbol': s.symbol,
        'headline': (s.headline[:80] + '...') if s.headline and len(s.headline) > 80 else (s.headline or ''),
        'source': s.source,
        'raw_score': float(s.raw_score) if s.raw_score is not None else None,
        'model': s.model,
        'timestamp': s.timestamp.isoformat() if s.timestamp else None,
    } for s in items])


# ── rotation / correlation ───────────────────────────────────────

@api_bp.route('/rotation/correlation', methods=['GET'])
@api_login_required
def rotation_correlation():
    """30-day rolling pairwise return correlation matrix for active ETFs."""
    import pandas as pd
    from app.models.price_bars import PriceBar
    from app.models.etf_universe import ETFUniverse

    days = min(int(request.args.get('days', 30)), 252)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 5)

    active = ETFUniverse.query.filter_by(is_active=True, in_active_set=True).all()
    symbols = [e.symbol for e in active][:20]

    if len(symbols) < 2:
        return jsonify({'symbols': [], 'matrix': []})

    rows = db.session.query(
        PriceBar.symbol, PriceBar.timestamp, PriceBar.close,
    ).filter(
        PriceBar.symbol.in_(symbols),
        PriceBar.timestamp >= cutoff,
        PriceBar.timeframe == '1d',
    ).order_by(PriceBar.timestamp).all()

    if not rows:
        return jsonify({'symbols': [], 'matrix': []})

    df = pd.DataFrame(rows, columns=['symbol', 'date', 'close'])
    df['close'] = df['close'].astype(float)
    pivot = df.pivot_table(index='date', columns='symbol', values='close')
    returns = pivot.pct_change().dropna()

    valid = returns.columns[returns.notna().sum() >= max(5, days // 3)]
    if len(valid) < 2:
        return jsonify({'symbols': [], 'matrix': []})

    corr = returns[valid].corr().round(3)
    syms = list(corr.columns)
    matrix = corr.values.tolist()

    return jsonify({'symbols': syms, 'matrix': matrix})
