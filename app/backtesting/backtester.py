"""Vectorized backtester for Paisabot weight strategies.

Loads historical price_bars, computes simplified factor proxies at each
rebalance date using only lookback data (no look-ahead bias), ranks the
universe by composite score with configurable weights, and tracks portfolio
returns with transaction cost modelling.

.. warning:: **Factor proxy limitation — do not use for weight optimisation**

    The factor proxies computed here (price-only momentum, realized vol, etc.)
    are *gross approximations* of the production factor pipeline.  Production
    factors are cross-sectionally percentile-ranked across the full universe
    using FinBERT-derived sentiment, breadth data, and options-derived
    dispersion — none of which are available to this backtester.

    Consequences:
    - Composite scores computed here will diverge from live scores.
    - Walk-forward results from this backtester **cannot** be used to tune
      production factor weights (DEFAULT_WEIGHTS in composite_scorer.py).
    - Use this backtester only for coarse regime/rebalance-frequency studies
      or for verifying that the execution and position-tracking logic is correct.

    For rigorous weight optimisation, run the full factor pipeline in
    ``research`` mode against a historical replay feed.
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

    # Almgren-Chriss constants (same as SlippageTracker)
    _AC_LAMBDA = 0.001  # temporary impact coefficient
    _AC_GAMMA = 0.5     # permanent impact coefficient
    _AC_SLIPPAGE_CAP_BPS = 50

    # Default ADV for ETFs when not found in universe table (~$50M/day)
    _DEFAULT_ADV_USD = 50_000_000

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
        self.slippage_bps = slippage_bps  # fallback when ADV unavailable

        # Normalize weights
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

        # ETF metadata (populated lazily at first run() call)
        self._adv_usd: dict[str, float] = {}
        self._inception_dates: dict[str, date] = {}

    def run(self, start_date: date, end_date: date, symbols: list[str] | None = None) -> BacktestResult:
        """Run backtest over the given date range."""
        self._load_etf_metadata()
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

        # pending_weights holds weights computed from T-close signals that will
        # be applied at T+1 open — matching production's T-close → T+1 execution.
        pending_weights: dict[str, float] | None = None

        for i, dt in enumerate(trading_dates):
            # Apply any T+1 weight transition at the start of the day
            if pending_weights is not None:
                # Slippage cost is incurred at T+1 (execution day).
                # Use Almgren-Chriss per-symbol estimate where ADV data is
                # available; fall back to the flat slippage_bps otherwise.
                cost_fraction = self._estimate_transition_cost(
                    current_weights, pending_weights, portfolio_value, prices, i,
                )
                portfolio_value *= (1 - cost_fraction)
                # Log trades with the execution date (T+1)
                for sym in set(list(current_weights.keys()) + list(pending_weights.keys())):
                    old_w = current_weights.get(sym, 0)
                    new_w = pending_weights.get(sym, 0)
                    if abs(new_w - old_w) > 0.001:
                        trade_log.append({
                            'date': dt.isoformat(),
                            'symbol': sym,
                            'side': 'buy' if new_w > old_w else 'sell',
                            'old_weight': round(old_w, 4),
                            'new_weight': round(new_w, 4),
                        })
                current_weights = pending_weights
                pending_weights = None

            # Apply today's returns with current (already-settled) weights
            if current_weights and i > 0:
                day_return = sum(
                    w * returns.loc[dt, sym]
                    for sym, w in current_weights.items()
                    if sym in returns.columns
                )
                portfolio_value *= (1 + day_return)

            equity[dt] = portfolio_value

            # Compute signals at T-close; weights will be applied tomorrow (T+1)
            if dt in rebalance_dates:
                lookback_end = i
                lookback_start = max(0, i - 60)
                if lookback_end - lookback_start < 10:
                    continue

                lookback_prices = prices.iloc[lookback_start:lookback_end + 1]
                scores = self._compute_scores(lookback_prices, symbols_list)
                ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                top_n = ranked[:self.max_positions]

                # Equal weight — stored as pending until next bar
                if top_n:
                    w = 1.0 / len(top_n)
                    pending_weights = {sym: w for sym, _score in top_n}

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

        .. warning:: These are **price-only proxies** — not the production factors.
            Sentiment uses raw momentum instead of FinBERT; breadth uses per-ETF
            return positivity instead of market-breadth indicators; dispersion uses
            cross-sectional return std instead of intra-ETF dispersion; liquidity is
            hard-coded to 0.8 because ADV time-series are not stored per bar.
            Scores produced here are NOT comparable to live composite scores.
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

    def _load_etf_metadata(self) -> None:
        """Load ADV and inception dates from ETFUniverse (once per run)."""
        if self._adv_usd and self._inception_dates:
            return  # already loaded
        from app.models.etf_universe import ETFUniverse
        etfs = ETFUniverse.query.filter_by(is_active=True).all()
        for e in etfs:
            if e.avg_daily_vol_m is not None:
                self._adv_usd[e.symbol] = float(e.avg_daily_vol_m) * 1_000_000
            if e.inception_date is not None:
                self._inception_dates[e.symbol] = e.inception_date

    def _estimate_transition_cost(
        self,
        old_weights: dict[str, float],
        new_weights: dict[str, float],
        portfolio_value: float,
        prices: pd.DataFrame,
        price_idx: int,
    ) -> float:
        """Estimate total transaction cost as a fraction of portfolio value.

        Uses Almgren-Chriss temporary + permanent impact where per-symbol ADV is
        available; falls back to flat ``self.slippage_bps`` otherwise.
        """
        all_syms = set(list(old_weights.keys()) + list(new_weights.keys()))
        total_cost_usd = 0.0
        flat_turnover = 0.0
        flat_symbols = []

        current_row = prices.iloc[price_idx]

        for sym in all_syms:
            delta = abs(new_weights.get(sym, 0) - old_weights.get(sym, 0))
            if delta < 1e-6:
                continue
            notional = portfolio_value * delta / 2  # one-way notional

            adv = self._adv_usd.get(sym)
            if adv is None or adv <= 0:
                # Symbol not in universe metadata — accumulate for flat fallback
                flat_turnover += delta / 2
                flat_symbols.append(sym)
                continue

            mid = current_row.get(sym, 0.0) if hasattr(current_row, 'get') else 0.0
            if mid <= 0:
                mid = float(current_row[sym]) if sym in current_row.index else 0.0
            if mid <= 0:
                flat_turnover += delta / 2
                continue

            # Realized vol from lookback window (annualised)
            lookback = max(0, price_idx - 20)
            col = prices[sym].iloc[lookback:price_idx + 1].dropna() if sym in prices.columns else pd.Series()
            vol = float(col.pct_change().dropna().std() * math.sqrt(252)) if len(col) > 2 else 0.20

            # Almgren-Chriss impact (bps)
            exec_min = 30
            minute_volume = adv / 390
            participation = (notional / max(minute_volume, 1)) / exec_min
            temp_impact = self._AC_LAMBDA * vol * math.sqrt(abs(participation))
            perm_impact = self._AC_GAMMA * vol * participation
            bps = min((temp_impact + perm_impact) * 10_000, self._AC_SLIPPAGE_CAP_BPS)

            total_cost_usd += notional * bps / 10_000

        # Add flat-rate cost for symbols without ADV data
        if flat_turnover > 0:
            total_cost_usd += portfolio_value * flat_turnover * self.slippage_bps / 10_000

        return total_cost_usd / portfolio_value if portfolio_value > 0 else 0.0

    def _load_prices(
        self, start_date: date, end_date: date, symbols: list[str] | None,
    ) -> pd.DataFrame:
        """Load daily close prices from price_bars table.

        Symbols are filtered to those with inception_date <= start_date to
        prevent look-ahead survivorship bias — ETFs that were not yet trading
        at the backtest start must be excluded.

        Note: price_bars contains only actual trading days (no weekend/holiday
        rows), so the resulting DataFrame index already reflects the real NYSE
        calendar and no further calendar filtering is needed.
        """
        from app.models.price_bars import PriceBar
        from app.models.etf_universe import ETFUniverse

        if symbols is None:
            etfs = ETFUniverse.query.filter_by(is_active=True).all()
            symbols = [e.symbol for e in etfs]

        # Filter out ETFs that were not yet trading at backtest start.
        # An ETF with inception_date > start_date would have no pre-start price
        # history, and including it biases selection toward "new" outperformers.
        eligible_symbols = []
        for sym in symbols:
            inception = self._inception_dates.get(sym)
            if inception is None:
                # No inception date recorded — include conservatively; the DB
                # query below will simply return no bars if there is no data.
                eligible_symbols.append(sym)
            elif inception <= start_date:
                eligible_symbols.append(sym)
            else:
                logger.debug(
                    'backtester_symbol_excluded_inception',
                    symbol=sym,
                    inception_date=str(inception),
                    backtest_start=str(start_date),
                )
        symbols = eligible_symbols

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
        """Determine rebalance dates based on frequency.

        Uses the first trading day of each ISO week or calendar month.
        Because ``dates`` is derived directly from the ``price_bars`` table —
        which only contains actual NYSE trading days — no separate holiday
        calendar is needed: the week/month boundary detection naturally picks
        the first *real* trading day of each period.
        """
        if not dates:
            return set()

        if self.rebalance_freq == 'daily':
            return set(dates)

        rebalance = set()
        prev_marker = None
        for dt in dates:
            if self.rebalance_freq == 'weekly':
                # ISO week number — changes on the first trading day of each week
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
        # Subtract risk-free rate (annualised) before computing Sharpe.
        # Using 4.5% as a conservative current-rate proxy; divide by 252 for daily.
        risk_free_daily = 0.045 / 252
        sharpe = ((mean_ret - risk_free_daily) / std_ret * np.sqrt(252)) if std_ret > 0 else 0

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
