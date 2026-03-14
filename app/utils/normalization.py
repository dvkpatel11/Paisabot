import numpy as np
from scipy.special import expit
from scipy.stats import percentileofscore


def percentile_rank(value: float, history: np.ndarray) -> float:
    """Returns percentile rank in [0, 1].

    Uses only data available at signal time — never the full dataset.
    """
    if len(history) < 2:
        return 0.5
    return percentileofscore(history, value, kind='rank') / 100.0


def zscore_sigmoid(value: float, history: np.ndarray) -> float:
    """Z-score then sigmoid, output in (0, 1)."""
    if len(history) < 2:
        return 0.5
    mu = np.mean(history)
    sigma = np.std(history)
    if sigma == 0:
        return 0.5
    z = (value - mu) / sigma
    return float(expit(z))


def min_max_cap(value: float, min_val: float, max_val: float) -> float:
    """Clip to [0, 1] based on known bounds."""
    if max_val == min_val:
        return 0.5
    return float(np.clip((value - min_val) / (max_val - min_val), 0.0, 1.0))


def cross_sectional_percentile_rank(values: dict[str, float]) -> dict[str, float]:
    """Percentile-rank values cross-sectionally across a universe.

    Returns {symbol: rank_in_[0,1]}. Momentum and trend must be computed
    cross-sectionally across the full universe before ranking.
    """
    if not values:
        return {}
    arr = np.array(list(values.values()))
    return {
        symbol: percentileofscore(arr, val, kind='rank') / 100.0
        for symbol, val in values.items()
    }
