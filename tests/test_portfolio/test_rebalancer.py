import json

import fakeredis
import pytest

from app.portfolio.constraints import PortfolioConstraints
from app.portfolio.rebalancer import RebalanceEngine


class TestRebalanceEngine:
    @pytest.fixture
    def redis(self):
        return fakeredis.FakeRedis()

    @pytest.fixture
    def engine(self, redis):
        return RebalanceEngine(redis_client=redis), redis

    @pytest.fixture
    def constraints(self):
        return PortfolioConstraints()

    def test_no_changes_needed(self, engine, constraints):
        eng, _ = engine
        current = {'SPY': 0.20, 'QQQ': 0.20}
        target = {'SPY': 0.20, 'QQQ': 0.20}
        orders = eng.generate_orders(target, current, 100_000, constraints)
        assert orders == []

    def test_simple_buy(self, engine, constraints):
        eng, _ = engine
        current = {}
        target = {'SPY': 0.20}
        orders = eng.generate_orders(target, current, 100_000, constraints)
        assert len(orders) == 1
        assert orders[0]['symbol'] == 'SPY'
        assert orders[0]['side'] == 'buy'
        assert orders[0]['notional'] == 20_000.0

    def test_simple_sell(self, engine, constraints):
        eng, _ = engine
        current = {'SPY': 0.20}
        target = {}
        orders = eng.generate_orders(target, current, 100_000, constraints)
        assert len(orders) == 1
        assert orders[0]['side'] == 'sell'

    def test_sells_before_buys(self, engine, constraints):
        eng, _ = engine
        current = {'SPY': 0.20, 'XLE': 0.00}
        target = {'SPY': 0.00, 'XLE': 0.20}
        orders = eng.generate_orders(target, current, 100_000, constraints)
        assert len(orders) == 2
        assert orders[0]['side'] == 'sell'
        assert orders[1]['side'] == 'buy'

    def test_skip_micro_trades(self, engine, constraints):
        eng, _ = engine
        current = {'SPY': 0.200}
        target = {'SPY': 0.202}  # 0.2% delta < 0.5% threshold
        orders = eng.generate_orders(target, current, 100_000, constraints)
        assert orders == []

    def test_turnover_limit_scales_back(self, engine):
        eng, _ = engine
        constraints = PortfolioConstraints(turnover_limit_pct=0.10)
        # 100% turnover: sell everything, buy new
        current = {'SPY': 0.50}
        target = {'QQQ': 0.50}
        orders = eng.generate_orders(target, current, 100_000, constraints)
        # Should scale back to halfway
        for o in orders:
            if o['symbol'] == 'SPY':
                assert o['target_weight'] == pytest.approx(0.25, abs=0.001)
            if o['symbol'] == 'QQQ':
                assert o['target_weight'] == pytest.approx(0.25, abs=0.001)

    def test_notional_calculation(self, engine, constraints):
        eng, _ = engine
        current = {'SPY': 0.10}
        target = {'SPY': 0.20}
        orders = eng.generate_orders(target, current, 200_000, constraints)
        assert len(orders) == 1
        assert orders[0]['notional'] == pytest.approx(20_000.0, abs=1)

    def test_run_rebalance_cycle_pushes_to_redis(self, engine, constraints):
        eng, redis = engine
        target = {'SPY': 0.20, 'QQQ': 0.15}
        current = {}
        orders = eng.run_rebalance_cycle(
            target, current, 100_000, regime='trending', constraints=constraints,
        )
        assert len(orders) == 2

        # Check Redis queue
        msg = redis.rpop('channel:orders_proposed')
        assert msg is not None
        data = json.loads(msg)
        assert len(data['orders']) == 2
        assert data['regime'] == 'trending'

    def test_run_rebalance_caches_portfolio(self, engine, constraints):
        eng, redis = engine
        target = {'SPY': 0.20}
        eng.run_rebalance_cycle(target, {}, 100_000, constraints=constraints)
        cached = redis.get('cache:portfolio:current')
        assert cached is not None

    def test_no_orders_no_push(self, engine, constraints):
        eng, redis = engine
        current = {'SPY': 0.20}
        target = {'SPY': 0.20}
        orders = eng.run_rebalance_cycle(
            target, current, 100_000, constraints=constraints,
        )
        assert orders == []
        assert redis.llen('channel:orders_proposed') == 0

    def test_delta_weight_in_orders(self, engine, constraints):
        eng, _ = engine
        current = {'SPY': 0.10}
        target = {'SPY': 0.20}
        orders = eng.generate_orders(target, current, 100_000, constraints)
        assert orders[0]['delta_weight'] == pytest.approx(0.10, abs=0.001)
