"""Tests for PipelineOrchestrator — end-to-end pipeline chain."""
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import fakeredis
import pytest

from app.models.etf_universe import ETFUniverse
from app.models.factor_scores import FactorScore
from app.models.price_bars import PriceBar
from app.models.signals import Signal
from app.pipeline.orchestrator import PipelineOrchestrator


def _seed_universe(db_session):
    """Seed 3 ETFs for testing."""
    for sym, sector in [('SPY', 'Broad'), ('QQQ', 'Tech'), ('XLE', 'Energy')]:
        etf = ETFUniverse(
            symbol=sym, name=sym, sector=sector,
            is_active=True,
        )
        db_session.add(etf)
    db_session.commit()


def _seed_price_bars(db_session, symbols, days=25):
    """Create synthetic price bars for testing."""
    from datetime import timedelta
    base_date = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for sym in symbols:
        base_price = {'SPY': 500, 'QQQ': 400, 'XLE': 80}.get(sym, 100)
        for i in range(days):
            ts = base_date + timedelta(days=i)
            bar = PriceBar(
                symbol=sym, timeframe='1d', timestamp=ts,
                open=Decimal(str(base_price + i * 0.5)),
                high=Decimal(str(base_price + i * 0.5 + 2)),
                low=Decimal(str(base_price + i * 0.5 - 1)),
                close=Decimal(str(base_price + i * 0.5 + 1)),
                volume=1_000_000,
            )
            db_session.add(bar)
    db_session.commit()


def _seed_signals(redis_mock):
    """Seed signals in Redis cache."""
    signals = {
        'long': [
            {'symbol': 'SPY', 'composite_score': 0.75, 'signal_type': 'long', 'regime': 'trending', 'regime_confidence': 0.7},
            {'symbol': 'QQQ', 'composite_score': 0.70, 'signal_type': 'long', 'regime': 'trending', 'regime_confidence': 0.7},
        ],
        'neutral': [],
        'avoid': [
            {'symbol': 'XLE', 'composite_score': 0.35, 'signal_type': 'avoid', 'regime': 'trending', 'regime_confidence': 0.7},
        ],
    }
    redis_mock.set('cache:signals:latest', json.dumps(signals))
    redis_mock.set('cache:regime:current', json.dumps({'regime': 'trending', 'confidence': 0.7}))


class TestPipelineOrchestrator:
    def test_no_signals_returns_early(self, db_session, redis_mock):
        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        result = orchestrator.run()

        assert result['status'] == 'stopped'
        assert result['reason'] == 'no_signals'

    def test_no_price_data_returns_early(self, db_session, redis_mock):
        _seed_universe(db_session)
        _seed_signals(redis_mock)

        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        result = orchestrator.run()

        assert result['status'] == 'stopped'
        assert result['reason'] == 'no_price_data'

    def test_full_pipeline_runs_with_data(self, db_session, redis_mock):
        _seed_universe(db_session)
        _seed_price_bars(db_session, ['SPY', 'QQQ', 'XLE'])
        _seed_signals(redis_mock)

        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        result = orchestrator.run()

        # Should at least reach the portfolio stage
        assert 'n_signals' in result or 'stage' in result

    def test_load_signals_from_db_fallback(self, db_session, redis_mock):
        """Signals can be loaded from DB when Redis cache is empty."""
        _seed_universe(db_session)

        now = datetime.now(timezone.utc)
        for sym, score, sig_type in [('SPY', 0.75, 'long'), ('QQQ', 0.70, 'long')]:
            sig = Signal(
                symbol=sym, signal_time=now,
                composite_score=Decimal(str(score)),
                signal_type=sig_type, regime='trending',
                regime_confidence=Decimal('0.7'),
            )
            db_session.add(sig)
        db_session.commit()

        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        signals = orchestrator._load_signals()
        assert 'SPY' in signals
        assert 'QQQ' in signals

    def test_build_sector_map(self, db_session, redis_mock):
        _seed_universe(db_session)

        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        sector_map = orchestrator._build_sector_map()

        assert sector_map['SPY'] == 'Broad'
        assert sector_map['QQQ'] == 'Tech'

    def test_get_regime_from_cache(self, db_session, redis_mock):
        redis_mock.set('cache:regime:current', json.dumps({'regime': 'risk_off'}))

        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        assert orchestrator._get_regime({}) == 'risk_off'

    def test_get_regime_fallback_to_signals(self, db_session, redis_mock):
        signals = {'SPY': {'regime': 'rotation'}}

        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        assert orchestrator._get_regime(signals) == 'rotation'

    def test_publishes_summary_to_redis(self, db_session, redis_mock):
        orchestrator = PipelineOrchestrator(redis_mock, db_session)
        summary = {'n_signals': 3, 'n_filled': 2, 'timestamp': 'now'}
        orchestrator._publish_summary(summary)

        cached = json.loads(redis_mock.get('cache:pipeline:latest'))
        assert cached['n_signals'] == 3
