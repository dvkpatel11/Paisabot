"""Vectorized backtester for Paisabot weight strategies — enhanced edition.

Loads historical price_bars, computes simplified factor proxies at each
rebalance date using only lookback data (no look-ahead bias), ranks the
universe by composite score with configurable weights, and tracks portfolio
returns with transaction cost modelling.

Gaps fixed vs. the original implementation
-------------------------------------------
1. **Stale DEFAULT_WEIGHTS** — dispersion removed; weights now match the
   production CompositeScorer (trend 30 %, volatility 25 %, sentiment 15 %,
   breadth 15 %, liquidity 15 %).
2. **Equal weighting** — replaced with score-proportional allocation
   (``use_factor_weights=True``); capital is tilted toward highest-conviction
   ETFs instead of spreading evenly across top-N.
3. **No volatility targeting** — added ``vol_target`` parameter; weights are
   scaled down when implied portfolio vol exceeds the target (excess held as
   cash).  Defaults to 15 % (slightly aggressive; matches user risk tolerance).
4. **No drawdown halt** — added ``max_drawdown_halt``; rebalancing is suspended
   once the portfolio drops more than the threshold from its all-time high,
   matching the production kill-switch at −15 %.
5. **No per-position stop-loss** — added ``position_stop_loss`` (hard stop at
   −5 % from entry) and ``trailing_stop`` (−8 % from HWM); breached positions
   are force-liquidated at next bar open.
6. **No cash buffer** — added ``cash_buffer`` (default 5 %); total invested
   weight is capped at (1 − cash_buffer).
7. **No turnover limit** — when one-way turnover for a rebalance exceeds
   ``turnover_limit``, trades are scaled halfway toward the target instead of
   applying the full rebalance.
8. **No sector constraints** — ``max_sector_exposure`` caps any single sector's
   aggregate weight using a static SECTOR_MAP for the ETF universe.
9. **No benchmark** — SPY daily returns are extracted from the price matrix
   (when present) and used to compute alpha, beta, information ratio, and
   tracking error.
10. **Missing metrics** — Sortino ratio and Calmar ratio are now computed
    alongside Sharpe.

.. warning:: **Factor proxy limitation**
    Factor proxies here (price-only momentum, realized vol, etc.) are gross
    approximations of the production factor pipeline.  Do NOT use these results
    for tuning production factor weights.  Use them only for coarse
    regime/rebalance-frequency studies or execution-logic validation.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import structlog

from app.backtesting.result import BacktestResult

logger = structlog.get_logger()

# Production composite weights — dispersion excluded per CLAUDE.md.
# Dispersion's 15 % was redistributed: +5 % Trend, +5 % Volatility, +5 % Liquidity.
DEFAULT_WEIGHTS: dict[str, float] = {
    'trend':      0.30,
    'volatility': 0.25,
    'sentiment':  0.15,
    'breadth':    0.15,
    'liquidity':  0.15,
}

# Static sector map for the ETF universe (used for sector-exposure capping).
SECTOR_MAP: dict[str, str] = {
    'SPY':  'broad',          'QQQ':  'tech',          'DIA':  'broad',
    'XLK':  'tech',           'XLF':  'financials',    'XLE':  'energy',
    'XLV':  'healthcare',     'XLI':  'industrials',   'XLC':  'communication',
    'XLY':  'consumer_disc',  'XLP':  'consumer_stap', 'XLU':  'utilities',
    'XLRE': 'real_estate',    'XLB':  'materials',
    'GLD':  'commodities',    'SLV':  'commodities',   'USO':  'energy',
    'TLT':  'bonds',          'IEF':  'bonds',         'AGG':  'bonds',
    'BND':  'bonds',          'EEM':  'emerging',      'EFA':  'intl',
    'VWO':  'emerging',       'IWM':  'small_cap',     'MDY':  'mid_cap',
    'VNQ':  'real_estate',    'ARKK': 'tech',
}


class VectorizedBacktester:
    """Run a vectorized backtest using historical price data.

    At each rebalance date the backtester:
      1. Computes factor proxies from lookback data only (no look-ahead)
      2. Ranks universe by composite score using configured weights
      3. Selects top-N ETFs
      4. Allocates by factor score (or equal weight when disabled)
      5. Applies vol targeting, sector cap, and turnover limit
      6. Tracks portfolio returns net of Almgren-Chriss slippage
      7. Enforces per-position stop-losses and portfolio drawdown halt
    """

    # Almgren-Chriss constants (same as SlippageTracker)
    _AC_LAMBDA = 0.001
    _AC_GAMMA  = 0.5
    _AC_SLIPPAGE_CAP_BPS = 50

    # Default ADV for ETFs when not found in universe table (~$50 M/day)
    _DEFAULT_ADV_USD = 50_000_000

    def __init__(
        self,
        db_session,
        weights:              dict[str, float] | None = None,
        initial_capital:      float = 100_000,
        rebalance_freq:       str   = 'weekly',
        max_positions:        int   = 5,        # tightened from 10 for concentration
        slippage_bps:         float = 2.0,
        vol_target:           float = 0.15,     # 15 % — slightly aggressive
        cash_buffer:          float = 0.05,     # 5 % permanent cash reserve
        use_factor_weights:   bool  = True,     # score-proportional vs equal weight
        max_drawdown_halt:    float = -0.15,    # halt rebalancing at −15 %
        position_stop_loss:   float = -0.05,    # hard stop per position from entry
        trailing_stop:        float = -0.08,    # trailing stop from HWM per position
        turnover_limit:       float = 0.50,     # max one-way turnover per rebalance
        max_sector_exposure:  float = 0.40,     # max aggregate weight per sector
    ):
        self._db                 = db_session
        self.initial_capital     = initial_capital
        self.rebalance_freq      = rebalance_freq
        self.max_positions       = max_positions
        self.slippage_bps        = slippage_bps
        self.vol_target          = vol_target
        self.cash_buffer         = cash_buffer
        self.use_factor_weights  = use_factor_weights
        self.max_drawdown_halt   = max_drawdown_halt
        self.position_stop_loss  = position_stop_loss
        self.trailing_stop       = trailing_stop
        self.turnover_limit      = turnover_limit
        self.max_sector_exposure = max_sector_exposure

        self.weights = (weights or DEFAULT_WEIGHTS).copy()
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

        # ETF metadata populated lazily at first run() call
        self._adv_usd:         dict[str, float] = {}
        self._inception_dates: dict[str, date]  = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        start_date: date,
        end_date:   date,
        symbols:    list[str] | None = None,
    ) -> BacktestResult:
        """Run backtest over the given date range."""
        self._load_etf_metadata()
        prices = self._load_prices(start_date, end_date, symbols)
        if prices.empty or len(prices) < 20:
            return self._empty_result()

        symbols_list    = list(prices.columns)
        returns         = prices.pct_change().fillna(0)
        trading_dates   = prices.index.tolist()
        rebalance_dates = self._get_rebalance_dates(trading_dates)

        portfolio_value = self.initial_capital
        peak_value      = self.initial_capital
        trading_halted  = False
        equity          = pd.Series(dtype=float)
        current_weights: dict[str, float]        = {}
        pending_weights: dict[str, float] | None = None

        # Per-position tracking for stop-loss logic
        entry_prices: dict[str, float] = {}  # sym → price at first entry
        hwm_prices:   dict[str, float] = {}  # sym → high-water-mark price

        trade_log: list[dict] = []

        for i, dt in enumerate(trading_dates):

            # ── Apply T+1 pending weight transition ──────────────────────────
            if pending_weights is not None and not trading_halted:
                cost_fraction = self._estimate_transition_cost(
                    current_weights, pending_weights, portfolio_value, prices, i,
                )
                portfolio_value *= (1 - cost_fraction)

                price_row = prices.iloc[i]
                for sym in set(list(current_weights) + list(pending_weights)):
                    old_w = current_weights.get(sym, 0.0)
                    new_w = pending_weights.get(sym, 0.0)
                    if abs(new_w - old_w) > 0.001:
                        trade_log.append({
                            'date':       dt.isoformat(),
                            'symbol':     sym,
                            'side':       'buy' if new_w > old_w else 'sell',
                            'old_weight': round(old_w, 4),
                            'new_weight': round(new_w, 4),
                        })
                        # Record entry price for new / increased positions
                        if new_w > old_w and sym in price_row.index:
                            px = float(price_row[sym])
                            if px > 0 and (old_w == 0 or sym not in entry_prices):
                                entry_prices[sym] = px
                                hwm_prices[sym]   = px
                    # Clear tracking when fully exited
                    if new_w == 0:
                        entry_prices.pop(sym, None)
                        hwm_prices.pop(sym, None)

                current_weights = dict(pending_weights)
                pending_weights = None

            # ── Apply today's returns ─────────────────────────────────────────
            if current_weights and i > 0:
                day_return = sum(
                    w * returns.loc[dt, sym]
                    for sym, w in current_weights.items()
                    if sym in returns.columns
                )
                portfolio_value *= (1 + day_return)

            equity[dt] = portfolio_value

            # ── Update HWM and check per-position stop-losses ─────────────────
            if i > 0 and current_weights:
                price_row      = prices.iloc[i]
                stops_triggered: set[str] = set()

                for sym, w in list(current_weights.items()):
                    if w <= 0 or sym not in price_row.index:
                        continue
                    px = float(price_row[sym])
                    if px <= 0:
                        continue

                    hwm_prices[sym] = max(hwm_prices.get(sym, px), px)

                    ep       = entry_prices.get(sym, px)
                    hwm      = hwm_prices[sym]
                    from_ep  = (px - ep)  / ep  if ep  > 0 else 0.0
                    from_hwm = (px - hwm) / hwm if hwm > 0 else 0.0

                    if from_ep < self.position_stop_loss or from_hwm < self.trailing_stop:
                        stops_triggered.add(sym)
                        reason = (
                            'stop_loss'    if from_ep  < self.position_stop_loss
                            else 'trailing_stop'
                        )
                        trade_log.append({
                            'date':       dt.isoformat(),
                            'symbol':     sym,
                            'side':       'sell',
                            'old_weight': round(w, 4),
                            'new_weight': 0.0,
                            'reason':     reason,
                        })

                for sym in stops_triggered:
                    current_weights.pop(sym, None)
                    entry_prices.pop(sym, None)
                    hwm_prices.pop(sym, None)

            # ── Drawdown halt check ───────────────────────────────────────────
            peak_value = max(peak_value, portfolio_value)
            dd = (portfolio_value - peak_value) / peak_value
            if dd < self.max_drawdown_halt and not trading_halted:
                trading_halted = True
                logger.warning(
                    'backtester_drawdown_halt',
                    drawdown=round(dd, 4),
                    threshold=self.max_drawdown_halt,
                    date=str(dt.date() if hasattr(dt, 'date') else dt),
                )

            # ── Generate signals at T-close for T+1 execution ────────────────
            if dt in rebalance_dates and not trading_halted:
                lookback_end   = i
                lookback_start = max(0, i - 60)
                if lookback_end - lookback_start < 10:
                    continue

                lookback_prices = prices.iloc[lookback_start:lookback_end + 1]
                scores          = self._compute_scores(lookback_prices, symbols_list)
                ranked          = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                top_n           = ranked[:self.max_positions]

                if not top_n:
                    continue

                raw = self._compute_target_weights(top_n, lookback_prices)
                raw = self._apply_sector_constraints(raw)
                raw = self._apply_vol_target(raw, lookback_prices)
                raw = self._apply_turnover_limit(current_weights, raw)

                pending_weights = raw

        if equity.empty:
            return self._empty_result()

        daily_returns      = equity.pct_change().fillna(0)
        cumulative_returns = (1 + daily_returns).cumprod() - 1
        peak               = equity.cummax()
        drawdown           = (equity - peak) / peak
        spy_returns        = self._load_spy_returns(prices)

        metrics = self._compute_metrics(
            equity, daily_returns, drawdown, trade_log, spy_returns,
        )

        return BacktestResult(
            equity_curve       = equity,
            daily_returns      = daily_returns,
            cumulative_returns = cumulative_returns,
            drawdown           = drawdown,
            trade_log          = trade_log,
            metrics            = metrics,
        )

    # ── Weight computation ────────────────────────────────────────────────────

    def _compute_target_weights(
        self,
        top_n:           list[tuple[str, float]],
        lookback_prices: pd.DataFrame,
    ) -> dict[str, float]:
        """Compute target weights for top-N symbols scaled to (1 − cash_buffer).

        When ``use_factor_weights=True`` capital is allocated proportional to
        each ETF's composite score so higher-conviction bets receive more weight.
        Falls back to equal weight when the flag is False or all scores are zero.
        """
        investable = 1.0 - self.cash_buffer

        if self.use_factor_weights:
            total_score = sum(score for _, score in top_n)
            if total_score > 0:
                return {
                    sym: (score / total_score) * investable
                    for sym, score in top_n
                }

        # Equal weight fallback
        w = investable / len(top_n)
        return {sym: w for sym, _ in top_n}

    def _apply_sector_constraints(
        self, weights: dict[str, float],
    ) -> dict[str, float]:
        """Scale back any sector whose aggregate weight exceeds max_sector_exposure."""
        sector_totals: dict[str, float] = {}
        for sym, w in weights.items():
            sec = SECTOR_MAP.get(sym, 'other')
            sector_totals[sec] = sector_totals.get(sec, 0.0) + w

        overweight = {
            sec: tot for sec, tot in sector_totals.items()
            if tot > self.max_sector_exposure
        }
        if not overweight:
            return weights

        adjusted = dict(weights)
        for sec, total in overweight.items():
            scale = self.max_sector_exposure / total
            for sym in list(adjusted):
                if SECTOR_MAP.get(sym, 'other') == sec:
                    adjusted[sym] *= scale
        return adjusted

    def _apply_vol_target(
        self,
        weights:         dict[str, float],
        lookback_prices: pd.DataFrame,
    ) -> dict[str, float]:
        """Scale all weights down when implied portfolio vol exceeds vol_target.

        The scale factor is min(vol_target / portfolio_vol, 1.0) — the backtester
        never leverages up, matching production's ``apply_vol_target`` logic.
        """
        if self.vol_target <= 0:
            return weights

        syms = [s for s in weights if s in lookback_prices.columns and weights[s] > 0]
        if len(syms) < 2:
            return weights

        try:
            rets     = lookback_prices[syms].pct_change().dropna()
            if len(rets) < 10:
                return weights
            w_arr    = np.array([weights[s] for s in syms])
            cov      = rets.cov().values * 252
            port_vol = float(np.sqrt(w_arr @ cov @ w_arr))
            if port_vol <= 0:
                return weights
            scale = min(self.vol_target / port_vol, 1.0)
            return {sym: weights[sym] * scale for sym in weights}
        except Exception:
            return weights

    def _apply_turnover_limit(
        self,
        current:  dict[str, float],
        proposed: dict[str, float],
    ) -> dict[str, float]:
        """If one-way turnover > turnover_limit, move only halfway to target."""
        if self._calc_turnover(current, proposed) <= self.turnover_limit:
            return proposed
        all_syms = set(list(current) + list(proposed))
        return {
            sym: current.get(sym, 0.0) + 0.5 * (proposed.get(sym, 0.0) - current.get(sym, 0.0))
            for sym in all_syms
            if proposed.get(sym, 0.0) > 0 or current.get(sym, 0.0) > 0
        }

    # ── Factor proxy computation ──────────────────────────────────────────────

    def _compute_scores(
        self, prices: pd.DataFrame, symbols: list[str],
    ) -> dict[str, float]:
        """Compute simplified factor proxies from price data only.

        Uses only the lookback window provided (no look-ahead).

        .. warning:: These are **price-only proxies** — not the production factors.
            Sentiment uses raw momentum instead of FinBERT; breadth uses per-ETF
            return positivity instead of market-breadth indicators; liquidity is
            assumed 0.8 for all liquid ETFs.  Scores are NOT comparable to live
            composite scores.
        """
        if len(prices) < 5:
            return {s: 0.5 for s in symbols}

        returns = prices.pct_change().dropna()
        if returns.empty:
            return {s: 0.5 for s in symbols}

        scores: dict[str, float] = {}
        for sym in symbols:
            if sym not in prices.columns:
                scores[sym] = 0.5
                continue

            col = prices[sym].dropna()
            ret = returns[sym].dropna() if sym in returns.columns else pd.Series(dtype=float)

            if len(col) < 5 or len(ret) < 5:
                scores[sym] = 0.5
                continue

            # Trend: price vs 20-day SMA
            sma20 = col.rolling(min(20, len(col))).mean().iloc[-1]
            trend = 0.5
            if sma20 > 0:
                ratio = col.iloc[-1] / sma20
                trend = max(0.0, min(1.0, (ratio - 0.95) / 0.10))

            # Volatility: lower realized vol → higher score
            vol        = ret.std() * math.sqrt(252) if len(ret) > 1 else 0.15
            volatility = max(0.0, min(1.0, 1.0 - (vol - 0.05) / 0.40))

            # Momentum as sentiment proxy
            n_back    = min(20, len(col))
            mom_20    = (col.iloc[-1] / col.iloc[-n_back] - 1) if col.iloc[-n_back] > 0 else 0.0
            sentiment = max(0.0, min(1.0, (mom_20 + 0.10) / 0.20))

            # Breadth proxy: fraction of recent daily returns that are positive
            breadth = float((ret.tail(20) > 0).mean()) if len(ret) >= 5 else 0.5

            # Liquidity proxy: constant for liquid ETFs
            liquidity = 0.8

            factor_scores = {
                'trend':      trend,
                'volatility': volatility,
                'sentiment':  sentiment,
                'breadth':    breadth,
                'liquidity':  liquidity,
            }

            composite = sum(
                factor_scores.get(f, 0.5) * self.weights.get(f, 0.0)
                for f in self.weights
            )
            scores[sym] = composite

        return scores

    # ── Benchmark ─────────────────────────────────────────────────────────────

    def _load_spy_returns(self, prices: pd.DataFrame) -> pd.Series | None:
        """Extract SPY daily returns from the already-loaded price matrix."""
        if 'SPY' in prices.columns:
            return prices['SPY'].pct_change().fillna(0)
        return None

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _compute_metrics(
        self,
        equity:        pd.Series,
        daily_returns: pd.Series,
        drawdown:      pd.Series,
        trade_log:     list[dict],
        spy_returns:   pd.Series | None = None,
    ) -> dict:
        """Compute performance metrics including Sortino, Calmar, alpha, and beta."""
        n_days = len(daily_returns)
        if n_days < 2:
            return {}

        total_return = float(equity.iloc[-1] / equity.iloc[0]) - 1.0
        years        = n_days / 252
        cagr         = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0

        mean_ret        = float(daily_returns.mean())
        std_ret         = float(daily_returns.std())
        risk_free_daily = 0.045 / 252  # ~4.5 % annualised

        sharpe = ((mean_ret - risk_free_daily) / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0

        # Sortino: penalise only downside deviations
        downside_rets = daily_returns[daily_returns < risk_free_daily] - risk_free_daily
        downside_std  = float(math.sqrt((downside_rets ** 2).mean())) if len(downside_rets) > 1 else std_ret
        sortino = ((mean_ret - risk_free_daily) / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0

        max_dd = float(drawdown.min())

        # Calmar ratio: CAGR / |max drawdown|
        calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

        win_rate = float((daily_returns > 0).mean())

        rebalance_dates = {t['date'] for t in trade_log}

        metrics: dict = {
            'total_return':   round(total_return, 4),
            'cagr':           round(float(cagr), 4),
            'sharpe':         round(float(sharpe), 2),
            'sortino':        round(float(sortino), 2),
            'calmar':         round(float(calmar), 2),
            'max_drawdown':   round(max_dd, 4),
            'win_rate':       round(win_rate, 4),
            'num_trades':     len(trade_log),
            'num_rebalances': len(rebalance_dates),
            'start_value':    round(float(equity.iloc[0]), 2),
            'end_value':      round(float(equity.iloc[-1]), 2),
        }

        # Alpha / beta / information ratio vs SPY benchmark
        if spy_returns is not None:
            try:
                aligned = pd.concat(
                    [daily_returns.rename('port'), spy_returns.rename('spy')], axis=1,
                ).dropna()
                if len(aligned) > 30:
                    cov_mat  = aligned.cov().values
                    beta     = float(cov_mat[0, 1] / cov_mat[1, 1]) if cov_mat[1, 1] > 0 else 1.0
                    spy_cagr = float((1 + aligned['spy'].mean()) ** 252 - 1)
                    rf_ann   = risk_free_daily * 252
                    alpha    = float(cagr) - (rf_ann + beta * (spy_cagr - rf_ann))
                    excess   = aligned['port'] - aligned['spy']
                    te       = float(excess.std() * math.sqrt(252))
                    ir       = float(excess.mean() / excess.std() * math.sqrt(252)) if excess.std() > 0 else 0.0
                    metrics.update({
                        'beta':              round(beta, 3),
                        'alpha_annual':      round(alpha, 4),
                        'information_ratio': round(ir, 2),
                        'tracking_error':    round(te, 4),
                    })
            except Exception:
                pass

        return metrics

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_etf_metadata(self) -> None:
        """Load ADV and inception dates from ETFUniverse (once per run)."""
        if self._adv_usd and self._inception_dates:
            return
        from app.models.etf_universe import ETFUniverse
        for e in ETFUniverse.query.filter_by(is_active=True).all():
            if e.avg_daily_vol_m is not None:
                self._adv_usd[e.symbol] = float(e.avg_daily_vol_m) * 1_000_000
            if e.inception_date is not None:
                self._inception_dates[e.symbol] = e.inception_date

    def _load_prices(
        self, start_date: date, end_date: date, symbols: list[str] | None,
    ) -> pd.DataFrame:
        """Load daily close prices from price_bars table.

        Filters out ETFs whose inception_date > start_date to avoid
        survivorship bias from newly-listed outperformers.
        """
        from app.models.price_bars import PriceBar
        from app.models.etf_universe import ETFUniverse

        if symbols is None:
            etfs    = ETFUniverse.query.filter_by(is_active=True).all()
            symbols = [e.symbol for e in etfs]

        eligible = []
        for sym in symbols:
            inception = self._inception_dates.get(sym)
            if inception is None or inception <= start_date:
                eligible.append(sym)
            else:
                logger.debug(
                    'backtester_symbol_excluded_inception',
                    symbol=sym,
                    inception_date=str(inception),
                    backtest_start=str(start_date),
                )
        symbols = eligible

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
            {
                'date':   b.timestamp.date() if hasattr(b.timestamp, 'date') else b.timestamp,
                'symbol': b.symbol,
                'close':  float(b.close),
            }
            for b in bars
        ]

        df    = pd.DataFrame(data)
        pivot = df.pivot_table(index='date', columns='symbol', values='close')
        pivot.index = pd.to_datetime(pivot.index)
        pivot = pivot.sort_index().ffill()
        return pivot

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _estimate_transition_cost(
        self,
        old_weights:     dict[str, float],
        new_weights:     dict[str, float],
        portfolio_value: float,
        prices:          pd.DataFrame,
        price_idx:       int,
    ) -> float:
        """Estimate total transaction cost as a fraction of portfolio value.

        Uses Almgren-Chriss temporary + permanent impact where per-symbol ADV
        is available; falls back to flat ``slippage_bps`` otherwise.
        """
        all_syms       = set(list(old_weights) + list(new_weights))
        total_cost_usd = 0.0
        flat_turnover  = 0.0
        current_row    = prices.iloc[price_idx]

        for sym in all_syms:
            delta    = abs(new_weights.get(sym, 0.0) - old_weights.get(sym, 0.0))
            if delta < 1e-6:
                continue
            notional = portfolio_value * delta / 2  # one-way notional

            adv = self._adv_usd.get(sym)
            if adv is None or adv <= 0:
                flat_turnover += delta / 2
                continue

            mid = float(current_row[sym]) if sym in current_row.index else 0.0
            if mid <= 0:
                flat_turnover += delta / 2
                continue

            lookback = max(0, price_idx - 20)
            col      = (
                prices[sym].iloc[lookback:price_idx + 1].dropna()
                if sym in prices.columns else pd.Series(dtype=float)
            )
            vol = float(col.pct_change().dropna().std() * math.sqrt(252)) if len(col) > 2 else 0.20

            exec_min      = 30
            minute_volume = adv / 390
            participation = (notional / max(minute_volume, 1)) / exec_min
            temp_impact   = self._AC_LAMBDA * vol * math.sqrt(abs(participation))
            perm_impact   = self._AC_GAMMA  * vol * participation
            bps           = min((temp_impact + perm_impact) * 10_000, self._AC_SLIPPAGE_CAP_BPS)
            total_cost_usd += notional * bps / 10_000

        total_cost_usd += portfolio_value * flat_turnover * self.slippage_bps / 10_000
        return total_cost_usd / portfolio_value if portfolio_value > 0 else 0.0

    def _get_rebalance_dates(self, dates: list) -> set:
        """Determine rebalance dates based on frequency.

        Uses the first trading day of each ISO week or calendar month.
        ``dates`` comes directly from price_bars (actual NYSE trading days)
        so no separate holiday calendar is needed.
        """
        if not dates:
            return set()
        if self.rebalance_freq == 'daily':
            return set(dates)

        rebalance    = set()
        prev_marker  = None
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
        all_syms = set(list(old) + list(new))
        return sum(abs(new.get(s, 0.0) - old.get(s, 0.0)) for s in all_syms) / 2

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            equity_curve       = pd.Series(dtype=float),
            daily_returns      = pd.Series(dtype=float),
            cumulative_returns = pd.Series(dtype=float),
            drawdown           = pd.Series(dtype=float),
            metrics            = {'error': 'Insufficient data for backtest'},
        )
