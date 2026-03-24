"""Tests for PerformanceRecorder — daily metrics computation and persistence."""
import pytest
from datetime import date, datetime, timezone
from decimal import Decimal

from app.risk.performance_recorder import PerformanceRecorder
from app.models.performance import PerformanceMetric
from app.models.positions import Position


class TestPerformanceRecorder:
    def test_record_daily_no_positions(self, db_session, redis_mock):
        """With no positions, portfolio value equals initial capital."""
        recorder = PerformanceRecorder(db_session, redis_mock)
        result = recorder.record_daily(target_date=date(2026, 3, 15))

        assert result['portfolio_value'] == 100_000.0
        assert result['daily_return'] == 0.0
        assert result['num_positions'] == 0

        metric = PerformanceMetric.query.first()
        assert metric is not None
        assert metric.date == date(2026, 3, 15)

    def test_record_daily_with_open_positions(self, db_session, redis_mock):
        """Portfolio value includes unrealized PnL from open positions."""
        pos = Position(
            symbol='SPY', broker='mock', direction='long',
            entry_price=Decimal('500'), current_price=Decimal('510'),
            quantity=Decimal('10'), notional=Decimal('5100'),
            unrealized_pnl=Decimal('100'), realized_pnl=Decimal('0'),
            sector='Broad Market', status='open',
            opened_at=datetime(2026, 3, 14, 14, 0, 0, tzinfo=timezone.utc),
        )
        db_session.add(pos)
        db_session.commit()

        recorder = PerformanceRecorder(db_session, redis_mock)
        result = recorder.record_daily(target_date=date(2026, 3, 15))

        # 100000 initial + 100 unrealized
        assert result['portfolio_value'] == 100_100.0
        assert result['num_positions'] == 1

    def test_record_daily_return_from_previous(self, db_session, redis_mock):
        """Daily return computed from previous day's metric."""
        # Insert yesterday's metric
        yesterday = PerformanceMetric(
            date=date(2026, 3, 14),
            portfolio_value=Decimal('100000'),
            daily_return=Decimal('0'),
        )
        db_session.add(yesterday)
        db_session.commit()

        # Add position with unrealized gain
        pos = Position(
            symbol='SPY', broker='mock', direction='long',
            entry_price=Decimal('500'), current_price=Decimal('510'),
            quantity=Decimal('10'), notional=Decimal('5100'),
            unrealized_pnl=Decimal('100'), realized_pnl=Decimal('0'),
            status='open', opened_at=datetime(2026, 3, 14, 14, 0, 0, tzinfo=timezone.utc),
        )
        db_session.add(pos)
        db_session.commit()

        recorder = PerformanceRecorder(db_session, redis_mock)
        result = recorder.record_daily(target_date=date(2026, 3, 15))

        # (100100 - 100000) / 100000 = 0.001
        assert abs(result['daily_return'] - 0.001) < 0.0001

    def test_drawdown_computation(self, db_session, redis_mock):
        """Drawdown is computed from cumulative peak."""
        # Peak was at 105000
        m1 = PerformanceMetric(
            date=date(2026, 3, 13),
            portfolio_value=Decimal('105000'),
            daily_return=Decimal('0.05'),
        )
        m2 = PerformanceMetric(
            date=date(2026, 3, 14),
            portfolio_value=Decimal('103000'),
            daily_return=Decimal('-0.019'),
        )
        db_session.add_all([m1, m2])
        db_session.commit()

        # No positions → portfolio at 100000 (below peak of 105000)
        recorder = PerformanceRecorder(db_session, redis_mock)
        result = recorder.record_daily(target_date=date(2026, 3, 15))

        # (100000 - 105000) / 105000 ≈ -0.047619
        assert result['drawdown'] < 0

    def test_upsert_existing_metric(self, db_session, redis_mock):
        """Re-recording the same date updates rather than duplicates."""
        recorder = PerformanceRecorder(db_session, redis_mock)
        recorder.record_daily(target_date=date(2026, 3, 15))
        recorder.record_daily(target_date=date(2026, 3, 15))

        assert PerformanceMetric.query.count() == 1

    def test_sharpe_and_vol_with_sufficient_data(self, db_session, redis_mock):
        """Sharpe and vol are computed when enough data points exist."""
        # Create 10 days of metrics
        for i in range(10):
            m = PerformanceMetric(
                date=date(2026, 3, 1 + i),
                portfolio_value=Decimal(str(100_000 + i * 100)),
                daily_return=Decimal('0.001'),
            )
            db_session.add(m)
        db_session.commit()

        recorder = PerformanceRecorder(db_session, redis_mock)
        result = recorder.record_daily(target_date=date(2026, 3, 15))

        assert result['sharpe_30d'] is not None
        assert result['volatility_30d'] is not None

    def test_sharpe_none_with_insufficient_data(self, db_session, redis_mock):
        """Sharpe is None when less than 5 data points."""
        recorder = PerformanceRecorder(db_session, redis_mock)
        result = recorder.record_daily(target_date=date(2026, 3, 15))

        # Only 1 data point (today's)
        assert result['sharpe_30d'] is None

    def test_compute_sharpe_uses_sample_variance(self):
        """Sharpe denominator must use n-1 (sample std), not n (population std)."""
        import math
        returns = [0.01, -0.005, 0.02, -0.01, 0.015, 0.0]  # 6 observations
        n = len(returns)
        mean = sum(returns) / n
        sample_std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
        expected_sharpe = (mean / sample_std) * math.sqrt(252)

        result = PerformanceRecorder._compute_sharpe(returns)
        assert result is not None
        assert abs(result - expected_sharpe) < 1e-9

    def test_compute_vol_uses_sample_variance(self):
        """Volatility must use sample std (n-1), not population std (n)."""
        import math
        returns = [0.01, -0.005, 0.02, -0.01, 0.015, 0.0]
        n = len(returns)
        mean = sum(returns) / n
        sample_std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
        expected_vol = sample_std * math.sqrt(252)

        result = PerformanceRecorder._compute_vol(returns)
        assert result is not None
        assert abs(result - expected_vol) < 1e-9
