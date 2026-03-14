import pytest

from app.execution.slippage_tracker import SlippageTracker


class TestSlippageTracker:
    @pytest.fixture
    def tracker(self):
        return SlippageTracker()

    # ── pre-trade estimation ───────────────────────────────────────

    def test_small_order_low_slippage(self, tracker):
        """Small order relative to volume should have minimal slippage."""
        result = tracker.estimate_pretrade(
            notional=1_000,
            mid_price=100.0,
            daily_volume_usd=50_000_000,
            volatility=0.20,
        )
        assert result >= 0
        assert result < 1.0  # well under 1 bps

    def test_large_order_higher_slippage(self, tracker):
        """Large order should produce higher estimated slippage."""
        small = tracker.estimate_pretrade(
            notional=1_000,
            mid_price=100.0,
            daily_volume_usd=50_000_000,
            volatility=0.20,
        )
        large = tracker.estimate_pretrade(
            notional=1_000_000,
            mid_price=100.0,
            daily_volume_usd=50_000_000,
            volatility=0.20,
        )
        assert large > small

    def test_higher_vol_higher_slippage(self, tracker):
        """Higher volatility should increase estimated slippage."""
        low_vol = tracker.estimate_pretrade(
            notional=10_000,
            mid_price=100.0,
            daily_volume_usd=50_000_000,
            volatility=0.10,
        )
        high_vol = tracker.estimate_pretrade(
            notional=10_000,
            mid_price=100.0,
            daily_volume_usd=50_000_000,
            volatility=0.40,
        )
        assert high_vol > low_vol

    def test_cap_at_50_bps(self, tracker):
        """Slippage estimate should be capped at 50 bps."""
        result = tracker.estimate_pretrade(
            notional=100_000_000,  # huge order
            mid_price=100.0,
            daily_volume_usd=1_000,  # tiny volume
            volatility=1.0,
        )
        assert result <= 50.0

    def test_zero_price_returns_zero(self, tracker):
        result = tracker.estimate_pretrade(
            notional=1000, mid_price=0, daily_volume_usd=50_000_000, volatility=0.2,
        )
        assert result == 0.0

    def test_zero_volume_returns_zero(self, tracker):
        result = tracker.estimate_pretrade(
            notional=1000, mid_price=100, daily_volume_usd=0, volatility=0.2,
        )
        assert result == 0.0

    # ── pre-trade check ────────────────────────────────────────────

    def test_check_acceptable(self, tracker):
        result = tracker.check_pretrade(
            notional=1_000,
            mid_price=100.0,
            daily_volume_usd=50_000_000,
            volatility=0.15,
        )
        assert result['acceptable'] is True
        assert result['estimated_bps'] < result['threshold_bps']

    def test_check_unacceptable(self, tracker):
        result = tracker.check_pretrade(
            notional=50_000_000,
            mid_price=100.0,
            daily_volume_usd=100_000,  # very low volume
            volatility=0.50,
        )
        assert result['acceptable'] is False

    def test_default_threshold(self, tracker):
        assert tracker.max_slippage_bps == 5.0

    # ── post-trade measurement ─────────────────────────────────────

    def test_buy_adverse_slippage(self):
        """Buy at higher price → positive (adverse) slippage."""
        bps = SlippageTracker.measure_posttrade(
            fill_price=100.10,
            mid_at_submission=100.00,
            side='buy',
        )
        assert bps > 0  # adverse
        assert abs(bps - 10.0) < 0.1  # ~10 bps

    def test_buy_favorable_slippage(self):
        """Buy below mid → negative (favorable) slippage."""
        bps = SlippageTracker.measure_posttrade(
            fill_price=99.95,
            mid_at_submission=100.00,
            side='buy',
        )
        assert bps < 0  # favorable

    def test_sell_adverse_slippage(self):
        """Sell below mid → positive (adverse) slippage."""
        bps = SlippageTracker.measure_posttrade(
            fill_price=99.90,
            mid_at_submission=100.00,
            side='sell',
        )
        assert bps > 0  # adverse for sell

    def test_sell_favorable_slippage(self):
        """Sell above mid → negative (favorable) slippage."""
        bps = SlippageTracker.measure_posttrade(
            fill_price=100.05,
            mid_at_submission=100.00,
            side='sell',
        )
        assert bps < 0  # favorable for sell

    def test_zero_mid_returns_zero(self):
        bps = SlippageTracker.measure_posttrade(100.0, 0.0, 'buy')
        assert bps == 0.0
