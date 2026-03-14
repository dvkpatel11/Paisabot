import json

import fakeredis
import pytest

from app.risk.liquidity_monitor import LiquidityMonitor


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def monitor(redis):
    return LiquidityMonitor(redis_client=redis)


# ── single-symbol checks ───────────────────────────────────────────

class TestCheckSymbol:
    def test_no_shock(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        result = monitor.check_symbol('XLK', current_adv=400)
        assert not result['is_shocked']
        assert result['reason'] == 'ok'

    def test_shock_detected(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        result = monitor.check_symbol('XLK', current_adv=200)
        assert result['is_shocked']
        assert 'adv_drop' in result['reason']

    def test_shock_at_boundary(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        # 50% ratio → not shocked (boundary is <50%)
        result = monitor.check_symbol('XLK', current_adv=250)
        assert not result['is_shocked']

    def test_shock_just_below_boundary(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        result = monitor.check_symbol('XLK', current_adv=249)
        assert result['is_shocked']

    def test_no_current_adv_checks_cache(self, redis, monitor):
        # No shock flag set → ok
        result = monitor.check_symbol('XLK')
        assert not result['is_shocked']

    def test_cached_shock_flag(self, redis, monitor):
        redis.set('liquidity_shock:XLK', '1')
        result = monitor.check_symbol('XLK')
        assert result['is_shocked']

    def test_no_historical_adv(self, monitor):
        result = monitor.check_symbol('XLK', current_adv=100)
        assert not result['is_shocked']
        assert result['reason'] == 'no_historical_adv'


# ── shock flag persistence ──────────────────────────────────────────

class TestShockFlag:
    def test_shock_sets_flag(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        monitor.check_symbol('XLK', current_adv=100)
        assert redis.get('liquidity_shock:XLK') == b'1'

    def test_shock_flag_has_ttl(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        monitor.check_symbol('XLK', current_adv=100)
        ttl = redis.ttl('liquidity_shock:XLK')
        assert 0 < ttl <= 86400

    def test_is_shocked_helper(self, redis, monitor):
        assert not monitor.is_shocked('XLK')
        redis.set('liquidity_shock:XLK', '1')
        assert monitor.is_shocked('XLK')


# ── universe scan ───────────────────────────────────────────────────

class TestScanUniverse:
    def test_scan_mixed_universe(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        redis.set('etf:XLE:adv_30d_m', '300')
        redis.set('etf:XLV:adv_30d_m', '400')
        advs = {'XLK': 400, 'XLE': 100, 'XLV': 350}
        result = monitor.scan_universe(['XLK', 'XLE', 'XLV'], advs)
        assert len(result['shocked']) == 1  # XLE shocked
        assert result['shocked'][0]['symbol'] == 'XLE'
        assert len(result['ok']) == 2
        assert result['total'] == 3

    def test_scan_no_advs_provided(self, monitor):
        result = monitor.scan_universe(['XLK', 'XLE'])
        assert len(result['shocked']) == 0


# ── alert publishing ────────────────────────────────────────────────

class TestLiquidityAlerts:
    def test_shock_publishes_alert(self, redis, monitor):
        redis.set('etf:XLK:adv_30d_m', '500')
        monitor.check_symbol('XLK', current_adv=100)
        alert_raw = redis.rpop('channel:risk_alerts')
        assert alert_raw is not None
        alert = json.loads(alert_raw)
        assert alert['type'] == 'liquidity_shock'
        assert alert['symbol'] == 'XLK'
