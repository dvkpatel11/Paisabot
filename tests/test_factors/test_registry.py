import pytest

from app.factors.factor_registry import FactorRegistry


class TestFactorRegistry:
    def test_init_all_factors(self):
        registry = FactorRegistry()
        enabled = registry.get_enabled_factors()
        assert 'trend_score' in enabled
        assert 'volatility_regime' in enabled
        assert 'dispersion_score' in enabled
        assert 'correlation_index' in enabled

    def test_compute_all(self, sample_price_data, db_session):
        registry = FactorRegistry()
        symbols = ['SPY', 'QQQ', 'XLK']
        results = registry.compute_all(symbols)

        assert len(results) == len(symbols)
        for sym in symbols:
            assert sym in results
            scores = results[sym]
            # Should have all enabled factors
            assert 'trend_score' in scores
            assert 'volatility_regime' in scores
            for name, score in scores.items():
                assert 0.0 <= score <= 1.0, f'{sym}.{name} = {score}'

    def test_compute_single(self, sample_price_data, db_session):
        registry = FactorRegistry()
        scores = registry.compute_single('trend_score', ['SPY', 'QQQ'])
        assert len(scores) == 2
        for score in scores.values():
            assert 0.0 <= score <= 1.0

    def test_unknown_factor(self):
        registry = FactorRegistry()
        scores = registry.compute_single('nonexistent', ['SPY'])
        assert scores == {'SPY': 0.5}

    def test_factor_disable(self):
        """Test that disabled factors are excluded."""
        from unittest.mock import MagicMock
        config = MagicMock()
        config.get.return_value = 'trend_score,volatility_regime'

        registry = FactorRegistry(config_loader=config)
        enabled = registry.get_enabled_factors()
        assert 'trend_score' in enabled
        assert 'volatility_regime' in enabled
        assert 'dispersion_score' not in enabled

    def test_results_cached_in_redis(self, sample_price_data, db_session):
        import fakeredis
        redis = fakeredis.FakeRedis(decode_responses=True)
        registry = FactorRegistry(redis_client=redis)

        results = registry.compute_all(['SPY'])
        # Check Redis cache was populated
        assert redis.get('scores:SPY') is not None
        assert redis.get('factor:SPY:trend_score') is not None
