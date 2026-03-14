import json

import fakeredis
import pytest

from app.streaming.publishers import (
    publish_config_change,
    publish_event,
    publish_factor_scores,
    publish_portfolio,
    publish_regime_change,
    publish_risk_alert,
    publish_signals,
    publish_system_health,
    publish_trade,
)


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


class TestPublishEvent:
    def test_publish_to_channel(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('test:channel')
        pubsub.get_message()  # subscription confirmation

        publish_event(redis, 'test:channel', {'key': 'value'})

        msg = pubsub.get_message()
        assert msg is not None
        assert json.loads(msg['data']) == {'key': 'value'}

    def test_none_redis_does_not_raise(self):
        publish_event(None, 'test:channel', {'key': 'value'})


class TestSpecificPublishers:
    def test_publish_factor_scores(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:factor_scores')
        pubsub.get_message()

        publish_factor_scores(redis, {'SPY': {'trend': 0.8}})
        msg = pubsub.get_message()
        assert msg is not None

    def test_publish_signals(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:signals')
        pubsub.get_message()

        publish_signals(redis, {'long': ['SPY']})
        msg = pubsub.get_message()
        assert msg is not None

    def test_publish_portfolio(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:portfolio')
        pubsub.get_message()

        publish_portfolio(redis, {'weights': {'SPY': 0.3}})
        msg = pubsub.get_message()
        assert msg is not None

    def test_publish_risk_alert(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:risk_alerts')
        pubsub.get_message()

        publish_risk_alert(redis, {'type': 'drawdown', 'value': -0.10})
        msg = pubsub.get_message()
        assert msg is not None

    def test_publish_trade(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:trades')
        pubsub.get_message()

        publish_trade(redis, {'symbol': 'SPY', 'side': 'buy'})
        msg = pubsub.get_message()
        assert msg is not None

    def test_publish_regime_change(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:regime_change')
        pubsub.get_message()

        publish_regime_change(redis, {'from': 'trending', 'to': 'risk_off'})
        msg = pubsub.get_message()
        assert msg is not None

    def test_publish_system_health(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:system_health')
        pubsub.get_message()

        publish_system_health(redis, {'status': 'ok'})
        msg = pubsub.get_message()
        assert msg is not None

    def test_publish_config_change(self, redis):
        pubsub = redis.pubsub()
        pubsub.subscribe('channel:config_change')
        pubsub.get_message()

        publish_config_change(redis, {'category': 'weights', 'key': 'trend'})
        msg = pubsub.get_message()
        assert msg is not None
