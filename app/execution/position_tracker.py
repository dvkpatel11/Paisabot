"""Position tracking from execution fills.

Creates, updates, and closes Position records based on trade fill results.
Also provides mark-to-market updates for open positions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import structlog

logger = structlog.get_logger()


class PositionTracker:
    """Tracks positions in the database from execution fills."""

    def __init__(self, db_session, redis_client=None):
        self._db = db_session
        self._redis = redis_client
        self._log = logger.bind(component='position_tracker')

    def update_from_fill(
        self,
        fill_result: dict,
        sector_map: dict[str, str] | None = None,
    ) -> None:
        """Create or update Position record from an execution result.

        Args:
            fill_result: dict from OrderManager with symbol, side, status,
                fill_price, filled_qty, notional, broker, regime, etc.
            sector_map: {symbol: sector_name} for sector tagging.
        """
        if fill_result.get('status') != 'filled':
            return

        symbol = fill_result['symbol']
        side = fill_result['side']
        fill_price = fill_result.get('fill_price')
        filled_qty = fill_result.get('filled_qty')

        if not fill_price or not filled_qty:
            return

        fill_price = float(fill_price)
        filled_qty = float(filled_qty)
        sector = (sector_map or {}).get(symbol)

        if side in ('buy', 'long'):
            self._open_or_add(fill_result, fill_price, filled_qty, sector)
        elif side in ('sell', 'short'):
            self._reduce_or_close(fill_result, fill_price, filled_qty)

    def _open_or_add(
        self,
        fill: dict,
        fill_price: float,
        filled_qty: float,
        sector: str | None,
    ) -> None:
        """Create new position or add to existing open position."""
        from app.models.positions import Position

        symbol = fill['symbol']
        existing = (
            Position.query
            .filter_by(symbol=symbol, status='open', direction='long')
            .first()
        )

        if existing:
            # Weighted average entry price
            old_qty = float(existing.quantity or 0)
            old_entry = float(existing.entry_price or 0)
            new_qty = old_qty + filled_qty
            if new_qty > 0:
                existing.entry_price = Decimal(str(round(
                    (old_entry * old_qty + fill_price * filled_qty) / new_qty,
                    4,
                )))
            existing.quantity = Decimal(str(round(new_qty, 6)))
            existing.current_price = Decimal(str(round(fill_price, 4)))
            existing.notional = Decimal(str(round(fill_price * new_qty, 2)))
            existing.high_watermark = max(
                existing.high_watermark or Decimal('0'),
                Decimal(str(round(fill_price, 4))),
            )
            existing.unrealized_pnl = Decimal(str(round(
                (fill_price - float(existing.entry_price)) * new_qty, 2,
            )))
            if sector:
                existing.sector = sector

            self._log.info(
                'position_added',
                symbol=symbol,
                new_qty=new_qty,
                entry_price=float(existing.entry_price),
            )
        else:
            pos = Position(
                symbol=symbol,
                broker=fill.get('broker', 'simulated'),
                broker_ref=fill.get('broker_order_id'),
                direction='long',
                entry_price=Decimal(str(round(fill_price, 4))),
                current_price=Decimal(str(round(fill_price, 4))),
                quantity=Decimal(str(round(filled_qty, 6))),
                notional=Decimal(str(round(fill_price * filled_qty, 2))),
                high_watermark=Decimal(str(round(fill_price, 4))),
                unrealized_pnl=Decimal('0'),
                realized_pnl=Decimal('0'),
                sector=sector,
                status='open',
                opened_at=datetime.now(timezone.utc),
            )
            self._db.add(pos)
            self._log.info(
                'position_opened',
                symbol=symbol,
                qty=filled_qty,
                price=fill_price,
            )

        self._db.commit()

    def _reduce_or_close(
        self,
        fill: dict,
        fill_price: float,
        filled_qty: float,
    ) -> None:
        """Reduce or close an existing open position on sell."""
        from app.models.positions import Position

        symbol = fill['symbol']
        existing = (
            Position.query
            .filter_by(symbol=symbol, status='open', direction='long')
            .first()
        )

        if not existing:
            self._log.warning('sell_no_open_position', symbol=symbol)
            return

        current_qty = float(existing.quantity or 0)
        entry_price = float(existing.entry_price or 0)
        sell_qty = min(filled_qty, current_qty)
        realized = round((fill_price - entry_price) * sell_qty, 2)

        existing.realized_pnl = (
            (existing.realized_pnl or Decimal('0'))
            + Decimal(str(realized))
        )

        remaining_qty = current_qty - sell_qty
        if remaining_qty <= 0.0001:
            # Fully closed
            existing.status = 'closed'
            existing.closed_at = datetime.now(timezone.utc)
            existing.close_reason = fill.get('reason', 'signal')
            existing.quantity = Decimal('0')
            existing.notional = Decimal('0')
            existing.unrealized_pnl = Decimal('0')
            self._log.info(
                'position_closed',
                symbol=symbol,
                realized_pnl=realized,
            )
        else:
            # Partial close
            existing.quantity = Decimal(str(round(remaining_qty, 6)))
            existing.current_price = Decimal(str(round(fill_price, 4)))
            existing.notional = Decimal(str(round(
                fill_price * remaining_qty, 2,
            )))
            existing.unrealized_pnl = Decimal(str(round(
                (fill_price - entry_price) * remaining_qty, 2,
            )))
            self._log.info(
                'position_reduced',
                symbol=symbol,
                remaining_qty=remaining_qty,
                realized_pnl=realized,
            )

        self._db.commit()

    def mark_to_market(
        self,
        current_prices: dict[str, float],
        portfolio_value: float,
    ) -> list[dict]:
        """Update all open positions with current market prices.

        Returns list of position dicts for downstream consumers.
        """
        from app.models.positions import Position

        positions = Position.query.filter_by(status='open').all()
        result = []

        for pos in positions:
            price = current_prices.get(pos.symbol)
            if price is None:
                result.append(self._pos_to_dict(pos, portfolio_value))
                continue

            pos.current_price = Decimal(str(round(price, 4)))
            pos.notional = Decimal(str(round(
                price * float(pos.quantity or 0), 2,
            )))
            pos.unrealized_pnl = Decimal(str(round(
                (price - float(pos.entry_price or 0)) * float(pos.quantity or 0),
                2,
            )))
            if portfolio_value > 0:
                pos.weight = Decimal(str(round(
                    float(pos.notional) / portfolio_value, 4,
                )))
            pos.high_watermark = max(
                pos.high_watermark or Decimal('0'),
                Decimal(str(round(price, 4))),
            )
            result.append(self._pos_to_dict(pos, portfolio_value))

        self._db.commit()
        return result

    def get_positions_summary(self) -> dict:
        """Get summary of all open positions.

        Returns:
            {
                'weights': {symbol: weight},
                'positions': [position_dicts],
                'total_notional': float,
                'num_positions': int,
            }
        """
        from app.models.positions import Position

        positions = Position.query.filter_by(status='open').all()
        weights = {}
        pos_list = []
        total_notional = 0.0

        for pos in positions:
            w = float(pos.weight or 0)
            weights[pos.symbol] = w
            total_notional += float(pos.notional or 0)
            pos_list.append({
                'symbol': pos.symbol,
                'direction': pos.direction,
                'quantity': float(pos.quantity or 0),
                'entry_price': float(pos.entry_price or 0),
                'current_price': float(pos.current_price or 0),
                'notional': float(pos.notional or 0),
                'weight': w,
                'unrealized_pnl': float(pos.unrealized_pnl or 0),
                'realized_pnl': float(pos.realized_pnl or 0),
                'sector': pos.sector,
                'status': pos.status,
            })

        return {
            'weights': weights,
            'positions': pos_list,
            'total_notional': total_notional,
            'num_positions': len(positions),
        }

    @staticmethod
    def _pos_to_dict(pos, portfolio_value: float) -> dict:
        return {
            'symbol': pos.symbol,
            'direction': pos.direction,
            'quantity': float(pos.quantity or 0),
            'entry_price': float(pos.entry_price or 0),
            'current_price': float(pos.current_price or 0),
            'notional': float(pos.notional or 0),
            'weight': float(pos.weight or 0),
            'unrealized_pnl': float(pos.unrealized_pnl or 0),
            'sector': pos.sector,
            'status': pos.status,
            'high_watermark': float(pos.high_watermark or 0),
        }
