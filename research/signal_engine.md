# Signal Engine

## Overview

The signal engine converts raw factor scores into a ranked, actionable signal for each ETF in the universe. It runs as a scheduled job (every 5 minutes intraday; full recompute at market close) and publishes results to Redis for downstream consumption by the portfolio constructor and WebSocket dashboard.

---

## Pipeline Architecture

```
Factor Scores (Redis)
       │
       ▼
CompositeScorer           ← weights from Redis config:weights
       │
       ▼
RegimeDetector            ← classifies trending/rotation/risk-off/consolidation
       │
       ▼
SignalFilter              ← liquidity gate, spread gate, kill switch check
       │
       ▼
SignalRanker              ← sorts by composite score; assigns signal_type
       │
       ▼
Redis: cache:latest_scores  +  DB: signals table
       │
       ▼
Redis pub/sub: channel:factor_scores  →  WebSocket dashboard
```

---

## Composite Scorer

```python
class CompositeScorer:
    FACTOR_KEYS = ['trend', 'volatility', 'sentiment', 'breadth', 'dispersion', 'liquidity']

    DEFAULT_WEIGHTS = {
        'trend':       0.25,
        'volatility':  0.20,
        'sentiment':   0.15,
        'breadth':     0.15,
        'dispersion':  0.15,
        'liquidity':   0.10,
    }

    def __init__(self, redis_client):
        self.redis = redis_client

    def load_weights(self) -> dict:
        """Config-driven weights; falls back to defaults."""
        raw = self.redis.hgetall('config:weights')
        if not raw:
            return self.DEFAULT_WEIGHTS
        weights = {k.decode(): float(v) for k, v in raw.items() if k.decode().startswith('weight_')}
        # Strip prefix and normalize
        weights = {k.replace('weight_', ''): v for k, v in weights.items()}
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()} if total > 0 else self.DEFAULT_WEIGHTS

    def compute(self, symbol: str, factor_scores: dict) -> float:
        """
        factor_scores: dict[factor_name → float in [0, 1]]
        Returns composite score in [0, 1].
        """
        weights = self.load_weights()
        score = sum(
            weights.get(k, 0) * factor_scores.get(k, 0.5)
            for k in self.FACTOR_KEYS
        )
        return round(float(np.clip(score, 0.0, 1.0)), 4)

    def rank_universe(self, all_factor_scores: dict[str, dict]) -> pd.DataFrame:
        """
        all_factor_scores: {symbol: {factor: score}}
        Returns DataFrame sorted by composite score descending.
        """
        rows = []
        for symbol, factors in all_factor_scores.items():
            composite = self.compute(symbol, factors)
            rows.append({'symbol': symbol, 'composite': composite, **factors})
        df = pd.DataFrame(rows).set_index('symbol')
        df['rank'] = df['composite'].rank(ascending=False).astype(int)
        return df.sort_values('composite', ascending=False)
```

---

## Signal Filter

```python
class SignalFilter:
    """
    Hard gates that block a signal regardless of composite score.
    All checks read from Redis config.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    def is_tradable(self, symbol: str, adv_m: float, spread_bps: float) -> tuple[bool, str]:
        # Kill switch check (highest priority)
        if self.redis.get('kill_switch:trading') == b'1':
            return False, 'kill_switch_active'
        if self.redis.get('kill_switch:all') == b'1':
            return False, 'kill_switch_all'

        # Liquidity hard gate
        min_adv = float(self.redis.hget('config:universe', 'min_avg_daily_vol_m') or 20)
        if adv_m < min_adv:
            return False, f'adv_below_threshold ({adv_m:.1f}M < {min_adv}M)'

        # Spread hard gate
        max_spread = float(self.redis.hget('config:universe', 'max_spread_bps') or 10)
        if spread_bps > max_spread:
            return False, f'spread_too_wide ({spread_bps:.1f} bps > {max_spread} bps)'

        # Maintenance mode — compute signals but do not route to execution
        if self.redis.get('kill_switch:maintenance') == b'1':
            return False, 'maintenance_mode'

        return True, 'ok'
```

---

## Signal Classifier

After ranking, each ETF is classified into one of three signal types used by the portfolio constructor:

| Score Range | Signal Type | Action |
|-------------|------------|--------|
| ≥ 0.65 | `long` | Eligible for inclusion in portfolio |
| 0.40 – 0.65 | `neutral` | Hold if already in portfolio; no new entry |
| < 0.40 | `avoid` | Exit if currently held |

```python
def classify_signal(composite_score: float) -> str:
    if composite_score >= 0.65:
        return 'long'
    elif composite_score >= 0.40:
        return 'neutral'
    else:
        return 'avoid'
```

**Regime overrides**:
- In `risk_off` regime: cap maximum eligible positions at 5 (instead of 10), boost threshold for `long` to 0.70
- In `rotation` regime: allow up to 12 ETFs ranked as `long` candidates to capture rotation breadth

---

## Signal Generation Job

```python
class SignalGenerator:
    def __init__(self, factor_registry, scorer, filter_, regime_tracker, redis_client, db_session):
        self.factors = factor_registry
        self.scorer  = scorer
        self.filter  = filter_
        self.regime  = regime_tracker
        self.redis   = redis_client
        self.db      = db_session

    def run(self, universe: list[str]) -> dict:
        """Full signal generation cycle. Returns signal dict for all ETFs."""

        # 1. Compute all factor scores
        all_scores = self.factors.compute_all(universe)  # returns {symbol: {factor: score}}

        # 2. Classify regime using market-level aggregated factors
        market_factors = self._aggregate_market_factors(all_scores, universe)
        regime, confidence = classify_regime(market_factors)
        regime = self.regime.update(regime, confidence, market_factors)

        # 3. Composite scoring + ranking
        ranked_df = self.scorer.rank_universe(all_scores)

        # 4. Filter + classify signals
        signals = {}
        for symbol in ranked_df.index:
            adv_m      = float(self.redis.hget(f'etf:{symbol}', 'adv_30d_m') or 0)
            spread_bps = float(self.redis.hget(f'etf:{symbol}', 'spread_bps') or 999)
            tradable, reason = self.filter.is_tradable(symbol, adv_m, spread_bps)

            score = ranked_df.loc[symbol, 'composite']
            signals[symbol] = {
                'composite_score': score,
                'signal_type':     classify_signal(score) if tradable else 'blocked',
                'rank':            int(ranked_df.loc[symbol, 'rank']),
                'regime':          regime,
                'tradable':        tradable,
                'block_reason':    reason if not tradable else None,
                'factors':         all_scores.get(symbol, {}),
                'calc_time':       pd.Timestamp.now(tz='UTC').isoformat(),
            }

        # 5. Publish to Redis + persist to DB
        self._publish(signals, regime, confidence)
        self._persist(signals)

        return signals

    def _aggregate_market_factors(self, all_scores: dict, universe: list[str]) -> dict:
        """Compute market-wide factor averages for regime classification."""
        df = pd.DataFrame.from_dict(all_scores, orient='index')
        return df.mean().to_dict()

    def _publish(self, signals: dict, regime: str, confidence: float):
        payload = json.dumps({
            'signals':    signals,
            'regime':     regime,
            'confidence': confidence,
            'timestamp':  pd.Timestamp.now(tz='UTC').isoformat(),
        })
        self.redis.set('cache:latest_scores', payload, ex=300)  # 5-minute TTL
        self.redis.publish('channel:factor_scores', payload)

    def _persist(self, signals: dict):
        """Bulk upsert signals to the signals table."""
        now = datetime.utcnow()
        rows = [Signal(
            symbol           = sym,
            signal_time      = now,
            composite_score  = s['composite_score'],
            trend_score      = s['factors'].get('trend'),
            volatility_score = s['factors'].get('volatility'),
            sentiment_score  = s['factors'].get('sentiment'),
            breadth_score    = s['factors'].get('breadth'),
            dispersion_score = s['factors'].get('dispersion'),
            liquidity_score  = s['factors'].get('liquidity'),
            regime           = s['regime'],
            signal_type      = s['signal_type'],
        ) for sym, s in signals.items()]
        self.db.bulk_save_objects(rows)
        self.db.commit()
```

---

## Data Requirements per Factor

| Factor | Primary Source | Fields Needed | Min History |
|--------|---------------|---------------|-------------|
| Trend | OHLCV (Alpaca/Polygon) | close | 252 days |
| Volatility | OHLCV + VIX | close, VIX close | 252 days |
| Sentiment | Finnhub, NewsAPI, Reddit, CBOE options | headlines, mentions, P/C ratio, fund flows | 30 days |
| Breadth | OHLCV | close | 200 days |
| Dispersion | OHLCV (sector ETFs) | close | 252 days |
| Correlation | OHLCV (sector ETFs) | close | 60 days |
| Liquidity | OHLCV + quotes | close, volume, bid, ask | 30 days |
| Slippage | OHLCV + quotes + order params | close, volume, bid, ask, order notional | 30 days |

---

## Update Schedule

| Job | Trigger | Factors Updated |
|-----|---------|----------------|
| Intraday fast | Every 5 min during market hours | Volatility (VIX), Liquidity, Slippage |
| Intraday medium | Every 15 min | Sentiment (news + Reddit) |
| Daily EOD | 16:30 ET | All 8 factors (full recompute) |
| Daily pre-open | 09:15 ET | Factor scores from prior day validated; stale check |
| On-demand | Admin trigger | Any single factor or full recompute |
