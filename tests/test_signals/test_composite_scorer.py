import fakeredis
import pytest

from app.signals.composite_scorer import CompositeScorer, DEFAULT_WEIGHTS


class TestCompositeScorer:
    @pytest.fixture
    def scorer(self):
        return CompositeScorer()

    @pytest.fixture
    def scorer_with_redis(self):
        redis = fakeredis.FakeRedis()
        return CompositeScorer(redis_client=redis), redis

    def test_load_default_weights(self, scorer):
        weights = scorer.load_weights()
        assert abs(sum(weights.values()) - 1.0) < 0.001
        assert 'trend_score' in weights

    def test_load_weights_from_redis(self, scorer_with_redis):
        scorer, redis = scorer_with_redis
        redis.hset('config:weights', 'weight_trend', '0.30')
        redis.hset('config:weights', 'weight_volatility', '0.20')
        redis.hset('config:weights', 'weight_sentiment', '0.15')
        redis.hset('config:weights', 'weight_breadth', '0.15')
        redis.hset('config:weights', 'weight_dispersion', '0.10')
        redis.hset('config:weights', 'weight_liquidity', '0.10')

        weights = scorer.load_weights()
        assert abs(sum(weights.values()) - 1.0) < 0.001
        # Trend should be highest
        assert weights['trend_score'] > weights['liquidity_score']

    def test_compute_all_neutral(self, scorer):
        """All factors at 0.5 → composite = 0.5."""
        scores = {k: 0.5 for k in DEFAULT_WEIGHTS}
        result = scorer.compute('SPY', scores)
        assert abs(result - 0.5) < 0.01

    def test_compute_all_high(self, scorer):
        """All factors at 1.0 → composite = 1.0."""
        scores = {k: 1.0 for k in DEFAULT_WEIGHTS}
        result = scorer.compute('SPY', scores)
        assert abs(result - 1.0) < 0.01

    def test_compute_all_low(self, scorer):
        """All factors at 0.0 → composite = 0.0."""
        scores = {k: 0.0 for k in DEFAULT_WEIGHTS}
        result = scorer.compute('SPY', scores)
        assert abs(result - 0.0) < 0.01

    def test_compute_clipped(self, scorer):
        """Scores are clipped to [0, 1]."""
        scores = {k: 2.0 for k in DEFAULT_WEIGHTS}
        result = scorer.compute('SPY', scores)
        assert result <= 1.0

    def test_compute_missing_factors(self, scorer):
        """Missing factors default to 0.5."""
        scores = {'trend_score': 0.8}
        result = scorer.compute('SPY', scores)
        assert 0.0 <= result <= 1.0

    def test_rank_universe(self, scorer):
        all_scores = {
            'SPY': {k: 0.8 for k in DEFAULT_WEIGHTS},
            'QQQ': {k: 0.6 for k in DEFAULT_WEIGHTS},
            'XLE': {k: 0.3 for k in DEFAULT_WEIGHTS},
        }
        df = scorer.rank_universe(all_scores)
        assert len(df) == 3
        assert df.index[0] == 'SPY'  # highest composite
        assert df.index[-1] == 'XLE'  # lowest composite
        assert list(df['rank']) == [1, 2, 3]

    def test_rank_empty(self, scorer):
        df = scorer.rank_universe({})
        assert df.empty

    def test_custom_weights(self, scorer):
        """Custom weights override defaults."""
        scores = {'trend_score': 1.0, 'volatility_regime': 0.0}
        weights = {'trend_score': 1.0, 'volatility_regime': 0.0}
        result = scorer.compute('SPY', scores, weights=weights)
        assert abs(result - 1.0) < 0.01
