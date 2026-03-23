from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()


class StopLossEngine:
    """Position-level stop-loss and trailing-stop scanner.

    Stop-loss ladder (from risk_framework.md):
      - Hard stop:    -5% from entry  → immediate exit
      - Trailing stop: -8% from HWM   → immediate exit
      - Soft warning:  -3% from entry  → reduce position 50%, monitor
    """

    def __init__(self, redis_client, config_loader=None):
        self._redis = redis_client
        self._config = config_loader
        self._log = logger.bind(component='stop_loss_engine')

    # ── thresholds ──────────────────────────────────────────────────

    # Default thresholds per direction.
    # Long positions use tighter stops; short positions use wider stops
    # to accommodate higher volatility of mean-reversion short trades.
    _DEFAULTS = {
        'long': {
            'position_stop_loss': -0.05,
            'position_trailing_stop': -0.08,
            'position_soft_warn': -0.03,
        },
        'short': {
            'position_stop_loss': -0.07,
            'position_trailing_stop': -0.10,
            'position_soft_warn': -0.04,
        },
    }

    def _threshold(self, key: str, default: float) -> float:
        if self._config is not None:
            return self._config.get_float('risk', key, default)
        if self._redis is not None:
            raw = self._redis.hget('config:risk', key)
            if raw is not None:
                return float(raw.decode() if isinstance(raw, bytes) else raw)
        return default

    def _get_thresholds(self, direction: str = 'long') -> dict[str, float]:
        """Return stop-loss thresholds for the given direction.

        Config keys:
            long  → position_stop_loss, position_trailing_stop, position_soft_warn
            short → short_stop_loss, short_trailing_stop, short_soft_warn
        """
        defaults = self._DEFAULTS.get(direction, self._DEFAULTS['long'])
        if direction == 'short':
            return {
                'hard': self._threshold('short_stop_loss', defaults['position_stop_loss']),
                'trailing': self._threshold('short_trailing_stop', defaults['position_trailing_stop']),
                'soft': self._threshold('short_soft_warn', defaults['position_soft_warn']),
            }
        return {
            'hard': self._threshold('position_stop_loss', defaults['position_stop_loss']),
            'trailing': self._threshold('position_trailing_stop', defaults['position_trailing_stop']),
            'soft': self._threshold('position_soft_warn', defaults['position_soft_warn']),
        }

    # Legacy properties for backward compatibility with tests / callers
    # that don't pass direction.
    @property
    def hard_stop_pct(self) -> float:
        return self._threshold('position_stop_loss', -0.05)

    @property
    def trailing_stop_pct(self) -> float:
        return self._threshold('position_trailing_stop', -0.08)

    @property
    def soft_warn_pct(self) -> float:
        """Soft warning at -3% from entry — reduce 50%."""
        return self._threshold('position_soft_warn', -0.03)

    # ── single-position check ───────────────────────────────────────

    def check_position(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        high_watermark: float,
        direction: str = 'long',
    ) -> dict:
        """Evaluate a single position against the stop-loss ladder.

        For short positions the PnL direction is inverted: price going *up*
        is a loss.  Thresholds are loaded from direction-specific config keys
        (``short_stop_loss`` etc.) so longs and shorts can have independent
        stop parameters.

        Returns:
            dict with keys: action ('exit'|'reduce'|'ok'),
            reason, from_entry, from_hwm, direction.
        """
        if entry_price <= 0 or high_watermark <= 0:
            return self._result('ok', 'invalid_price', 0.0, 0.0)

        thresholds = self._get_thresholds(direction)

        # For short positions, loss = price going UP, so invert the ratio.
        if direction == 'short':
            from_entry = (entry_price - current_price) / entry_price
            from_hwm = (high_watermark - current_price) / high_watermark
        else:
            from_entry = (current_price - entry_price) / entry_price
            from_hwm = (current_price - high_watermark) / high_watermark

        # 1. Hard stop
        if from_entry < thresholds['hard']:
            self._log.warning(
                'hard_stop_triggered',
                symbol=symbol,
                direction=direction,
                from_entry=round(from_entry, 4),
                threshold=thresholds['hard'],
            )
            return self._result(
                'exit',
                f'hard_stop ({from_entry:.1%} from entry)',
                from_entry, from_hwm,
            )

        # 2. Trailing stop
        if from_hwm < thresholds['trailing']:
            self._log.warning(
                'trailing_stop_triggered',
                symbol=symbol,
                direction=direction,
                from_hwm=round(from_hwm, 4),
                hwm=high_watermark,
                threshold=thresholds['trailing'],
            )
            return self._result(
                'exit',
                f'trailing_stop ({from_hwm:.1%} from HWM {high_watermark:.2f})',
                from_entry, from_hwm,
            )

        # 3. Soft warning → reduce 50%
        if from_entry < thresholds['soft']:
            self._log.info(
                'soft_warning',
                symbol=symbol,
                direction=direction,
                from_entry=round(from_entry, 4),
            )
            return self._result(
                'reduce',
                f'soft_warn ({from_entry:.1%} from entry)',
                from_entry, from_hwm,
            )

        return self._result('ok', 'ok', from_entry, from_hwm)

    # ── portfolio-wide scan ─────────────────────────────────────────

    def scan_all_positions(
        self,
        positions: list[dict],
        current_prices: dict[str, float],
        db_session=None,
    ) -> dict:
        """Scan all open positions for stop-loss breaches.

        Args:
            positions: list of position dicts with keys:
                symbol, entry_price, high_watermark, is_open (or status='open')
            current_prices: {symbol: current_price}
            db_session: optional SQLAlchemy session; when provided, HWM values
                are re-fetched from DB to avoid stale-read race conditions with
                concurrent mark-to-market updates.

        Returns:
            dict with keys: exits (list), reductions (list), ok_count (int)
        """
        exits = []
        reductions = []
        ok_count = 0

        # Bulk-fetch fresh HWM values from DB to avoid stale reads.
        # mark_to_market() may have updated HWM between the time positions
        # were loaded and now; using a stale HWM could mis-fire the trailing stop.
        fresh_hwm: dict[str, float] = {}
        if db_session is not None:
            try:
                from app.models.positions import Position
                rows = db_session.query(
                    Position.symbol, Position.high_watermark,
                ).filter_by(status='open').all()
                fresh_hwm = {
                    row.symbol: float(row.high_watermark)
                    for row in rows
                    if row.high_watermark is not None
                }
            except Exception as exc:
                self._log.warning('hwm_db_refresh_failed', error=str(exc))

        for pos in positions:
            is_open = pos.get('is_open', pos.get('status') == 'open')
            if not is_open:
                continue

            symbol = pos['symbol']
            current = current_prices.get(symbol)
            if current is None:
                continue

            # Prefer DB-fresh HWM; fall back to the value in the passed dict.
            hwm = fresh_hwm.get(
                symbol,
                float(pos.get('high_watermark', pos['entry_price'])),
            )

            direction = pos.get('direction', 'long')

            result = self.check_position(
                symbol=symbol,
                entry_price=float(pos['entry_price']),
                current_price=current,
                high_watermark=hwm,
                direction=direction,
            )

            if result['action'] == 'exit':
                exits.append({
                    'symbol': symbol,
                    'action': 'exit',
                    'reason': result['reason'],
                    'entry_price': float(pos['entry_price']),
                    'current_price': current,
                    'from_entry': result['from_entry'],
                    'from_hwm': result['from_hwm'],
                })
            elif result['action'] == 'reduce':
                reductions.append({
                    'symbol': symbol,
                    'action': 'reduce',
                    'reason': result['reason'],
                    'reduce_pct': 0.50,
                    'entry_price': float(pos['entry_price']),
                    'current_price': current,
                    'from_entry': result['from_entry'],
                })
            else:
                ok_count += 1

        if exits:
            self._publish_stop_alerts(exits)

        self._log.info(
            'stop_loss_scan_complete',
            exits=len(exits),
            reductions=len(reductions),
            ok=ok_count,
        )

        return {
            'exits': exits,
            'reductions': reductions,
            'ok_count': ok_count,
        }

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _result(action: str, reason: str, from_entry: float, from_hwm: float) -> dict:
        return {
            'action': action,
            'reason': reason,
            'from_entry': round(from_entry, 6),
            'from_hwm': round(from_hwm, 6),
        }

    def _publish_stop_alerts(self, exits: list[dict]) -> None:
        if self._redis is None:
            return
        for exit_info in exits:
            payload = {
                'type': 'stop_loss_exit',
                'level': 'critical',
                'symbol': exit_info['symbol'],
                'reason': exit_info['reason'],
                'from_entry': exit_info['from_entry'],
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
            try:
                msg = json.dumps(payload)
                self._redis.lpush('channel:risk_alerts', msg)   # reliable queue
                self._redis.publish('channel:risk_alerts', msg)  # real-time dashboard
            except Exception as exc:
                self._log.error('stop_alert_publish_failed', error=str(exc))
