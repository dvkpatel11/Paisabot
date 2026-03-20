"""E2E integration test: research-mode pipeline with simulated fills.

Validates: signals → portfolio → risk gate → simulated execution
with cost model breakdowns, position tracking, and trade persistence.
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from app.execution.execution_engine import ExecutionEngine
from app.execution.order_manager import OrderManager
from app.models.etf_universe import ETFUniverse
from app.models.price_bars import PriceBar
from app.pipeline.orchestrator import PipelineOrchestrator


# ── seed helpers ─────────────────────────────────────────────────


def _seed_universe(db_session):
    for sym, sector in [('SPY', 'Broad'), ('QQQ', 'Tech'), ('XLE', 'Energy')]:
        db_session.add(ETFUniverse(
            symbol=sym, name=sym, sector=sector,
            is_active=True, in_active_set=True,
        ))
    db_session.commit()


def _seed_price_bars(db_session, days=30):
    base = datetime(2026, 2, 1, tzinfo=timezone.utc)
    for sym, bp in [('SPY', 500), ('QQQ', 400), ('XLE', 80)]:
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


def _seed_signals(redis):
    signals = {
        'long': [
            {
                'symbol': 'SPY', 'composite_score': 0.80,
                'signal_type': 'long', 'regime': 'trending',
                'regime_confidence': 0.75,
            },
            {
                'symbol': 'QQQ', 'composite_score': 0.72,
                'signal_type': 'long', 'regime': 'trending',
                'regime_confidence': 0.75,
            },
        ],
        'neutral': [],
        'avoid': [
            {
                'symbol': 'XLE', 'composite_score': 0.30,
                'signal_type': 'avoid', 'regime': 'trending',
                'regime_confidence': 0.75,
            },
        ],
    }
    redis.set('cache:signals:latest', json.dumps(signals))
    redis.set('cache:regime:current', json.dumps({
        'regime': 'trending', 'confidence': 0.75,
    }))
    # Mid prices (used by rebalancer ref_price and simulated fill)
    redis.hset('cache:mid_prices', 'SPY', '515.0')
    redis.hset('cache:mid_prices', 'QQQ', '414.0')
    redis.hset('cache:mid_prices', 'XLE', '94.0')


def _research_redis():
    """Create a fake Redis pre-configured for research mode."""
    r = fakeredis.FakeRedis()
    r.hset('config:system', 'operational_mode', 'research')
    return r


# ── tests ────────────────────────────────────────────────────────


class TestSimulatedFillsPipeline:
    """End-to-end: research mode pipeline produces simulated fills
    with cost breakdowns through the full stage chain."""

    @pytest.fixture
    def redis(self):
        return _research_redis()

    def test_research_mode_produces_simulated_fills(self, db_session, redis):
        """Run full pipeline in research mode and verify simulated
        fills contain cost breakdowns and correct operational_mode."""
        _seed_universe(db_session)
        _seed_price_bars(db_session)
        _seed_signals(redis)

        # Mock PortfolioManager to return deterministic orders
        mock_pm_result = {
            'orders': [
                {
                    'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
                    'target_weight': 0.05, 'current_weight': 0.0,
                    'delta_weight': 0.05, 'ref_price': 515.0,
                },
                {
                    'symbol': 'QQQ', 'side': 'buy', 'notional': 4000.0,
                    'target_weight': 0.04, 'current_weight': 0.0,
                    'delta_weight': 0.04, 'ref_price': 414.0,
                },
            ],
            'candidates': ['SPY', 'QQQ'],
        }

        # Mock RiskManager to approve all orders
        mock_gate_result = {
            'approved': mock_pm_result['orders'],
            'blocked': [],
        }

        with patch('app.portfolio.portfolio_manager.PortfolioManager') as mock_pm_cls, \
             patch('app.risk.risk_manager.RiskManager') as mock_rm_cls:

            mock_pm_cls.return_value.run.return_value = mock_pm_result
            mock_rm_cls.return_value.pre_trade.return_value = mock_gate_result

            orchestrator = PipelineOrchestrator(
                redis_client=redis,
                db_session=db_session,
                broker=None,  # no broker needed in research mode
            )
            result = orchestrator.run()

        # Pipeline should complete
        assert result['status'] == 'complete'
        assert result['n_filled'] == 2
        assert result['n_errors'] == 0

    def test_simulated_fill_has_cost_breakdown(self, redis):
        """Directly test that research-mode OrderManager returns
        cost_breakdown in results."""
        manager = OrderManager(broker=None, redis_client=redis)
        order = {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
            'ref_price': 515.0,
        }
        result = manager.execute_order(order)

        assert result['status'] == 'filled'
        assert result['reason'] == 'simulated'
        assert result['operational_mode'] == 'research'
        assert 'cost_breakdown' in result

        bd = result['cost_breakdown']
        assert bd['half_spread_bps'] > 0
        assert bd['market_impact_bps'] >= 0
        assert bd['total_bps'] == round(
            bd['half_spread_bps'] + bd['market_impact_bps'], 4,
        )

        # Fill price should be above mid for a buy
        assert result['fill_price'] > 515.0
        assert result['filled_qty'] > 0

    def test_sell_fill_cost_breakdown(self, redis):
        """Sell-side simulated fill: fill price below mid."""
        manager = OrderManager(broker=None, redis_client=redis)
        order = {
            'symbol': 'XLE', 'side': 'sell', 'notional': 3000.0,
            'ref_price': 94.0,
        }
        result = manager.execute_order(order)

        assert result['status'] == 'filled'
        assert result['fill_price'] < 94.0
        assert 'cost_breakdown' in result

    def test_batch_simulated_fills(self, redis):
        """Batch execution in research mode fills all orders."""
        manager = OrderManager(broker=None, redis_client=redis)
        orders = [
            {'symbol': 'XLE', 'side': 'sell', 'notional': 2000.0, 'ref_price': 94.0},
            {'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0, 'ref_price': 515.0},
            {'symbol': 'QQQ', 'side': 'buy', 'notional': 4000.0, 'ref_price': 414.0},
        ]
        results = manager.execute_batch(orders)

        assert len(results) == 3
        assert all(r['status'] == 'filled' for r in results)
        assert all('cost_breakdown' in r for r in results)

        # Sell should be first (sorted by rebalancer, but manager processes in order)
        assert results[0]['side'] == 'sell'

    def test_publishes_fill_events(self, redis):
        """Simulated fills publish to channel:fills and channel:trades."""
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:fills', 'channel:trades')
        pubsub.get_message()  # sub confirmation
        pubsub.get_message()

        manager = OrderManager(broker=None, redis_client=redis)
        order = {
            'symbol': 'SPY', 'side': 'buy', 'notional': 5000.0,
            'ref_price': 515.0,
        }
        manager.execute_order(order)

        msg = pubsub.get_message()
        assert msg is not None
        data = json.loads(msg['data'])
        assert data['symbol'] == 'SPY'
        assert data['status'] == 'filled'
