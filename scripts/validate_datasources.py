"""
Paisabot — Data Source & Format Validation Script
==================================================
Validates connectivity and data formats for every service the backend
pipeline depends on. Run from the project root:

    python scripts/validate_datasources.py

Checks are grouped by layer:
  1. Infrastructure  — Redis (db 0/1/2), PostgreSQL
  2. DB schema       — all required tables and critical columns
  3. Redis key map   — keys the pipeline reads at runtime
  4. Alpaca API      — auth + bar/quote format
  5. VIX / FRED      — format of 252-day history cache
  6. CBOE P/C ratio  — format of 10-day MA cache
  7. Pipeline data   — ETF universe populated, signals exist

Results are printed as PASS / WARN / FAIL with detail.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

# ── project root on path ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

# ── colour helpers ────────────────────────────────────────────────────
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

_results: list[tuple[str, str, str]] = []  # (status, label, detail)


def _print(status: str, label: str, detail: str = '') -> None:
    colour = {PASS: GREEN, WARN: YELLOW, FAIL: RED}[status]
    icon   = {PASS: '✓', WARN: '!', FAIL: '✗'}[status]
    tag    = f'{colour}{BOLD}[{icon} {status}]{RESET}'
    print(f'  {tag}  {label}' + (f'  — {detail}' if detail else ''))
    _results.append((status, label, detail))


PASS = 'PASS'
WARN = 'WARN'
FAIL = 'FAIL'


def section(title: str) -> None:
    print(f'\n{CYAN}{BOLD}━━━  {title}  ━━━{RESET}')


# ═══════════════════════════════════════════════════════════════════
# 1. INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════

def check_redis() -> 'redis.Redis | None':
    section('1. Infrastructure — Redis')
    import redis as redis_lib

    url = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
    try:
        r = redis_lib.from_url(url, decode_responses=True, socket_timeout=3)
        info = r.info('server')
        version = info.get('redis_version', '?')
        _print(PASS, f'Redis app  (db 0)  {url}', f'v{version}')
        return r
    except Exception as exc:
        _print(FAIL, f'Redis app  (db 0)  {url}', str(exc))
        return None


def check_redis_celery() -> None:
    import redis as redis_lib

    for label, env_key, default in [
        ('Redis broker  (db 1)', 'CELERY_BROKER_URL',   'redis://127.0.0.1:6379/1'),
        ('Redis results (db 2)', 'CELERY_RESULT_BACKEND', 'redis://127.0.0.1:6379/2'),
    ]:
        url = os.environ.get(env_key, default)
        try:
            r = redis_lib.from_url(url, decode_responses=True, socket_timeout=3)
            r.ping()
            _print(PASS, label, url)
        except Exception as exc:
            _print(FAIL, label, str(exc))


def check_postgres() -> 'sqlalchemy.engine.Engine | None':
    section('1. Infrastructure — PostgreSQL')
    try:
        from sqlalchemy import create_engine, text
        url = os.environ.get('DATABASE_URL',
                             'postgresql://paisabot:paisabot@127.0.0.1:5432/paisabot')
        engine = create_engine(url, connect_args={'connect_timeout': 5})
        with engine.connect() as conn:
            row = conn.execute(text('SELECT version()')).fetchone()
            ver = row[0].split(',')[0] if row else '?'
        _print(PASS, f'PostgreSQL  {url}', ver)
        return engine
    except Exception as exc:
        _print(FAIL, 'PostgreSQL', str(exc))
        return None


# ═══════════════════════════════════════════════════════════════════
# 2. DB SCHEMA
# ═══════════════════════════════════════════════════════════════════

REQUIRED_TABLES = {
    'etf_universe': [
        'symbol', 'name', 'sector', 'is_active', 'in_active_set',
        'aum_bn', 'avg_daily_vol_m', 'spread_est_bps',
    ],
    'price_bars': [
        'symbol', 'timeframe', 'timestamp', 'open', 'high', 'low',
        'close', 'volume', 'vwap', 'is_synthetic', 'source',
    ],
    'signals': [
        'symbol', 'signal_time', 'composite_score', 'trend_score',
        'volatility_score', 'sentiment_score', 'breadth_score',
        'dispersion_score', 'liquidity_score', 'regime', 'signal_type',
    ],
    'positions': [
        'symbol', 'weight', 'notional', 'sector', 'status',
        'unrealized_pnl', 'realized_pnl',
    ],
    'trades': [
        'symbol', 'side', 'notional', 'fill_price', 'slippage_bps',
    ],
    'factor_scores': ['symbol', 'calc_time'],
    'performance_metrics': ['date', 'drawdown'],
    'system_config': ['category', 'key', 'value'],
}


def check_schema(engine) -> None:
    section('2. DB Schema')
    if engine is None:
        _print(WARN, 'Schema check skipped', 'PostgreSQL unavailable')
        return

    from sqlalchemy import inspect, text
    insp = inspect(engine)
    existing = set(insp.get_table_names())

    for table, required_cols in REQUIRED_TABLES.items():
        if table not in existing:
            _print(FAIL, f'Table: {table}', 'missing')
            continue

        actual_cols = {c['name'] for c in insp.get_columns(table)}
        missing_cols = [c for c in required_cols if c not in actual_cols]
        if missing_cols:
            _print(WARN, f'Table: {table}', f'missing columns: {missing_cols}')
        else:
            # Quick row count
            with engine.connect() as conn:
                count = conn.execute(
                    text(f'SELECT COUNT(*) FROM {table}')
                ).scalar()
            _print(PASS, f'Table: {table}', f'{count:,} rows')


# ═══════════════════════════════════════════════════════════════════
# 3. REDIS KEY MAP
# ═══════════════════════════════════════════════════════════════════

# (key_pattern, description, required_type, required)
REDIS_KEY_CHECKS = [
    # Config hashes — must exist for pipeline to run
    ('config:weights',            'Factor weights hash',          'hash',   True),
    ('config:portfolio',          'Portfolio constraints hash',   'hash',   True),
    ('config:risk',               'Risk params hash',             'hash',   True),
    ('config:universe',           'Universe filter hash',         'hash',   True),
    ('config:execution',          'Execution settings hash',      'hash',   True),
    # Kill switches — should be 0 / absent in normal operation
    ('kill_switch:trading',       'Kill switch: trading',         'string', False),
    ('kill_switch:rebalance',     'Kill switch: rebalance',       'string', False),
    ('kill_switch:all',           'Kill switch: all',             'string', False),
    # Runtime caches — populated after first pipeline run
    ('cache:signals:latest',      'Latest signals cache',         'string', False),
    ('cache:scores:latest',       'Latest factor scores cache',   'string', False),
    ('cache:regime:current',      'Current regime cache',         'string', False),
    ('cache:pipeline:latest',     'Last pipeline run summary',    'string', False),
    ('cache:portfolio:current',   'Portfolio positions cache',    'string', False),
    # Data caches — populated by refresh tasks
    ('vix:latest',                'VIX latest value',             'string', False),
    ('vix:history_252',           'VIX 252-day history',          'string', False),
    ('options:equity:pc_ratio',   'CBOE P/C ratio (10d MA)',      'string', False),
    ('options:equity:pc_history', 'CBOE P/C 252-day history',     'string', False),
]

KILL_SWITCH_KEYS = {
    'kill_switch:trading', 'kill_switch:rebalance', 'kill_switch:all',
    'kill_switch:force_liquidate',
}


def check_redis_keys(r) -> None:
    section('3. Redis Key Map')
    if r is None:
        _print(WARN, 'Redis key checks skipped', 'Redis unavailable')
        return

    for key, description, expected_type, required in REDIS_KEY_CHECKS:
        try:
            key_type = r.type(key)   # 'none', 'string', 'hash', 'list', ...
            if key_type == 'none':
                if required:
                    _print(FAIL, description, f'key missing: {key}')
                else:
                    _print(WARN, description, f'not yet populated: {key}')
                continue

            # Type mismatch
            if key_type != expected_type:
                _print(FAIL, description,
                       f'{key}  type={key_type}  expected={expected_type}')
                continue

            # Kill switch safety check — if active, warn operator
            if key in KILL_SWITCH_KEYS:
                val = r.get(key)
                if val == '1':
                    _print(WARN, description, f'ACTIVE ({key}={val}) — pipeline halted')
                else:
                    _print(PASS, description, f'{key}=0  (inactive)')
                continue

            # Validate JSON caches are parseable and non-empty
            if expected_type == 'string' and 'cache:' in key:
                raw = r.get(key)
                try:
                    parsed = json.loads(raw)
                    size = len(parsed) if isinstance(parsed, (dict, list)) else 1
                    ttl = r.ttl(key)
                    _print(PASS, description, f'{size} entries  TTL={ttl}s')
                except json.JSONDecodeError:
                    _print(FAIL, description, f'invalid JSON in {key}')
                continue

            # VIX history — should be a JSON list of floats
            if key == 'vix:history_252':
                raw = r.get(key)
                try:
                    hist = json.loads(raw)
                    if not isinstance(hist, list) or len(hist) < 50:
                        _print(WARN, description, f'only {len(hist)} values (need 252)')
                    else:
                        _print(PASS, description, f'{len(hist)} values  latest={hist[-1]}')
                except Exception:
                    _print(FAIL, description, 'invalid JSON')
                continue

            # CBOE P/C history — same
            if key == 'options:equity:pc_history':
                raw = r.get(key)
                try:
                    hist = json.loads(raw)
                    if not isinstance(hist, list) or len(hist) < 10:
                        _print(WARN, description, f'only {len(hist)} values')
                    else:
                        _print(PASS, description, f'{len(hist)} values  latest MA10={hist[-1]}')
                except Exception:
                    _print(FAIL, description, 'invalid JSON')
                continue

            # Generic: just confirm it exists and has a value
            if expected_type == 'string':
                val = r.get(key)
                ttl = r.ttl(key)
                _print(PASS, description, f'val={str(val)[:40]}  TTL={ttl}s')
            elif expected_type == 'hash':
                length = r.hlen(key)
                ttl = r.ttl(key)
                _print(PASS, description, f'{length} fields  TTL={ttl}s')

        except Exception as exc:
            _print(FAIL, description, str(exc))


# ═══════════════════════════════════════════════════════════════════
# 4. ALPACA API
# ═══════════════════════════════════════════════════════════════════

def check_alpaca() -> None:
    section('4. Alpaca API')
    api_key    = os.environ.get('ALPACA_API_KEY', '')
    secret_key = os.environ.get('ALPACA_SECRET_KEY', '')

    if not api_key or not secret_key:
        _print(FAIL, 'Alpaca credentials', 'ALPACA_API_KEY / ALPACA_SECRET_KEY not set')
        return

    masked = f'{api_key[:4]}...{api_key[-4:]}' if len(api_key) > 8 else '****'
    _print(PASS, 'Alpaca credentials present', f'key={masked}')

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import datetime, timezone

        client = StockHistoricalDataClient(api_key, secret_key)

        # ── bar format check ──────────────────────────────────────
        end   = date.today() - timedelta(days=1)
        start = end - timedelta(days=5)
        req = StockBarsRequest(
            symbol_or_symbols='SPY',
            timeframe=TimeFrame.Day,
            start=datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc),
            end=datetime.combine(end,   datetime.min.time()).replace(tzinfo=timezone.utc),
        )
        bars_response = client.get_stock_bars(req)
        df = bars_response.df

        if df.empty:
            _print(WARN, 'Alpaca daily bars (SPY)', 'empty response — market closed or bad dates')
        else:
            required_cols = {'open', 'high', 'low', 'close', 'volume'}
            actual_cols   = set(df.reset_index().columns)
            missing       = required_cols - actual_cols
            if missing:
                _print(FAIL, 'Alpaca bar schema', f'missing columns: {missing}')
            else:
                row = df.reset_index().iloc[-1]
                _print(PASS, 'Alpaca daily bars (SPY)',
                       f'{len(df)} bars  close={float(row.close):.2f}  '
                       f'vol={int(row.volume):,}')

            # Type checks
            bad_types = []
            for col in ['open', 'high', 'low', 'close']:
                try:
                    float(df[col].iloc[-1])
                except (ValueError, TypeError):
                    bad_types.append(col)
            if bad_types:
                _print(FAIL, 'Alpaca bar types', f'non-numeric: {bad_types}')
            else:
                _print(PASS, 'Alpaca bar types', 'OHLCV all numeric — matches PriceBar schema')

        # ── quote format check ────────────────────────────────────
        try:
            quote_req = StockLatestQuoteRequest(symbol_or_symbols='SPY')
            quote_resp = client.get_stock_latest_quote(quote_req)
            quote = quote_resp.get('SPY')
            if quote:
                bp = getattr(quote, 'bid_price', None)
                ap = getattr(quote, 'ask_price', None)
                if bp and ap and ap > bp:
                    spread_bps = ((ap - bp) / ((ap + bp) / 2)) * 10_000
                    _print(PASS, 'Alpaca latest quote (SPY)',
                           f'bid={bp:.2f}  ask={ap:.2f}  spread={spread_bps:.1f}bps')
                else:
                    _print(WARN, 'Alpaca latest quote (SPY)',
                           f'bid={bp}  ask={ap}  — check market hours')
            else:
                _print(WARN, 'Alpaca latest quote (SPY)', 'no quote returned')
        except Exception as exc:
            _print(WARN, 'Alpaca latest quote', str(exc))

    except ImportError:
        _print(FAIL, 'alpaca-py package', 'not installed — run: pip install alpaca-py')
    except Exception as exc:
        _print(FAIL, 'Alpaca API call', str(exc))


# ═══════════════════════════════════════════════════════════════════
# 5. VIX / FRED
# ═══════════════════════════════════════════════════════════════════

def _fred_fetch(series: str, start: date, end: date) -> 'pd.DataFrame':
    """Fetch a FRED series via direct HTTP (avoids pandas_datareader/pandas 3.x compat issues)."""
    import requests
    import pandas as pd

    url = (
        f'https://api.stlouisfed.org/fred/series/observations'
        f'?series_id={series}'
        f'&observation_start={start}'
        f'&observation_end={end}'
        f'&file_type=json'
        f'&api_key=noanrealkey'  # FRED allows anonymous reads on public series via browser
    )
    # FRED also exposes a no-auth CSV endpoint
    csv_url = (
        f'https://fred.stlouisfed.org/graph/fredgraph.csv'
        f'?id={series}&vintage_date={end}'
    )
    resp = requests.get(csv_url, timeout=15,
                        headers={'User-Agent': 'paisabot/1.0 (validation)'})
    resp.raise_for_status()

    import io
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = ['date', series]
    df['date'] = pd.to_datetime(df['date'])
    df[series] = pd.to_numeric(df[series], errors='coerce')
    df = df.dropna(subset=[series])
    df = df[(df['date'] >= pd.Timestamp(start)) & (df['date'] <= pd.Timestamp(end))]
    return df.reset_index(drop=True)


def check_vix() -> None:
    section('5. VIX / FRED Data Source')
    try:
        end   = date.today()
        start = end - timedelta(days=10)
        df = _fred_fetch('VIXCLS', end - timedelta(days=400), end)

        if df.empty:
            _print(WARN, 'FRED VIXCLS series', 'empty — weekend or holiday or network blocked')
            return

        latest_val  = float(df['VIXCLS'].iloc[-1])
        latest_date = df['date'].iloc[-1].date()

        if not (5.0 <= latest_val <= 150.0):
            _print(WARN, 'VIX value range', f'{latest_val} outside expected [5,150]')
        else:
            _print(PASS, 'FRED VIXCLS series', f'VIX={latest_val}  date={latest_date}')

        _print(PASS, 'VIX DataFrame format',
               f'shape={df.shape}  columns={list(df.columns)}')

        hist = df['VIXCLS'].tail(252).tolist()
        if len(hist) < 100:
            _print(WARN, 'VIX 252-day history', f'only {len(hist)} trading days available')
        else:
            _print(PASS, 'VIX 252-day history',
                   f'{len(hist)} values  min={min(hist):.1f}  max={max(hist):.1f}')

    except Exception as exc:
        _print(FAIL, 'VIX/FRED fetch', str(exc))


# ═══════════════════════════════════════════════════════════════════
# 6. CBOE PUT/CALL
# ═══════════════════════════════════════════════════════════════════

def check_cboe() -> None:
    section('6. CBOE Put/Call Ratio')
    import requests
    from io import StringIO
    import pandas as pd

    CBOE_URL = (
        'https://cdn.cboe.com/data/us/options/market_statistics'
        '/daily_volume/equity_put_call_ratio.csv'
    )

    try:
        resp = requests.get(CBOE_URL, timeout=15,
                            headers={'User-Agent': 'paisabot/1.0 (validation)'})
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))

        _print(PASS, 'CBOE CDN CSV endpoint', f'HTTP {resp.status_code}  rows={len(df)}')

        # Check columns
        cols_lower = [c.lower().strip() for c in df.columns]
        has_date  = any(c in ('date', 'trade_date', 'trade date') for c in cols_lower)
        has_ratio = any('ratio' in c or 'p/c' in c or 'put' in c for c in cols_lower)

        if not has_date or not has_ratio:
            _print(WARN, 'CBOE CSV column detection',
                   f'columns={list(df.columns)}  — positional fallback will be used')
        else:
            _print(PASS, 'CBOE CSV column detection',
                   f'date_col={df.columns[0]}  ratio_col={df.columns[-1]}')

        # Parse and validate the P/C ratio range
        try:
            ratio_col = df.columns[-1]
            ratios = pd.to_numeric(df[ratio_col], errors='coerce').dropna()
            latest = float(ratios.iloc[-1])
            if not (0.3 <= latest <= 3.0):
                _print(WARN, 'CBOE P/C ratio value', f'{latest} outside normal [0.3,3.0]')
            else:
                _print(PASS, 'CBOE P/C ratio value', f'latest={latest:.3f}  rows={len(ratios)}')
        except Exception as exc:
            _print(FAIL, 'CBOE P/C parse', str(exc))

    except requests.exceptions.HTTPError as exc:
        _print(WARN, 'CBOE CDN endpoint', f'HTTP {exc.response.status_code} — FRED fallback will trigger')
        _check_cboe_fred_fallback()
    except Exception as exc:
        _print(FAIL, 'CBOE CDN endpoint', str(exc))
        _check_cboe_fred_fallback()


def _check_cboe_fred_fallback() -> None:
    try:
        end   = date.today()
        df = _fred_fetch('EQUITYPC', end - timedelta(days=400), end)
        if df.empty:
            _print(WARN, 'CBOE FRED fallback (EQUITYPC)', 'empty')
        else:
            _print(PASS, 'CBOE FRED fallback (EQUITYPC)',
                   f'{len(df)} rows  latest={float(df["EQUITYPC"].iloc[-1]):.3f}')
    except Exception as exc:
        _print(FAIL, 'CBOE FRED fallback', str(exc))


# ═══════════════════════════════════════════════════════════════════
# 7. PIPELINE DATA READINESS
# ═══════════════════════════════════════════════════════════════════

def check_pipeline_data(engine, r) -> None:
    section('7. Pipeline Data Readiness')

    if engine is None:
        _print(WARN, 'Pipeline data checks skipped', 'PostgreSQL unavailable')
        return

    from sqlalchemy import text

    with engine.connect() as conn:
        # ETF universe
        total = conn.execute(text('SELECT COUNT(*) FROM etf_universe')).scalar()
        active = conn.execute(
            text("SELECT COUNT(*) FROM etf_universe WHERE is_active = true")
        ).scalar()
        in_pipeline = conn.execute(
            text("SELECT COUNT(*) FROM etf_universe WHERE in_active_set = true")
        ).scalar()

        if total == 0:
            _print(FAIL, 'ETF universe', 'empty — run: python scripts/universe_setup.py')
        elif in_pipeline == 0:
            _print(WARN, 'ETF universe — active set',
                   f'{total} ETFs total, {active} active, but 0 in_active_set=true '
                   f'(pipeline has nothing to trade)')
        else:
            _print(PASS, 'ETF universe',
                   f'{total} total  {active} active  {in_pipeline} in pipeline')

        # price_bars depth
        bar_count = conn.execute(text('SELECT COUNT(*) FROM price_bars')).scalar()
        if bar_count == 0:
            _print(FAIL, 'price_bars', 'empty — run: python scripts/backfill_history.py')
        else:
            latest_bar = conn.execute(
                text("SELECT MAX(timestamp) FROM price_bars WHERE timeframe='1d'")
            ).scalar()
            # Check if bars are stale (older than 5 trading days)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            if latest_bar:
                if hasattr(latest_bar, 'tzinfo') and latest_bar.tzinfo is None:
                    from datetime import timezone as tz
                    latest_bar = latest_bar.replace(tzinfo=tz.utc)
                age_days = (now - latest_bar).days
                if age_days > 7:
                    _print(WARN, 'price_bars', f'{bar_count:,} rows  latest={latest_bar.date()}  ({age_days}d stale)')
                else:
                    _print(PASS, 'price_bars', f'{bar_count:,} rows  latest={latest_bar.date()}  ({age_days}d ago)')
            else:
                _print(PASS, 'price_bars', f'{bar_count:,} rows')

        # Per-symbol bar coverage for in_active_set ETFs
        if in_pipeline > 0:
            rows = conn.execute(text("""
                SELECT u.symbol, COUNT(p.id) as bar_count
                FROM etf_universe u
                LEFT JOIN price_bars p ON p.symbol = u.symbol AND p.timeframe = '1d'
                WHERE u.in_active_set = true
                GROUP BY u.symbol
                ORDER BY bar_count ASC
                LIMIT 5
            """)).fetchall()
            thin = [(r[0], r[1]) for r in rows if r[1] < 50]
            if thin:
                _print(WARN, 'Bar coverage (bottom 5 symbols)',
                       '  '.join(f'{s}:{n}bars' for s, n in thin))
            else:
                min_bars = min(r[1] for r in rows)
                _print(PASS, 'Bar coverage (all pipeline ETFs)',
                       f'min {min_bars} bars per symbol')

        # Latest signals
        sig_count = conn.execute(text('SELECT COUNT(*) FROM signals')).scalar()
        if sig_count == 0:
            _print(WARN, 'signals table', 'empty — run factor compute task first')
        else:
            latest_sig = conn.execute(
                text('SELECT MAX(signal_time), COUNT(DISTINCT symbol) FROM signals')
            ).fetchone()
            _print(PASS, 'signals table',
                   f'{sig_count:,} rows  latest={latest_sig[0]}  {latest_sig[1]} symbols')

        # system_config seeded?
        cfg_count = conn.execute(text('SELECT COUNT(*) FROM system_config')).scalar()
        if cfg_count == 0:
            _print(FAIL, 'system_config', 'empty — run: python scripts/seed_config.py')
        else:
            _print(PASS, 'system_config', f'{cfg_count} rows seeded')

    # Redis: confirm config:weights is set for composite scorer
    if r:
        weights = r.hgetall('config:weights') if r.type('config:weights') == 'hash' else {}
        if not weights:
            _print(WARN, 'config:weights (Redis)', 'not set — composite scorer uses hard-coded defaults')
        else:
            total_w = sum(float(v) for v in weights.values() if _is_weight_key(k) for k, v in [('k', v)])
            # simpler approach
            weight_vals = {k: float(v) for k, v in weights.items() if k.startswith('weight_')}
            total_w = sum(weight_vals.values())
            if weight_vals and abs(total_w - 1.0) > 0.05:
                _print(WARN, 'config:weights sum', f'{total_w:.3f} (should sum to 1.0)')
            else:
                _print(PASS, 'config:weights (Redis)',
                       '  '.join(f'{k}={v:.2f}' for k, v in sorted(weight_vals.items())))


def _is_weight_key(k: str) -> bool:
    return k.startswith('weight_')


# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════

def print_summary() -> int:
    passes  = sum(1 for s, _, _ in _results if s == PASS)
    warns   = sum(1 for s, _, _ in _results if s == WARN)
    fails   = sum(1 for s, _, _ in _results if s == FAIL)
    total   = len(_results)

    print(f'\n{BOLD}{"═"*60}{RESET}')
    print(f'{BOLD}  SUMMARY  {RESET}')
    print(f'{"─"*60}')
    print(f'  {GREEN}{BOLD}{passes:>3} PASS{RESET}  '
          f'{YELLOW}{BOLD}{warns:>3} WARN{RESET}  '
          f'{RED}{BOLD}{fails:>3} FAIL{RESET}   '
          f'({total} checks)')
    print(f'{"─"*60}')

    if fails > 0:
        print(f'{RED}{BOLD}  FAILED checks:{RESET}')
        for s, label, detail in _results:
            if s == FAIL:
                print(f'    {RED}✗ {label}{RESET}' + (f'  — {detail}' if detail else ''))

    if warns > 0:
        print(f'{YELLOW}{BOLD}  WARNINGS (pipeline may still start):{RESET}')
        for s, label, detail in _results:
            if s == WARN:
                print(f'    {YELLOW}! {label}{RESET}' + (f'  — {detail}' if detail else ''))

    print(f'{"═"*60}\n')

    if fails > 0:
        print(f'{RED}{BOLD}  ✗ System NOT ready — fix FAIL items before starting pipeline{RESET}\n')
    elif warns > 0:
        print(f'{YELLOW}{BOLD}  ! System partially ready — WARNs may degrade factor quality{RESET}\n')
    else:
        print(f'{GREEN}{BOLD}  ✓ All checks passed — system ready{RESET}\n')

    return 1 if fails > 0 else 0


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    print(f'\n{BOLD}Paisabot — Data Source & Format Validation{RESET}')
    print(f'{"═"*60}')
    print(f'  Date: {date.today()}  |  Env: {os.environ.get("FLASK_ENV", "development")}')

    r      = check_redis()
    check_redis_celery()
    engine = check_postgres()
    check_schema(engine)
    check_redis_keys(r)
    check_alpaca()
    check_vix()
    check_cboe()
    check_pipeline_data(engine, r)

    return print_summary()


if __name__ == '__main__':
    sys.exit(main())
