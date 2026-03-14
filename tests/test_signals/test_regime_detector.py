import fakeredis
import numpy as np
import pandas as pd
import pytest

from app.signals.regime_detector import (
    REGIMES,
    RegimeTracker,
    classify_regime,
    detect_correlation_collapse,
    momentum_divergence_score,
)


class TestClassifyRegime:
    def test_strong_trending(self):
        factors = {
            'breadth_score': 0.9,
            'trend_score': 0.9,
            'volatility_regime': 0.8,
            'dispersion_score': 0.3,
            'correlation_index': 0.3,
        }
        regime, confidence = classify_regime(factors)
        assert regime == 'trending'
        assert confidence > 0.55

    def test_risk_off(self):
        factors = {
            'breadth_score': 0.1,
            'trend_score': 0.1,
            'volatility_regime': 0.1,
            'dispersion_score': 0.3,
            'correlation_index': 0.3,
        }
        regime, confidence = classify_regime(factors)
        assert regime == 'risk_off'
        assert confidence > 0.55

    def test_rotation(self):
        factors = {
            'breadth_score': 0.5,
            'trend_score': 0.5,
            'volatility_regime': 0.5,
            'dispersion_score': 0.9,
            'correlation_index': 0.9,
        }
        regime, confidence = classify_regime(factors)
        assert regime == 'rotation'

    def test_low_confidence_defaults_consolidation(self):
        """When no regime scores highly, consolidation is returned."""
        factors = {
            'breadth_score': 0.5,
            'trend_score': 0.5,
            'volatility_regime': 0.5,
            'dispersion_score': 0.5,
            'correlation_index': 0.5,
        }
        regime, confidence = classify_regime(factors)
        assert regime == 'consolidation'
        assert confidence == 0.50

    def test_missing_factors_default_to_neutral(self):
        regime, confidence = classify_regime({})
        assert regime == 'consolidation'
        assert confidence == 0.50

    def test_valid_regime_names(self):
        factors = {'trend_score': 0.95, 'breadth_score': 0.95, 'volatility_regime': 0.95}
        regime, _ = classify_regime(factors)
        assert regime in REGIMES

    def test_confidence_is_rounded(self):
        factors = {
            'breadth_score': 0.9,
            'trend_score': 0.9,
            'volatility_regime': 0.8,
        }
        _, confidence = classify_regime(factors)
        # Confidence is rounded to 4 decimals
        assert confidence == round(confidence, 4)


class TestRegimeTracker:
    @pytest.fixture
    def tracker(self):
        redis = fakeredis.FakeRedis()
        return RegimeTracker(redis_client=redis), redis

    def test_initial_state(self, tracker):
        t, _ = tracker
        assert t.current_regime == 'consolidation'
        assert t.current_confidence == 0.50

    def test_same_regime_resets_pending(self, tracker):
        t, _ = tracker
        result = t.update('consolidation', 0.6)
        assert result == 'consolidation'
        assert t.pending_regime is None
        assert t.pending_count == 0

    def test_requires_3_consecutive_days(self, tracker):
        t, _ = tracker
        # Day 1: pending
        assert t.update('trending', 0.7) == 'consolidation'
        assert t.pending_count == 1
        # Day 2: still pending
        assert t.update('trending', 0.7) == 'consolidation'
        assert t.pending_count == 2
        # Day 3: switches
        assert t.update('trending', 0.7) == 'trending'
        assert t.current_regime == 'trending'

    def test_interrupted_pending_resets(self, tracker):
        t, _ = tracker
        t.update('trending', 0.7)
        t.update('trending', 0.7)
        # Interrupt with different regime
        t.update('rotation', 0.7)
        assert t.pending_regime == 'rotation'
        assert t.pending_count == 1

    def test_risk_off_exit_requires_4_days(self, tracker):
        t, _ = tracker
        # Force into risk_off state
        t.current_regime = 'risk_off'
        t.current_confidence = 0.8

        # 3 consecutive days of trending is not enough to exit risk_off
        t.update('trending', 0.7)
        t.update('trending', 0.7)
        t.update('trending', 0.7)
        assert t.current_regime == 'risk_off'

        # 4th day triggers exit
        assert t.update('trending', 0.7) == 'trending'

    def test_risk_off_exit_blocked_low_confidence(self, tracker):
        t, _ = tracker
        t.current_regime = 'risk_off'
        t.current_confidence = 0.8

        # Even 10 days at low confidence won't exit risk_off
        for _ in range(10):
            result = t.update('trending', 0.60)
            assert result == 'risk_off'

    def test_risk_off_exit_needs_065_confidence(self, tracker):
        t, _ = tracker
        t.current_regime = 'risk_off'

        for _ in range(4):
            t.update('trending', 0.65)
        assert t.current_regime == 'trending'

    def test_state_persisted_to_redis(self, tracker):
        t, redis = tracker
        t.update('trending', 0.7)
        raw = redis.get('regime:tracker_state')
        assert raw is not None

    def test_state_restored_from_redis(self, tracker):
        _, redis = tracker
        import json
        state = {
            'current_regime': 'trending',
            'current_confidence': 0.85,
            'pending_regime': None,
            'pending_count': 0,
        }
        redis.set('regime:tracker_state', json.dumps(state))

        t2 = RegimeTracker(redis_client=redis)
        assert t2.current_regime == 'trending'
        assert t2.current_confidence == 0.85

    def test_regime_change_pushed_to_queue(self, tracker):
        t, redis = tracker
        for _ in range(3):
            t.update('trending', 0.7)
        msg = redis.rpop('channel:regime_change')
        assert msg is not None
        import json
        data = json.loads(msg)
        assert data['from_regime'] == 'consolidation'
        assert data['to_regime'] == 'trending'


class TestCorrelationCollapse:
    def test_collapse_detected(self):
        np.random.seed(42)
        # Normal correlation around 0.5, then sudden drop
        history = np.concatenate([
            np.random.normal(0.5, 0.05, 250),
            [0.1],  # collapse
        ])
        series = pd.Series(history)
        assert detect_correlation_collapse(series) is True

    def test_no_collapse_normal(self):
        np.random.seed(42)
        history = np.random.normal(0.5, 0.05, 300)
        series = pd.Series(history)
        assert detect_correlation_collapse(series) is False

    def test_insufficient_data(self):
        series = pd.Series([0.5] * 30)
        assert detect_correlation_collapse(series) is False


class TestMomentumDivergence:
    def test_returns_value_in_range(self):
        np.random.seed(42)
        n = 200
        cols = {f'sector_{i}': np.cumsum(np.random.randn(n) * 0.01) + 100
                for i in range(8)}
        df = pd.DataFrame(cols)
        score = momentum_divergence_score(df)
        assert 0.0 <= score <= 1.0

    def test_insufficient_data(self):
        df = pd.DataFrame({'a': [100, 101, 102], 'b': [100, 99, 98]})
        assert momentum_divergence_score(df) == 0.5

    def test_empty_dataframe(self):
        assert momentum_divergence_score(pd.DataFrame()) == 0.5
