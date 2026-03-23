"""Stock universe, account management, fundamentals, and asset-class-scoped endpoints.

Fills the API gaps so stock mode is a first-class citizen alongside ETF mode.
All routes are registered on the shared api_bp blueprint.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import jsonify, request

from app.api import api_bp
from app.auth import api_login_required
from app.extensions import db, redis_client


def _to_float(val) -> float | None:
    if val is None:
        return None
    return float(val)


# =====================================================================
# STOCK UNIVERSE CRUD
# =====================================================================

def _serialize_stock(s) -> dict:
    """Serialize a StockUniverse row to JSON-safe dict."""
    return {
        'id': s.id,
        'symbol': s.symbol,
        'name': s.name,
        'sector': s.sector,
        'industry': s.industry,
        # market metadata
        'market_cap_bn': _to_float(s.market_cap_bn),
        'avg_daily_vol_m': _to_float(s.avg_daily_vol_m),
        'spread_est_bps': _to_float(s.spread_est_bps),
        'liquidity_score': _to_float(s.liquidity_score),
        'float_shares_m': _to_float(s.float_shares_m),
        'short_interest_pct': _to_float(s.short_interest_pct),
        'beta': _to_float(s.beta),
        'options_market': s.options_market,
        # fundamentals
        'pe_ratio': _to_float(s.pe_ratio),
        'forward_pe': _to_float(s.forward_pe),
        'pb_ratio': _to_float(s.pb_ratio),
        'ps_ratio': _to_float(s.ps_ratio),
        'roe': _to_float(s.roe),
        'debt_to_equity': _to_float(s.debt_to_equity),
        'revenue_growth_yoy': _to_float(s.revenue_growth_yoy),
        'earnings_growth_yoy': _to_float(s.earnings_growth_yoy),
        'dividend_yield': _to_float(s.dividend_yield),
        'profit_margin': _to_float(s.profit_margin),
        # earnings
        'next_earnings_date': s.next_earnings_date.isoformat() if s.next_earnings_date else None,
        'last_earnings_date': s.last_earnings_date.isoformat() if s.last_earnings_date else None,
        'last_earnings_surprise': _to_float(s.last_earnings_surprise),
        'earnings_surprise_3q_avg': _to_float(s.earnings_surprise_3q_avg),
        # watchlist
        'is_active': s.is_active,
        'in_active_set': s.in_active_set,
        'active_set_reason': s.active_set_reason,
        # operator
        'notes': s.notes,
        'your_rating': s.your_rating,
        'tags': s.tags,
        # cached performance
        'last_signal_type': s.last_signal_type,
        'last_composite_score': _to_float(s.last_composite_score),
        'last_signal_at': s.last_signal_at.isoformat() if s.last_signal_at else None,
        'perf_1w': _to_float(s.perf_1w),
        'perf_1m': _to_float(s.perf_1m),
        'perf_3m': _to_float(s.perf_3m),
        'correlation_to_spy': _to_float(s.correlation_to_spy),
        # meta
        'fundamentals_updated_at': (
            s.fundamentals_updated_at.isoformat() if s.fundamentals_updated_at else None
        ),
        'added_at': s.added_at.isoformat() if s.added_at else None,
    }


@api_bp.route('/stock-universe', methods=['GET'])
@api_login_required
def get_stock_universe():
    """List all stocks in the universe with fundamentals and cached scores.

    Query params:
        active_only (bool): only is_active=True (default true)
        sector (str): filter by sector
    """
    from app.models.stock_universe import StockUniverse

    q = StockUniverse.query
    if request.args.get('active_only', '1') in ('1', 'true'):
        q = q.filter_by(is_active=True)
    sector = request.args.get('sector')
    if sector:
        q = q.filter_by(sector=sector)

    stocks = q.order_by(StockUniverse.symbol).all()

    # Enrich with live prices from Redis
    result = []
    for s in stocks:
        data = _serialize_stock(s)
        # Try to attach live price
        price_raw = redis_client.hget('cache:mid_prices', s.symbol)
        data['live_price'] = float(price_raw) if price_raw else None
        result.append(data)

    return jsonify(result)


@api_bp.route('/stock-universe', methods=['POST'])
@api_login_required
def create_stock():
    """Add a new stock to the universe.

    Body (required): {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"}
    Body (optional): {"industry": "...", "market_cap_bn": 3000, "beta": 1.2, ...}
    """
    from app.models.stock_universe import StockUniverse

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

    if StockUniverse.query.filter_by(symbol=symbol).first():
        return jsonify({'error': f'{symbol} already exists in stock universe'}), 409

    stock = StockUniverse(
        symbol=symbol,
        name=name,
        sector=sector,
        industry=data.get('industry'),
        market_cap_bn=data.get('market_cap_bn'),
        avg_daily_vol_m=data.get('avg_daily_vol_m'),
        spread_est_bps=data.get('spread_est_bps'),
        liquidity_score=data.get('liquidity_score'),
        float_shares_m=data.get('float_shares_m'),
        short_interest_pct=data.get('short_interest_pct'),
        beta=data.get('beta'),
        options_market=data.get('options_market', True),
        notes=data.get('notes'),
        your_rating=data.get('your_rating'),
        tags=data.get('tags'),
        is_active=True,
        in_active_set=False,
    )
    db.session.add(stock)
    db.session.commit()
    return jsonify(_serialize_stock(stock)), 201


@api_bp.route('/stock-universe/<symbol>', methods=['PATCH'])
@api_login_required
def update_stock(symbol: str):
    """Update stock metadata and operator fields.

    Body: {"notes": "...", "your_rating": 4, "tags": "...", "industry": "..."}
    """
    from app.models.stock_universe import StockUniverse

    stock = StockUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not stock:
        return jsonify({'error': f'{symbol} not found'}), 404

    data = request.get_json(silent=True) or {}

    allowed = (
        'notes', 'your_rating', 'tags', 'industry',
        'market_cap_bn', 'avg_daily_vol_m', 'spread_est_bps',
        'liquidity_score', 'float_shares_m', 'beta',
    )
    for field in allowed:
        if field in data:
            setattr(stock, field, data[field])

    db.session.commit()
    return jsonify(_serialize_stock(stock))


@api_bp.route('/stock-universe/<symbol>/active-set', methods=['PATCH'])
@api_login_required
def toggle_stock_active_set(symbol: str):
    """Add or remove a stock from the active trading set.

    Body: {"active": true, "reason": "Strong fundamentals + momentum"}
    """
    from app.models.stock_universe import StockUniverse

    stock = StockUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not stock:
        return jsonify({'error': f'{symbol} not found'}), 404

    data = request.get_json(silent=True) or {}
    active = data.get('active', not stock.in_active_set)

    stock.in_active_set = active
    stock.active_set_reason = data.get('reason', stock.active_set_reason)
    stock.active_set_changed_at = datetime.now(timezone.utc)
    db.session.commit()

    # Sync Redis active set for stocks
    _sync_stock_active_set()

    # Trigger bar backfill for newly activated stocks
    if active:
        try:
            from app.data.tasks import backfill_bars
            backfill_bars.delay(symbol.upper(), 756)
        except Exception:
            pass  # Celery not running

    return jsonify({
        'symbol': stock.symbol,
        'in_active_set': stock.in_active_set,
        'reason': stock.active_set_reason,
    })


@api_bp.route('/stock-universe/<symbol>', methods=['DELETE'])
@api_login_required
def delete_stock(symbol: str):
    """Soft-delete a stock (sets is_active=False). ?hard=1 for permanent removal."""
    from app.models.stock_universe import StockUniverse

    stock = StockUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not stock:
        return jsonify({'error': f'{symbol} not found'}), 404

    hard = request.args.get('hard', '0') == '1'
    if hard:
        mode = redis_client.hget('config:system', 'operational_mode') or ''
        if isinstance(mode, bytes):
            mode = mode.decode()
        if mode == 'live':
            return jsonify({'error': 'Hard delete not permitted in live mode'}), 403

        from app.models.price_bars import PriceBar
        from app.models.factor_scores import FactorScore
        PriceBar.query.filter_by(symbol=stock.symbol).filter_by(asset_class='stock').delete()
        FactorScore.query.filter_by(symbol=stock.symbol).filter_by(asset_class='stock').delete()
        db.session.delete(stock)
        db.session.commit()
        redis_client.srem('config:stock_active_symbols', stock.symbol)
        return jsonify({'deleted': stock.symbol, 'hard': True})

    stock.in_active_set = False
    stock.is_active = False
    db.session.commit()
    redis_client.srem('config:stock_active_symbols', stock.symbol)
    return jsonify({'deleted': stock.symbol, 'hard': False})


def _sync_stock_active_set():
    """Rebuild Redis SET config:stock_active_symbols from DB."""
    from app.models.stock_universe import StockUniverse

    stocks = StockUniverse.query.filter_by(is_active=True, in_active_set=True).all()
    symbols = [s.symbol for s in stocks]

    pipe = redis_client.pipeline()
    pipe.delete('config:stock_active_symbols')
    if symbols:
        pipe.sadd('config:stock_active_symbols', *symbols)
    pipe.execute()
    return symbols


# =====================================================================
# ACCOUNT MANAGEMENT
# =====================================================================

def _serialize_account(a) -> dict:
    return {
        'id': a.id,
        'name': a.name,
        'asset_class': a.asset_class,
        'initial_capital': _to_float(a.initial_capital),
        'cash_balance': _to_float(a.cash_balance),
        'portfolio_value': _to_float(a.portfolio_value),
        'nav': a.nav,
        'cash_pct': round(a.cash_pct, 4),
        'total_pnl': _to_float(a.total_pnl),
        'realized_pnl': _to_float(a.realized_pnl),
        'unrealized_pnl': _to_float(a.unrealized_pnl),
        'high_watermark': _to_float(a.high_watermark),
        'current_drawdown': _to_float(a.current_drawdown),
        'broker': a.broker,
        'operational_mode': a.operational_mode,
        'is_active': a.is_active,
        'max_positions': a.max_positions,
        'max_position_pct': _to_float(a.max_position_pct),
        'max_sector_pct': _to_float(a.max_sector_pct),
        'vol_target': _to_float(a.vol_target),
        'created_at': a.created_at.isoformat() if a.created_at else None,
        'updated_at': a.updated_at.isoformat() if a.updated_at else None,
    }


@api_bp.route('/accounts', methods=['GET'])
@api_login_required
def list_accounts():
    """List all accounts. Optionally filter by ?asset_class=etf|stock."""
    from app.models.account import Account

    q = Account.query
    ac = request.args.get('asset_class')
    if ac and ac in ('etf', 'stock'):
        q = q.filter_by(asset_class=ac)

    accounts = q.order_by(Account.asset_class, Account.name).all()
    return jsonify([_serialize_account(a) for a in accounts])


@api_bp.route('/accounts', methods=['POST'])
@api_login_required
def create_account():
    """Create a new account.

    Body: {"name": "Stock Portfolio", "asset_class": "stock", "initial_capital": 100000}
    """
    from app.models.account import Account

    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    asset_class = data.get('asset_class', 'etf')
    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    existing = Account.query.filter_by(name=name, asset_class=asset_class).first()
    if existing:
        return jsonify({'error': f'Account "{name}" [{asset_class}] already exists'}), 409

    capital = data.get('initial_capital', 100_000)

    account = Account(
        name=name,
        asset_class=asset_class,
        initial_capital=capital,
        cash_balance=capital,
        high_watermark=capital,
        max_positions=data.get('max_positions', 20 if asset_class == 'etf' else 30),
        max_position_pct=data.get('max_position_pct', 0.05),
        max_sector_pct=data.get('max_sector_pct', 0.25 if asset_class == 'etf' else 0.20),
        vol_target=data.get('vol_target', 0.12 if asset_class == 'etf' else 0.15),
    )
    db.session.add(account)
    db.session.commit()
    return jsonify(_serialize_account(account)), 201


@api_bp.route('/accounts/<asset_class>', methods=['GET'])
@api_login_required
def get_account(asset_class: str):
    """Get the primary account for an asset class."""
    from app.models.account import Account

    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    account = Account.query.filter_by(
        asset_class=asset_class, is_active=True,
    ).first()

    if not account:
        return jsonify({'error': f'No active {asset_class} account found'}), 404

    return jsonify(_serialize_account(account))


@api_bp.route('/accounts/<asset_class>', methods=['PATCH'])
@api_login_required
def update_account(asset_class: str):
    """Update account constraints and settings.

    Body: {"max_positions": 15, "max_sector_pct": 0.20, "vol_target": 0.15}
    """
    from app.models.account import Account

    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    account = Account.query.filter_by(
        asset_class=asset_class, is_active=True,
    ).first()
    if not account:
        return jsonify({'error': f'No active {asset_class} account found'}), 404

    data = request.get_json(silent=True) or {}

    allowed = (
        'name', 'max_positions', 'max_position_pct', 'max_sector_pct',
        'vol_target', 'broker', 'operational_mode',
    )
    for field in allowed:
        if field in data:
            setattr(account, field, data[field])

    db.session.commit()
    return jsonify(_serialize_account(account))


@api_bp.route('/accounts/<asset_class>/performance', methods=['GET'])
@api_login_required
def get_account_performance(asset_class: str):
    """Get performance history for an asset class account.

    Query params:
        days (int): lookback period, default 90
    """
    from app.models.performance import PerformanceMetric

    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    days = min(int(request.args.get('days', 90)), 756)

    rows = PerformanceMetric.query.filter_by(
        asset_class=asset_class,
    ).order_by(PerformanceMetric.date).all()

    # Take last N days
    if days and len(rows) > days:
        rows = rows[-days:]

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


@api_bp.route('/accounts/<asset_class>/positions', methods=['GET'])
@api_login_required
def get_account_positions(asset_class: str):
    """Get open positions for a specific asset class."""
    from app.models.positions import Position

    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    positions = Position.query.filter_by(
        asset_class=asset_class, is_open=True,
    ).order_by(Position.symbol).all()

    result = []
    for p in positions:
        entry = {
            'symbol': p.symbol,
            'side': p.side,
            'quantity': _to_float(p.quantity),
            'avg_entry_price': _to_float(p.avg_entry_price),
            'current_price': _to_float(p.current_price),
            'market_value': _to_float(p.market_value),
            'unrealized_pnl': _to_float(p.unrealized_pnl),
            'unrealized_pnl_pct': _to_float(p.unrealized_pnl_pct),
            'stop_price': _to_float(p.stop_price) if hasattr(p, 'stop_price') else None,
            'sector': getattr(p, 'sector', None),
            'opened_at': p.opened_at.isoformat() if hasattr(p, 'opened_at') and p.opened_at else None,
        }
        result.append(entry)

    return jsonify(result)


@api_bp.route('/accounts/<asset_class>/trades', methods=['GET'])
@api_login_required
def get_account_trades(asset_class: str):
    """Get trade history for a specific asset class.

    Query params:
        limit (int): max results, default 50
        symbol (str): filter by symbol
    """
    from app.models.trades import Trade

    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    limit = min(int(request.args.get('limit', 50)), 200)

    q = Trade.query.filter_by(asset_class=asset_class)
    symbol = request.args.get('symbol')
    if symbol:
        q = q.filter_by(symbol=symbol.upper())

    trades = q.order_by(Trade.trade_time.desc()).limit(limit).all()

    return jsonify([{
        'id': t.id,
        'symbol': t.symbol,
        'side': t.side,
        'quantity': _to_float(t.quantity),
        'price': _to_float(t.price),
        'notional': _to_float(t.notional),
        'slippage_bps': _to_float(t.slippage_bps) if hasattr(t, 'slippage_bps') else None,
        'trade_time': t.trade_time.isoformat() if t.trade_time else None,
        'status': getattr(t, 'status', 'filled'),
    } for t in trades])


# =====================================================================
# FUNDAMENTALS & EARNINGS DATA
# =====================================================================

@api_bp.route('/stocks/<symbol>/fundamentals', methods=['GET'])
@api_login_required
def get_stock_fundamentals(symbol: str):
    """Get fundamental data for a stock (PE, ROE, margins, growth, etc.).

    Returns data from StockUniverse + Redis cache, with staleness indicator.
    """
    from app.models.stock_universe import StockUniverse

    stock = StockUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not stock:
        return jsonify({'error': f'{symbol} not found in stock universe'}), 404

    # Check Redis cache for freshest data
    cached = redis_client.hgetall(f'fundamentals:{symbol.upper()}')

    fundamentals = {
        'symbol': stock.symbol,
        'name': stock.name,
        'sector': stock.sector,
        'industry': stock.industry,
        # Valuation
        'pe_ratio': _to_float(stock.pe_ratio),
        'forward_pe': _to_float(stock.forward_pe),
        'pb_ratio': _to_float(stock.pb_ratio),
        'ps_ratio': _to_float(stock.ps_ratio),
        # Quality
        'roe': _to_float(stock.roe),
        'profit_margin': _to_float(stock.profit_margin),
        # Growth
        'revenue_growth_yoy': _to_float(stock.revenue_growth_yoy),
        'earnings_growth_yoy': _to_float(stock.earnings_growth_yoy),
        # Health
        'debt_to_equity': _to_float(stock.debt_to_equity),
        'dividend_yield': _to_float(stock.dividend_yield),
        # Market
        'market_cap_bn': _to_float(stock.market_cap_bn),
        'beta': _to_float(stock.beta),
        'float_shares_m': _to_float(stock.float_shares_m),
        'short_interest_pct': _to_float(stock.short_interest_pct),
        # Meta
        'fundamentals_updated_at': (
            stock.fundamentals_updated_at.isoformat() if stock.fundamentals_updated_at else None
        ),
        'is_stale': _is_fundamentals_stale(stock),
    }

    return jsonify(fundamentals)


@api_bp.route('/stocks/<symbol>/earnings', methods=['GET'])
@api_login_required
def get_stock_earnings(symbol: str):
    """Get earnings calendar and surprise history for a stock."""
    from app.models.stock_universe import StockUniverse

    stock = StockUniverse.query.filter_by(symbol=symbol.upper()).first()
    if not stock:
        return jsonify({'error': f'{symbol} not found'}), 404

    # Earnings proximity from Redis
    days_to_raw = redis_client.get(f'earnings:{symbol.upper()}:days_to')
    days_to = int(days_to_raw) if days_to_raw else None

    # Earnings surprises from FMP cache
    import os
    surprises = []
    try:
        fmp_key = os.environ.get('FMP_API_KEY', '')
        if fmp_key:
            from app.data.fmp_provider import FMPProvider
            fmp = FMPProvider(fmp_key)
            surprises = fmp.get_earnings_surprises(symbol.upper(), limit=8)
    except Exception:
        pass

    return jsonify({
        'symbol': stock.symbol,
        'next_earnings_date': stock.next_earnings_date.isoformat() if stock.next_earnings_date else None,
        'last_earnings_date': stock.last_earnings_date.isoformat() if stock.last_earnings_date else None,
        'days_to_earnings': days_to,
        'in_blackout': days_to is not None and days_to <= 3,
        'last_earnings_surprise': _to_float(stock.last_earnings_surprise),
        'earnings_surprise_3q_avg': _to_float(stock.earnings_surprise_3q_avg),
        'surprise_history': [{
            'date': s.get('date'),
            'eps_estimated': _to_float(s.get('eps_estimated')),
            'eps_actual': _to_float(s.get('eps_actual')),
            'surprise_pct': _to_float(s.get('surprise_pct')),
        } for s in surprises],
    })


@api_bp.route('/stocks/<symbol>/dividends', methods=['GET'])
@api_login_required
def get_stock_dividends(symbol: str):
    """Get dividend history and summary from Yahoo Finance."""
    from app.data.yfinance_provider import YFinanceProvider

    yf = YFinanceProvider(redis_client)
    summary = yf.get_dividend_summary(symbol.upper())
    history = yf.get_dividends(symbol.upper(), years=5)

    return jsonify({
        'symbol': symbol.upper(),
        **summary,
        'history': history[:20],  # last 20 dividend payments
    })


@api_bp.route('/stocks/<symbol>/splits', methods=['GET'])
@api_login_required
def get_stock_splits(symbol: str):
    """Get stock split history from Yahoo Finance."""
    from app.data.yfinance_provider import YFinanceProvider

    yf = YFinanceProvider(redis_client)
    splits = yf.get_splits(symbol.upper())

    return jsonify({
        'symbol': symbol.upper(),
        'splits': splits,
    })


@api_bp.route('/stocks/<symbol>/options', methods=['GET'])
@api_login_required
def get_stock_options(symbol: str):
    """Get options chain for a stock.

    Query params:
        expiration (str): specific expiration date (YYYY-MM-DD)
    """
    from app.data.yfinance_provider import YFinanceProvider

    yf = YFinanceProvider(redis_client)
    expiration = request.args.get('expiration')

    chain = yf.get_options_chain(symbol.upper(), expiration)
    return jsonify(chain)


@api_bp.route('/stocks/<symbol>/options/expirations', methods=['GET'])
@api_login_required
def get_stock_options_expirations(symbol: str):
    """Get available options expiration dates for a stock."""
    from app.data.yfinance_provider import YFinanceProvider

    yf = YFinanceProvider(redis_client)
    expirations = yf.get_options_expirations(symbol.upper())

    return jsonify({
        'symbol': symbol.upper(),
        'expirations': expirations,
    })


@api_bp.route('/data/refresh-fundamentals', methods=['POST'])
@api_login_required
def refresh_fundamentals():
    """Trigger fundamental data refresh for stocks.

    Body (optional): {"symbols": ["AAPL", "MSFT"], "sync": false}
    Defaults to all stale stocks, async (Celery).
    """
    data = request.get_json(silent=True) or {}
    sync = data.get('sync', False)
    symbols = data.get('symbols')

    if not symbols:
        from app.data.fundamentals_ingestion import get_stale_stocks
        symbols = get_stale_stocks(max_age_days=7)

    if not symbols:
        return jsonify({'status': 'ok', 'message': 'No stale stocks to refresh'})

    if sync:
        import os
        from app.data.fmp_provider import FMPProvider
        from app.data.fundamentals_ingestion import update_stock_fundamentals

        fmp_key = os.environ.get('FMP_API_KEY', '')
        if not fmp_key:
            return jsonify({'error': 'FMP_API_KEY not set'}), 400

        fmp = FMPProvider(fmp_key)
        results = []
        errors = []
        for sym in symbols[:40]:  # cap at 40 to stay within rate limits
            try:
                fundamentals = fmp.get_full_fundamentals(sym)
                if fundamentals:
                    update_stock_fundamentals(sym, fundamentals, redis_client)
                    results.append({'symbol': sym, 'status': 'ok'})
                else:
                    results.append({'symbol': sym, 'status': 'no_data'})
            except Exception as exc:
                errors.append({'symbol': sym, 'error': str(exc)})

        return jsonify({
            'status': 'completed',
            'mode': 'sync',
            'refreshed': len(results),
            'errors': errors,
            'results': results,
        })

    # Async
    try:
        from app.data.fundamentals_tasks import refresh_stock_fundamentals
        task = refresh_stock_fundamentals.delay()
        return jsonify({'status': 'dispatched', 'task_id': task.id})
    except Exception:
        return jsonify({'error': 'Celery not available, use sync=true'}), 503


# =====================================================================
# ASSET-CLASS SCOPED OPERATIONS
# =====================================================================

@api_bp.route('/pipeline/run/<asset_class>', methods=['POST'])
@api_login_required
def run_pipeline_for_asset_class(asset_class: str):
    """Run the full trading pipeline for a specific asset class.

    Body (optional): {"sync": true}
    """
    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

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
            asset_class=asset_class,
        )
        result = orchestrator.run()
        return jsonify(result)

    # Async
    try:
        if asset_class == 'stock':
            from app.pipeline.tasks import launch_stock_pipeline
            task = launch_stock_pipeline.delay()
        else:
            from app.pipeline.tasks import run_trading_pipeline
            task = run_trading_pipeline.delay()
        return jsonify({'status': 'dispatched', 'asset_class': asset_class, 'task_id': task.id})
    except Exception:
        return jsonify({'error': 'Celery not available, use sync=true'}), 503


@api_bp.route('/data/compute/<asset_class>', methods=['POST'])
@api_login_required
def compute_factors_for_asset_class(asset_class: str):
    """Compute factors and signals for a specific asset class.

    Body (optional): {"sync": true}
    """
    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    data = request.get_json(silent=True) or {}
    sync = data.get('sync', False)

    if sync:
        from app.factors.factor_registry import FactorRegistry
        from app.signals.signal_generator import SignalGenerator

        # Get active symbols for this asset class
        if asset_class == 'stock':
            from app.models.stock_universe import StockUniverse
            items = StockUniverse.query.filter_by(
                is_active=True, in_active_set=True,
            ).all()
        else:
            from app.models.etf_universe import ETFUniverse
            items = ETFUniverse.query.filter_by(
                is_active=True, in_active_set=True,
            ).all()

        symbols = [e.symbol for e in items]
        if not symbols:
            return jsonify({'error': f'No {asset_class}s in active set'}), 400

        # Compute factors
        registry = FactorRegistry(
            redis_client=redis_client,
            db_session=db.session,
            asset_class=asset_class,
        )
        scores = registry.compute_all(symbols)

        # Cache scores
        cache_key = f'cache:scores:{asset_class}:latest' if asset_class == 'stock' else 'cache:scores:latest'
        redis_client.set(
            cache_key,
            json.dumps({
                sym: {k: round(v, 4) for k, v in factors.items()}
                for sym, factors in scores.items()
            }),
            ex=3600,
        )

        # Generate signals
        generator = SignalGenerator(
            redis_client=redis_client,
            db_session=db.session,
            asset_class=asset_class,
        )
        signals = generator.run(symbols)

        # Cache signals
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

        sig_cache_key = f'cache:signals:{asset_class}:latest' if asset_class == 'stock' else 'cache:signals:latest'
        redis_client.set(sig_cache_key, json.dumps(groups), ex=3600)

        return jsonify({
            'status': 'completed',
            'mode': 'sync',
            'asset_class': asset_class,
            'symbols': len(symbols),
            'signals': len(signals),
        })

    # Async
    try:
        if asset_class == 'stock':
            from app.data.fundamentals_tasks import compute_stock_factors
            task = compute_stock_factors.delay()
        else:
            from app.data.tasks import compute_all_factors
            task = compute_all_factors.delay()
        return jsonify({'status': 'dispatched', 'asset_class': asset_class, 'task_id': task.id})
    except Exception:
        return jsonify({'error': 'Celery not available, use sync=true'}), 503


@api_bp.route('/control/force_liquidate/<asset_class>', methods=['POST'])
@api_login_required
def force_liquidate_asset_class(asset_class: str):
    """Force liquidate all positions for a specific asset class.

    Body: {"confirm": true}
    """
    if asset_class not in ('etf', 'stock'):
        return jsonify({'error': 'asset_class must be etf or stock'}), 400

    data = request.get_json(silent=True) or {}
    if not data.get('confirm'):
        return jsonify({
            'error': 'Confirmation required',
            'message': f'Send {{"confirm": true}} to liquidate all {asset_class} positions',
        }), 400

    from app.models.positions import Position

    positions = Position.query.filter_by(
        asset_class=asset_class, is_open=True,
    ).all()

    if not positions:
        return jsonify({'status': 'ok', 'message': f'No open {asset_class} positions'})

    # Set kill switch
    redis_client.set(f'kill_switch:trading:{asset_class}', '1')

    # Build liquidation orders
    orders = []
    for p in positions:
        orders.append({
            'symbol': p.symbol,
            'side': 'sell' if p.side == 'long' else 'buy',
            'notional': float(p.market_value or 0),
            'asset_class': asset_class,
            'reason': 'force_liquidate',
        })

    # Push to approved queue
    if orders:
        redis_client.lpush(
            'channel:orders_approved',
            json.dumps({'orders': orders}),
        )

    return jsonify({
        'status': 'liquidating',
        'asset_class': asset_class,
        'positions': len(orders),
        'kill_switch_set': True,
    })


# =====================================================================
# HELPERS
# =====================================================================

def _is_fundamentals_stale(stock) -> bool:
    """Check if fundamental data is older than 7 days."""
    if stock.fundamentals_updated_at is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    updated = stock.fundamentals_updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated < cutoff
