import json
import time

import fakeredis
import pytest

from app.streaming.redis_bridge import CHANNEL_EVENT_MAP, RedisBridge


class MockSocketIO:
    """Simple mock for Flask-SocketIO emit tracking."""

    def __init__(self):
        self.emitted = []

    def emit(self, event, data, namespace=None):
        self.emitted.append({
            'event': event,
            'data': data,
            'namespace': namespace,
        })


class TestChannelEventMap:
    def test_all_channels_mapped(self):
        expected = [
            'channel:factor_scores',
            'channel:signals',
            'channel:portfolio',
            'channel:risk_alerts',
            'channel:trades',
            'channel:fills',
            'channel:regime_change',
            'channel:system_health',
            'channel:config_change',
        ]
        for ch in expected:
            assert ch in CHANNEL_EVENT_MAP

    def test_event_names(self):
        assert CHANNEL_EVENT_MAP['channel:factor_scores'] == 'factor_scores'
        assert CHANNEL_EVENT_MAP['channel:risk_alerts'] == 'risk_alert'
        assert CHANNEL_EVENT_MAP['channel:trades'] == 'trade'


class TestRedisBridge:
    def test_start_and_stop(self):
        redis = fakeredis.FakeRedis()
        sio = MockSocketIO()
        bridge = RedisBridge(redis, sio)

        bridge.start()
        assert bridge._thread is not None
        assert bridge._thread.is_alive()

        bridge.stop()
        time.sleep(0.1)

    def test_relay_message(self):
        redis = fakeredis.FakeRedis()
        sio = MockSocketIO()
        bridge = RedisBridge(redis, sio)
        bridge.start()

        # Give the bridge thread time to subscribe
        time.sleep(0.2)

        # Publish a message
        redis.publish('channel:trades', json.dumps({'symbol': 'SPY', 'side': 'buy'}))

        # Wait for relay
        time.sleep(0.5)
        bridge.stop()

        # Check that the message was relayed
        trade_events = [e for e in sio.emitted if e['event'] == 'trade']
        assert len(trade_events) >= 1
        assert trade_events[0]['data']['symbol'] == 'SPY'
        assert trade_events[0]['namespace'] == '/dashboard'

    def test_ignores_unknown_channels(self):
        redis = fakeredis.FakeRedis()
        sio = MockSocketIO()
        bridge = RedisBridge(redis, sio)
        bridge.start()
        time.sleep(0.2)

        redis.publish('channel:unknown', json.dumps({'foo': 'bar'}))
        time.sleep(0.3)
        bridge.stop()

        # Should not emit anything for unknown channels
        unknown_events = [e for e in sio.emitted if e['data'].get('foo') == 'bar']
        assert len(unknown_events) == 0

    def test_double_start_is_safe(self):
        redis = fakeredis.FakeRedis()
        sio = MockSocketIO()
        bridge = RedisBridge(redis, sio)
        bridge.start()
        bridge.start()  # should not create second thread
        bridge.stop()
