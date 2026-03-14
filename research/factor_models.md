# Factor Models Reference

## Composite Score Formula

```
ETF_SCORE = 0.25 * Trend
           + 0.20 * Volatility_Regime
           + 0.15 * Sentiment
           + 0.15 * Breadth
           + 0.15 * Dispersion
           + 0.10 * Liquidity
```

All factor scores are normalized to **[0, 1]** via percentile rank against a 252-day rolling history before weighting. A score of 1.0 is maximally favorable; 0.0 is least favorable.

---

## Factor 1: Volatility

**Purpose**: Classify the current volatility regime to adjust position sizing and avoid high-vol environments.

**Formula**:
```
realized_vol_20d = std(daily_log_returns[-20:]) * sqrt(252)   # annualized
vol_percentile   = percentile_rank(realized_vol_20d, 252-day history)
vix_percentile   = percentile_rank(VIX_close, 252-day history)
garch_ratio      = realized_vol_20d / garch_1d_forecast_vol   # >1 = vol elevated vs forecast

volatility_score = 0.40 * (1 - vol_percentile)
                 + 0.40 * (1 - vix_percentile)
                 + 0.20 * min(1 / garch_ratio, 1.0)
```

Higher score = favorable (lower / declining vol).

**Libraries**: `arch` (GARCH), `scipy.stats.percentileofscore`
**Data**: Daily OHLCV (any provider), VIX from CBOE/FRED (`VIXCLS`)
**Lookbacks**: 20-day realized vol; 252-day for percentile; GARCH fit on trailing 252 bars
**Update frequency**: Intraday every 5 min (VIX); GARCH daily
**Edge cases**:
- GARCH requires ≥250 observations; use realized vol only on cold start
- VIX has 15-min delay on free CBOE feed — use FRED for daily close
- Vol spikes during crises cause rank compression; do not use raw vol as position-size multiplier without capping

---

## Factor 2: Market Sentiment

**Formula**:
```
news_score    = confidence-weighted mean(FinBERT_3class_scores, last 24h headlines)
reddit_score  = (bull_mentions - bear_mentions) / total_mentions  [normalized over 30d rolling]
options_score = 1 - percentile_rank(put_call_ratio_10d_MA, 252d)   # inverted: low P/C = bullish
flow_score    = percentile_rank(net_fund_flow_5d, 252d)

sentiment_composite = 0.35 * news_score
                    + 0.25 * reddit_score
                    + 0.25 * options_score
                    + 0.15 * flow_score
```

**Libraries**: `transformers` (ProsusAI/finbert), `vaderSentiment` (fallback), `praw`, `newsapi-python`
**Data**:
- News: Finnhub (60 req/min free), NewsAPI.org (100 req/day free)
- Reddit: PRAW — r/wallstreetbets, r/investing, r/stocks
- P/C ratio: CBOE options data; Tradier (free sandbox)
- Fund flows: FMP API (free 5 req/min), VettaFi (paid)
**Lookbacks**: 24h for news; 30 days for Reddit; 10-day MA for P/C ratio
**Update frequency**: Every 15 minutes
**Edge cases**:
- FinBERT on CPU takes ~50–200ms per call; always batch (batch_size=32)
- VADER acceptable fallback for Reddit short-form text
- P/C ratio noisy on low-volume options days; use 10-day EMA
- Assign neutral score (0.5) when insufficient data (<5 headlines)

---

## Factor 3: Dispersion

**Purpose**: High dispersion among sector ETF returns signals capital rotation and differentiated sector dynamics.

**Formula**:
```
sector_returns_5d = pct_change(sector_etf_prices, periods=5)
current_dispersion = std(sector_returns_5d)   # cross-sectional std at time t

rolling_dispersion_history = [std(sector_returns_5d) for each day in trailing 252d]
dispersion_zscore = (current_dispersion - mean(rolling_dispersion_history)) /
                     std(rolling_dispersion_history)

dispersion_score = sigmoid(dispersion_zscore)  # maps R → (0,1); higher dispersion = higher score
```

**Libraries**: `numpy`, `pandas`, `scipy.special.expit` (sigmoid)
**Data**: Daily OHLCV for all sector ETFs (XLK, XLE, XLF, XLV, XLI, XLC, XLY, XLP, XLU, XLRE, XLB)
**Lookback**: 5-day return window; 252-day history for normalization
**Update frequency**: Daily after market close
**Edge cases**:
- Crisis dispersion (2008, COVID) is fear-driven, not opportunity-driven — gate with vol regime: if vol_score < 0.3, cap dispersion contribution at 0.5
- Requires ≥12 sector ETFs for a meaningful std; do not compute with <8

---

## Factor 4: Correlation

**Purpose**: Low cross-sector correlation enables diversified multi-ETF allocation. Correlation collapse signals regime change.

**Formula**:
```
returns_matrix  = log_returns(sector_etf_closes, window=60)
corr_matrix     = rolling_pairwise_correlation(returns_matrix, window=60)
upper_triangle  = corr_matrix[triu_indices(k=1)]
avg_corr_60d    = mean(upper_triangle)

rolling_corr_history = [avg_corr_60d for each day in trailing 252d]
correlation_score = 1 - percentile_rank(avg_corr_60d, rolling_corr_history)
# Inverted: low correlation = better diversification = high score
```

**Collapse detection** (triggers rotation regime flag):
```
corr_zscore = (avg_corr_60d - mean_252d) / std_252d
collapse_detected = corr_zscore < -2.0
```

**Libraries**: `pandas`, `numpy`, `scipy.stats.percentileofscore`
**Data**: Daily OHLCV for sector ETFs
**Lookback**: 60-day rolling correlation; 252-day for percentile
**Update frequency**: Daily
**Edge cases**:
- Correlation → 1.0 during market crashes — trigger risk-off mode when avg_corr > 0.85 for 3+ consecutive days
- Minimum 20 observations required for stable pairwise correlation

---

## Factor 5: Market Breadth

**Formula**:
```
pct_above_50ma  = float(etf_close[-1] > SMA(50)[-1])   # binary for ETF-level; use component data for deep breadth
pct_above_200ma = float(etf_close[-1] > SMA(200)[-1])
ad_ema_score    = clip((EMA(daily_changes, span=10)[-1] + 0.01) / 0.02, 0, 1)
sector_participation = count(sectors with positive 5d return) / total_sectors

breadth_score = 0.30 * pct_above_50ma
              + 0.30 * pct_above_200ma
              + 0.20 * ad_ema_score
              + 0.20 * sector_participation
```

**Deterioration detection** (warning flag):
```
breadth_5d_change = breadth_score[-1] - breadth_score[-5]
deterioration_flag = breadth_5d_change < -0.15
```

**Libraries**: `pandas-ta` (SMA/EMA), `numpy`
**Data**: Daily OHLCV for each ETF in universe
**Lookbacks**: SMA-50, SMA-200; 10-day EMA for A/D; 5-day for sector participation
**Update frequency**: Daily
**Edge cases**:
- ETF-level breadth is a proxy; for deeper analysis, use Polygon's snapshot endpoint to get component-level data for SPY/QQQ
- McClellan Oscillator requires NYSE advance/decline data (FRED series `USAADVN` / `USADECN`)

---

## Factor 6: Trend

**Formula**:
```
# MA alignment (bull = 1.0, bear = 0.0, mixed = 0.5)
ema20  = EWM(close, span=20).mean()
ema50  = EWM(close, span=50).mean()
ema200 = EWM(close, span=200).mean()
ma_score = 1.0 if ema20 > ema50 > ema200 else
           0.0 if ema20 < ema50 < ema200 else 0.5

# Jegadeesh-Titman momentum (12-1 months)
momentum_12_1 = pct_change(close, 252) - pct_change(close, 21)

# Relative strength vs SPY
rs_3m = pct_change(close, 63) / pct_change(SPY_close, 63)

# Cross-sectional percentile ranking (computed across full universe)
momentum_pct = percentile_rank(momentum_12_1, cross_section_of_all_etfs)
rs_pct       = percentile_rank(rs_3m, cross_section_of_all_etfs)

trend_score = 0.40 * ma_score
            + 0.35 * momentum_pct
            + 0.25 * rs_pct
```

**Libraries**: `pandas-ta` (EMA), `scipy.stats.percentileofscore`
**Data**: Daily OHLCV for all ETFs + SPY as benchmark
**Lookbacks**: EMA 20/50/200; 252-day for 12m momentum; 21-day for 1m; 63-day for RS
**Update frequency**: Daily
**Edge cases**:
- Momentum must be computed cross-sectionally — run for all ETFs, then rank
- Use excess returns (ETF − SPY) for relative strength, not raw returns, to isolate alpha
- 12-1 momentum skips most recent month to avoid short-term reversal contamination

---

## Factor 7: Liquidity

**Formula**:
```
adv_30d       = mean(close * volume, periods=30)            # 30-day average dollar volume
adv_score     = min(adv_30d / 20_000_000, 1.0)             # $20M threshold; capped at 1.0

# Roll (1984) spread estimator from OHLCV
log_returns   = log(close / close.shift(1))
cov_ret       = rolling_cov(log_returns, log_returns.shift(1), window=20)
spread_est_bps = 2 * sqrt(max(-cov_ret, 0)) * 10_000
spread_score  = max(0, 1 - spread_est_bps / 10)            # 0 at 10 bps, 1 at 0 bps

liquidity_score = 0.50 * adv_score + 0.50 * spread_score
```

**Minimum threshold gate** (hard filter, not scored):
```
if adv_30d < 20_000_000 or spread_est_bps > 10:
    exclude_from_universe()
```

**Libraries**: `numpy`, `pandas`
**Data**: Daily OHLCV; Level 1 quotes (Alpaca) for real spread
**Lookbacks**: 30-day ADV; 20-day for spread
**Update frequency**: Daily (ADV); intraday for real-time spread monitoring
**Edge cases**:
- Roll estimator is a proxy; use real bid/ask from Alpaca quotes endpoint when available
- ADV can spike on news days; use median not mean for robustness

---

## Factor 8: Slippage

**Purpose**: Pre-trade cost estimate to gate order submission and score tradability.

**Formula** (Almgren-Chriss simplified):
```
participation_rate = order_notional / adv_30d
linear_impact      = spread_bps / 2
market_impact_bps  = 0.10 * daily_vol_pct * 100 * sqrt(participation_rate)
# alpha=0.10 calibrated for liquid ETFs

total_slippage_bps = linear_impact + market_impact_bps

slippage_score = max(0, 1 - total_slippage_bps / 10)
# 0 at 10+ bps expected cost; 1 at near-zero cost
```

**Post-trade measurement**:
```
realized_slippage_bps = (fill_price - mid_price_at_submission) / mid_price * 10_000
```

**Libraries**: `numpy`
**Data**: ADV from OHLCV; real-time spread from quotes; daily realized vol
**Update frequency**: Pre-trade (computed just before order submission)
**Edge cases**:
- Always measure and log realized vs estimated slippage; recalibrate `alpha` coefficient quarterly
- Do not submit orders with estimated slippage > 8 bps unless forced by risk management

---

## Normalization Reference

All factors use one of these three normalization approaches:

| Method | Formula | When to Use |
|--------|---------|-------------|
| Percentile Rank | `percentileofscore(history, value) / 100` | Fat-tailed distributions (vol, momentum) |
| Z-Score → Sigmoid | `sigmoid((x - μ) / σ)` | Symmetric distributions (dispersion, correlation) |
| Min-Max Cap | `clip((x - min) / (max - min), 0, 1)` | Hard-bounded metrics (ADV score, spread score) |

The standard lookback for normalization history is **252 trading days** (≈1 year) unless specified otherwise.
