#!/usr/bin/env python3
"""
scripts/test_signal_pipeline.py

End-to-end validation of the signal-generation and market-regime pipeline.
Uses in-memory SQLite + fakeredis — no running DB, Redis, or broker required.

Data design
-----------
13 symbols split into 3 clearly differentiated groups:
  trend   (7) : SPY QQQ XLK XLY XLI XLV XLC  — strong uptrend  (+0.0009–0.0015/day)
  flat    (3) : XLRE XLB XLP                   — near-zero drift (≤ 0.0001/day)
  decline (3) : XLE XLF XLU                    — clear downtrend (−0.0006–0.0008/day)

360 business-day bars are generated (satisfies every lookback window, including
CorrelationFactor's deepest requirement of 350 rows).

Why you get 0.5 for every factor
---------------------------------
FactorBase._get_multi_closes / _get_daily_closes / _get_daily_ohlcv all query
the live PriceBar table.  Running factors standalone — without inserting mock
data first — returns an empty DataFrame, triggering the `if closes_df.empty`
guard in every factor and falling through to `return {s: 0.5 for s in symbols}`.
This script inserts the required rows before running the pipeline.

Known bug flagged (not fixed here — separate issue)
----------------------------------------------------
BreadthFactor._compute_ad_ema_score uses:
    score = np.clip((ema + 0.01) / 0.02, 0.0, 1.0)
The denominator 0.02 is far too small for an EMA of ±1 signals — it clips
everything outside a ±0.01 band to 0 or 1.  The formula should be
    score = (ema + 1.0) / 2.0
to properly map the [-1, 1] range to [0, 1].  Until that is fixed, the
ad_ema component behaves as a hard binary (0 or 1), not a smooth indicator.

Assertions (Exit 0 = all pass, Exit 1 = failures printed)
----------------------------------------------------------
A1  No symbol has ≥ 4 of its 5 active factors stuck at exactly 0.5
A2  Mean trend_score: trending group > declining group by ≥ 0.20
A3  Breadth ordering: trend_mean ≥ flat_mean ≥ decline_mean
A4  Direct classify_regime() call returns confidence > 0.50 (data-driven)
A5  At least 2 distinct signal_types in the output
A6  Composite score spread (max − min) ≥ 0.25

Usage
-----
    python scripts/test_signal_pipeline.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timezone

import numpy as np
import pandas as pd

# ── minimal env so Flask doesn't complain before create_app is reached ────────
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('FLASK_ENV', 'testing')

# ── minimal Flask bootstrap: SQLAlchemy only, no SocketIO / blueprints ────────
from flask import Flask                          # noqa: E402
from app.extensions import db                    # noqa: E402
import app.models                                # noqa: E402  — registers all ORM classes


def _make_minimal_app() -> Flask:
    """Bare Flask app: SQLite in-memory + SQLAlchemy only."""
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI='sqlite://',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY='test-secret',
        WTF_CSRF_ENABLED=False,
    )
    db.init_app(app)
    return app


# ── symbol configuration ──────────────────────────────────────────────────────
# Three groups with deliberately large price-regime differences.
# avg_vol × close >> $20 M/day so every symbol passes LiquidityFactor's hard
# ADV filter without needing special treatment.

SYMBOLS_BY_GROUP: dict[str, list[str]] = {
    'trend':   ['SPY', 'QQQ', 'XLK', 'XLY', 'XLI', 'XLV', 'XLC'],
    'flat':    ['XLRE', 'XLB', 'XLP'],
    'decline': ['XLE', 'XLF', 'XLU'],
}

_SYMBOL_CFG: dict[str, dict] = {
    # ── uptrend ────────────────────────────────────────────────────────────
    'SPY':  {'start': 450.0, 'drift':  0.0010, 'vol': 0.010, 'seed':  1, 'avg_vol': 5_000_000},
    'QQQ':  {'start': 380.0, 'drift':  0.0012, 'vol': 0.012, 'seed':  2, 'avg_vol': 4_000_000},
    'XLK':  {'start': 200.0, 'drift':  0.0015, 'vol': 0.013, 'seed':  3, 'avg_vol': 3_000_000},
    'XLY':  {'start': 175.0, 'drift':  0.0009, 'vol': 0.012, 'seed':  9, 'avg_vol': 2_500_000},
    'XLI':  {'start': 110.0, 'drift':  0.0009, 'vol': 0.011, 'seed':  7, 'avg_vol': 2_500_000},
    'XLV':  {'start': 140.0, 'drift':  0.0005, 'vol': 0.009, 'seed':  6, 'avg_vol': 2_000_000},
    'XLC':  {'start':  75.0, 'drift':  0.0005, 'vol': 0.010, 'seed':  8, 'avg_vol': 1_500_000},
    # ── flat ───────────────────────────────────────────────────────────────
    'XLRE': {'start':  42.0, 'drift':  0.0001, 'vol': 0.010, 'seed': 12, 'avg_vol': 1_200_000},
    'XLB':  {'start':  88.0, 'drift':  0.0001, 'vol': 0.012, 'seed': 13, 'avg_vol': 1_500_000},
    'XLP':  {'start':  78.0, 'drift': -0.0001, 'vol': 0.009, 'seed': 10, 'avg_vol': 1_500_000},
    # ── downtrend ──────────────────────────────────────────────────────────
    'XLE':  {'start':  85.0, 'drift': -0.0008, 'vol': 0.015, 'seed':  4, 'avg_vol': 3_000_000},
    'XLF':  {'start':  38.0, 'drift': -0.0007, 'vol': 0.013, 'seed':  5, 'avg_vol': 2_000_000},
    'XLU':  {'start':  68.0, 'drift': -0.0006, 'vol': 0.010, 'seed': 11, 'avg_vol': 1_500_000},
}

UNIVERSE: list[str] = list(_SYMBOL_CFG.keys())
ACTIVE_FACTORS: list[str] = [
    'trend_score', 'volatility_regime', 'sentiment_score',
    'breadth_score', 'liquidity_score',
]

# 360 business days satisfies the deepest lookback (CorrelationFactor: 350 rows)
N_TRADING_DAYS = 360


# ── price data ────────────────────────────────────────────────────────────────

def _build_price_bars(dates: pd.DatetimeIndex) -> list:
    """Generate GBM OHLCV PriceBar objects for all symbols × all dates."""
    from app.models.price_bars import PriceBar

    bars: list[PriceBar] = []
    n = len(dates)

    for symbol, cfg in _SYMBOL_CFG.items():
        rng = np.random.RandomState(cfg['seed'])

        # Geometric Brownian Motion log-prices
        log_rets = cfg['drift'] + cfg['vol'] * rng.randn(n - 1)
        log_px = np.empty(n)
        log_px[0] = np.log(cfg['start'])
        log_px[1:] = log_px[0] + np.cumsum(log_rets)
        closes = np.exp(log_px)

        # Plausible intraday range and volume
        daily_range_pct = 0.005 + cfg['vol'] * rng.rand(n) * 0.5
        opens  = closes * np.exp(rng.randn(n) * 0.002)
        highs  = closes * (1.0 + daily_range_pct)
        lows   = closes * (1.0 - daily_range_pct)
        vols   = (cfg['avg_vol'] * (0.7 + 0.6 * rng.rand(n))).astype(int)

        for i, ts in enumerate(dates):
            bars.append(PriceBar(
                symbol=symbol,
                timeframe='1d',
                timestamp=ts.to_pydatetime().replace(tzinfo=timezone.utc),
                open=float(opens[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                close=float(closes[i]),
                volume=int(vols[i]),
                is_synthetic=False,
                source='test',
            ))

    return bars


# ── fakeredis setup ───────────────────────────────────────────────────────────

def _build_fake_redis():
    """
    Return a FakeRedis pre-loaded with VIX history and per-symbol
    sentiment signals (options P/C + fund-flow) differentiated by group.
    """
    import fakeredis

    r = fakeredis.FakeRedis()  # decode_responses=False — factors handle both

    # ── VIX: calm (15) sitting near the low end of 10–35 history ──────────
    # percentile_rank(15, linspace(10,35,252)) ≈ 0.20
    # → vix_component = 1 − 0.20 = 0.80 for all symbols (uniformly good)
    vix_history = list(np.linspace(10.0, 35.0, 252))
    r.set(b'vix:latest', b'15.0')
    r.set(b'vix:history_252', json.dumps(vix_history).encode())

    # ── Sentiment signals ─────────────────────────────────────────────────
    # P/C ratio history: 100 points spanning [0.5, 2.0] (median ≈ 1.25)
    # Flow history:      100 points spanning [-500M, +500M] (median ≈ 0)
    pc_history_bytes  = json.dumps(list(np.linspace(0.5, 2.0, 100))).encode()
    flow_hist_bytes   = json.dumps(list(np.linspace(-500e6, 500e6, 100))).encode()

    for symbol, cfg in _SYMBOL_CFG.items():
        group = next(g for g, syms in SYMBOLS_BY_GROUP.items() if symbol in syms)

        if group == 'trend':
            pc_ratio = 0.55    # low P/C → bullish → high options_score
            net_flow = 450e6   # strong inflow → high flow_score
        elif group == 'flat':
            pc_ratio = 1.05    # neutral P/C
            net_flow = 0.0
        else:                  # decline
            pc_ratio = 1.90    # high P/C → bearish → low options_score
            net_flow = -450e6  # strong outflow → low flow_score

        r.set(f'options:{symbol}:pc_ratio'.encode(),  str(pc_ratio).encode())
        r.set(f'options:{symbol}:pc_history'.encode(), pc_history_bytes)
        r.set(f'flow:{symbol}:net_5d'.encode(),        str(net_flow).encode())
        r.set(f'flow:{symbol}:history'.encode(),       flow_hist_bytes)

    return r


# ── assertions ────────────────────────────────────────────────────────────────

def _group_mean(
    all_scores: dict[str, dict[str, float]],
    factor: str,
) -> dict[str, float]:
    """Mean factor score per group."""
    return {
        group: float(np.mean([
            all_scores[s].get(factor, 0.5)
            for s in syms if s in all_scores
        ]))
        for group, syms in SYMBOLS_BY_GROUP.items()
    }


def _run_assertions(
    all_scores: dict[str, dict[str, float]],
    ranked_df: pd.DataFrame,
    signals: dict[str, str],
    raw_regime: str,
    raw_confidence: float,
) -> list[str]:
    failures: list[str] = []

    # A1 — no symbol should have ≥ 4 of 5 active factors pinned at 0.5
    for sym in UNIVERSE:
        scores = all_scores.get(sym, {})
        n_pinned = sum(1 for f in ACTIVE_FACTORS if scores.get(f) == 0.5)
        if n_pinned >= 4:
            failures.append(
                f'A1  {sym}: {n_pinned}/5 active factors == 0.5 '
                f'(data is not reaching compute methods)'
            )

    # A2 — trending group must outperform declining by ≥ 0.20 on trend_score
    t_means = _group_mean(all_scores, 'trend_score')
    gap = t_means['trend'] - t_means['decline']
    if gap < 0.20:
        failures.append(
            f'A2  trend_score gap (trending − decline) = {gap:.3f}  '
            f'[trending={t_means["trend"]:.3f}  decline={t_means["decline"]:.3f}]  '
            f'expected ≥ 0.20'
        )

    # A3 — breadth ordering: trending ≥ flat ≥ declining
    b_means = _group_mean(all_scores, 'breadth_score')
    if not (b_means['trend'] >= b_means['flat'] >= b_means['decline']):
        failures.append(
            f'A3  breadth ordering violated: '
            f'trend={b_means["trend"]:.3f}  '
            f'flat={b_means["flat"]:.3f}  '
            f'decline={b_means["decline"]:.3f}  '
            f'(expected trend ≥ flat ≥ decline)'
        )

    # A4 — direct classify_regime() call must yield confidence > 0.50
    #      (the regime tracker adds 3-day persistence so a single call to
    #       SignalGenerator.run() may still report 'consolidation' on day 1;
    #       we test the raw classifier separately)
    if raw_confidence <= 0.50:
        failures.append(
            f'A4  classify_regime() returned confidence={raw_confidence:.4f} ≤ 0.50 '
            f'(regime={raw_regime}); market factors are not driving classification'
        )

    # A5 — at least 2 distinct signal types (not uniform)
    unique_types = set(signals.values())
    if len(unique_types) < 2:
        failures.append(
            f'A5  only one signal type in output: {unique_types}  '
            f'composite spread is too narrow or threshold logic is broken'
        )

    # A6 — composite score spread ≥ 0.25
    composites = ranked_df['composite'].values.astype(float)
    spread = float(composites.max() - composites.min())
    if spread < 0.25:
        failures.append(
            f'A6  composite spread = {spread:.4f} (expected ≥ 0.25); '
            f'all symbols scoring near-identically'
        )

    return failures


# ── report printing ────────────────────────────────────────────────────────────

def _print_report(
    all_scores: dict[str, dict[str, float]],
    ranked_df: pd.DataFrame,
    signals: dict[str, str],
    effective_regime: str,
    raw_regime: str,
    raw_confidence: float,
) -> None:
    cols = ACTIVE_FACTORS + ['correlation_index', 'slippage_estimator']
    header_factors = '  '.join(f'{f[:7]:<7}' for f in cols)
    print(f'\n{"":─<100}')
    print(f'  {"Symbol":<7}  {header_factors}  {"composite":>9}  {"signal":<8}  group')
    print(f'{"":─<100}')

    for sym in ranked_df.index:
        scores  = all_scores.get(sym, {})
        group   = next((g for g, s in SYMBOLS_BY_GROUP.items() if sym in s), '?')
        factor_vals = '  '.join(f'{scores.get(f, float("nan")):7.3f}' for f in cols)
        composite   = float(ranked_df.loc[sym, 'composite'])
        sig         = signals.get(sym, '?')
        print(f'  {sym:<7}  {factor_vals}  {composite:9.4f}  {sig:<8}  {group}')

    print(f'{"":─<100}')

    t_means = _group_mean(all_scores, 'trend_score')
    b_means = _group_mean(all_scores, 'breadth_score')
    composites = ranked_df['composite'].values.astype(float)

    print('\n  Group summary:')
    for group in ('trend', 'flat', 'decline'):
        syms  = SYMBOLS_BY_GROUP[group]
        comps = [float(ranked_df.loc[s, 'composite']) for s in syms if s in ranked_df.index]
        print(
            f'    {group:<8}  trend={t_means[group]:.3f}  '
            f'breadth={b_means[group]:.3f}  '
            f'composite_mean={np.mean(comps):.3f}'
        )

    print(
        f'\n  Raw classify_regime()  →  {raw_regime}  (confidence={raw_confidence:.4f})'
    )
    print(
        f'  Effective regime (tracker, 1st call)  →  {effective_regime}'
    )
    print(
        f'  Composite spread: {composites.max():.4f} − {composites.min():.4f} '
        f'= {composites.max() - composites.min():.4f}'
    )
    print(f'  Signal mix: {dict(sorted((t, list(signals.values()).count(t)) for t in set(signals.values())))}')


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    try:
        import fakeredis  # noqa: F401
    except ImportError:
        print('ERROR: fakeredis not installed — run: pip install fakeredis')
        return 1

    app = _make_minimal_app()

    with app.app_context():
        # ── 1. create tables & insert mock price data ──────────────────────
        db.create_all()

        end_date = pd.Timestamp.utcnow().normalize()
        dates    = pd.bdate_range(end=end_date, periods=N_TRADING_DAYS)
        bars     = _build_price_bars(dates)
        db.session.bulk_save_objects(bars)
        db.session.commit()

        print(
            f'[setup] inserted {len(bars):,} bars  '
            f'({len(UNIVERSE)} symbols × {N_TRADING_DAYS} trading days)'
        )

        # ── 2. build fakeredis with VIX + sentiment data ───────────────────
        fake_redis = _build_fake_redis()

        # ── 3. run the full SignalGenerator pipeline ───────────────────────
        from app.signals.signal_generator import SignalGenerator, classify_signal

        generator = SignalGenerator(
            redis_client=fake_redis,
            db_session=db.session,
        )
        signals_full = generator.run(UNIVERSE)

        if not signals_full:
            print('ERROR: SignalGenerator.run() returned empty dict')
            return 1

        # Extract outputs
        all_scores: dict[str, dict[str, float]] = {
            sym: sig.get('factors', {}) for sym, sig in signals_full.items()
        }
        signals: dict[str, str] = {
            sym: sig['signal_type'] for sym, sig in signals_full.items()
        }
        effective_regime = next(iter(signals_full.values()))['regime']

        # ── 4. build ranked DataFrame from the factor scores ───────────────
        from app.signals.composite_scorer import CompositeScorer
        scorer     = CompositeScorer(redis_client=fake_redis)
        ranked_df  = scorer.rank_universe(all_scores)

        # ── 5. call classify_regime() directly for the raw classification ──
        #       (bypasses the 3-day persistence filter in RegimeTracker)
        market_factors = (
            pd.DataFrame(all_scores)
            .T
            .reindex(columns=ACTIVE_FACTORS + ['correlation_index'])
            .mean()
            .to_dict()
        )
        from app.signals.regime_detector import classify_regime
        raw_regime, raw_confidence = classify_regime(market_factors)

        # ── 6. print human-readable report ────────────────────────────────
        _print_report(
            all_scores, ranked_df, signals,
            effective_regime, raw_regime, raw_confidence,
        )

        # ── 7. run assertions ──────────────────────────────────────────────
        failures = _run_assertions(
            all_scores, ranked_df, signals, raw_regime, raw_confidence,
        )

        print(f'\n{"":─<100}')
        if failures:
            print(f'FAILED ({len(failures)} assertion(s)):')
            for f in failures:
                print(f'  ✗  {f}')
            print()
            return 1

        print(
            f'  ✓  All 6 assertions passed  '
            f'(raw_regime={raw_regime}  confidence={raw_confidence:.3f}  '
            f'spread={ranked_df["composite"].max() - ranked_df["composite"].min():.3f}  '
            f'signals={sorted(set(signals.values()))})'
        )
        print()
        return 0


if __name__ == '__main__':
    sys.exit(main())
