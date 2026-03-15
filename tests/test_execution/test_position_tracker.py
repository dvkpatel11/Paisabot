"""Tests for PositionTracker — position creation, updates, and mark-to-market."""
import pytest
from decimal import Decimal

from app.execution.position_tracker import PositionTracker
from app.models.positions import Position


class TestPositionTracker:
    def test_open_new_position_on_buy(self, db_session, redis_mock):
        tracker = PositionTracker(db_session, redis_mock)
        fill = {
            'symbol': 'SPY',
            'side': 'buy',
            'status': 'filled',
            'fill_price': 500.0,
            'filled_qty': 10.0,
            'notional': 5000.0,
            'broker': 'mock',
        }
        tracker.update_from_fill(fill, sector_map={'SPY': 'Broad Market'})

        pos = Position.query.filter_by(symbol='SPY', status='open').first()
        assert pos is not None
        assert float(pos.entry_price) == 500.0
        assert float(pos.quantity) == 10.0
        assert pos.direction == 'long'
        assert pos.sector == 'Broad Market'

    def test_add_to_existing_position_weighted_avg(self, db_session, redis_mock):
        tracker = PositionTracker(db_session, redis_mock)

        # First buy
        tracker.update_from_fill({
            'symbol': 'QQQ', 'side': 'buy', 'status': 'filled',
            'fill_price': 400.0, 'filled_qty': 10.0,
        })

        # Second buy at different price
        tracker.update_from_fill({
            'symbol': 'QQQ', 'side': 'buy', 'status': 'filled',
            'fill_price': 420.0, 'filled_qty': 10.0,
        })

        pos = Position.query.filter_by(symbol='QQQ', status='open').first()
        assert float(pos.quantity) == 20.0
        # Weighted avg: (400*10 + 420*10) / 20 = 410
        assert float(pos.entry_price) == 410.0

    def test_close_position_on_sell(self, db_session, redis_mock):
        tracker = PositionTracker(db_session, redis_mock)

        tracker.update_from_fill({
            'symbol': 'XLK', 'side': 'buy', 'status': 'filled',
            'fill_price': 200.0, 'filled_qty': 5.0,
        })

        tracker.update_from_fill({
            'symbol': 'XLK', 'side': 'sell', 'status': 'filled',
            'fill_price': 210.0, 'filled_qty': 5.0,
        })

        pos = Position.query.filter_by(symbol='XLK').first()
        assert pos.status == 'closed'
        assert float(pos.realized_pnl) == 50.0  # (210-200)*5

    def test_partial_sell_reduces_position(self, db_session, redis_mock):
        tracker = PositionTracker(db_session, redis_mock)

        tracker.update_from_fill({
            'symbol': 'XLE', 'side': 'buy', 'status': 'filled',
            'fill_price': 100.0, 'filled_qty': 20.0,
        })

        tracker.update_from_fill({
            'symbol': 'XLE', 'side': 'sell', 'status': 'filled',
            'fill_price': 110.0, 'filled_qty': 10.0,
        })

        pos = Position.query.filter_by(symbol='XLE', status='open').first()
        assert pos is not None
        assert float(pos.quantity) == 10.0
        assert float(pos.realized_pnl) == 100.0  # (110-100)*10

    def test_skips_non_filled_status(self, db_session, redis_mock):
        tracker = PositionTracker(db_session, redis_mock)
        tracker.update_from_fill({
            'symbol': 'SPY', 'side': 'buy', 'status': 'blocked',
            'fill_price': 500.0, 'filled_qty': 10.0,
        })
        assert Position.query.count() == 0

    def test_mark_to_market(self, db_session, redis_mock):
        tracker = PositionTracker(db_session, redis_mock)

        tracker.update_from_fill({
            'symbol': 'SPY', 'side': 'buy', 'status': 'filled',
            'fill_price': 500.0, 'filled_qty': 10.0,
        })

        result = tracker.mark_to_market({'SPY': 520.0}, 100_000.0)
        assert len(result) == 1
        assert result[0]['symbol'] == 'SPY'

        pos = Position.query.filter_by(symbol='SPY').first()
        assert float(pos.current_price) == 520.0
        assert float(pos.unrealized_pnl) == 200.0  # (520-500)*10
        assert float(pos.high_watermark) == 520.0

    def test_get_positions_summary(self, db_session, redis_mock):
        tracker = PositionTracker(db_session, redis_mock)

        tracker.update_from_fill({
            'symbol': 'SPY', 'side': 'buy', 'status': 'filled',
            'fill_price': 500.0, 'filled_qty': 10.0,
        })
        tracker.update_from_fill({
            'symbol': 'QQQ', 'side': 'buy', 'status': 'filled',
            'fill_price': 400.0, 'filled_qty': 5.0,
        })

        summary = tracker.get_positions_summary()
        assert summary['num_positions'] == 2
        assert 'SPY' in summary['weights']
        assert 'QQQ' in summary['weights']
        assert summary['total_notional'] == 7000.0  # 5000 + 2000
