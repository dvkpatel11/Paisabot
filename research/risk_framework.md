# Risk Management Framework

## Risk Controls Summary

| Control | Threshold | Action |
|---------|-----------|--------|
| Portfolio max drawdown | -15% | Halt all trading; alert |
| Daily loss limit | -3% | Halt for remainder of session |
| Position stop-loss | -5% from entry | Auto-liquidate position |
| Position trailing stop | -8% from high-water mark | Auto-liquidate position |
| VaR (95%, 1-day) | >2% of portfolio | Reduce position sizes |
| Average portfolio correlation | >0.85 for 3 days | Force diversification |
| Vol targeting | 12% annualized | Scale positions down |
| Liquidity shock | ADV drops >50% | Suspend new entries for affected ETF |

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

```python
class StopLossEngine:
    def __init__(self, redis_client):
        self.redis = redis_client

    def check_position(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        high_watermark: float,  # highest close since entry
    ) -> tuple[bool, str]:
        """
        Returns (should_exit: bool, reason: str)
        """
        stop_loss_pct     = float(self.redis.hget('config:risk', 'position_stop_loss') or -0.05)
        trailing_stop_pct = float(self.redis.hget('config:risk', 'position_trailing_stop') or -0.08)

        from_entry = (current_price - entry_price) / entry_price
        from_hwm   = (current_price - high_watermark) / high_watermark

        if from_entry < stop_loss_pct:
            return True, f'stop_loss ({from_entry:.1%} from entry)'

        if from_hwm < trailing_stop_pct:
            return True, f'trailing_stop ({from_hwm:.1%} from high watermark of {high_watermark:.2f})'

        return False, 'ok'

    def scan_all_positions(self, positions: list[dict], current_prices: dict) -> list[dict]:
        """Returns list of positions that should be exited."""
        exits = []
        for pos in positions:
            if not pos['is_open']:
                continue
            current = current_prices.get(pos['symbol'])
            if current is None:
                continue
            should_exit, reason = self.check_position(
                symbol         = pos['symbol'],
                entry_price    = pos['entry_price'],
                current_price  = current,
                high_watermark = pos.get('high_watermark', pos['entry_price']),
            )
            if should_exit:
                exits.append({**pos, 'exit_reason': reason})
        return exits
```

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
Positions do not use a single fixed stop. The ladder is:
- Hard stop: -5% from entry → immediate exit
- Trailing stop: -8% from high-water mark → immediate exit
- Soft warning: -3% from entry → reduce position by 50%, monitor
