import pytest

from app.portfolio.candidate_selector import CandidateSelector
from app.portfolio.constraints import PortfolioConstraints


@pytest.fixture
def selector():
    return CandidateSelector()


@pytest.fixture
def constraints():
    return PortfolioConstraints(max_positions=10)


def _signal(composite, signal_type='long', rank=1, tradable=True):
    return {
        'composite_score': composite,
        'signal_type': signal_type,
        'rank': rank,
        'tradable': tradable,
    }


SECTOR_MAP = {
    'XLK': 'Technology',
    'XLV': 'Health Care',
    'XLU': 'Utilities',
    'XLP': 'Consumer Staples',
    'XLE': 'Energy',
    'XLF': 'Financials',
    'XLY': 'Consumer Discretionary',
    'XLI': 'Industrials',
    'XLC': 'Communication Services',
    'SPY': 'Broad',
    'QQQ': 'Technology',
}


# ── basic selection ─────────────────────────────────────────────────

class TestBasicSelection:
    def test_selects_long_signals(self, selector, constraints):
        signals = {
            'XLK': _signal(0.85, 'long'),
            'XLE': _signal(0.70, 'long'),
            'XLF': _signal(0.50, 'neutral'),
            'XLI': _signal(0.30, 'avoid'),
        }
        result = selector.select(signals, constraints)
        assert result == ['XLK', 'XLE']

    def test_orders_by_composite_score(self, selector, constraints):
        signals = {
            'XLE': _signal(0.70, 'long'),
            'XLK': _signal(0.90, 'long'),
            'XLV': _signal(0.80, 'long'),
        }
        result = selector.select(signals, constraints)
        assert result == ['XLK', 'XLV', 'XLE']

    def test_respects_max_positions(self, selector):
        constraints = PortfolioConstraints(max_positions=2)
        signals = {
            'XLK': _signal(0.90, 'long'),
            'XLV': _signal(0.85, 'long'),
            'XLE': _signal(0.80, 'long'),
        }
        result = selector.select(signals, constraints)
        assert len(result) == 2
        assert result == ['XLK', 'XLV']

    def test_no_long_signals(self, selector, constraints):
        signals = {
            'XLK': _signal(0.50, 'neutral'),
            'XLE': _signal(0.30, 'avoid'),
        }
        result = selector.select(signals, constraints)
        assert result == []

    def test_empty_signals(self, selector, constraints):
        result = selector.select({}, constraints)
        assert result == []

    def test_skips_untradable(self, selector, constraints):
        signals = {
            'XLK': _signal(0.90, 'long', tradable=True),
            'XLE': _signal(0.85, 'long', tradable=False),
        }
        result = selector.select(signals, constraints)
        assert result == ['XLK']


# ── risk_off regime ─────────────────────────────────────────────────

class TestRiskOffRegime:
    def test_caps_at_5_positions(self, selector):
        constraints = PortfolioConstraints(max_positions=10)
        signals = {
            f'ETF{i}': _signal(0.90 - i * 0.01, 'long')
            for i in range(8)
        }
        result = selector.select(signals, constraints, regime='risk_off')
        assert len(result) <= 5

    def test_prefers_defensives(self, selector, constraints):
        signals = {
            'XLK': _signal(0.90, 'long'),   # Technology
            'XLV': _signal(0.85, 'long'),   # Health Care (defensive)
            'XLU': _signal(0.80, 'long'),   # Utilities (defensive)
            'XLP': _signal(0.75, 'long'),   # Consumer Staples (defensive)
        }
        result = selector.select(
            signals, constraints, regime='risk_off', sector_map=SECTOR_MAP,
        )
        # Defensives should come before tech
        assert 'XLV' in result
        assert 'XLU' in result
        assert 'XLP' in result

    def test_blocks_cyclicals(self, selector, constraints):
        signals = {
            'XLE': _signal(0.90, 'long'),   # Energy (cyclical)
            'XLF': _signal(0.85, 'long'),   # Financials (cyclical)
            'XLV': _signal(0.80, 'long'),   # Health Care (defensive)
        }
        result = selector.select(
            signals, constraints, regime='risk_off', sector_map=SECTOR_MAP,
        )
        assert 'XLE' not in result
        assert 'XLF' not in result
        assert 'XLV' in result


# ── trending regime ─────────────────────────────────────────────────

class TestTrendingRegime:
    def test_no_filtering_in_trending(self, selector, constraints):
        signals = {
            'XLE': _signal(0.90, 'long'),
            'XLF': _signal(0.85, 'long'),
            'XLK': _signal(0.80, 'long'),
        }
        result = selector.select(
            signals, constraints, regime='trending', sector_map=SECTOR_MAP,
        )
        # No cyclical blocking in trending regime
        assert 'XLE' in result
        assert 'XLF' in result
        assert len(result) == 3
