# Alpha Factor Library

This library is the **single source of truth** for all factor definitions used in Paisabot. Every factor used in research, backtesting, or live trading must have a record here with all 7 attributes filled in. Factors are registered by name in `FactorRegistry`; adding a new factor means adding an entry here and a corresponding class in `app/factors/`.

---

## Factor Schema

Every factor must define:

| Attribute | Description |
|-----------|-------------|
| **Name** | Unique snake_case identifier used in code and Redis keys |
| **Type** | Categorical: `momentum`, `mean_reversion`, `risk`, `sentiment`, `liquidity`, `execution_risk`, `breadth`, `diversification` |
| **Calculation** | Formula or methodology — exact enough to re-implement from scratch |
| **Update Frequency** | `real_time`, `intraday_5min`, `intraday_15min`, `daily_eod` |
| **Data Requirements** | Source feeds and instrument data needed |
| **Signal Usage** | How the score is consumed: composite weighting, position sizing, hard filter, or regime classifier |
| **Implementation** | Python class and module path |

All scores are normalized to **[0, 1]** before use. Higher is always more favorable (bullish / lower-risk / more liquid). Full normalization methodology for each factor is in [factor_models.md](factor_models.md).

---

## Factor Catalog

### F01 — `trend_score`

| Attribute | Value |
|-----------|-------|
| **Name** | `trend_score` |
| **Type** | Momentum |
| **Calculation** | MA alignment: EMA-20 / EMA-50 / EMA-200 (1.0 = fully bullish stack, 0.0 = fully bearish, 0.5 = mixed). Blended with cross-sectional Jegadeesh–Titman momentum (12-month minus 1-month return, percentile-ranked across universe) and 3-month relative strength vs SPY. Weights: 0.40 MA + 0.35 momentum + 0.25 RS |
| **Update Frequency** | `daily_eod` |
| **Data Requirements** | Daily OHLCV (close), SPY as benchmark |
| **Signal Usage** | Primary composite score weight (0.25). Long if uptrend (score ≥ 0.65), short candidate if downtrend (score < 0.30) in risk-off regime |
| **Implementation** | `app/factors/trend.py` → `TrendFactor` |

---

### F02 — `volatility_regime`

| Attribute | Value |
|-----------|-------|
| **Name** | `volatility_regime` |
| **Type** | Risk |
| **Calculation** | Blend of: (1) 1 − percentile_rank(realized_20d_vol, 252d) — lower realized vol = better; (2) 1 − percentile_rank(VIX_close, 252d); (3) min(1 / GARCH_ratio, 1.0) where GARCH_ratio = realized_vol / GARCH(1,1) 1-day forecast — declining vol = better. Weights: 0.40 / 0.40 / 0.20 |
| **Update Frequency** | `intraday_5min` (VIX component); `daily_eod` (GARCH refit) |
| **Data Requirements** | Daily OHLCV (close), VIX from CBOE/FRED (`VIXCLS`) |
| **Signal Usage** | Composite score weight (0.20). Also drives volatility-targeted position sizing: when score < 0.35 (high-vol regime), all position sizes scaled down proportionally |
| **Implementation** | `app/factors/volatility.py` → `VolatilityFactor` |

---

### F03 — `sentiment_score`

| Attribute | Value |
|-----------|-------|
| **Name** | `sentiment_score` |
| **Type** | Sentiment / Behavioral |
| **Calculation** | Weighted blend: 0.35 × news_score (FinBERT confidence-weighted mean over last 24h headlines) + 0.25 × reddit_score (bull − bear mentions / total, normalized over 30d rolling) + 0.25 × options_score (1 − percentile_rank(put_call_ratio_10d_MA, 252d)) + 0.15 × flow_score (percentile_rank(net_fund_flow_5d, 252d)) |
| **Update Frequency** | `intraday_15min` |
| **Data Requirements** | Finnhub/NewsAPI headlines, Reddit PRAW (r/wallstreetbets, r/investing), CBOE/Tradier options chains (put/call ratio), FMP fund flow API |
| **Signal Usage** | Composite score weight (0.15). Boosts allocation to ETFs with strong positive news + retail sentiment confluence |
| **Implementation** | `app/factors/sentiment.py` → `SentimentFactor` |

---

### F04 — `dispersion_score`

| Attribute | Value |
|-----------|-------|
| **Name** | `dispersion_score` |
| **Type** | Diversification |
| **Calculation** | `std(sector_returns_5d)` across all sector ETFs → z-score against 252d rolling history → sigmoid(z-score). High cross-sectional dispersion = capital is differentiating between sectors = rotation opportunity |
| **Update Frequency** | `daily_eod` |
| **Data Requirements** | Daily OHLCV for all sector ETFs (XLK, XLE, XLF, XLV, XLI, XLC, XLY, XLP, XLU, XLRE, XLB). Minimum 8 sectors required |
| **Signal Usage** | Composite score weight (0.15). Also feeds the rotation regime detector as a primary input. Crisis dispersion (vol_regime < 0.35) is capped at 0.50 to avoid mistaking fear for opportunity |
| **Implementation** | `app/factors/dispersion.py` → `DispersionFactor` |

---

### F05 — `correlation_index`

| Attribute | Value |
|-----------|-------|
| **Name** | `correlation_index` |
| **Type** | Risk / Diversification |
| **Calculation** | 60-day rolling pairwise correlation matrix of sector ETF returns → mean of upper triangle → 1 − percentile_rank(avg_corr, 252d). Inverted: low correlation = favorable diversification environment = high score. Collapse detection: z-score < −2.0 triggers `rotation` regime flag |
| **Update Frequency** | `daily_eod` |
| **Data Requirements** | Daily OHLCV for sector ETFs |
| **Signal Usage** | Inputs to regime classifier (rotation detection). Low correlation environment enables sector-selection strategies. Also a portfolio risk monitor: avg pairwise corr > 0.85 for 3+ days triggers risk alert |
| **Implementation** | `app/factors/correlation.py` → `CorrelationFactor` |

---

### F06 — `breadth_score`

| Attribute | Value |
|-----------|-------|
| **Name** | `breadth_score` |
| **Type** | Market Breadth |
| **Calculation** | 0.30 × pct_above_SMA50 + 0.30 × pct_above_SMA200 + 0.20 × ad_ema_score (10-day EMA of daily directional changes, clipped to [0,1]) + 0.20 × sector_participation (fraction of sectors with positive 5d return) |
| **Update Frequency** | `daily_eod` |
| **Data Requirements** | Daily OHLCV for all ETFs in universe |
| **Signal Usage** | Composite score weight (0.15). Breadth deterioration detection: score drops > 0.15 over 5 days → breadth warning flag. Primary input to regime classifier (trending vs risk-off distinction) |
| **Implementation** | `app/factors/breadth.py` → `BreadthFactor` |

---

### F07 — `liquidity_score`

| Attribute | Value |
|-----------|-------|
| **Name** | `liquidity_score` |
| **Type** | Liquidity / Execution |
| **Calculation** | 0.50 × adv_score (min(ADV_30d / $20M threshold, 1.0)) + 0.50 × spread_score (max(0, 1 − spread_est_bps / 10)). Spread estimated via Roll (1984) OHLCV estimator: `2 × sqrt(max(−cov(Δlog_p_t, Δlog_p_{t-1}), 0)) × 10,000` |
| **Update Frequency** | `intraday_5min` (spread component); `daily_eod` (ADV component) |
| **Data Requirements** | Daily OHLCV (close, volume); Level 1 quotes (Alpaca) for real spread when available |
| **Signal Usage** | Composite score weight (0.10). Also used as a hard filter: ADV < $20M or spread > 10 bps → ETF blocked from all signals regardless of composite score |
| **Implementation** | `app/factors/liquidity.py` → `LiquidityFactor` |

---

### F08 — `slippage_estimator`

| Attribute | Value |
|-----------|-------|
| **Name** | `slippage_estimator` |
| **Type** | Execution Risk |
| **Calculation** | Almgren-Chriss simplified: `half_spread + 0.10 × daily_vol_pct × 100 × sqrt(participation_rate)` where `participation_rate = order_notional / ADV_30d`. Result in basis points. Score = `max(0, 1 − est_slippage_bps / 10)` — inverted so lower expected cost = higher score |
| **Update Frequency** | `real_time` (computed on-demand pre-trade) |
| **Data Requirements** | ADV_30d, real-time spread, 20-day realized daily vol, order notional |
| **Signal Usage** | Pre-trade gate: block order if estimated slippage > `execution.max_slippage_bps` (default 8 bps). Post-trade measurement feeds model recalibration |
| **Implementation** | `app/factors/slippage.py` → `SlippageFactor` |

---

### F09 — `sector_momentum_rank`

| Attribute | Value |
|-----------|-------|
| **Name** | `sector_momentum_rank` |
| **Type** | Momentum / Relative Strength |
| **Calculation** | Cross-sectional percentile rank of each sector ETF's 90-day total return within the sector ETF universe. `rank = percentile_rank(return_90d, cross_section_of_sector_etfs)`. Updated to also include 63-day return for a blended rank: `0.60 × rank_90d + 0.40 × rank_63d` |
| **Update Frequency** | `daily_eod` |
| **Data Requirements** | Daily OHLCV for all sector ETFs |
| **Signal Usage** | Standalone signal: top quartile (rank ≥ 0.75) → long candidates; bottom quartile (rank ≤ 0.25) → short candidates (in rotation/risk-off regime). Also used in rotation detection as the `momentum_divergence_score` component |
| **Implementation** | `app/factors/trend.py` → `TrendFactor.compute_sector_momentum_rank()` (sub-method, not a separate class) |

---

### F10 — `beta_adjusted_momentum`

| Attribute | Value |
|-----------|-------|
| **Name** | `beta_adjusted_momentum` |
| **Type** | Risk-Adjusted Trend |
| **Calculation** | `beta_adj_momentum = trend_score / beta_vs_SPY` where `beta_vs_SPY = cov(ETF_returns_63d, SPY_returns_63d) / var(SPY_returns_63d)`. Resulting raw value is percentile-ranked cross-sectionally and normalized to [0, 1]. High score = strong momentum relative to market exposure taken |
| **Update Frequency** | `daily_eod` |
| **Data Requirements** | Daily OHLCV for each ETF + SPY benchmark |
| **Signal Usage** | Portfolio weighting adjustment: when `beta_adjusted_momentum` rank significantly diverges from raw `trend_score` rank, prefer beta-adjusted version for position sizing in beta-constrained portfolios. Optional composite component (add as `beta_momentum` with low weight 0.05–0.10 when beta-neutral mode is enabled in `config:portfolio`) |
| **Implementation** | `app/factors/trend.py` → `TrendFactor.compute_beta_adjusted()` (sub-method) |

---

### F11 — `vol_adjusted_position_size`

| Attribute | Value |
|-----------|-------|
| **Name** | `vol_adjusted_position_size` |
| **Type** | Risk / Execution |
| **Calculation** | Not a score — a sizing multiplier: `scale = min(vol_target / realized_vol_20d_annualized, 1.0)` where `vol_target = config:risk.vol_target` (default 0.12). Applied as a post-optimization weight scaler. If `portfolio_realized_vol > vol_target`, all weights multiplied by `scale`. Never leverages up (scale capped at 1.0) |
| **Update Frequency** | `real_time` (recomputed before every rebalance) |
| **Data Requirements** | 20-day rolling realized vol from OHLCV; portfolio-level covariance matrix |
| **Signal Usage** | Dynamic position sizing: replaces static max-weight constraints when `vol_scaling_enabled = true`. Ensures the portfolio always targets 12% annualized volatility regardless of market conditions |
| **Implementation** | `app/portfolio/sizer.py` → `PositionSizer.apply_vol_target()` |

---

## Factor Dependency Map

Some factors share upstream computations. Cache these in Redis to avoid redundant computation:

| Shared Computation | Used By | Redis Cache Key |
|--------------------|---------|----------------|
| 20-day realized vol (per ETF) | `volatility_regime`, `slippage_estimator`, `vol_adjusted_position_size` | `factor:{symbol}:realized_vol_20d` |
| 30-day ADV (per ETF) | `liquidity_score`, `slippage_estimator` | `etf:{symbol}:adv_30d_m` |
| 63-day return (per ETF) | `trend_score`, `sector_momentum_rank`, `beta_adjusted_momentum` | `factor:{symbol}:return_63d` |
| SPY 63-day return | `trend_score` (RS), `beta_adjusted_momentum` | `factor:SPY:return_63d` |
| Sector ETF pairwise returns | `dispersion_score`, `correlation_index`, `sector_momentum_rank` | `factor:sector_returns:5d`, `factor:sector_returns:63d` |

---

## Factor Registry Pattern

The `FactorRegistry` instantiates all active factors from config and runs them in the correct order (shared dependencies first). New factors are enabled or disabled via `config:factors.enabled_factors` in the admin UI.

```python
class FactorRegistry:
    AVAILABLE_FACTORS: dict[str, type[FactorBase]] = {
        'trend_score':            TrendFactor,
        'volatility_regime':      VolatilityFactor,
        'sentiment_score':        SentimentFactor,
        'dispersion_score':       DispersionFactor,
        'correlation_index':      CorrelationFactor,
        'breadth_score':          BreadthFactor,
        'liquidity_score':        LiquidityFactor,
        'slippage_estimator':     SlippageFactor,
        # F09–F11 are sub-methods within existing classes; no separate entry
    }

    def __init__(self, redis_client, db_session, config_loader):
        enabled = config_loader.get('factors', 'enabled_factors', default='all')
        names   = list(self.AVAILABLE_FACTORS) if enabled == 'all' else enabled.split(',')
        self.factors = {
            name: cls(redis_client, db_session, config_loader)
            for name, cls in self.AVAILABLE_FACTORS.items()
            if name in names
        }

    def compute_all(self, symbols: list[str]) -> dict[str, dict[str, float]]:
        """Returns {symbol: {factor_name: score}} for all active factors."""
        results: dict[str, dict] = {sym: {} for sym in symbols}
        for name, factor in self.factors.items():
            scores = factor.compute(symbols)
            for sym, score in scores.items():
                results[sym][name] = score
        return results
```

---

## Adding a New Factor

To add a factor to the library:

1. Add an entry to this document with all 7 attributes
2. Create `app/factors/{factor_name}.py` implementing `FactorBase`
3. Register it in `FactorRegistry.AVAILABLE_FACTORS`
4. Add the factor name to `config:weights` with a default weight (even if 0.0)
5. Add the factor to the `system_config` seed script (`scripts/seed_config.py`)
6. Add the factor column to the `signals` table via an Alembic migration
7. Add the factor panel to the Factor Explorer dashboard view

---

## Composite Score Weights (Current Defaults)

The 8 factors that contribute to `ETF_SCORE` are the first 8 in the catalog (F01–F08). F09–F11 are supplementary signals used for specific purposes (rotation detection, beta neutrality, position sizing) rather than direct composite weighting.

```
ETF_SCORE = 0.25 * trend_score
           + 0.20 * volatility_regime
           + 0.15 * sentiment_score
           + 0.15 * breadth_score
           + 0.15 * dispersion_score
           + 0.10 * liquidity_score
```

Weights are runtime-configurable via `config:weights` Redis hash without code changes.
