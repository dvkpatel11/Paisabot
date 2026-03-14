"""Shared fixtures for factor tests.

Provides synthetic price data for 5 ETFs + SPY over 252 trading days.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.models.price_bars import PriceBar


def _generate_price_series(
    start_price: float,
    n_days: int,
    drift: float = 0.0003,
    volatility: float = 0.015,
    seed: int = 42,
) -> list[float]:
    """Generate synthetic daily close prices using geometric Brownian motion."""
    rng = np.random.RandomState(seed)
    prices = [start_price]
    for _ in range(n_days - 1):
        ret = drift + volatility * rng.randn()
        prices.append(prices[-1] * np.exp(ret))
    return prices


@pytest.fixture
def sample_price_data(db_session):
    """Insert 252 days of synthetic OHLCV data for 5 ETFs + SPY + sector ETFs.

    Returns list of symbols that were inserted.
    """
    symbols_config = {
        'SPY': {'start': 450.0, 'drift': 0.0004, 'vol': 0.012, 'seed': 1},
        'QQQ': {'start': 380.0, 'drift': 0.0005, 'vol': 0.015, 'seed': 2},
        'XLK': {'start': 200.0, 'drift': 0.0006, 'vol': 0.016, 'seed': 3},
        'XLE': {'start': 85.0, 'drift': 0.0001, 'vol': 0.022, 'seed': 4},
        'XLF': {'start': 38.0, 'drift': 0.0003, 'vol': 0.014, 'seed': 5},
        # Sector ETFs for dispersion/correlation
        'XLV': {'start': 140.0, 'drift': 0.0002, 'vol': 0.013, 'seed': 6},
        'XLI': {'start': 110.0, 'drift': 0.0003, 'vol': 0.014, 'seed': 7},
        'XLC': {'start': 75.0, 'drift': 0.0004, 'vol': 0.017, 'seed': 8},
        'XLY': {'start': 180.0, 'drift': 0.0003, 'vol': 0.016, 'seed': 9},
        'XLP': {'start': 78.0, 'drift': 0.0001, 'vol': 0.010, 'seed': 10},
        'XLU': {'start': 68.0, 'drift': 0.0001, 'vol': 0.011, 'seed': 11},
        'XLRE': {'start': 42.0, 'drift': 0.0002, 'vol': 0.018, 'seed': 12},
        'XLB': {'start': 88.0, 'drift': 0.0002, 'vol': 0.015, 'seed': 13},
    }

    n_days = 400  # calendar days to generate (~280 trading days after weekend skip)
    # Start date must be recent enough for _get_multi_closes lookback
    start_date = datetime.now(timezone.utc) - timedelta(days=n_days)

    all_bars = []
    for symbol, cfg in symbols_config.items():
        closes = _generate_price_series(
            cfg['start'], n_days,
            drift=cfg['drift'], volatility=cfg['vol'], seed=cfg['seed'],
        )
        for i, close in enumerate(closes):
            ts = start_date + timedelta(days=i)
            # Skip weekends
            if ts.weekday() >= 5:
                continue
            spread = close * 0.002
            bar = PriceBar(
                symbol=symbol,
                timeframe='1d',
                timestamp=ts,
                open=close * (1 - 0.001),
                high=close * (1 + 0.005),
                low=close * (1 - 0.005),
                close=close,
                volume=int(np.random.RandomState(cfg['seed'] + i).randint(1_000_000, 100_000_000)),
            )
            all_bars.append(bar)

    db_session.add_all(all_bars)
    db_session.commit()

    return list(symbols_config.keys())
