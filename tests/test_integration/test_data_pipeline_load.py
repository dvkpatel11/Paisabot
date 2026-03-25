"""Integration test: realistic mock data provider → fetch → ingest → stream.

Generates ~260 trading days × 20 ETFs of realistic OHLCV data behind a mock
DataProvider, then exercises the full fetch → DB ingest → Redis cache → pub/sub
stream pipeline.  Collects and prints wall-clock stats for every stage.

Run:
    pytest tests/test_integration/test_data_pipeline_load.py -v -s
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import fakeredis
import pytest

from app import create_app
from app.extensions import db as _db
from app.data.base import DataProvider
from app.data.ingestion import ingest_daily_bars, update_redis_cache, detect_gaps
from app.models.price_bars import PriceBar


# ── realistic ETF universe (from research/ETF_universe.csv) ──────────

ETF_UNIVERSE = [
    ('SPY', 470.0, 25_000_000),
    ('QQQ', 400.0, 12_000_000),
    ('IWM', 200.0, 3_500_000),
    ('XLK', 190.0, 1_800_000),
    ('XLF', 38.0, 2_200_000),
    ('XLE', 80.0, 1_500_000),
    ('XLV', 140.0, 1_000_000),
    ('XLI', 110.0, 700_000),
    ('XLC', 75.0, 600_000),
    ('XLY', 180.0, 650_000),
    ('XLP', 75.0, 450_000),
    ('XLU', 68.0, 400_000),
    ('XLRE', 40.0, 300_000),
    ('XLB', 85.0, 280_000),
    ('GDX', 30.0, 700_000),
    ('EEM', 40.0, 800_000),
    ('EFA', 75.0, 600_000),
    ('TLT', 95.0, 2_000_000),
    ('HYG', 75.0, 600_000),
    ('GLD', 185.0, 900_000),
]

SYMBOLS = [s for s, _, _ in ETF_UNIVERSE]
DATE_START = date(2025, 1, 2)
DATE_END = date(2025, 12, 31)


# ── mock data provider ───────────────────────────────────────────────

class MockMarketDataProvider(DataProvider):
    """In-process mock that serves realistic GBM-generated OHLCV data.

    Uses per-symbol deterministic seeds so the same (symbol, date_range)
    always produces identical data — critical for deduplication tests.
    Tracks every call for stats collection.
    """

    def __init__(self, base_seed: int = 42):
        self._base_seed = base_seed
        self._call_log: list[dict] = []
        self._latency_ms: float = 0.5  # simulated per-call overhead
        # Separate RNG for non-deterministic calls (quotes, latest bars)
        self._rng = np.random.RandomState(base_seed)

    @property
    def call_log(self) -> list[dict]:
        return list(self._call_log)

    @property
    def total_calls(self) -> int:
        return len(self._call_log)

    # ── DataProvider interface ───────────────────────────────────────

    def get_daily_bars(
        self, symbol: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        t0 = time.perf_counter()
        time.sleep(self._latency_ms / 1000)

        ref_price, avg_vol = self._lookup(symbol)
        df = self._generate_bars(symbol, ref_price, avg_vol, start_date, end_date)

        elapsed = (time.perf_counter() - t0) * 1000
        self._call_log.append({
            'method': 'get_daily_bars',
            'symbol': symbol,
            'rows': len(df),
            'elapsed_ms': round(elapsed, 2),
        })
        return df

    def get_latest_bar(self, symbol: str) -> dict | None:
        t0 = time.perf_counter()
        time.sleep(self._latency_ms / 1000)

        ref_price, avg_vol = self._lookup(symbol)
        now = datetime.now(timezone.utc)
        bar = {
            'symbol': symbol,
            'timestamp': now,
            'open': ref_price,
            'high': round(ref_price * 1.005, 4),
            'low': round(ref_price * 0.995, 4),
            'close': round(ref_price * (1 + self._rng.normal(0, 0.002)), 4),
            'volume': int(avg_vol * self._rng.uniform(0.8, 1.2)),
        }

        elapsed = (time.perf_counter() - t0) * 1000
        self._call_log.append({
            'method': 'get_latest_bar',
            'symbol': symbol,
            'rows': 1,
            'elapsed_ms': round(elapsed, 2),
        })
        return bar

    def get_latest_quote(self, symbol: str) -> dict | None:
        t0 = time.perf_counter()
        time.sleep(self._latency_ms / 1000)

        ref_price, _ = self._lookup(symbol)
        spread_bps = self._rng.uniform(0.3, 2.0)
        half_spread = ref_price * spread_bps / 10000
        quote = {
            'bid': round(ref_price - half_spread, 4),
            'ask': round(ref_price + half_spread, 4),
            'mid': ref_price,
            'spread_bps': round(spread_bps, 2),
            'timestamp': datetime.now(timezone.utc),
        }

        elapsed = (time.perf_counter() - t0) * 1000
        self._call_log.append({
            'method': 'get_latest_quote',
            'symbol': symbol,
            'rows': 1,
            'elapsed_ms': round(elapsed, 2),
        })
        return quote

    def get_multi_bars(
        self, symbols: list[str], start_date: date, end_date: date,
    ) -> dict[str, pd.DataFrame]:
        t0 = time.perf_counter()
        time.sleep(self._latency_ms / 1000 * 2)

        result = {}
        total_rows = 0
        for sym in symbols:
            ref_price, avg_vol = self._lookup(sym)
            df = self._generate_bars(sym, ref_price, avg_vol, start_date, end_date)
            result[sym] = df
            total_rows += len(df)

        elapsed = (time.perf_counter() - t0) * 1000
        self._call_log.append({
            'method': 'get_multi_bars',
            'symbol': f'{len(symbols)}_symbols',
            'rows': total_rows,
            'elapsed_ms': round(elapsed, 2),
        })
        return result

    # ── internals ────────────────────────────────────────────────────

    def _lookup(self, symbol: str) -> tuple[float, int]:
        for sym, price, vol in ETF_UNIVERSE:
            if sym == symbol:
                return price, vol
        return 100.0, 500_000

    def _symbol_seed(self, symbol: str) -> int:
        """Deterministic seed per symbol so repeated calls produce same data."""
        return self._base_seed + hash(symbol) % 2**31

    def _generate_bars(
        self,
        symbol: str,
        ref_price: float,
        avg_vol: int,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Generate realistic daily OHLCV using geometric Brownian motion.

        Uses a per-symbol RNG seed so the same (symbol, date_range)
        always produces identical output.
        """
        rng = np.random.RandomState(self._symbol_seed(symbol))

        trading_dates = []
        d = start_date
        while d <= end_date:
            if d.weekday() < 5:
                trading_dates.append(d)
            d += timedelta(days=1)

        n = len(trading_dates)
        if n == 0:
            return pd.DataFrame()

        # GBM: daily returns with realistic vol (~16% annualized)
        daily_vol = 0.16 / np.sqrt(252)
        drift = 0.08 / 252
        returns = rng.normal(drift, daily_vol, n)
        prices = ref_price * np.exp(np.cumsum(returns))

        rows = []
        for i, dt in enumerate(trading_dates):
            close = round(float(prices[i]), 4)
            intraday_range = close * rng.uniform(0.005, 0.02)
            high = round(close + intraday_range * rng.uniform(0.3, 0.7), 4)
            low = round(close - intraday_range * rng.uniform(0.3, 0.7), 4)
            opn = round(low + (high - low) * rng.uniform(0.2, 0.8), 4)
            vol = int(avg_vol * rng.uniform(0.5, 2.0))
            vwap = round((high + low + close) / 3, 4)

            rows.append({
                'timestamp': datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc),
                'open': opn,
                'high': high,
                'low': low,
                'close': close,
                'volume': vol,
                'vwap': vwap,
                'trade_count': int(vol * rng.uniform(0.01, 0.03)),
            })

        return pd.DataFrame(rows)


# ── stats collector ──────────────────────────────────────────────────

class PipelineStats:
    """Collects timing and throughput stats across pipeline stages."""

    def __init__(self):
        self.stages: dict[str, dict[str, Any]] = {}

    def record(self, stage: str, **kwargs):
        self.stages[stage] = kwargs

    def summary(self) -> str:
        lines = [
            '',
            '=' * 72,
            '  DATA PIPELINE LOAD TEST — RESULTS',
            '=' * 72,
        ]
        for stage, data in self.stages.items():
            lines.append(f'\n  ── {stage} ──')
            for k, v in data.items():
                if isinstance(v, float):
                    lines.append(f'    {k:.<40s} {v:>10.2f}')
                else:
                    lines.append(f'    {k:.<40s} {str(v):>10s}')
        lines.append('\n' + '=' * 72)
        return '\n'.join(lines)


# ── module-scoped fixtures (DB persists across all tests) ────────────

@pytest.fixture(scope='module')
def app():
    """Module-scoped Flask app — single DB for all tests in this file."""
    app = create_app('testing')
    with app.app_context():
        yield app


@pytest.fixture(scope='module')
def setup_db(app):
    """Create tables once, drop at module teardown."""
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        yield _db
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture(scope='module')
def provider():
    return MockMarketDataProvider(base_seed=42)


@pytest.fixture(scope='module')
def stats():
    return PipelineStats()


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


# ── tests (run in order) ─────────────────────────────────────────────

class TestDataPipelineLoad:
    """End-to-end load test: fetch → ingest → cache → stream."""

    # ── Stage 1: Bulk historical fetch via get_daily_bars ────────────

    def test_01_bulk_fetch_individual(self, provider, stats, app, setup_db):
        """Fetch 260 days x 20 ETFs one-by-one (simulates backfill task)."""
        t0 = time.perf_counter()
        all_frames: dict[str, pd.DataFrame] = {}
        for sym in SYMBOLS:
            df = provider.get_daily_bars(sym, DATE_START, DATE_END)
            all_frames[sym] = df
        elapsed_ms = (time.perf_counter() - t0) * 1000

        total_rows = sum(len(df) for df in all_frames.values())
        api_calls = provider.total_calls

        stats.record('1_bulk_fetch_individual', **{
            'symbols': str(len(SYMBOLS)),
            'api_calls': str(api_calls),
            'total_rows_fetched': str(total_rows),
            'wall_clock_ms': elapsed_ms,
            'avg_ms_per_call': elapsed_ms / api_calls if api_calls else 0,
            'rows_per_second': total_rows / (elapsed_ms / 1000) if elapsed_ms else 0,
        })

        assert len(all_frames) == 20
        for sym, df in all_frames.items():
            assert len(df) >= 250, f'{sym} got {len(df)} rows'
            assert set(df.columns) >= {'timestamp', 'open', 'high', 'low', 'close', 'volume'}

    # ── Stage 2: Batch fetch via get_multi_bars ──────────────────────

    def test_02_batch_fetch(self, provider, stats, app, setup_db):
        """Fetch all 20 ETFs in a single batch call."""
        calls_before = provider.total_calls
        t0 = time.perf_counter()
        result = provider.get_multi_bars(SYMBOLS, DATE_START, DATE_END)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        total_rows = sum(len(df) for df in result.values())
        batch_calls = provider.total_calls - calls_before

        stats.record('2_batch_fetch', **{
            'symbols': str(len(SYMBOLS)),
            'api_calls': str(batch_calls),
            'total_rows_fetched': str(total_rows),
            'wall_clock_ms': elapsed_ms,
        })

        assert batch_calls == 1
        assert len(result) == 20

    # ── Stage 3: DB ingestion ────────────────────────────────────────

    def test_03_db_ingestion(self, provider, stats, app, setup_db):
        """Ingest all fetched bars into SQLite (via ingest_daily_bars)."""
        frames = provider.get_multi_bars(SYMBOLS, DATE_START, DATE_END)

        t0 = time.perf_counter()
        total_inserted = 0
        per_symbol: dict[str, int] = {}
        for sym, df in frames.items():
            inserted = ingest_daily_bars(sym, df, source='mock', asset_class='etf')
            total_inserted += inserted
            per_symbol[sym] = inserted
        elapsed_ms = (time.perf_counter() - t0) * 1000

        stats.record('3_db_ingestion', **{
            'total_rows_inserted': str(total_inserted),
            'symbols': str(len(per_symbol)),
            'wall_clock_ms': elapsed_ms,
            'rows_per_second': total_inserted / (elapsed_ms / 1000) if elapsed_ms else 0,
            'avg_ms_per_symbol': elapsed_ms / len(SYMBOLS) if SYMBOLS else 0,
        })

        total_in_db = PriceBar.query.count()
        assert total_in_db == total_inserted
        assert total_inserted >= 20 * 250

    # ── Stage 4: DB query (read-back) ────────────────────────────────

    def test_04_db_readback(self, stats, app, setup_db):
        """Query bars back from DB — simulates pipeline load_prices_df."""
        t0 = time.perf_counter()
        frames = {}
        for sym in SYMBOLS:
            bars = (
                PriceBar.query
                .filter_by(symbol=sym, timeframe='1d', asset_class='etf')
                .order_by(PriceBar.timestamp.desc())
                .limit(252)
                .all()
            )
            if bars:
                frames[sym] = pd.Series(
                    {b.timestamp: float(b.close) for b in bars},
                ).sort_index()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        prices_df = pd.DataFrame(frames).dropna(how='all')

        stats.record('4_db_readback', **{
            'symbols_loaded': str(len(frames)),
            'df_shape': str(prices_df.shape),
            'wall_clock_ms': elapsed_ms,
            'avg_ms_per_symbol': elapsed_ms / len(SYMBOLS) if SYMBOLS else 0,
        })

        assert len(frames) == 20
        assert prices_df.shape[0] >= 250

    # ── Stage 5: Gap detection ───────────────────────────────────────

    def test_05_gap_detection(self, stats, app, setup_db):
        """Run gap detection on all symbols."""
        t0 = time.perf_counter()
        total_gaps = 0
        for sym in SYMBOLS:
            gaps = detect_gaps(sym, DATE_START, DATE_END, asset_class='etf')
            total_gaps += len(gaps)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        stats.record('5_gap_detection', **{
            'symbols_scanned': str(len(SYMBOLS)),
            'total_gaps_found': str(total_gaps),
            'wall_clock_ms': elapsed_ms,
        })

        # Mock generates all weekdays; gap detector also expects all weekdays
        assert total_gaps == 0

    # ── Stage 6: Redis cache update ──────────────────────────────────

    def test_06_redis_cache(self, provider, stats, redis, app, setup_db):
        """Cache all bars in Redis (ohlcv:{symbol}:{date} hashes)."""
        frames = provider.get_multi_bars(SYMBOLS, DATE_START, DATE_END)

        t0 = time.perf_counter()
        total_keys = 0
        for sym, df in frames.items():
            keys = update_redis_cache(sym, df, redis, ttl=86400)
            total_keys += keys
        elapsed_ms = (time.perf_counter() - t0) * 1000

        stats.record('6_redis_cache', **{
            'total_keys_set': str(total_keys),
            'wall_clock_ms': elapsed_ms,
            'keys_per_second': total_keys / (elapsed_ms / 1000) if elapsed_ms else 0,
        })

        # Spot-check a cached bar
        sample_key = 'ohlcv:SPY:2025-06-02'
        cached = redis.hgetall(sample_key)
        assert cached

    # ── Stage 7: Quote fetching (latest bar + quote) ─────────────────

    def test_07_quote_fetch(self, provider, stats):
        """Fetch latest bar + quote for all symbols (tick-level sim)."""
        calls_before = provider.total_calls
        t0 = time.perf_counter()
        bars = {}
        quotes = {}
        for sym in SYMBOLS:
            bars[sym] = provider.get_latest_bar(sym)
            quotes[sym] = provider.get_latest_quote(sym)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        quote_calls = provider.total_calls - calls_before

        stats.record('7_quote_fetch', **{
            'api_calls': str(quote_calls),
            'symbols': str(len(SYMBOLS)),
            'wall_clock_ms': elapsed_ms,
            'avg_ms_per_call': elapsed_ms / quote_calls if quote_calls else 0,
        })

        assert quote_calls == 40
        for sym in SYMBOLS:
            assert bars[sym]['close'] > 0
            assert quotes[sym]['spread_bps'] > 0

    # ── Stage 8: Streaming simulation (pub/sub throughput) ───────────

    def test_08_streaming_pubsub(self, stats, redis):
        """Simulate 1 minute of bar streaming: publish + subscribe."""
        n_ticks = 60  # 1 bar/sec for 60 sec = 1200 total messages

        received: list[dict] = []
        errors: list[str] = []

        pubsub = redis.pubsub()
        pubsub.subscribe('channel:bars')
        ready = threading.Event()

        def subscriber():
            ready.set()
            for msg in pubsub.listen():
                if msg['type'] == 'message':
                    try:
                        data = json.loads(msg['data'])
                        received.append(data)
                    except Exception as e:
                        errors.append(str(e))
                if len(received) >= len(SYMBOLS) * n_ticks:
                    break

        sub_thread = threading.Thread(target=subscriber, daemon=True)
        sub_thread.start()
        ready.wait(timeout=2)

        t0 = time.perf_counter()
        published = 0
        for tick in range(n_ticks):
            for sym in SYMBOLS:
                bar_data = {
                    'symbol': sym,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'open': 100.0,
                    'high': 101.0,
                    'low': 99.0,
                    'close': 100.5 + tick * 0.01,
                    'volume': 50000,
                    'asset_class': 'etf',
                }
                redis.hset('cache:prices:latest', sym, str(bar_data['close']))
                redis.publish('channel:bars', json.dumps(bar_data))
                published += 1
        elapsed_ms = (time.perf_counter() - t0) * 1000

        sub_thread.join(timeout=5)
        pubsub.unsubscribe()
        pubsub.close()

        stats.record('8_streaming_pubsub', **{
            'ticks': str(n_ticks),
            'symbols': str(len(SYMBOLS)),
            'messages_published': str(published),
            'messages_received': str(len(received)),
            'delivery_rate_pct': round(len(received) / published * 100, 1) if published else 0,
            'wall_clock_ms': elapsed_ms,
            'msgs_per_second': published / (elapsed_ms / 1000) if elapsed_ms else 0,
            'errors': str(len(errors)),
        })

        assert published == n_ticks * len(SYMBOLS)
        assert len(received) >= published * 0.95

    # ── Stage 9: Deduplication (re-ingest identical data) ────────────

    def test_09_deduplication(self, provider, stats, app, setup_db):
        """Re-ingest the same bars — verify deduplication works.

        MockMarketDataProvider uses per-symbol deterministic seeds, so
        calling get_daily_bars('SPY', ...) twice yields identical rows.
        """
        sym = 'SPY'
        df = provider.get_daily_bars(sym, DATE_START, DATE_END)

        count_before = PriceBar.query.filter_by(symbol=sym).count()

        t0 = time.perf_counter()
        inserted = ingest_daily_bars(sym, df, source='mock', asset_class='etf')
        elapsed_ms = (time.perf_counter() - t0) * 1000

        count_after = PriceBar.query.filter_by(symbol=sym).count()

        stats.record('9_deduplication', **{
            'rows_attempted': str(len(df)),
            'rows_inserted': str(inserted),
            'db_count_before': str(count_before),
            'db_count_after': str(count_after),
            'wall_clock_ms': elapsed_ms,
        })

        assert inserted == 0, f'Expected 0 new inserts but got {inserted}'
        assert count_after == count_before

    # ── Stage 10: Print full stats report ────────────────────────────

    def test_99_print_stats(self, provider, stats):
        """Print the full stats summary (must run last)."""
        log = provider.call_log
        methods: dict[str, dict] = {}
        for entry in log:
            m = entry['method']
            if m not in methods:
                methods[m] = {'calls': 0, 'rows': 0, 'total_ms': 0.0}
            methods[m]['calls'] += 1
            methods[m]['rows'] += entry['rows']
            methods[m]['total_ms'] += entry['elapsed_ms']

        stats.record('provider_api_summary', **{
            'total_api_calls': str(provider.total_calls),
            'total_rows_served': str(sum(e['rows'] for e in log)),
        })
        for method, data in methods.items():
            stats.record(f'provider_{method}', **{
                'calls': str(data['calls']),
                'rows_served': str(data['rows']),
                'total_ms': data['total_ms'],
                'avg_ms_per_call': data['total_ms'] / data['calls'] if data['calls'] else 0,
            })

        print(stats.summary())
