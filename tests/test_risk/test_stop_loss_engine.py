import json

import fakeredis
import pytest

from app.risk.stop_loss_engine import StopLossEngine


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def engine(redis):
    return StopLossEngine(redis_client=redis)


# ── single-position checks ─────────────────────────────────────────

class TestCheckPosition:
    def test_position_ok(self, engine):
        result = engine.check_position('XLK', 100.0, 101.0, 102.0)
        assert result['action'] == 'ok'

    def test_hard_stop_triggered(self, engine):
        # -6% from entry → hard stop (-5% threshold)
        result = engine.check_position('XLK', 100.0, 94.0, 105.0)
        assert result['action'] == 'exit'
        assert 'hard_stop' in result['reason']

    def test_hard_stop_exact_boundary(self, engine):
        # exactly -5% from entry, HWM = entry so trailing doesn't fire
        result = engine.check_position('XLK', 100.0, 95.0, 100.0)
        assert result['action'] == 'reduce'  # -5.0% not < -0.05, but < -0.03 soft warn

    def test_hard_stop_just_below(self, engine):
        result = engine.check_position('XLK', 100.0, 94.99, 105.0)
        assert result['action'] == 'exit'

    def test_trailing_stop_triggered(self, engine):
        # +10% from entry, -9% from HWM → trailing stop
        result = engine.check_position('XLK', 100.0, 100.1, 110.0)
        assert result['action'] == 'exit'
        assert 'trailing_stop' in result['reason']

    def test_trailing_stop_from_high_watermark(self, engine):
        # HWM=120, current=109 → -9.17% from HWM
        result = engine.check_position('XLK', 100.0, 109.0, 120.0)
        assert result['action'] == 'exit'
        assert 'trailing_stop' in result['reason']

    def test_soft_warning_reduce(self, engine):
        # -3.5% from entry → soft warning
        result = engine.check_position('XLK', 100.0, 96.5, 100.0)
        assert result['action'] == 'reduce'
        assert 'soft_warn' in result['reason']

    def test_soft_warning_not_triggered_small_loss(self, engine):
        # -2% from entry → ok
        result = engine.check_position('XLK', 100.0, 98.0, 100.0)
        assert result['action'] == 'ok'

    def test_invalid_entry_price(self, engine):
        result = engine.check_position('XLK', 0.0, 100.0, 100.0)
        assert result['action'] == 'ok'

    def test_hard_stop_takes_priority_over_trailing(self, engine):
        # Both triggers active: -6% from entry AND -9% from HWM
        result = engine.check_position('XLK', 100.0, 94.0, 103.0)
        assert result['action'] == 'exit'
        assert 'hard_stop' in result['reason']


# ── scan all positions ──────────────────────────────────────────────

class TestScanAllPositions:
    def test_scan_multiple_positions(self, engine):
        positions = [
            {'symbol': 'XLK', 'entry_price': 100, 'high_watermark': 105, 'status': 'open'},
            {'symbol': 'XLE', 'entry_price': 50, 'high_watermark': 52, 'status': 'open'},
            {'symbol': 'XLF', 'entry_price': 40, 'high_watermark': 45, 'status': 'open'},
        ]
        prices = {'XLK': 93, 'XLE': 51, 'XLF': 38}  # XLK hard stop, XLF hard stop
        result = engine.scan_all_positions(positions, prices)
        assert len(result['exits']) == 2
        exit_symbols = {e['symbol'] for e in result['exits']}
        assert 'XLK' in exit_symbols
        assert 'XLF' in exit_symbols
        assert result['ok_count'] == 1

    def test_scan_skips_closed_positions(self, engine):
        positions = [
            {'symbol': 'XLK', 'entry_price': 100, 'high_watermark': 105, 'status': 'closed'},
        ]
        result = engine.scan_all_positions(positions, {'XLK': 50})
        assert len(result['exits']) == 0
        assert result['ok_count'] == 0

    def test_scan_skips_missing_prices(self, engine):
        positions = [
            {'symbol': 'XLK', 'entry_price': 100, 'high_watermark': 105, 'status': 'open'},
        ]
        result = engine.scan_all_positions(positions, {})
        assert len(result['exits']) == 0

    def test_scan_includes_reductions(self, engine):
        positions = [
            {'symbol': 'XLK', 'entry_price': 100, 'high_watermark': 100, 'status': 'open'},
        ]
        prices = {'XLK': 96.5}  # -3.5% → soft warning
        result = engine.scan_all_positions(positions, prices)
        assert len(result['reductions']) == 1
        assert result['reductions'][0]['reduce_pct'] == 0.50

    def test_scan_publishes_exit_alerts(self, redis, engine):
        positions = [
            {'symbol': 'XLK', 'entry_price': 100, 'high_watermark': 105, 'status': 'open'},
        ]
        engine.scan_all_positions(positions, {'XLK': 93})
        alert_raw = redis.rpop('channel:risk_alerts')
        assert alert_raw is not None
        alert = json.loads(alert_raw)
        assert alert['type'] == 'stop_loss_exit'


# ── custom thresholds ───────────────────────────────────────────────

class TestStopLossConfig:
    def test_custom_hard_stop(self, redis):
        redis.hset('config:risk', 'position_stop_loss', '-0.03')
        eng = StopLossEngine(redis_client=redis)
        result = eng.check_position('XLK', 100.0, 96.5, 100.0)
        assert result['action'] == 'exit'

    def test_custom_trailing_stop(self, redis):
        redis.hset('config:risk', 'position_trailing_stop', '-0.05')
        eng = StopLossEngine(redis_client=redis)
        # -6% from HWM with custom -5% trailing
        result = eng.check_position('XLK', 100.0, 99.0, 105.3)
        assert result['action'] == 'exit'
