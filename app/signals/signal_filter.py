from __future__ import annotations

import structlog

logger = structlog.get_logger()


class SignalFilter:
    """Pre-signal filter checking tradability constraints.

    Checks: kill switches, ADV gate, spread gate, maintenance mode,
    liquidity shock, factor staleness.
    """

    def __init__(self, redis_client=None, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='signal_filter')

    def is_tradable(
        self,
        symbol: str,
        adv_m: float | None = None,
        spread_bps: float | None = None,
    ) -> tuple[bool, str]:
        """Check if a symbol passes all tradability filters.

        Returns (True, 'ok') if tradable, or (False, reason) if blocked.
        """
        # 1. Kill switch: trading
        if self._check_kill_switch('trading'):
            return False, 'kill_switch_active'

        # 2. Kill switch: all
        if self._check_kill_switch('all'):
            return False, 'kill_switch_all'

        # 3. ADV gate
        min_adv = self._get_config_float('universe', 'min_avg_daily_vol_m', 20.0)
        if adv_m is not None and adv_m < min_adv:
            return False, f'adv_below_threshold ({adv_m:.1f}M < {min_adv}M)'

        # 4. Spread gate
        max_spread = self._get_config_float('universe', 'max_spread_bps', 10.0)
        if spread_bps is not None and spread_bps > max_spread:
            return False, f'spread_too_wide ({spread_bps:.1f}bps > {max_spread}bps)'

        # 5. Maintenance mode
        if self._check_kill_switch('maintenance'):
            return False, 'maintenance_mode'

        # 6. Liquidity shock
        if self._check_liquidity_shock(symbol):
            return False, 'liquidity_shock'

        # 7. Factor staleness
        if self._check_factor_staleness(symbol):
            return False, 'factor_data_stale'

        return True, 'ok'

    def _check_kill_switch(self, name: str) -> bool:
        if self._redis is None:
            return False
        try:
            val = self._redis.get(f'kill_switch:{name}')
            if val is None:
                return False
            decoded = val.decode() if isinstance(val, bytes) else val
            return decoded == '1'
        except Exception:
            return False

    def _get_config_float(
        self, category: str, key: str, default: float,
    ) -> float:
        """Read a config value from Redis hash."""
        if self._redis is not None:
            try:
                val = self._redis.hget(f'config:{category}', key)
                if val is not None:
                    decoded = val.decode() if isinstance(val, bytes) else val
                    return float(decoded)
            except (ValueError, TypeError):
                pass

        if self._config is not None:
            try:
                return self._config.get_float(category, key) or default
            except Exception:
                pass

        return default

    def _check_liquidity_shock(self, symbol: str) -> bool:
        """Check for active liquidity shock flag."""
        if self._redis is None:
            return False
        try:
            val = self._redis.get(f'liquidity_shock:{symbol}')
            if val is None:
                return False
            decoded = val.decode() if isinstance(val, bytes) else val
            return decoded == '1'
        except Exception:
            return False

    def _check_factor_staleness(self, symbol: str) -> bool:
        """Check if factor data is too old to be reliable.

        Factor scores should be refreshed within the last hour.
        Returns True (stale → block signal) on any Redis error so the filter
        fails safe rather than allowing signals through on infrastructure faults.
        """
        if self._redis is None:
            return True  # no Redis = can't verify freshness → block

        try:
            ttl = self._redis.ttl(f'scores:{symbol}')
            # scores:{symbol} has 15min TTL; if key doesn't exist, data is stale
            if ttl is None or ttl == -2:  # -2 means key doesn't exist
                return True
            return False
        except Exception:
            return True  # fail-safe: treat as stale on Redis error
