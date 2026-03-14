# Backtesting Framework & Methodology

## Library Selection

| Phase | Library | Rationale |
|-------|---------|-----------|
| Research / parameter optimization | `vectorbt` | Numba-accelerated; runs 1M+ simulations in seconds; ideal for factor weight sweeps |
| ETF rotation validation | `bt` | Purpose-built for basket rotation and calendar rebalancing |
| Production validation | `backtrader` or `nautilus_trader` | Event-driven; realistic fill models; supports Alpaca live |

Install:
```bash
pip install vectorbt bt backtrader quantstats
```

---

## Historical Data Requirements

| Requirement | Specification |
|-------------|--------------|
| History length | 10+ years (2014–present minimum; 2008 crash desirable) |
| Data source | Polygon.io (daily OHLCV), CBOE (VIX), FRED (macro) |
| Resolution | Daily bars for factors; 1-minute bars for slippage modeling |
| Options data | CBOE DataShop for historical IV backfill |
| News sentiment | EODHD or Benzinga archive for pre-scored sentiment |
| Survivorship bias | Use point-in-time universe (see below) |

---

## Survivorship Bias Control

ETFs have minimal survivorship bias vs equities, but pitfalls remain:

1. **Point-in-time AUM filter**: An ETF meeting the $2B AUM threshold today may not have met it in 2015. Use historical AUM snapshots from EODHD.

2. **Inception date enforcement**: Only include an ETF in the backtest universe from its inception date + 180-day buffer.

3. **Index reconstitution**: Sector ETF composition changes over time. For deep research, use annual holdings files from SPDR/iShares IR websites.

```python
def build_point_in_time_universe(
    target_date: date,
    etf_metadata_df: pd.DataFrame,  # columns: symbol, inception_date, aum_bn_history (timeseries)
    aum_threshold_bn: float = 2.0,
    vol_threshold_m: float = 20.0,
) -> list[str]:
    eligible = etf_metadata_df[
        (etf_metadata_df['inception_date'] <= target_date - timedelta(days=180)) &
        (etf_metadata_df['aum_bn_at_date'].apply(
            lambda ts: ts.asof(target_date) if isinstance(ts, pd.Series) else ts
        ) >= aum_threshold_bn)
    ]
    return eligible['symbol'].tolist()
```

---

## Transaction Cost Model

```python
class TransactionCostModel:
    # Alpaca paper/live: zero commission
    COMMISSION      = 0.0
    SEC_FEE_PER_DOLLAR = 0.0000229    # $22.90 per $1M sold (2026 SEC rate)
    FINRA_TAF_PER_SHARE = 0.000166   # $0.000166 per share sold (max $8.30)

    def estimate_total_cost_bps(
        self,
        symbol: str,
        notional: float,
        side: str,           # 'buy' or 'sell'
        adv_m: float,        # 30-day ADV in dollars
        spread_bps: float,   # current bid-ask spread in bps
        daily_vol_pct: float # 1-day historical vol in decimal (e.g., 0.01 = 1%)
    ) -> float:
        """Returns total estimated transaction cost in basis points."""

        # Half-spread cost (market order assumption)
        half_spread = spread_bps / 2

        # Almgren-Chriss simplified market impact
        participation = notional / adv_m
        market_impact = 0.10 * daily_vol_pct * 10_000 * np.sqrt(participation)

        # Regulatory fees (sell side only)
        if side == 'sell':
            shares_est  = notional / 50  # rough estimate at $50/share
            reg_fees    = (self.SEC_FEE_PER_DOLLAR * notional +
                           self.FINRA_TAF_PER_SHARE * shares_est)
            reg_fees_bps = reg_fees / notional * 10_000
        else:
            reg_fees_bps = 0.0

        return round(half_spread + market_impact + reg_fees_bps, 4)
```

**Typical costs for liquid ETFs** (SPY, QQQ, XLK):
- Half-spread: 0.15–0.30 bps
- Market impact (0.1% ADV participation): ~0.1–0.5 bps
- Regulatory fees: ~0.3–0.5 bps (sell side)
- **Total round-trip: ~1–3 bps**

---

## Walk-Forward Testing Methodology

Use `TimeSeriesSplit` with an expanding window and a 21-day gap to prevent leakage between train and test sets.

```python
from sklearn.model_selection import TimeSeriesSplit
import pandas as pd
import numpy as np

def walk_forward_test(
    strategy_fn,       # callable(prices, params) → pd.Series of daily returns
    prices_df: pd.DataFrame,
    param_grid: list[dict],
    n_splits: int = 8,
    gap_days: int = 21,
) -> pd.DataFrame:
    """
    For each fold:
      1. Optimize factor weights on training data
      2. Evaluate on out-of-sample test data (no refitting)
    """
    tscv = TimeSeriesSplit(n_splits=n_splits, gap=gap_days)
    results = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(prices_df)):
        train_df = prices_df.iloc[train_idx]
        test_df  = prices_df.iloc[test_idx]

        # Grid search on train
        best_params, best_is_sharpe = _grid_search(strategy_fn, train_df, param_grid)

        # Evaluate on test (true OOS)
        test_returns = strategy_fn(test_df, best_params)
        oos_sharpe   = compute_sharpe(test_returns)
        oos_mdd      = compute_max_drawdown(test_returns)

        results.append({
            'fold':         fold + 1,
            'train_start':  train_df.index[0].date(),
            'train_end':    train_df.index[-1].date(),
            'test_start':   test_df.index[0].date(),
            'test_end':     test_df.index[-1].date(),
            'is_sharpe':    round(best_is_sharpe, 3),
            'oos_sharpe':   round(oos_sharpe, 3),
            'oos_mdd':      round(oos_mdd, 4),
            'best_params':  best_params,
        })

    return pd.DataFrame(results)


def _grid_search(strategy_fn, train_df, param_grid):
    best_sharpe, best_params = -np.inf, {}
    for params in param_grid:
        returns = strategy_fn(train_df, params)
        sharpe  = compute_sharpe(returns)
        if sharpe > best_sharpe:
            best_sharpe, best_params = sharpe, params
    return best_params, best_sharpe
```

**Degradation check**: If OOS Sharpe < 0.5 × IS Sharpe on average across folds, the strategy is overfit. Simplify factor model or widen lookback windows.

---

## Monte Carlo Stress Testing

```python
def monte_carlo_bootstrap(
    portfolio_returns: pd.Series,
    n_simulations: int = 10_000,
    seed: int = 42,
) -> dict:
    """
    Bootstrap resampling of return sequence.
    Tests whether performance persists under different orderings.
    """
    np.random.seed(seed)
    n = len(portfolio_returns)
    sharpes, mdds = [], []

    for _ in range(n_simulations):
        sample    = portfolio_returns.sample(n=n, replace=True).values
        cum_ret   = np.cumprod(1 + sample)
        sharpe    = (sample.mean() / sample.std()) * np.sqrt(252)
        peak      = np.maximum.accumulate(cum_ret)
        mdd       = float(((cum_ret - peak) / peak).min())
        sharpes.append(sharpe)
        mdds.append(mdd)

    return {
        'sharpe_p5':    round(np.percentile(sharpes, 5), 3),
        'sharpe_p50':   round(np.percentile(sharpes, 50), 3),
        'sharpe_p95':   round(np.percentile(sharpes, 95), 3),
        'mdd_median':   round(np.percentile(mdds, 50), 4),
        'mdd_worst_5pct': round(np.percentile(mdds, 5), 4),
        'prob_sharpe_above_1_5': round(np.mean(np.array(sharpes) > 1.5), 4),
        'prob_mdd_worse_15pct':  round(np.mean(np.array(mdds) < -0.15), 4),
    }
```

---

## Performance Metrics

All metrics computed using `quantstats`.

```python
import quantstats as qs

def full_tearsheet(
    returns: pd.Series,
    benchmark: pd.Series,  # SPY daily returns
    output_html: str = 'tearsheet.html',
) -> dict:
    qs.reports.html(returns, benchmark=benchmark, output=output_html)

    return {
        'sharpe_ratio':      round(qs.stats.sharpe(returns), 3),
        'sortino_ratio':     round(qs.stats.sortino(returns), 3),
        'calmar_ratio':      round(qs.stats.calmar(returns), 3),
        'max_drawdown':      round(qs.stats.max_drawdown(returns), 4),
        'cagr':              round(qs.stats.cagr(returns), 4),
        'win_rate':          round(qs.stats.win_rate(returns), 4),
        'profit_factor':     round(qs.stats.profit_factor(returns), 3),
        'information_ratio': round(qs.stats.information_ratio(returns, benchmark), 3),
        'beta':              round(qs.stats.beta(returns, benchmark), 3),
        'alpha_annual':      round(qs.stats.alpha(returns, benchmark), 4),
        'tail_ratio':        round(qs.stats.tail_ratio(returns), 3),
        'var_95':            round(qs.stats.value_at_risk(returns), 4),
    }
```

---

## Target Metrics vs Observed (Template)

Fill in after running backtests. This table should be updated as research progresses.

| Metric | Target | IS Result | OOS Result | Notes |
|--------|--------|-----------|------------|-------|
| Sharpe ratio | > 1.5 | — | — | |
| Max drawdown | < -15% | — | — | |
| Win rate | > 50% | — | — | |
| CAGR | > 12% | — | — | |
| Calmar ratio | > 0.8 | — | — | |
| Annual turnover | Optimized | — | — | |
| Avg transaction cost | < 3 bps RT | — | — | |

---

## Known Backtesting Pitfalls to Avoid

1. **Look-ahead bias**: Never use data not available at signal time (e.g., today's close to generate today's trade entry at today's open).
2. **Filling at open, not close**: Generate signals at T-close; execute at T+1 open.
3. **Bid-ask bounce**: Using mid-price overstates returns; model half-spread on every fill.
4. **VIX data delay**: VIX close is not available until after the close; use T-1 VIX when computing T signals.
5. **Factor score look-ahead**: The 252-day normalization window must only use data available at the signal date — never use the full dataset for normalization in backtests.
6. **Overfitting to 2021–2023 momentum**: Walk-forward results must cover at least one bear market (2022 drawdown) and one high-vol regime (COVID March 2020).
