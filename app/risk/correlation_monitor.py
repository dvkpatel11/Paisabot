from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class CorrelationMonitor:
    """Monitor average pairwise portfolio correlation.

    Warns when avg correlation exceeds threshold for consecutive days,
    indicating concentration risk / correlation collapse.
    """

    LOOKBACK_DAYS = 60
    CONSECUTIVE_DAYS_KEY = 'risk:corr_breach_days'

    def __init__(self, redis_client, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='correlation_monitor')

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
    def correlation_limit(self) -> float:
        return self._threshold('correlation_limit', 0.85)

    @property
    def consecutive_days_trigger(self) -> int:
        """Number of consecutive days above limit before forcing action."""
        return 3

    # ── core check ──────────────────────────────────────────────────

    def check(
        self,
        position_symbols: list[str],
        prices_df: pd.DataFrame,
    ) -> dict:
        """Compute average pairwise correlation for held positions.

        Args:
            position_symbols: list of symbols currently held.
            prices_df: DataFrame with daily close prices, symbols as columns.

        Returns:
            dict with avg_corr, status ('ok'|'warn'|'force_diversify'),
            consecutive_breach_days, pairs_above_limit.
        """
        if len(position_symbols) < 2:
            self._reset_breach_counter()
            return self._result(0.0, 'ok', 0, [])

        available = [s for s in position_symbols if s in prices_df.columns]
        if len(available) < 2:
            self._reset_breach_counter()
            return self._result(0.0, 'ok', 0, [])

        returns = prices_df[available].pct_change().dropna()
        if len(returns) < self.LOOKBACK_DAYS:
            return self._result(0.0, 'insufficient_data', 0, [])

        # Use the most recent LOOKBACK_DAYS
        recent = returns.tail(self.LOOKBACK_DAYS)
        corr_matrix = recent.corr()

        n = len(available)
        upper_tri_indices = np.triu_indices(n, k=1)
        corr_values = corr_matrix.values[upper_tri_indices]

        if len(corr_values) == 0:
            return self._result(0.0, 'ok', 0, [])

        avg_corr = float(np.nanmean(corr_values))

        # Find pairs above the limit
        pairs_above = []
        for idx in range(len(corr_values)):
            i, j = upper_tri_indices[0][idx], upper_tri_indices[1][idx]
            if corr_values[idx] > self.correlation_limit:
                pairs_above.append({
                    'pair': (available[i], available[j]),
                    'correlation': round(float(corr_values[idx]), 4),
                })

        # Track consecutive breach days (decrement gradually rather than
        # hard-reset so near-consecutive breach days still accumulate)
        limit = self.correlation_limit
        if avg_corr > limit:
            breach_days = self._increment_breach_counter()
        else:
            breach_days = self._decrement_breach_counter()

        # Determine status
        if breach_days >= self.consecutive_days_trigger:
            status = 'force_diversify'
            self._publish_alert(avg_corr, breach_days, pairs_above)
        elif avg_corr > limit:
            status = 'warn'
            self._publish_alert(avg_corr, breach_days, pairs_above)
        else:
            status = 'ok'

        self._log.info(
            'correlation_check',
            avg_corr=round(avg_corr, 4),
            status=status,
            breach_days=breach_days,
            pairs_above_limit=len(pairs_above),
        )

        return self._result(avg_corr, status, breach_days, pairs_above)

    # ── breach counter ──────────────────────────────────────────────
    # Counter tracks calendar days in breach, not invocation count.
    # A date-stamped sentinel key (risk:corr_breach_date:<YYYY-MM-DD>)
    # ensures the counter increments at most once per UTC trading day,
    # regardless of how many times the monitor runs intraday.

    def _today_key(self) -> str:
        """Return a date-stamped key for today's breach record."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        return f'risk:corr_breach_date:{today}'

    def _increment_breach_counter(self) -> int:
        if self._redis is None:
            return 1
        today_key = self._today_key()
        # Only increment the running count if today hasn't been recorded yet.
        if not self._redis.exists(today_key):
            self._redis.set(today_key, '1', ex=86400 * 8)  # sentinel expires in 8 days
            self._redis.incr(self.CONSECUTIVE_DAYS_KEY)
            self._redis.expire(self.CONSECUTIVE_DAYS_KEY, 86400 * 7)
        val = self._redis.get(self.CONSECUTIVE_DAYS_KEY)
        return int(val) if val else 1

    def _decrement_breach_counter(self) -> int:
        """Decrement breach counter by 1 (min 0) instead of hard-resetting."""
        if self._redis is None:
            return 0
        val = self._redis.get(self.CONSECUTIVE_DAYS_KEY)
        current = int(val) if val else 0
        if current <= 1:
            self._redis.delete(self.CONSECUTIVE_DAYS_KEY)
            return 0
        self._redis.set(self.CONSECUTIVE_DAYS_KEY, str(current - 1), ex=86400 * 7)
        return current - 1

    def _reset_breach_counter(self) -> None:
        if self._redis is not None:
            self._redis.delete(self.CONSECUTIVE_DAYS_KEY)

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _result(
        avg_corr: float,
        status: str,
        breach_days: int,
        pairs_above: list,
    ) -> dict:
        return {
            'avg_corr': round(avg_corr, 4),
            'status': status,
            'consecutive_breach_days': breach_days,
            'pairs_above_limit': pairs_above,
        }

    def _publish_alert(
        self, avg_corr: float, breach_days: int, pairs: list,
    ) -> None:
        if self._redis is None:
            return
        payload = {
            'type': 'correlation_warning',
            'level': 'warning' if breach_days < self.consecutive_days_trigger else 'critical',
            'avg_corr': round(avg_corr, 4),
            'threshold': self.correlation_limit,
            'consecutive_days': breach_days,
            'pairs_count': len(pairs),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._redis.lpush('channel:risk_alerts', json.dumps(payload))
        except Exception as exc:
            self._log.error('corr_alert_publish_failed', error=str(exc))
