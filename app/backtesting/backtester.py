"""Vectorized backtester for Paisabot weight strategies.

Loads historical price_bars, computes simplified factor proxies at each
rebalance date using only lookback data (no look-ahead bias), ranks the
universe by composite score with configurable weights, and tracks portfolio
returns with transaction cost modelling.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import structlog

from app.backtesting.result import BacktestResult

logger = structlog.get_logger()

DEFAULT_WEIGHTS = {
    'trend': 0.25,
    'volatility': 0.20,
    'sentiment': 0.15,
    'breadth': 0.15,
    'dispersion': 0.15,
    'liquidity': 0.10,
}


class VectorizedBacktester:
    """Run a vectorized backtest using historical price data.

    At each rebalance date the backtester:
      1. Computes factor proxies from lookback data only
      2. Ranks universe by composite score using provided weights
      3. Selects top-N ETFs
      4. Equal-weights the selected positions
      5. Tracks portfolio returns net of slippage
    """

    def __init__(
        self,
        db_session,
        weights: dict[str, float] | None = None,
        initial_capital: float = 100_000,
        rebalance_freq: str = 'weekly',
        max_positions: int = 10,
        slippage_bps: float = 2.0,
    ):
        self._db = db_session
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self.initial_capital = initial_capital
        self.rebalance_freq = rebalance_freq
        self.max_positions = max_positions
        self.slippage_bps = slippage_bps

        # Normalize weights
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

    def run(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> BacktestResult:
        """Run backtest over the given date range."""
        prices = self._load_prices(start_date, end_date, symbols)
        if prices.empty or len(prices) < 20:
            return self._empty_result()

        symbols_list = list(prices.columns)
        returns = prices.pct_change().fillna(0)
        trading_dates = prices.index.tolist()

        # Determine rebalance dates
        rebalance_dates = self._get_rebalance_dates(trading_dates)

        # Run simulation
        portfolio_value = self.initial_capital
        equity = pd.Series(dtype=float)
        current_weights = {}
        trade_log = []

        for i, dt in enumerate(trading_dates):
            # Apply returns
            if current_weights and i > 0:
                day_return = sum(
                    w * returns.loc[dt, sym]
                    for sym, w in current_weights.items()
                    if sym in returns.columns
                )
                portfolio_value *= (1 + day_return)

            equity[dt] = portfolio_value

            # Rebalance
            if dt in rebalance_dates:
                lookback_end = i
                lookback_start = max(0, i - 60)
                if lookback_end - lookback_start < 10:
                    continue

                lookback_prices = prices.iloc[lookback_start:lookback_end + 1]
                scores = self._compute_scores(lookback_prices, symbols_list)
                ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                top_n = ranked[:self.max_positions]

                # Equal weight
                new_weights = {}
                if top_n:
                    w = 1.0 / len(top_n)
                    for sym, score in top_n:
                        new_weights[sym] = w

                # Slippage cost on turnover
                turnover = self._calc_turnover(current_weights, new_weights)
                cost = turnover * self.slippage_bps / 10_000
                portfolio_value *= (1 - cost)
                equity[dt] = portfolio_value

                # Log trades
                for sym in set(list(current_weights.keys()) + list(new_weights.keys())):
                    old_w = current_weights.get(sym, 0)
                    new_w = new_weights.get(sym, 0)
                    if abs(new_w - old_w) > 0.001:
                        trade_log.append({
                            'date': dt.isoformat(),
                            'symbol': sym,
                            'side': 'buy' if new_w > old_w else 'sell',
                            'old_weight': round(old_w, 4),
                            'new_weight': round(new_w, 4),
                            'score': round(scores.get(sym, 0), 4),
                        })

                current_weights = new_weights

        if equity.empty:
            return self._empty_result()

        # Compute result series
        daily_returns = equity.pct_change().fillna(0)
        cumulative_returns = (1 + daily_returns).cumprod() - 1
        peak = equity.cummax()
        drawdown = (equity - peak) / peak

        metrics = self._compute_metrics(equity, daily_returns, drawdown, trade_log)

        return BacktestResult(
            equity_curve=equity,
            daily_returns=daily_returns,
            cumulative_returns=cumulative_returns,
            drawdown=drawdown,
            trade_log=trade_log,
            metrics=metrics,
        )

    # ── factor proxy computation ─────────────────────────────────

    def _compute_scores(
        self, prices: pd.DataFrame, symbols: list[str],
    ) -> dict[str, float]:
        """Compute simplified factor proxies from price data only.

        Uses only the lookback window provided (no look-ahead).
        """
        if len(prices) < 5:
            return {s: 0.5 for s in symbols}

        returns = prices.pct_change().dropna()
        if returns.empty:
            return {s: 0.5 for s in symbols}

        scores = {}
        for sym in symbols:
            if sym not in prices.columns:
                scores[sym] = 0.5
                continue

            col = prices[sym].dropna()
            ret = returns[sym].dropna() if sym in returns.columns else pd.Series(dtype=float)

            if len(col) < 5 or len(ret) < 5:
                scores[sym] = 0.5
                continue

            # Trend: price vs 20-day SMA (normalized to 0-1)
            sma20 = col.rolling(min(20, len(col))).mean().iloc[-1]
            trend = 0.5
            if sma20 > 0:
                ratio = col.iloc[-1] / sma20
                trend = max(0, min(1, (ratio - 0.95) / 0.10))

            # Volatility: lower realized vol → higher score
            vol = ret.std() * math.sqrt(252) if len(ret) > 1 else 0.15
            volatility = max(0, min(1, 1 - (vol - 0.05) / 0.40))

            # Momentum as sentiment proxy: 20-day return percentile
            mom_20 = (col.iloc[-1] / col.iloc[-min(20, len(col))] - 1) if col.iloc[-min(20, len(col))] > 0 else 0
            sentiment = max(0, min(1, (mom_20 + 0.10) / 0.20))

            # Breadth proxy: fraction of recent returns positive
            breadth = (ret.tail(20) > 0).mean() if len(ret) >= 5 else 0.5

            # Dispersion proxy: 1 - normalized return dispersion
            cross_std = returns.tail(20).std().mean() if len(returns) >= 5 else 0.15
            dispersion = max(0, min(1, 1 - (cross_std - 0.005) / 0.03))

            # Liquidity proxy: use 1.0 (assumed liquid ETFs)
            liquidity = 0.8

            factor_scores = {
                'trend': trend,
                'volatility': volatility,
                'sentiment': sentiment,
                'breadth': breadth,
                'dispersion': dispersion,
                'liquidity': liquidity,
            }

            composite = sum(
                factor_scores.get(f, 0.5) * self.weights.get(f, 0)
                for f in self.weights
            )
            scores[sym] = composite

        return scores

    # ── helpers ──────────────────────────────────────────────────

    def _load_prices(
        self, start_date: date, end_date: date, symbols: list[str] | None,
    ) -> pd.DataFrame:
        """Load daily close prices from price_bars table."""
        from app.models.price_bars import PriceBar
        from app.models.etf_universe import ETFUniverse

        if symbols is None:
            etfs = ETFUniverse.query.filter_by(is_active=True).all()
            symbols = [e.symbol for e in etfs]

        # Need lookback before start_date for factor computation
        lookback_start = start_date - timedelta(days=90)

        bars = PriceBar.query.filter(
            PriceBar.symbol.in_(symbols),
            PriceBar.timeframe == '1d',
            PriceBar.timestamp >= lookback_start,
            PriceBar.timestamp <= end_date,
        ).all()

        if not bars:
            return pd.DataFrame()

        data = [
            {'date': b.timestamp.date() if hasattr(b.timestamp, 'date') else b.timestamp,
             'symbol': b.symbol,
             'close': float(b.close)}
            for b in bars
        ]

        df = pd.DataFrame(data)
        pivot = df.pivot_table(index='date', columns='symbol', values='close')
        pivot.index = pd.to_datetime(pivot.index)
        pivot = pivot.sort_index().ffill()

        return pivot

    def _get_rebalance_dates(self, dates: list) -> set:
        """Determine rebalance dates based on frequency."""
        if not dates:
            return set()

        rebalance = set()
        if self.rebalance_freq == 'daily':
            return set(dates)

        prev_marker = None
        for dt in dates:
            if self.rebalance_freq == 'weekly':
                marker = dt.isocalendar()[1]
            elif self.rebalance_freq == 'monthly':
                marker = dt.month if hasattr(dt, 'month') else dt.to_pydatetime().month
            else:
                marker = dt.isocalendar()[1]

            if marker != prev_marker:
                rebalance.add(dt)
                prev_marker = marker

        return rebalance

    def _calc_turnover(self, old: dict, new: dict) -> float:
        """One-way turnover between two weight dicts."""
        all_syms = set(list(old.keys()) + list(new.keys()))
        return sum(abs(new.get(s, 0) - old.get(s, 0)) for s in all_syms) / 2

    def _compute_metrics(
        self,
        equity: pd.Series,
        daily_returns: pd.Series,
        drawdown: pd.Series,
        trade_log: list,
    ) -> dict:
        """Compute performance metrics."""
        n_days = len(daily_returns)
        if n_days < 2:
            return {}

        total_return = (equity.iloc[-1] / equity.iloc[0]) - 1
        years = n_days / 252
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0

        mean_ret = daily_returns.mean()
        std_ret = daily_returns.std()
        sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0

        max_dd = drawdown.min()

        # Win rate: fraction of positive-return days
        win_rate = (daily_returns > 0).mean()

        # Count rebalance events
        rebalance_dates = set()
        for t in trade_log:
            rebalance_dates.add(t['date'])

        return {
            'total_return': round(float(total_return), 4),
            'cagr': round(float(cagr), 4),
            'sharpe': round(float(sharpe), 2),
            'max_drawdown': round(float(max_dd), 4),
            'win_rate': round(float(win_rate), 4),
            'num_trades': len(trade_log),
            'num_rebalances': len(rebalance_dates),
            'start_value': round(float(equity.iloc[0]), 2),
            'end_value': round(float(equity.iloc[-1]), 2),
        }

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            equity_curve=pd.Series(dtype=float),
            daily_returns=pd.Series(dtype=float),
            cumulative_returns=pd.Series(dtype=float),
            drawdown=pd.Series(dtype=float),
            metrics={'error': 'Insufficient data for backtest'},
        )
