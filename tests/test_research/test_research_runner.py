"""Tests for the ResearchRunner service."""
import json
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import fakeredis
import pytest

from app.research.research_runner import ResearchRunner, ResearchResult


# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def redis():
    r = fakeredis.FakeRedis()
    r.set('cache:regime:current', json.dumps({
        'regime': 'trending', 'confidence': 0.75,
    }))
    return r


def _seed_universe(db_session):
    from app.models.etf_universe import ETFUniverse
    for sym, sector in [('SPY', 'Broad'), ('QQQ', 'Tech'), ('XLE', 'Energy')]:
        db_session.add(ETFUniverse(
            symbol=sym, name=sym, sector=sector,
            is_active=True, in_active_set=True,
        ))
    db_session.commit()


def _seed_prices(db_session, symbols=None, days=30):
    from app.models.price_bars import PriceBar
    if symbols is None:
        symbols = [('SPY', 500), ('QQQ', 400), ('XLE', 80)]
    base = datetime(2026, 2, 1, tzinfo=timezone.utc)
    for sym, bp in symbols:
        for i in range(days):
            ts = base + timedelta(days=i)
            db_session.add(PriceBar(
                symbol=sym, timeframe='1d', timestamp=ts,
                open=Decimal(str(bp + i * 0.5)),
                high=Decimal(str(bp + i * 0.5 + 2)),
                low=Decimal(str(bp + i * 0.5 - 1)),
                close=Decimal(str(bp + i * 0.5 + 1)),
                volume=2_000_000,
            ))
    db_session.commit()


# ── tests ─────────────────────────────────────────────────────────


class TestResearchRunnerBasic:
    """Basic ResearchRunner functionality."""

    def test_result_dataclass(self):
        """ResearchResult should be convertible to dict."""
        result = ResearchResult(
            run_id='test', symbols=['SPY'], rankings=[],
            hypothetical_weights={}, hypothetical_orders=[],
            factor_scores={}, composite_weights_used={},
            regime='trending', portfolio_value=100000,
            expected_vol=None, exposure=None,
            timestamp='2026-01-01T00:00:00', duration_ms=100,
        )
        d = result.to_dict()
        assert d['run_id'] == 'test'
        assert d['symbols'] == ['SPY']
        assert d['portfolio_value'] == 100000

    def test_run_with_mocked_factors(self, db_session, redis):
        """Run research with mocked factor engine."""
        _seed_universe(db_session)
        _seed_prices(db_session)

        mock_scores = {
            'SPY': {'trend_score': 0.8, 'volatility_regime': 0.7,
                     'sentiment_score': 0.6, 'breadth_score': 0.7,
                     'liquidity_score': 0.9},
            'QQQ': {'trend_score': 0.6, 'volatility_regime': 0.5,
                     'sentiment_score': 0.5, 'breadth_score': 0.6,
                     'liquidity_score': 0.8},
        }

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.return_value = mock_scores

            runner = ResearchRunner(
                redis_client=redis,
                db_session=db_session,
            )
            result = runner.run(
                symbols=['SPY', 'QQQ'],
                portfolio_value=100_000,
            )

        assert isinstance(result, ResearchResult)
        assert result.run_id
        assert len(result.rankings) == 2
        assert result.rankings[0]['symbol'] == 'SPY'  # higher score
        assert result.regime == 'trending'
        assert result.duration_ms > 0
        assert result.portfolio_value == 100_000

    def test_run_with_custom_weights(self, db_session, redis):
        """Custom weights override defaults."""
        _seed_universe(db_session)
        _seed_prices(db_session)

        mock_scores = {
            'SPY': {'trend_score': 0.3, 'liquidity_score': 0.9},
            'QQQ': {'trend_score': 0.9, 'liquidity_score': 0.3},
        }

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.return_value = mock_scores

            runner = ResearchRunner(redis_client=redis, db_session=db_session)

            # Weight all on trend → QQQ wins
            result = runner.run(
                symbols=['SPY', 'QQQ'],
                custom_weights={'trend_score': 1.0},
            )

        assert result.rankings[0]['symbol'] == 'QQQ'

    def test_run_with_regime_override(self, db_session, redis):
        """Explicit regime overrides auto-detection."""
        _seed_universe(db_session)
        _seed_prices(db_session)

        mock_scores = {
            'SPY': {'trend_score': 0.5, 'volatility_regime': 0.5},
        }

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.return_value = mock_scores

            runner = ResearchRunner(redis_client=redis, db_session=db_session)
            result = runner.run(
                symbols=['SPY'],
                regime='risk_off',
            )

        assert result.regime == 'risk_off'

    def test_caches_result_in_redis(self, db_session, redis):
        """Research result should be cached in Redis."""
        _seed_universe(db_session)
        _seed_prices(db_session)

        mock_scores = {'SPY': {'trend_score': 0.7}}

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.return_value = mock_scores

            runner = ResearchRunner(redis_client=redis, db_session=db_session)
            runner.run(symbols=['SPY'])

        cached = redis.get('cache:research:latest')
        assert cached is not None
        data = json.loads(cached)
        assert data['symbols'] == ['SPY']


class TestResearchRunnerEdgeCases:
    """Edge cases and error handling."""

    def test_factor_failure_returns_neutral(self, db_session, redis):
        """When factor computation fails, neutral scores still rank."""
        _seed_universe(db_session)
        _seed_prices(db_session)

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.side_effect = RuntimeError('boom')

            runner = ResearchRunner(redis_client=redis, db_session=db_session)
            result = runner.run(symbols=['SPY'])

        assert len(result.errors) > 0
        assert 'factor_compute' in result.errors[0]

    def test_no_redis(self, db_session):
        """Runner works without Redis (no caching)."""
        _seed_universe(db_session)
        _seed_prices(db_session)

        mock_scores = {'SPY': {'trend_score': 0.7}}

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.return_value = mock_scores

            runner = ResearchRunner(db_session=db_session)
            result = runner.run(symbols=['SPY'])

        assert result.regime in ('trending', 'consolidation', 'risk_off')

    def test_empty_symbols(self, db_session, redis):
        """Empty symbols list produces empty results."""
        mock_scores = {}

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.return_value = mock_scores

            runner = ResearchRunner(redis_client=redis, db_session=db_session)
            result = runner.run(symbols=[])

        assert result.rankings == []

    def test_regime_detection_from_factors(self, db_session):
        """Without Redis, regime is inferred from trend scores."""
        _seed_universe(db_session)
        _seed_prices(db_session)

        # High trend → trending
        mock_scores = {
            'SPY': {'trend_score': 0.8},
            'QQQ': {'trend_score': 0.9},
        }

        with patch('app.factors.factor_registry.FactorRegistry') as mock_fr:
            mock_fr.return_value.compute_all.return_value = mock_scores

            runner = ResearchRunner(db_session=db_session)
            result = runner.run(symbols=['SPY', 'QQQ'])

        assert result.regime == 'trending'
