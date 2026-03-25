"""Daily performance metrics recorder.

Computes portfolio value, daily returns, drawdown, rolling Sharpe/vol,
and VaR from open positions, then persists to the performance_metrics table.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal

import structlog

logger = structlog.get_logger()

DEFAULT_INITIAL_CAPITAL = 100_000.0


class PerformanceRecorder:
    """Records daily portfolio performance metrics."""

    def __init__(self, db_session, redis_client=None):
        self._db = db_session
        self._redis = redis_client
        self._log = logger.bind(component='performance_recorder')

    def record_daily(self, target_date: date | None = None) -> dict:
        """Compute and persist daily performance metrics for both asset classes.

        Args:
            target_date: date to record for (defaults to today).

        Returns:
            dict with per-asset-class results.
        """
        if target_date is None:
            target_date = date.today()

        results = {}
        for ac in ('etf', 'stock'):
            result = self._record_for_asset_class(ac, target_date)
            if result:
                results[ac] = result

        return results

    def _record_for_asset_class(
        self,
        asset_class: str,
        target_date: date,
    ) -> dict | None:
        """Compute and persist daily performance for a single asset class."""
        from app.models.performance import PerformanceMetric
        from app.models.positions import Position

        # 1. Compute portfolio value from open positions + initial capital
        positions = Position.query.filter_by(
            status='open', asset_class=asset_class,
        ).all()

        total_notional = sum(float(p.notional or 0) for p in positions)
        total_unrealized = sum(float(p.unrealized_pnl or 0) for p in positions)
        total_realized = sum(float(p.realized_pnl or 0) for p in positions)

        # Also sum realized PnL from closed positions
        closed = Position.query.filter_by(
            status='closed', asset_class=asset_class,
        ).all()
        total_realized += sum(float(p.realized_pnl or 0) for p in closed)

        initial_capital = self._get_initial_capital(asset_class)
        portfolio_value = initial_capital + total_unrealized + total_realized

        num_positions = len(positions)
        cash = portfolio_value - total_notional
        cash_pct = cash / portfolio_value if portfolio_value > 0 else 1.0

        # Update Account model if it exists
        self._update_account(asset_class, portfolio_value, total_notional,
                             total_realized, total_unrealized, cash)

        # 2. Get previous day's metric for daily return calc
        prev = (
            PerformanceMetric.query
            .filter(
                PerformanceMetric.date < target_date,
                PerformanceMetric.asset_class == asset_class,
            )
            .order_by(PerformanceMetric.date.desc())
            .first()
        )

        prev_value = float(prev.portfolio_value) if prev else initial_capital
        daily_return = (
            (portfolio_value - prev_value) / prev_value
            if prev_value > 0 else 0.0
        )

        # 3. Cumulative return from initial capital
        cumulative_return = (
            (portfolio_value - initial_capital) / initial_capital
            if initial_capital > 0 else 0.0
        )

        # 4. Drawdown from peak
        all_metrics = (
            PerformanceMetric.query
            .filter_by(asset_class=asset_class)
            .order_by(PerformanceMetric.date.asc())
            .all()
        )
        peak = initial_capital
        for m in all_metrics:
            val = float(m.portfolio_value or 0)
            if val > peak:
                peak = val
        if portfolio_value > peak:
            peak = portfolio_value
        drawdown = (portfolio_value - peak) / peak if peak > 0 else 0.0

        # 5. Rolling 30-day Sharpe and volatility
        recent = (
            PerformanceMetric.query
            .filter_by(asset_class=asset_class)
            .order_by(PerformanceMetric.date.desc())
            .limit(30)
            .all()
        )
        recent_returns = [
            float(m.daily_return) for m in recent
            if m.daily_return is not None
        ]
        # Include today's return
        recent_returns.insert(0, daily_return)
        recent_returns = recent_returns[:30]

        rf_rate = self._get_risk_free_rate()
        sharpe_30d = self._compute_sharpe(recent_returns, risk_free_rate=rf_rate)
        volatility_30d = self._compute_vol(recent_returns)
        var_95 = self._compute_var(recent_returns, portfolio_value)

        # 6. Get regime from Redis
        regime = 'unknown'
        if self._redis:
            raw = self._redis.get('cache:regime:current')
            if raw:
                try:
                    regime_data = json.loads(raw)
                    regime = regime_data.get('regime', 'unknown')
                except (json.JSONDecodeError, TypeError):
                    pass

        # 7. Get account_id for FK
        account_id = self._get_account_id(asset_class)

        # 8. Upsert metric (unique on date + asset_class)
        existing = PerformanceMetric.query.filter_by(
            date=target_date, asset_class=asset_class,
        ).first()
        if existing:
            existing.portfolio_value = Decimal(str(round(portfolio_value, 2)))
            existing.daily_return = Decimal(str(round(daily_return, 6)))
            existing.cumulative_return = Decimal(str(round(cumulative_return, 6)))
            existing.drawdown = Decimal(str(round(drawdown, 6)))
            existing.sharpe_30d = Decimal(str(round(sharpe_30d, 4))) if sharpe_30d else None
            existing.volatility_30d = Decimal(str(round(volatility_30d, 4))) if volatility_30d else None
            existing.var_95 = Decimal(str(round(var_95, 6))) if var_95 else None
            existing.regime = regime
            existing.num_positions = num_positions
            existing.cash_pct = Decimal(str(round(cash_pct, 4)))
            if account_id:
                existing.account_id = account_id
        else:
            metric = PerformanceMetric(
                date=target_date,
                portfolio_value=Decimal(str(round(portfolio_value, 2))),
                daily_return=Decimal(str(round(daily_return, 6))),
                cumulative_return=Decimal(str(round(cumulative_return, 6))),
                drawdown=Decimal(str(round(drawdown, 6))),
                sharpe_30d=Decimal(str(round(sharpe_30d, 4))) if sharpe_30d else None,
                volatility_30d=Decimal(str(round(volatility_30d, 4))) if volatility_30d else None,
                var_95=Decimal(str(round(var_95, 6))) if var_95 else None,
                regime=regime,
                num_positions=num_positions,
                cash_pct=Decimal(str(round(cash_pct, 4))),
                asset_class=asset_class,
                account_id=account_id,
            )
            self._db.add(metric)

        self._db.commit()

        self._log.info(
            'daily_performance_recorded',
            asset_class=asset_class,
            portfolio_value=round(portfolio_value, 2),
            drawdown=round(drawdown, 6),
        )

        return {
            'date': str(target_date),
            'asset_class': asset_class,
            'portfolio_value': round(portfolio_value, 2),
            'daily_return': round(daily_return, 6),
            'cumulative_return': round(cumulative_return, 6),
            'drawdown': round(drawdown, 6),
            'sharpe_30d': round(sharpe_30d, 4) if sharpe_30d else None,
            'volatility_30d': round(volatility_30d, 4) if volatility_30d else None,
            'num_positions': num_positions,
        }

    def _get_initial_capital(self, asset_class: str = 'etf') -> float:
        """Read initial capital from Account model, config, or default."""
        # Try Account model first
        try:
            from app.models.account import Account
            account = Account.query.filter_by(
                asset_class=asset_class, is_active=True,
            ).first()
            if account and account.initial_capital:
                return float(account.initial_capital)
        except Exception:
            pass

        # Fall back to Redis config
        if self._redis:
            raw = self._redis.hget('config:portfolio', 'initial_capital')
            if raw:
                try:
                    return float(raw)
                except (ValueError, TypeError):
                    pass
        return DEFAULT_INITIAL_CAPITAL

    def _get_account_id(self, asset_class: str) -> int | None:
        """Get the Account primary key for an asset class."""
        try:
            from app.models.account import Account
            account = Account.query.filter_by(
                asset_class=asset_class, is_active=True,
            ).first()
            if account:
                return account.id
            self._log.warning(
                'no_active_account',
                asset_class=asset_class,
                detail='PerformanceMetric will be saved without account_id',
            )
            return None
        except Exception as exc:
            self._log.warning(
                'account_lookup_failed',
                error=str(exc),
                asset_class=asset_class,
            )
            return None

    def _update_account(
        self,
        asset_class: str,
        portfolio_value: float,
        total_notional: float,
        total_realized: float,
        total_unrealized: float,
        cash: float,
    ) -> None:
        """Update Account model with latest EOD metrics."""
        try:
            from app.models.account import Account
            account = Account.query.filter_by(
                asset_class=asset_class, is_active=True,
            ).first()
            if not account:
                return

            account.portfolio_value = Decimal(str(round(total_notional, 2)))
            account.cash_balance = Decimal(str(round(cash, 2)))
            account.realized_pnl = Decimal(str(round(total_realized, 2)))
            account.unrealized_pnl = Decimal(str(round(total_unrealized, 2)))
            account.total_pnl = Decimal(str(round(total_realized + total_unrealized, 2)))

            # Update high watermark and drawdown
            nav = float(account.nav)
            hwm = float(account.high_watermark or account.initial_capital or DEFAULT_INITIAL_CAPITAL)
            if nav > hwm:
                account.high_watermark = Decimal(str(round(nav, 2)))
                hwm = nav
            account.current_drawdown = Decimal(str(round(
                (nav - hwm) / hwm if hwm > 0 else 0.0, 6,
            )))

            self._db.commit()
            self._log.info(
                'account_updated',
                asset_class=asset_class,
                nav=round(nav, 2),
                drawdown=float(account.current_drawdown),
            )
        except Exception as exc:
            self._log.error('account_update_failed', error=str(exc), asset_class=asset_class)

    # Annual risk-free rate (default 4.5%, matching backtester).
    # Overridden at runtime from config:risk:risk_free_rate if available.
    RISK_FREE_RATE = 0.045

    @staticmethod
    def _compute_sharpe(
        returns: list[float],
        window: int = 30,
        risk_free_rate: float | None = None,
    ) -> float | None:
        """Annualized Sharpe ratio from daily excess returns."""
        if len(returns) < 5:
            return None
        rf_daily = (risk_free_rate or PerformanceRecorder.RISK_FREE_RATE) / 252
        vals = returns[:window]
        n = len(vals)
        mean_ret = sum(vals) / n
        variance = sum((r - mean_ret) ** 2 for r in vals) / (n - 1)  # sample variance
        std_ret = math.sqrt(variance)
        if std_ret < 1e-10:
            return None
        return ((mean_ret - rf_daily) / std_ret) * math.sqrt(252)

    def _get_risk_free_rate(self) -> float:
        """Read annual risk-free rate from Redis config or use default."""
        if self._redis:
            raw = self._redis.hget('config:risk', 'risk_free_rate')
            if raw is not None:
                try:
                    return float(raw.decode() if isinstance(raw, bytes) else raw)
                except (ValueError, TypeError):
                    pass
        return self.RISK_FREE_RATE

    @staticmethod
    def _compute_vol(returns: list[float], window: int = 30) -> float | None:
        """Annualized volatility from daily returns."""
        if len(returns) < 5:
            return None
        vals = returns[:window]
        n = len(vals)
        mean_ret = sum(vals) / n
        variance = sum((r - mean_ret) ** 2 for r in vals) / (n - 1)  # sample variance
        return math.sqrt(variance) * math.sqrt(252)

    @staticmethod
    def _compute_var(
        returns: list[float],
        portfolio_value: float,
    ) -> float | None:
        """Parametric VaR at 95% confidence level."""
        if len(returns) < 5:
            return None
        n = len(returns)
        mean_ret = sum(returns) / n
        variance = sum((r - mean_ret) ** 2 for r in returns) / (n - 1)  # sample variance
        std_ret = math.sqrt(variance)
        # 95% VaR: negative value representing loss
        return -1.645 * std_ret * portfolio_value
