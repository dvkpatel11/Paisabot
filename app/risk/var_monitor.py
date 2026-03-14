from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class VaRMonitor:
    """Value-at-Risk and Expected Shortfall (CVaR) monitor.

    Computes parametric VaR (normal assumption) and historical VaR.
    Triggers alerts when 1-day VaR exceeds the configured limit.
    """

    def __init__(self, redis_client, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='var_monitor')

    # ── thresholds ──────────────────────────────────────────────────

    def _threshold(self, key: str, default: float) -> float:
        if self._config is not None:
            return self._config.get_float('risk', key, default)
        if self._redis is not None:
            raw = self._redis.hget('config:risk', key)
            if raw is not None:
                return float(raw.decode() if isinstance(raw, bytes) else raw)
        return default

    @property
    def confidence(self) -> float:
        return self._threshold('var_confidence', 0.95)

    @property
    def var_limit_pct(self) -> float:
        return self._threshold('var_limit_pct', 0.02)

    # ── core computation ────────────────────────────────────────────

    def compute(
        self,
        returns: pd.Series,
        portfolio_value: float = 100_000.0,
    ) -> dict:
        """Compute VaR and CVaR from a return series.

        Args:
            returns: daily portfolio return series.
            portfolio_value: current portfolio NAV.

        Returns:
            dict with var_pct, var_dollar, cvar_pct, cvar_dollar,
            parametric_var, confidence, status, breach.
        """
        if returns.empty or len(returns) < 5:
            return self._empty_result(portfolio_value)

        returns_clean = returns.dropna()
        if len(returns_clean) < 5:
            return self._empty_result(portfolio_value)

        conf = self.confidence

        # Historical VaR — percentile of actual returns
        hist_var = float(returns_clean.quantile(1 - conf))

        # Parametric VaR — normal distribution assumption
        from scipy import stats as sp_stats
        z_score = sp_stats.norm.ppf(1 - conf)
        param_var = float(returns_clean.mean() + z_score * returns_clean.std())

        # CVaR (Expected Shortfall) — mean of returns below VaR
        tail = returns_clean[returns_clean <= hist_var]
        cvar = float(tail.mean()) if len(tail) > 0 else hist_var

        # Check against limit
        breach = abs(hist_var) > self.var_limit_pct
        status = 'breach' if breach else 'ok'

        if breach:
            self._publish_alert(hist_var, portfolio_value)

        result = {
            'var_pct': round(hist_var, 6),
            'var_dollar': round(abs(hist_var) * portfolio_value, 2),
            'cvar_pct': round(cvar, 6),
            'cvar_dollar': round(abs(cvar) * portfolio_value, 2),
            'parametric_var': round(param_var, 6),
            'confidence': conf,
            'status': status,
            'breach': breach,
        }

        self._log.info(
            'var_computed',
            var_pct=result['var_pct'],
            cvar_pct=result['cvar_pct'],
            status=status,
        )

        return result

    # ── helpers ─────────────────────────────────────────────────────

    def _empty_result(self, portfolio_value: float) -> dict:
        return {
            'var_pct': 0.0,
            'var_dollar': 0.0,
            'cvar_pct': 0.0,
            'cvar_dollar': 0.0,
            'parametric_var': 0.0,
            'confidence': self.confidence,
            'status': 'insufficient_data',
            'breach': False,
        }

    def _publish_alert(self, var_pct: float, portfolio_value: float) -> None:
        if self._redis is None:
            return
        payload = {
            'type': 'var_breach',
            'level': 'warning',
            'var_pct': round(var_pct, 6),
            'var_dollar': round(abs(var_pct) * portfolio_value, 2),
            'limit_pct': self.var_limit_pct,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._redis.lpush('channel:risk_alerts', json.dumps(payload))
        except Exception as exc:
            self._log.error('var_alert_publish_failed', error=str(exc))
