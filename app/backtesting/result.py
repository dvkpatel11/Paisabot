"""BacktestResult — container for backtesting output."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class BacktestResult:
    """Holds all output from a backtest run."""

    equity_curve: pd.Series          # date → portfolio value
    daily_returns: pd.Series         # date → daily return
    cumulative_returns: pd.Series    # date → cumulative return
    drawdown: pd.Series              # date → drawdown from peak
    trade_log: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialize for API response."""
        return {
            'equity_curve': {
                'dates': [d.isoformat() for d in self.equity_curve.index],
                'values': [round(v, 2) for v in self.equity_curve.values],
            },
            'daily_returns': {
                'dates': [d.isoformat() for d in self.daily_returns.index],
                'values': [round(v, 6) for v in self.daily_returns.values],
            },
            'drawdown': {
                'dates': [d.isoformat() for d in self.drawdown.index],
                'values': [round(v, 6) for v in self.drawdown.values],
            },
            'metrics': self.metrics,
            'trade_log': self.trade_log[-200:],  # cap for response size
        }
