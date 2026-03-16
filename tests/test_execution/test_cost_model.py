"""Unit tests for TransactionCostModel."""

import pytest

from app.execution.cost_model import CostBreakdown, TransactionCostModel
from app.execution.slippage_tracker import SlippageTracker


@pytest.fixture
def tracker():
    return SlippageTracker()


@pytest.fixture
def model(tracker):
    return TransactionCostModel(tracker)


# ── basic behaviour ──────────────────────────────────────────────


class TestCostBreakdownStructure:
    def test_breakdown_components_sum_to_total(self, model):
        bd = model.estimate(
            symbol='SPY', side='buy', notional=5_000,
            mid_price=450.0, daily_volume_usd=30_000_000_000,
            volatility=0.15, spread_bps=1.0,
        )
        expected_total = round(bd.half_spread_bps + bd.market_impact_bps, 4)
        assert bd.total_bps == expected_total

    def test_breakdown_is_dataclass(self, model):
        bd = model.estimate(
            symbol='SPY', side='buy', notional=5_000,
            mid_price=450.0, daily_volume_usd=30_000_000_000,
            volatility=0.15,
        )
        assert isinstance(bd, CostBreakdown)
        assert hasattr(bd, 'half_spread_bps')
        assert hasattr(bd, 'market_impact_bps')
        assert hasattr(bd, 'total_bps')
        assert hasattr(bd, 'fill_price')
        assert hasattr(bd, 'filled_qty')


# ── fill price direction ─────────────────────────────────────────


class TestFillPriceDirection:
    def test_buy_fills_above_mid(self, model):
        bd = model.estimate(
            symbol='SPY', side='buy', notional=5_000,
            mid_price=450.0, daily_volume_usd=30_000_000_000,
            volatility=0.15,
        )
        assert bd.fill_price > 450.0

    def test_sell_fills_below_mid(self, model):
        bd = model.estimate(
            symbol='SPY', side='sell', notional=5_000,
            mid_price=450.0, daily_volume_usd=30_000_000_000,
            volatility=0.15,
        )
        assert bd.fill_price < 450.0


# ── edge cases ───────────────────────────────────────────────────


class TestEdgeCases:
    def test_zero_volume_no_crash(self, model):
        """Zero daily volume should not raise; impact stays within cap."""
        bd = model.estimate(
            symbol='XLK', side='buy', notional=5_000,
            mid_price=200.0, daily_volume_usd=0,
            volatility=0.20,
        )
        assert bd.total_bps >= 0
        assert bd.fill_price > 0
        assert bd.filled_qty > 0

    def test_zero_mid_price(self, model):
        """Zero mid price: filled_qty should be 0 (guard)."""
        bd = model.estimate(
            symbol='XLK', side='buy', notional=5_000,
            mid_price=0.0, daily_volume_usd=1_000_000,
            volatility=0.20,
        )
        assert bd.filled_qty == 0.0

    def test_zero_notional(self, model):
        """Zero-dollar order: impact should be zero or near-zero."""
        bd = model.estimate(
            symbol='SPY', side='buy', notional=0,
            mid_price=450.0, daily_volume_usd=30_000_000_000,
            volatility=0.15,
        )
        # Half-spread is the only component; impact is 0 for 0 notional
        assert bd.half_spread_bps > 0
        assert bd.market_impact_bps == 0.0


# ── realistic cost range ─────────────────────────────────────────


class TestRealisticCosts:
    def test_spy_small_order_low_cost(self, model):
        """SPY $5k order should cost roughly 0.5-3 bps total."""
        bd = model.estimate(
            symbol='SPY', side='buy', notional=5_000,
            mid_price=450.0,
            daily_volume_usd=30_000_000_000,  # SPY ~$30B ADV
            volatility=0.15,
            spread_bps=0.3,  # very tight spread
        )
        assert 0.1 <= bd.total_bps <= 5.0

    def test_high_volatility_higher_impact(self, model):
        """Higher vol → higher market impact (same order size)."""
        low_vol = model.estimate(
            symbol='XLK', side='buy', notional=50_000,
            mid_price=200.0, daily_volume_usd=1_000_000_000,
            volatility=0.10,
        )
        high_vol = model.estimate(
            symbol='XLK', side='buy', notional=50_000,
            mid_price=200.0, daily_volume_usd=1_000_000_000,
            volatility=0.40,
        )
        assert high_vol.market_impact_bps > low_vol.market_impact_bps
        assert high_vol.total_bps > low_vol.total_bps

    def test_larger_order_higher_impact(self, model):
        """Larger notional → higher market impact."""
        small = model.estimate(
            symbol='XLE', side='buy', notional=5_000,
            mid_price=80.0, daily_volume_usd=500_000_000,
            volatility=0.25,
        )
        large = model.estimate(
            symbol='XLE', side='buy', notional=500_000,
            mid_price=80.0, daily_volume_usd=500_000_000,
            volatility=0.25,
        )
        assert large.market_impact_bps > small.market_impact_bps


# ── spread parameter ─────────────────────────────────────────────


class TestSpreadHandling:
    def test_default_spread_is_one_bps(self, model):
        bd = model.estimate(
            symbol='SPY', side='buy', notional=5_000,
            mid_price=450.0, daily_volume_usd=30_000_000_000,
            volatility=0.15,
        )
        assert bd.half_spread_bps == 0.5  # 1.0 / 2

    def test_custom_spread(self, model):
        bd = model.estimate(
            symbol='SPY', side='buy', notional=5_000,
            mid_price=450.0, daily_volume_usd=30_000_000_000,
            volatility=0.15,
            spread_bps=4.0,
        )
        assert bd.half_spread_bps == 2.0
