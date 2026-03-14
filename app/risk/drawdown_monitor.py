from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class DrawdownMonitor:
    """Monitor portfolio-level drawdown and daily loss limits.

    Sets kill switches and publishes alerts when thresholds are breached.
    """

    def __init__(self, redis_client, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='drawdown_monitor')

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
    def max_drawdown(self) -> float:
        return self._threshold('max_drawdown', -0.15)

    @property
    def daily_loss_limit(self) -> float:
        return self._threshold('daily_loss_limit', -0.03)

    @property
    def warn_drawdown(self) -> float:
        return self._threshold('alert_drawdown_warn', -0.08)

    @property
    def critical_drawdown(self) -> float:
        return self._threshold('alert_drawdown_critical', -0.12)

    # ── core check ──────────────────────────────────────────────────

    def check(self, portfolio_values: pd.Series) -> dict:
        """Evaluate drawdown and daily-loss against limits.

        Args:
            portfolio_values: time-indexed Series of portfolio NAVs.

        Returns:
            dict with keys: status ('ok'|'warn'|'critical'|'halt'),
            current_drawdown, daily_return, breach_reason (or None).
        """
        if portfolio_values.empty or len(portfolio_values) < 2:
            return self._result('ok', 0.0, 0.0)

        peak = portfolio_values.cummax()
        dd = (portfolio_values - peak) / peak
        current_dd = float(dd.iloc[-1])
        daily_ret = float(
            (portfolio_values.iloc[-1] / portfolio_values.iloc[-2]) - 1,
        )

        # 1. Daily loss limit
        if daily_ret < self.daily_loss_limit:
            self._halt('daily_loss_limit_breached', daily_ret)
            return self._result('halt', current_dd, daily_ret,
                                f'daily_loss {daily_ret:.2%} < {self.daily_loss_limit:.2%}')

        # 2. Max drawdown halt
        if current_dd < self.max_drawdown:
            self._halt('max_drawdown_breached', current_dd)
            return self._result('halt', current_dd, daily_ret,
                                f'drawdown {current_dd:.2%} < {self.max_drawdown:.2%}')

        # 3. Critical warning
        if current_dd < self.critical_drawdown:
            self._alert('critical', 'drawdown_critical',
                        f'Drawdown at {current_dd:.1%} — approaching halt at {self.max_drawdown:.1%}')
            return self._result('critical', current_dd, daily_ret)

        # 4. Warning
        if current_dd < self.warn_drawdown:
            self._alert('warning', 'drawdown_warning',
                        f'Drawdown at {current_dd:.1%} — warn threshold {self.warn_drawdown:.1%}')
            return self._result('warn', current_dd, daily_ret)

        return self._result('ok', current_dd, daily_ret)

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _result(
        status: str,
        drawdown: float,
        daily_return: float,
        breach_reason: str | None = None,
    ) -> dict:
        return {
            'status': status,
            'current_drawdown': round(drawdown, 6),
            'daily_return': round(daily_return, 6),
            'breach_reason': breach_reason,
        }

    def _halt(self, reason: str, value: float) -> None:
        """Activate trading kill switch and publish critical alert."""
        if self._config is not None:
            self._config.set_kill_switch('trading', True)
        elif self._redis is not None:
            self._redis.set('kill_switch:trading', '1')

        self._publish_alert('critical', reason, value)
        self._log.critical(
            'trading_halted',
            reason=reason,
            value=round(value, 6),
        )

    def _alert(self, level: str, alert_type: str, message: str) -> None:
        self._publish_alert(level, alert_type, message=message)
        self._log.warning(alert_type, message=message)

    def _publish_alert(
        self,
        level: str,
        alert_type: str,
        value: float | None = None,
        message: str | None = None,
    ) -> None:
        if self._redis is None:
            return
        payload = {
            'type': alert_type,
            'level': level,
            'value': value,
            'message': message or alert_type,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._redis.lpush('channel:risk_alerts', json.dumps(payload))
        except Exception as exc:
            self._log.error('alert_publish_failed', error=str(exc))
