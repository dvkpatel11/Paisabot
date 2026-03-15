from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()


class LiquidityMonitor:
    """Detect liquidity shocks — ADV drops > 50% vs 30-day average.

    Suspends new entries for affected ETFs for the trading session (24h TTL).
    """

    SHOCK_TTL = 86400  # 24 hours

    def __init__(self, redis_client, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='liquidity_monitor')

    @property
    def shock_threshold(self) -> float:
        """ADV must drop below this fraction of 30-day avg to trigger."""
        return 0.50

    # ── single-symbol check ─────────────────────────────────────────

    def check_symbol(
        self,
        symbol: str,
        current_adv: float | None = None,
    ) -> dict:
        """Check if a symbol is experiencing a liquidity shock.

        If current_adv is not provided, checks only the cached shock flag.

        Returns:
            dict with symbol, is_shocked, reason.
        """
        # Check existing shock flag first
        if self._is_shocked_cached(symbol):
            return {
                'symbol': symbol,
                'is_shocked': True,
                'reason': 'liquidity_shock_active',
            }

        if current_adv is None:
            return {
                'symbol': symbol,
                'is_shocked': False,
                'reason': 'ok',
            }

        hist_adv = self._get_historical_adv(symbol)
        if hist_adv is None or hist_adv <= 0:
            return {
                'symbol': symbol,
                'is_shocked': False,
                'reason': 'no_historical_adv',
            }

        ratio = current_adv / hist_adv

        if ratio < self.shock_threshold:
            self._set_shock_flag(symbol)
            self._publish_alert(symbol, current_adv, hist_adv, ratio)
            self._log.warning(
                'liquidity_shock_detected',
                symbol=symbol,
                current_adv=current_adv,
                hist_adv=hist_adv,
                ratio=round(ratio, 4),
            )
            return {
                'symbol': symbol,
                'is_shocked': True,
                'reason': f'adv_drop {ratio:.0%} of 30d avg',
            }

        return {
            'symbol': symbol,
            'is_shocked': False,
            'reason': 'ok',
        }

    # ── portfolio-wide scan ─────────────────────────────────────────

    def scan_universe(
        self,
        symbols: list[str],
        current_advs: dict[str, float] | None = None,
    ) -> dict:
        """Scan a list of symbols for liquidity shocks.

        Returns:
            dict with shocked (list of symbols), ok (list), total.
        """
        if current_advs is None:
            current_advs = {}

        shocked = []
        ok = []

        for symbol in symbols:
            result = self.check_symbol(symbol, current_advs.get(symbol))
            if result['is_shocked']:
                shocked.append(result)
            else:
                ok.append(symbol)

        self._log.info(
            'liquidity_scan_complete',
            shocked=len(shocked),
            ok=len(ok),
        )

        return {
            'shocked': shocked,
            'ok': ok,
            'total': len(symbols),
        }

    def is_shocked(self, symbol: str) -> bool:
        """Quick check if a symbol has an active liquidity shock flag."""
        return self._is_shocked_cached(symbol)

    # ── helpers ─────────────────────────────────────────────────────

    def _is_shocked_cached(self, symbol: str) -> bool:
        if self._redis is None:
            return False
        val = self._redis.get(f'liquidity_shock:{symbol}')
        return val == '1'

    def _set_shock_flag(self, symbol: str) -> None:
        if self._redis is not None:
            self._redis.set(f'liquidity_shock:{symbol}', '1', ex=self.SHOCK_TTL)

    def _get_historical_adv(self, symbol: str) -> float | None:
        if self._redis is None:
            return None
        raw = self._redis.get(f'etf:{symbol}:adv_30d_m')
        if raw is None:
            return None
        try:
            return float(raw.decode() if isinstance(raw, bytes) else raw)
        except (ValueError, TypeError):
            return None

    def _publish_alert(
        self,
        symbol: str,
        current_adv: float,
        hist_adv: float,
        ratio: float,
    ) -> None:
        if self._redis is None:
            return
        payload = {
            'type': 'liquidity_shock',
            'level': 'warning',
            'symbol': symbol,
            'current_adv': current_adv,
            'hist_adv': hist_adv,
            'ratio': round(ratio, 4),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._redis.lpush('channel:risk_alerts', json.dumps(payload))
        except Exception as exc:
            self._log.error('liquidity_alert_publish_failed', error=str(exc))
