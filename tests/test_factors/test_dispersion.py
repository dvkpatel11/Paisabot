import pytest

from app.factors.dispersion import DispersionFactor, SECTOR_ETFS


class TestDispersionFactor:
    @pytest.fixture
    def factor(self):
        return DispersionFactor()

    def test_compute_returns_scores(self, factor, sample_price_data, db_session):
        symbols = ['SPY', 'QQQ', 'XLK']
        scores = factor.compute(symbols)

        assert len(scores) == len(symbols)
        for sym, score in scores.items():
            assert 0.0 <= score <= 1.0, f'{sym} score {score} out of range'

    def test_market_level_uniform(self, factor, sample_price_data, db_session):
        """Dispersion is a market-level factor: all symbols get the same score."""
        symbols = ['SPY', 'QQQ', 'XLK', 'XLE', 'XLF']
        scores = factor.compute(symbols)

        values = list(scores.values())
        assert all(v == values[0] for v in values), \
            f'Dispersion scores should be uniform: {scores}'

    def test_crisis_gate_caps_score(self, factor):
        """When vol score < 0.35, dispersion should be capped at 0.50."""
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)
        factor._redis.set('factor:SPY:volatility_regime', '0.20')

        # High dispersion that would normally score > 0.50
        capped = factor._apply_crisis_gate(0.85)
        assert capped == 0.50

    def test_no_crisis_gate_without_redis(self, factor):
        """Without Redis, crisis gate is a no-op."""
        factor._redis = None
        result = factor._apply_crisis_gate(0.85)
        assert result == 0.85

    def test_normal_vol_no_cap(self, factor):
        """When vol score >= 0.35, dispersion is not capped."""
        import fakeredis
        factor._redis = fakeredis.FakeRedis(decode_responses=True)
        factor._redis.set('factor:SPY:volatility_regime', '0.60')

        result = factor._apply_crisis_gate(0.85)
        assert result == 0.85
