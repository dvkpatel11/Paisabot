# Sector Rotation Detection Model

## Regime Classification

The system classifies the market into one of four regimes. Each regime drives a distinct portfolio action.

| Regime | Characteristics | Portfolio Action |
|--------|----------------|-----------------|
| **Trending** | High breadth, rising momentum, low vol, avg corr declining | Concentrate in top-ranked sectors; full position sizing |
| **Rotation** | High dispersion, falling correlation, mixed momentum divergence | Diversify; hold leaders, exit laggards; moderate sizing |
| **Risk-Off** | Falling breadth, rising correlation, negative momentum | Reduce net exposure; rotate into XLV/XLU; hold cash buffer |
| **Consolidation** | Low dispersion, stable correlation, flat momentum | Hold positions; no new entries; reduce turnover |

---

## Rotation Confidence Score

```python
def classify_regime(factor_scores: dict) -> tuple[str, float]:
    """
    factor_scores: dict with keys:
        breadth, correlation, dispersion, trend, volatility
        All values normalized to [0, 1].
        correlation score is already inverted (high score = low actual correlation).

    Returns: (regime_name, confidence_score [0.0 - 1.0])
    """
    b = factor_scores['breadth']
    c = factor_scores['correlation']   # high = low actual corr
    d = factor_scores['dispersion']
    t = factor_scores['trend']
    v = factor_scores['volatility']    # high = low/declining vol

    # Rotation: high dispersion + low correlation + mixed trend
    rotation_score = (d * 0.40
                    + c * 0.30
                    + (0.5 - abs(t - 0.5)) * 0.30)  # penalizes strong trend in either direction

    # Trending: high breadth + strong trend + stable vol
    trending_score = (b * 0.35
                    + t * 0.35
                    + v * 0.30)

    # Risk-Off: low breadth + weak trend + high vol
    risk_off_score = ((1 - b) * 0.35
                    + (1 - t) * 0.35
                    + (1 - v) * 0.30)

    # Consolidation: residual (none of the above is dominant)
    scores = {
        'rotation':      rotation_score,
        'trending':      trending_score,
        'risk_off':      risk_off_score,
        'consolidation': 1.0 - max(rotation_score, trending_score, risk_off_score),
    }

    best_regime = max(scores, key=scores.get)
    confidence  = scores[best_regime]

    # Regime persistence: require ≥0.55 confidence to switch regime
    # If below threshold, caller should retain previous regime
    if confidence < 0.55:
        return ('consolidation', 0.50)

    return (best_regime, round(confidence, 4))
```

---

## Correlation Collapse Detection

Triggered when rolling 60-day average pairwise sector correlation drops by more than 2 standard deviations below its 252-day mean. This signals a potential regime break and the start of a rotation window.

```python
def detect_correlation_collapse(
    corr_history: pd.Series,
    threshold_zscore: float = -2.0
) -> bool:
    """
    corr_history: daily series of average pairwise sector correlation (60-day rolling)

    Returns True when correlation has collapsed significantly — rotation opportunity signal.
    """
    if len(corr_history) < 60:
        return False

    current   = corr_history.iloc[-1]
    mean_252  = corr_history.rolling(252).mean().iloc[-1]
    std_252   = corr_history.rolling(252).std().iloc[-1]

    if std_252 == 0 or np.isnan(std_252):
        return False

    zscore = (current - mean_252) / std_252
    return zscore < threshold_zscore
```

**Interpretation**: When `collapse_detected = True`:
- The market is transitioning from a risk-on/correlated state to differentiated sector performance
- Raise dispersion factor weight or override regime to `rotation`
- Increase position count from default up to max (10)

---

## Momentum Divergence Score

Measures the spread between the best and worst performing sectors over a 3-month horizon. High divergence with positive leadership = textbook rotation signal.

```python
def momentum_divergence_score(
    sector_etfs: list[str],
    prices_df: pd.DataFrame,
    lookback_days: int = 63
) -> float:
    """
    Returns percentile rank of current leader/laggard momentum spread
    vs trailing 252-day history. Range: [0, 1].

    High score = strong, historically significant divergence across sectors.
    """
    returns = prices_df[sector_etfs].pct_change(lookback_days)

    # Build 252-day history of divergence
    divergence_history = []
    for i in range(lookback_days + 252, len(returns)):
        r = returns.iloc[i]
        top3    = r.nlargest(3).mean()
        bottom3 = r.nsmallest(3).mean()
        divergence_history.append(top3 - bottom3)

    if len(divergence_history) < 60:
        return 0.5  # neutral on insufficient history

    current_r   = returns.iloc[-1]
    current_div = current_r.nlargest(3).mean() - current_r.nsmallest(3).mean()

    from scipy.stats import percentileofscore
    return round(percentileofscore(divergence_history, current_div) / 100, 4)
```

---

## Rotation Entry Criteria

A rotation trade is actionable when **all three** signals align:

| Signal | Entry Condition |
|--------|----------------|
| Correlation | `avg_pairwise_corr` below 252-day mean (z-score < -1.0) |
| Dispersion | `dispersion_score` > 0.65 (top tertile) |
| Momentum Divergence | `momentum_divergence_score` > 0.60 |

And `regime = 'rotation'` with `confidence ≥ 0.55`.

**Exit / regime change**: Regime reverts to `trending` or `risk_off` when:
- Correlation recovers above 252-day mean
- Dispersion score drops below 0.40 for 3+ consecutive days

---

## Regime Persistence Rules

To prevent excessive regime flipping (churn), enforce:
1. Minimum 3 consecutive days at a new regime before declaring a regime change
2. When in `risk_off`, require `confidence ≥ 0.65` to exit (higher bar to re-enter risk)
3. Log all regime changes with timestamp, confidence score, and factor snapshot

```python
class RegimeTracker:
    """Maintains current regime with persistence logic."""

    def __init__(self, min_consecutive: int = 3):
        self.current_regime     = 'consolidation'
        self.pending_regime     = None
        self.pending_count      = 0
        self.min_consecutive    = min_consecutive
        self.regime_history: list[dict] = []

    def update(self, new_regime: str, confidence: float, factor_scores: dict) -> str:
        if new_regime == self.current_regime:
            self.pending_regime = None
            self.pending_count  = 0
        elif new_regime == self.pending_regime:
            self.pending_count += 1
            if self.pending_count >= self.min_consecutive:
                # Extra bar for exiting risk_off
                min_needed = self.min_consecutive + 1 if self.current_regime == 'risk_off' else self.min_consecutive
                if self.pending_count >= min_needed:
                    self._record_change(new_regime, confidence, factor_scores)
                    self.current_regime  = new_regime
                    self.pending_regime  = None
                    self.pending_count   = 0
        else:
            self.pending_regime = new_regime
            self.pending_count  = 1

        return self.current_regime

    def _record_change(self, new_regime, confidence, factor_scores):
        self.regime_history.append({
            'timestamp':    pd.Timestamp.now(tz='UTC'),
            'from_regime':  self.current_regime,
            'to_regime':    new_regime,
            'confidence':   confidence,
            'factors':      factor_scores.copy(),
        })
```
