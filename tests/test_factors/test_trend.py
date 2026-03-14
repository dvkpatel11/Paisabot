import pandas as pd
import pytest

from app.factors.trend import TrendFactor


class TestTrendFactor:
    @pytest.fixture
    def factor(self):
        return TrendFactor()

    def test_compute_returns_scores(self, factor, sample_price_data, db_session):
        symbols = ['SPY', 'QQQ', 'XLK', 'XLE', 'XLF']
        scores = factor.compute(symbols)

        assert len(scores) == len(symbols)
        for sym, score in scores.items():
            assert 0.0 <= score <= 1.0, f'{sym} score {score} out of range'

    def test_scores_differentiate(self, factor, sample_price_data, db_session):
        """Different ETFs should get different trend scores."""
        symbols = ['SPY', 'QQQ', 'XLK', 'XLE', 'XLF']
        scores = factor.compute(symbols)

        # Not all scores should be identical (unless truly coincidental)
        values = list(scores.values())
        assert len(set(round(v, 4) for v in values)) > 1

    def test_ma_alignment_bullish(self, factor):
        """Bullish stacking: prices trending up strongly."""
        prices = pd.Series([100 + i * 0.5 for i in range(250)])
        score = factor._compute_ma_alignment(prices)
        assert score == 1.0

    def test_ma_alignment_bearish(self, factor):
        """Bearish stacking: prices trending down strongly."""
        prices = pd.Series([250 - i * 0.5 for i in range(250)])
        score = factor._compute_ma_alignment(prices)
        assert score == 0.0

    def test_ma_alignment_insufficient_data(self, factor):
        """Not enough data defaults to 0.5."""
        prices = pd.Series([100, 101, 102])
        score = factor._compute_ma_alignment(prices)
        assert score == 0.5

    def test_pct_change(self, factor):
        series = pd.Series([100.0, 110.0, 121.0])
        result = factor._pct_change(series, 2)
        assert abs(result - 0.21) < 0.01

    def test_pct_change_insufficient(self, factor):
        series = pd.Series([100.0])
        result = factor._pct_change(series, 5)
        assert result is None

    def test_sector_momentum_rank(self, factor, sample_price_data, db_session):
        symbols = ['XLK', 'XLE', 'XLF', 'XLV', 'XLI']
        ranks = factor.compute_sector_momentum_rank(symbols)
        assert len(ranks) == len(symbols)
        for score in ranks.values():
            assert 0.0 <= score <= 1.0
