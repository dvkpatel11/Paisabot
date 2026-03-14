import fakeredis
import pytest
from unittest.mock import patch, MagicMock

from app.signals.signal_generator import SignalGenerator, classify_signal


class TestClassifySignal:
    def test_long_normal_regime(self):
        assert classify_signal(0.70, 'trending') == 'long'
        assert classify_signal(0.65, 'consolidation') == 'long'

    def test_long_risk_off_higher_threshold(self):
        assert classify_signal(0.70, 'risk_off') == 'long'
        assert classify_signal(0.68, 'risk_off') == 'neutral'  # below 0.70

    def test_neutral(self):
        assert classify_signal(0.50, 'trending') == 'neutral'
        assert classify_signal(0.40, 'trending') == 'neutral'
        assert classify_signal(0.64, 'trending') == 'neutral'

    def test_avoid(self):
        assert classify_signal(0.39, 'trending') == 'avoid'
        assert classify_signal(0.10, 'risk_off') == 'avoid'
        assert classify_signal(0.0, 'consolidation') == 'avoid'

    def test_boundary_065(self):
        assert classify_signal(0.65, 'trending') == 'long'
        assert classify_signal(0.6499, 'trending') == 'neutral'

    def test_boundary_040(self):
        assert classify_signal(0.40, 'trending') == 'neutral'
        assert classify_signal(0.3999, 'trending') == 'avoid'


class TestSignalGenerator:
    @pytest.fixture
    def redis(self):
        return fakeredis.FakeRedis(decode_responses=True)

    @pytest.fixture
    def mock_factor_scores(self):
        """Simulated factor scores for 3 symbols."""
        return {
            'SPY': {
                'trend_score': 0.8,
                'volatility_regime': 0.7,
                'sentiment_score': 0.6,
                'breadth_score': 0.7,
                'dispersion_score': 0.5,
                'liquidity_score': 0.9,
            },
            'QQQ': {
                'trend_score': 0.5,
                'volatility_regime': 0.5,
                'sentiment_score': 0.5,
                'breadth_score': 0.5,
                'dispersion_score': 0.5,
                'liquidity_score': 0.8,
            },
            'XLE': {
                'trend_score': 0.2,
                'volatility_regime': 0.3,
                'sentiment_score': 0.3,
                'breadth_score': 0.2,
                'dispersion_score': 0.4,
                'liquidity_score': 0.6,
            },
        }

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_run_produces_signals(self, MockRegistry, redis, mock_factor_scores, app, db_session):
        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = mock_factor_scores
        MockRegistry.return_value = mock_reg

        # Set factor freshness keys
        for sym in ['SPY', 'QQQ', 'XLE']:
            redis.set(f'scores:{sym}', '{}', ex=900)
            redis.set(f'etf:{sym}:adv_30d_m', '100.0')
            redis.set(f'etf:{sym}:spread_bps', '2.0')

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        signals = gen.run(['SPY', 'QQQ', 'XLE'])

        assert len(signals) == 3
        for sym, sig in signals.items():
            assert 'composite_score' in sig
            assert 'signal_type' in sig
            assert sig['signal_type'] in ('long', 'neutral', 'avoid', 'blocked')
            assert 'regime' in sig
            assert 'rank' in sig

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_blocked_signals(self, MockRegistry, redis, mock_factor_scores, app, db_session):
        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = mock_factor_scores
        MockRegistry.return_value = mock_reg

        # Kill switch active
        redis.set('kill_switch:trading', '1')
        for sym in ['SPY', 'QQQ', 'XLE']:
            redis.set(f'scores:{sym}', '{}', ex=900)

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        signals = gen.run(['SPY', 'QQQ', 'XLE'])

        for sig in signals.values():
            assert sig['signal_type'] == 'blocked'
            assert sig['tradable'] is False

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_ranking_order(self, MockRegistry, redis, mock_factor_scores, app, db_session):
        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = mock_factor_scores
        MockRegistry.return_value = mock_reg

        for sym in ['SPY', 'QQQ', 'XLE']:
            redis.set(f'scores:{sym}', '{}', ex=900)
            redis.set(f'etf:{sym}:adv_30d_m', '100.0')
            redis.set(f'etf:{sym}:spread_bps', '2.0')

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        signals = gen.run(['SPY', 'QQQ', 'XLE'])

        # SPY has highest scores → rank 1
        assert signals['SPY']['rank'] == 1
        assert signals['XLE']['rank'] == 3

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_publishes_to_redis(self, MockRegistry, redis, mock_factor_scores, app, db_session):
        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = mock_factor_scores
        MockRegistry.return_value = mock_reg

        for sym in ['SPY', 'QQQ', 'XLE']:
            redis.set(f'scores:{sym}', '{}', ex=900)
            redis.set(f'etf:{sym}:adv_30d_m', '100.0')
            redis.set(f'etf:{sym}:spread_bps', '2.0')

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        gen.run(['SPY', 'QQQ', 'XLE'])

        assert redis.get('cache:latest_scores') is not None

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_empty_universe(self, MockRegistry, redis, app, db_session):
        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = {}
        MockRegistry.return_value = mock_reg

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        signals = gen.run([])

        assert signals == {}

    @patch('app.signals.signal_generator.FactorRegistry')
    def test_risk_off_raises_long_threshold(self, MockRegistry, redis, app, db_session):
        """In risk_off regime, long threshold is 0.70 not 0.65."""
        scores = {
            'SPY': {
                'trend_score': 0.1,
                'volatility_regime': 0.1,
                'sentiment_score': 0.1,
                'breadth_score': 0.1,
                'dispersion_score': 0.5,
                'liquidity_score': 0.5,
            },
            'QQQ': {
                'trend_score': 0.1,
                'volatility_regime': 0.1,
                'sentiment_score': 0.1,
                'breadth_score': 0.1,
                'dispersion_score': 0.5,
                'liquidity_score': 0.5,
            },
        }
        mock_reg = MagicMock()
        mock_reg.compute_all.return_value = scores
        MockRegistry.return_value = mock_reg

        for sym in ['SPY', 'QQQ']:
            redis.set(f'scores:{sym}', '{}', ex=900)
            redis.set(f'etf:{sym}:adv_30d_m', '100.0')
            redis.set(f'etf:{sym}:spread_bps', '2.0')

        gen = SignalGenerator(redis_client=redis)
        gen.factors = mock_reg
        # Force risk_off regime
        gen.regime.current_regime = 'risk_off'
        gen.regime.current_confidence = 0.8
        signals = gen.run(['SPY', 'QQQ'])

        # Low scores → should be avoid regardless
        for sig in signals.values():
            assert sig['signal_type'] in ('avoid', 'blocked')
