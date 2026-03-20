import fakeredis
import pytest

from app.signals.signal_filter import SignalFilter


class TestSignalFilter:
    @pytest.fixture
    def redis(self):
        return fakeredis.FakeRedis()

    @pytest.fixture
    def filter(self, redis):
        return SignalFilter(redis_client=redis), redis

    def test_tradable_no_issues(self, filter):
        f, redis = filter
        redis.set('scores:SPY', '{}', ex=900)
        ok, reason = f.is_tradable('SPY', adv_m=50.0, spread_bps=2.0)
        assert ok is True
        assert reason == 'ok'

    def test_kill_switch_trading(self, filter):
        f, redis = filter
        redis.set('kill_switch:trading', '1')
        ok, reason = f.is_tradable('SPY')
        assert ok is False
        assert reason == 'kill_switch_active'

    def test_kill_switch_all(self, filter):
        f, redis = filter
        redis.set('kill_switch:all', '1')
        ok, reason = f.is_tradable('SPY')
        assert ok is False
        assert reason == 'kill_switch_all'

    def test_adv_below_threshold(self, filter):
        f, _ = filter
        ok, reason = f.is_tradable('TINY', adv_m=5.0, spread_bps=2.0)
        assert ok is False
        assert 'adv_below_threshold' in reason

    def test_spread_too_wide(self, filter):
        f, _ = filter
        ok, reason = f.is_tradable('WIDE', adv_m=50.0, spread_bps=15.0)
        assert ok is False
        assert 'spread_too_wide' in reason

    def test_maintenance_mode(self, filter):
        f, redis = filter
        redis.set('kill_switch:maintenance', '1')
        ok, reason = f.is_tradable('SPY', adv_m=50.0, spread_bps=2.0)
        assert ok is False
        assert reason == 'maintenance_mode'

    def test_liquidity_shock(self, filter):
        f, redis = filter
        redis.set('liquidity_shock:SPY', '1')
        ok, reason = f.is_tradable('SPY', adv_m=50.0, spread_bps=2.0)
        assert ok is False
        assert reason == 'liquidity_shock'

    def test_factor_staleness(self, filter):
        """No scores key means stale data."""
        f, _ = filter
        # No scores:SPY key exists → stale
        ok, reason = f.is_tradable('SPY', adv_m=50.0, spread_bps=2.0)
        assert ok is False
        assert reason == 'factor_data_stale'

    def test_factor_not_stale_when_key_exists(self, filter):
        f, redis = filter
        redis.set('scores:SPY', '{}', ex=900)
        ok, reason = f.is_tradable('SPY', adv_m=50.0, spread_bps=2.0)
        assert ok is True

    def test_kill_switch_priority(self, filter):
        """Kill switches checked before ADV/spread."""
        f, redis = filter
        redis.set('kill_switch:trading', '1')
        ok, reason = f.is_tradable('SPY', adv_m=5.0, spread_bps=50.0)
        assert reason == 'kill_switch_active'

    def test_no_redis_blocks_on_staleness(self):
        """Without Redis, factor staleness fails safe (blocks signals)."""
        f = SignalFilter(redis_client=None)
        ok, reason = f.is_tradable('SPY', adv_m=50.0, spread_bps=2.0)
        # No Redis → can't verify factor freshness → fail-safe blocks
        assert ok is False
        assert reason == 'factor_data_stale'

    def test_custom_adv_threshold_from_redis(self, filter):
        f, redis = filter
        redis.set('scores:SPY', '{}', ex=900)
        redis.hset('config:universe', 'min_avg_daily_vol_m', '10.0')
        # 15M > custom 10M threshold → passes
        ok, _ = f.is_tradable('SPY', adv_m=15.0, spread_bps=2.0)
        assert ok is True

    def test_none_adv_skips_check(self, filter):
        """If ADV not provided, gate is skipped."""
        f, redis = filter
        redis.set('scores:SPY', '{}', ex=900)
        ok, _ = f.is_tradable('SPY', adv_m=None, spread_bps=2.0)
        assert ok is True
