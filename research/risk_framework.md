# Risk Management Framework

## Risk Controls Summary

| Control | Threshold | Action |
|---------|-----------|--------|
| Portfolio max drawdown | -15% | Halt all trading; alert |
| Daily loss limit | -3% | Halt for remainder of session |
| Long hard stop | -5% from entry | Auto-liquidate position |
| Long trailing stop | -8% from high-water mark | Auto-liquidate position |
| Long soft warning | -3% from entry | Reduce position 50%, monitor |
| Short hard stop | -4% from entry (tighter) | Auto-liquidate position |
| Short trailing stop | -6% from low-water mark (tighter) | Auto-liquidate position |
| Short soft warning | -2.5% from entry (tighter) | Reduce position 50%, monitor |
| VaR (95%, 1-day) | >2% of portfolio | Reduce position sizes |
| Average portfolio correlation | >0.85 for 3 days | Force diversification |
| Vol targeting | 12% annualized | Scale positions down |
| Liquidity shock | ADV drops >50% | Suspend new entries for affected ETF |

**Why shorts use tighter stops than longs**: upside risk on a short is theoretically unlimited; gap-ups and short squeezes are faster and more violent than sell-offs. We cut losing shorts faster by design. The cash risk per trade remains comparable across books because position sizing is adjusted inversely to stop distance.

---

## Drawdown Monitor

```python
class DrawdownMonitor:
    def __init__(self, redis_client, alert_manager):
        self.redis  = redis_client
        self.alerts = alert_manager

    def check(self, portfolio_value_series: pd.Series) -> str:
        """
        Returns: 'ok' | 'warn' | 'halt'
        """
        peak   = portfolio_value_series.cummax()
        dd     = (portfolio_value_series - peak) / peak
        current_dd = float(dd.iloc[-1])

        max_dd_limit  = float(self.redis.hget('config:risk', 'max_drawdown')   or -0.15)
        warn_dd_limit = float(self.redis.hget('config:risk', 'alert_drawdown_warn') or -0.08)
        daily_limit   = float(self.redis.hget('config:risk', 'daily_loss_limit') or -0.03)

        # Daily loss check
        if len(portfolio_value_series) >= 2:
            daily_return = (portfolio_value_series.iloc[-1] / portfolio_value_series.iloc[-2]) - 1
            if daily_return < daily_limit:
                self._halt('daily_loss_limit_breached', daily_return)
                return 'halt'

        if current_dd < max_dd_limit:
            self._halt('max_drawdown_breached', current_dd)
            return 'halt'

        if current_dd < warn_dd_limit:
            self.alerts.send(
                level='warning',
                message=f'Drawdown at {current_dd:.1%} — approaching limit of {max_dd_limit:.1%}'
            )
            return 'warn'

        return 'ok'

    def _halt(self, reason: str, value: float):
        self.redis.set('kill_switch:trading', '1')
        self.alerts.send(
            level='critical',
            message=f'TRADING HALTED — {reason}: {value:.2%}',
            channels=['email', 'slack']
        )
```

---

## Stop-Loss Engine

Stop thresholds are direction-aware. Shorts use **tighter** stops than longs.

```python
# Default thresholds per direction (all configurable via config:risk Redis hash)
_DEFAULTS = {
    'long': {
        'position_stop_loss':    -0.05,   # -5% from entry
        'position_trailing_stop': -0.08,  # -8% from high-water mark
        'position_soft_warn':    -0.03,   # -3% from entry → reduce 50%
    },
    'short': {
        'short_stop_loss':    -0.04,      # -4% from entry (tighter)
        'short_trailing_stop': -0.06,     # -6% from low-water mark (tighter)
        'short_soft_warn':    -0.025,     # -2.5% from entry → reduce 50%
    },
}
```

**Watermark semantics differ by direction**:
- **Longs**: `high_watermark` = highest close since entry. Trailing stop fires when price
  retreats more than 8% from that peak.
- **Shorts**: `high_watermark` column stores the *lowest* close since entry (the best
  profit point — the "low-water mark"). Trailing stop fires when price rallies more than
  6% above that trough.  `mark_to_market()` in `PositionTracker` updates this field with
  `min()` for shorts and `max()` for longs accordingly.

**Directional P&L math** (both sides checked by `StopLossEngine.check_position`):
```python
if direction == 'short':
    from_entry = (entry_price - current_price) / entry_price  # positive = profit
    from_hwm   = (low_watermark - current_price) / low_watermark  # negative = rally from best
else:
    from_entry = (current_price - entry_price) / entry_price
    from_hwm   = (current_price - high_watermark) / high_watermark
```

**R-multiple tracking**: the `trades` table records `stop_distance_at_entry` (the fraction
of entry price to the stop) and `r_multiple` (filled at close: `realized_pnl_pct /
stop_distance_at_entry`). +1R = closed at exactly the stop loss distance in profit;
-1R = stopped out. Weekly analytics group R-multiples by signal score bucket to measure
whether high-conviction signals produce better outcomes than marginal ones.

---

## Volatility Targeting

Position sizes are scaled so that the portfolio targets a 12% annualized volatility. When realized portfolio vol exceeds the target, all weights are proportionally reduced, and excess is held as cash.

```python
def apply_vol_target(
    weights: dict[str, float],
    returns_df: pd.DataFrame,
    vol_target: float,
) -> tuple[dict[str, float], float]:
    """
    Returns (scaled_weights, scale_factor).
    scale_factor < 1.0 means positions were reduced.
    """
    syms = [s for s in weights if s in returns_df.columns]
    if not syms:
        return weights, 1.0

    w_arr = np.array([weights[s] for s in syms])
    cov   = returns_df[syms].rolling(60).cov().dropna().iloc[-len(syms):].values

    if cov.shape != (len(syms), len(syms)):
        return weights, 1.0

    portfolio_vol = float(np.sqrt(w_arr @ cov @ w_arr * 252))
    if portfolio_vol <= 0:
        return weights, 1.0

    scale = min(vol_target / portfolio_vol, 1.0)  # never leverage up
    scaled = {sym: weights[sym] * scale for sym in weights}
    return scaled, scale
```

---

## VaR Monitoring

```python
def compute_var(
    returns: pd.Series,
    confidence: float = 0.95,
    portfolio_value: float = 100_000,
) -> dict:
    """
    Parametric VaR + Historical VaR.
    """
    import scipy.stats as stats

    # Historical VaR
    hist_var = returns.quantile(1 - confidence)

    # Parametric VaR (normal distribution)
    z_score   = stats.norm.ppf(1 - confidence)
    param_var = returns.mean() + z_score * returns.std()

    # CVaR (Expected Shortfall)
    cvar = returns[returns <= hist_var].mean()

    return {
        'var_pct':        round(hist_var, 6),
        'var_dollar':     round(abs(hist_var) * portfolio_value, 2),
        'cvar_pct':       round(cvar, 6),
        'cvar_dollar':    round(abs(cvar) * portfolio_value, 2),
        'parametric_var': round(param_var, 6),
        'confidence':     confidence,
    }
```

---

## Correlation Risk Monitor

```python
def check_portfolio_correlation(
    positions: list[str],
    prices_df: pd.DataFrame,
    redis_client,
) -> dict:
    """
    Warn when average portfolio-level pairwise correlation exceeds threshold.
    """
    if len(positions) < 2:
        return {'avg_corr': 0.0, 'status': 'ok'}

    corr_limit = float(redis_client.hget('config:risk', 'correlation_limit') or 0.85)
    returns    = prices_df[positions].pct_change().dropna()
    corr_mat   = returns.rolling(60).corr().iloc[-len(positions):]
    upper_tri  = corr_mat.values[np.triu_indices(len(positions), k=1)]
    avg_corr   = float(np.mean(upper_tri))

    status = 'warn' if avg_corr > corr_limit else 'ok'
    if status == 'warn':
        redis_client.publish('channel:risk_alerts', json.dumps({
            'type':    'correlation_warning',
            'avg_corr': avg_corr,
            'threshold': corr_limit,
        }))

    return {'avg_corr': round(avg_corr, 4), 'status': status}
```

---

## Liquidity Shock Detection

```python
def detect_liquidity_shock(
    symbol: str,
    current_adv: float,
    redis_client,
) -> bool:
    """
    Detects if a symbol's ADV has dropped >50% relative to its 30-day average.
    Suspends new entries for that ETF for the session.
    """
    hist_adv_key = f'etf:{symbol}:adv_30d_m'
    hist_adv = float(redis_client.get(hist_adv_key) or current_adv)

    if hist_adv > 0 and (current_adv / hist_adv) < 0.50:
        redis_client.set(f'liquidity_shock:{symbol}', '1', ex=86400)
        return True
    return False
```

---

## Drawdown Mitigation Strategies

### 1. Regime-Based De-risking
When `regime = 'risk_off'`:
- Reduce `max_positions` from 10 → 5
- Increase cash buffer from 5% → 20%
- Rotate into defensive ETFs (XLV, XLU) regardless of composite score
- Disable new entries for cyclical sectors (XLE, XLF, XLY)

### 2. Vol-Triggered Position Reduction
When rolling 20-day portfolio vol > 1.5× vol_target (i.e., > 18%):
- Scale all positions to 50% of their target weights
- Restore gradually as vol reverts (10% per day)

### 3. Incremental Re-entry After Halt
After a trading halt from drawdown breach:
1. Require 5 consecutive `risk_ok` days before resuming
2. Start with 50% of normal position sizes for 10 days
3. Return to full sizing only after Sharpe > 0.5 on rolling 30-day window
4. Admin must manually flip `kill_switch:trading` from `1` → `0` (no auto-resume)

### 4. Stop-Loss Laddering
Positions do not use a single fixed stop. Thresholds differ by direction — shorts are
tighter at every rung because squeeze risk is asymmetric.

| Level | Long | Short |
|-------|------|-------|
| Soft warning | -3% from entry → reduce 50% | -2.5% from entry → reduce 50% |
| Hard stop | -5% from entry → immediate exit | -4% from entry → immediate exit |
| Trailing stop | -8% from high-water mark → exit | -6% from low-water mark → exit |

All thresholds are admin-configurable via `config:risk` Redis hash keys.
