import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from app.factors.sentiment import SentimentFactor


class TestSentimentFactor:
    @pytest.fixture
    def factor(self):
        return SentimentFactor()

    def test_compute_no_data_returns_neutral(self, factor):
        """With no data sources, all components return None → 0.5."""
        scores = factor.compute(['SPY', 'QQQ'])
        assert len(scores) == 2
        for score in scores.values():
            assert score == 0.5  # fully neutral

    def test_compute_with_news_only(self, factor):
        """When only news is available, score is based on news alone."""
        with patch.object(factor, '_compute_news_score', return_value=0.7), \
             patch.object(factor, '_compute_reddit_score', return_value=None), \
             patch.object(factor, '_compute_options_score', return_value=None), \
             patch.object(factor, '_compute_flow_score', return_value=None):
            scores = factor.compute(['SPY'])
        # Only news available (weight 0.35, redistributed to 1.0)
        assert abs(scores['SPY'] - 0.7) < 0.01

    def test_compute_with_all_components(self, factor):
        """With all components, weights sum correctly."""
        with patch.object(factor, '_compute_news_score', return_value=0.8), \
             patch.object(factor, '_compute_reddit_score', return_value=0.6), \
             patch.object(factor, '_compute_options_score', return_value=0.7), \
             patch.object(factor, '_compute_flow_score', return_value=0.5):
            scores = factor.compute(['SPY'])

        expected = 0.35 * 0.8 + 0.25 * 0.6 + 0.25 * 0.7 + 0.15 * 0.5
        assert abs(scores['SPY'] - expected) < 0.01

    def test_weight_redistribution(self, factor):
        """When some components missing, weights redistribute proportionally."""
        with patch.object(factor, '_compute_news_score', return_value=0.8), \
             patch.object(factor, '_compute_reddit_score', return_value=0.4), \
             patch.object(factor, '_compute_options_score', return_value=None), \
             patch.object(factor, '_compute_flow_score', return_value=None):
            scores = factor.compute(['SPY'])

        # Available weight = 0.35 + 0.25 = 0.60
        expected = (0.35 / 0.60) * 0.8 + (0.25 / 0.60) * 0.4
        assert abs(scores['SPY'] - expected) < 0.01

    def test_reddit_score_calculation(self, factor):
        """Reddit bull/bear ratio normalized correctly."""
        # 7 bull, 3 bear, 0 neutral → ratio = (7-3)/10 = 0.4 → (0.4+1)/2 = 0.7
        with patch.object(factor, '_get_reddit_counts', return_value={
            'bull': 7, 'bear': 3, 'neutral': 0, 'total': 10,
        }):
            score = factor._compute_reddit_score('SPY')
        assert abs(score - 0.7) < 0.01

    def test_reddit_score_bearish(self, factor):
        """All bear mentions should give low score."""
        with patch.object(factor, '_get_reddit_counts', return_value={
            'bull': 0, 'bear': 10, 'neutral': 0, 'total': 10,
        }):
            score = factor._compute_reddit_score('SPY')
        assert score == 0.0

    def test_reddit_insufficient_data(self, factor):
        """Too few mentions returns None."""
        with patch.object(factor, '_get_reddit_counts', return_value={
            'bull': 1, 'bear': 0, 'neutral': 0, 'total': 1,
        }):
            score = factor._compute_reddit_score('SPY')
        assert score is None

    def test_vader_fallback(self, factor):
        """VADER should return a score in [0, 1]."""
        texts = [
            'Great earnings report, stock is soaring!',
            'Company beats expectations',
            'Terrible losses, company in trouble',
        ]
        score = factor._vader_score(texts)
        if score is not None:  # VADER may not be installed
            assert 0.0 <= score <= 1.0

    def test_options_score_from_redis(self, factor):
        """Options score reads from Redis cache."""
        import json
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)
        factor._redis.set('options:SPY:pc_ratio', '0.8')
        factor._redis.set(
            'options:SPY:pc_history',
            json.dumps([float(x) for x in np.random.uniform(0.5, 1.5, 252)]),
        )
        score = factor._compute_options_score('SPY')
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_news_score_insufficient_headlines(self, factor):
        """Fewer than 5 headlines returns None."""
        with patch.object(factor, '_get_news_headlines', return_value=['h1', 'h2']):
            score = factor._compute_news_score('SPY')
        assert score is None
