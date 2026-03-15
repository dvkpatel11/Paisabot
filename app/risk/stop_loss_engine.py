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

    def _threshold(self, key: str, default: float) -> float:
        if self._config is not None:
            return self._config.get_float('risk', key, default)
        if self._redis is not None:
            raw = self._redis.hget('config:risk', key)
            if raw is not None:
                return float(raw.decode() if isinstance(raw, bytes) else raw)
        return default

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
    ) -> dict:
        """Evaluate a single position against the stop-loss ladder.

        Returns:
            dict with keys: action ('exit'|'reduce'|'ok'),
            reason, from_entry, from_hwm.
        """
        if entry_price <= 0 or high_watermark <= 0:
            return self._result('ok', 'invalid_price', 0.0, 0.0)

        from_entry = (current_price - entry_price) / entry_price
        from_hwm = (current_price - high_watermark) / high_watermark

        # 1. Hard stop — -5% from entry
        if from_entry < self.hard_stop_pct:
            self._log.warning(
                'hard_stop_triggered',
                symbol=symbol,
                from_entry=round(from_entry, 4),
                threshold=self.hard_stop_pct,
            )
            return self._result(
                'exit',
                f'hard_stop ({from_entry:.1%} from entry)',
                from_entry, from_hwm,
            )

        # 2. Trailing stop — -8% from high-water mark
        if from_hwm < self.trailing_stop_pct:
            self._log.warning(
                'trailing_stop_triggered',
                symbol=symbol,
                from_hwm=round(from_hwm, 4),
                hwm=high_watermark,
                threshold=self.trailing_stop_pct,
            )
            return self._result(
                'exit',
                f'trailing_stop ({from_hwm:.1%} from HWM {high_watermark:.2f})',
                from_entry, from_hwm,
            )

        # 3. Soft warning — -3% from entry → reduce 50%
        if from_entry < self.soft_warn_pct:
            self._log.info(
                'soft_warning',
                symbol=symbol,
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

            result = self.check_position(
                symbol=symbol,
                entry_price=float(pos['entry_price']),
                current_price=current,
                high_watermark=hwm,
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
                self._redis.lpush('channel:risk_alerts', json.dumps(payload))
            except Exception as exc:
                self._log.error('stop_alert_publish_failed', error=str(exc))
