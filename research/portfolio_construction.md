# Portfolio Construction

## Constraints Summary

| Parameter | Value | Notes |
|-----------|-------|-------|
| Max positions | 10 | Reduced to 5 in risk-off regime |
| Max sector exposure | 25% | Enforced via PyPortfolioOpt sector constraint |
| Max position size | 5% | Hard cap per position |
| Min position size | 1% | Avoids micro-allocations |
| Rebalance frequency | Daily | At 09:45 ET after open |
| Turnover limit | 50% per rebalance | Prevents excessive churn |
| Cash buffer | 5% | Permanent cash reserve |
| Optimization objective | Max Sharpe | Configurable: max_sharpe / min_vol / equal_weight / hrp |

---

## Allocation Method

### Step 1: Candidate Selection

```python
def select_candidates(ranked_signals: pd.DataFrame, regime: str, constraints) -> list[str]:
    """Select top-N ETFs with 'long' signal type."""
    max_pos = constraints.max_positions
    if regime == 'risk_off':
        max_pos = min(max_pos, 5)

    eligible = ranked_signals[ranked_signals['signal_type'] == 'long']
    return eligible.head(max_pos).index.tolist()
```

### Step 2: PyPortfolioOpt with Sector Constraints

```python
from pypfopt import EfficientFrontier, risk_models, expected_returns
from pypfopt.efficient_frontier import EfficientFrontier

def build_target_weights(
    candidates: list[str],
    prices_df: pd.DataFrame,
    sector_map: dict,
    constraints,
    objective: str = 'max_sharpe'
) -> dict[str, float]:
    """
    Returns target weights dict {symbol: weight}.
    Weights sum to (1 - cash_buffer).
    """
    if len(candidates) < 2:
        # Fallback: equal weight single position
        return {candidates[0]: 1.0 - constraints.cash_buffer_pct} if candidates else {}

    prices_subset = prices_df[candidates].dropna()

    mu = expected_returns.mean_historical_return(prices_subset, frequency=252)
    S  = risk_models.CovarianceShrinkage(prices_subset).ledoit_wolf()

    ef = EfficientFrontier(mu, S)

    # Max position size
    ef.add_constraint(lambda w: w <= constraints.max_position_size)   # 0.05
    ef.add_constraint(lambda w: w >= constraints.min_position_size)   # 0.01

    # Sector constraints: group by sector, cap at 25%
    _add_sector_constraints(ef, candidates, sector_map, constraints.max_sector_exposure)

    if objective == 'max_sharpe':
        weights = ef.max_sharpe(risk_free_rate=0.05)
    elif objective == 'min_vol':
        weights = ef.min_volatility()
    elif objective == 'equal_weight':
        n = len(candidates)
        return {sym: min(1.0 / n, constraints.max_position_size) for sym in candidates}
    elif objective == 'hrp':
        from pypfopt import HRPOpt
        hrp = HRPOpt(returns=prices_subset.pct_change().dropna())
        weights = hrp.optimize()
    else:
        weights = ef.max_sharpe()

    # Scale by (1 - cash_buffer)
    investable = 1.0 - constraints.cash_buffer_pct
    cleaned = ef.clean_weights()
    return {sym: round(w * investable, 6) for sym, w in cleaned.items() if w > 0.001}


def _add_sector_constraints(ef, candidates, sector_map, max_sector_pct):
    sectors = {}
    for sym in candidates:
        sec = sector_map.get(sym, 'Unknown')
        sectors.setdefault(sec, []).append(sym)

    for sec, syms in sectors.items():
        if len(syms) > 1:
            indices = [candidates.index(s) for s in syms]
            ef.add_constraint(lambda w, idx=indices: sum(w[i] for i in idx) <= max_sector_pct)
```

---

## Volatility-Adjusted Position Sizing

When `vol_scaling_enabled = true`, positions are scaled to target a portfolio-level annualized volatility of `vol_target` (default 12%).

```python
def volatility_scale_weights(
    weights: dict[str, float],
    prices_df: pd.DataFrame,
    vol_target: float = 0.12
) -> dict[str, float]:
    """
    Scale down all weights proportionally when portfolio vol > vol_target.
    """
    returns = prices_df[list(weights.keys())].pct_change().dropna()
    w_arr   = np.array([weights[sym] for sym in returns.columns])

    cov_matrix = returns.rolling(60).cov().iloc[-len(returns.columns):].values
    portfolio_vol = np.sqrt(w_arr @ cov_matrix @ w_arr * 252)

    if portfolio_vol > vol_target:
        scale_factor = vol_target / portfolio_vol
        return {sym: w * scale_factor for sym, w in weights.items()}

    return weights
```

---

## Beta-Neutral Alternative (Research Mode)

For a market-neutral overlay, compute portfolio beta vs SPY and hedge residual exposure:

```python
def compute_portfolio_beta(weights: dict, prices_df: pd.DataFrame, spy_returns: pd.Series) -> float:
    port_returns = sum(
        w * prices_df[sym].pct_change()
        for sym, w in weights.items()
    )
    cov = np.cov(port_returns.dropna(), spy_returns.dropna())
    return cov[0, 1] / cov[1, 1]

def hedge_beta(weights: dict, portfolio_beta: float, spy_symbol: str = 'SPY') -> dict:
    """Add short SPY position to neutralize beta. For research purposes only."""
    hedged = weights.copy()
    hedged[spy_symbol] = hedged.get(spy_symbol, 0) - portfolio_beta
    return hedged
```

---

## Rebalance Engine

```python
class RebalanceEngine:
    MIN_TRADE_THRESHOLD = 0.005  # 50 bps; skip micro-trades

    def generate_orders(
        self,
        target_weights: dict[str, float],
        current_positions: dict[str, float],  # symbol → current weight
        portfolio_value: float,
        constraints,
    ) -> list[dict]:
        orders = []
        all_symbols = set(target_weights) | set(current_positions)

        # Turnover check
        total_turnover = sum(
            abs(target_weights.get(sym, 0) - current_positions.get(sym, 0))
            for sym in all_symbols
        ) / 2  # one-way turnover

        if total_turnover > constraints.turnover_limit_pct:
            # Scale back rebalance: move halfway toward target
            target_weights = {
                sym: current_positions.get(sym, 0) +
                     0.5 * (target_weights.get(sym, 0) - current_positions.get(sym, 0))
                for sym in all_symbols
            }

        for sym in all_symbols:
            target = target_weights.get(sym, 0.0)
            current = current_positions.get(sym, 0.0)
            delta = target - current

            if abs(delta) < self.MIN_TRADE_THRESHOLD:
                continue  # skip micro-rebalance

            notional = abs(delta) * portfolio_value
            orders.append({
                'symbol':   sym,
                'side':     'buy' if delta > 0 else 'sell',
                'notional': round(notional, 2),
                'target_weight': target,
                'current_weight': current,
            })

        # Sort: sells first (free up cash before buys)
        orders.sort(key=lambda o: (0 if o['side'] == 'sell' else 1))
        return orders
```

---

## Portfolio State Snapshot (Redis)

After each rebalance, cache current holdings for fast reads:

```python
# Write
redis.set('cache:portfolio:current', json.dumps({
    'weights':         target_weights,
    'positions':       positions_dict,
    'portfolio_value': portfolio_value,
    'rebalance_time':  timestamp,
    'regime':          current_regime,
}), ex=300)

# Read (anywhere in the stack)
snapshot = json.loads(redis.get('cache:portfolio:current') or '{}')
```
