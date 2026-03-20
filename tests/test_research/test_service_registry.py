"""Tests for the service registry and tooltip metadata."""
import json

import fakeredis
import pytest

from app.services import SERVICE_REGISTRY, get_service_status


class TestServiceRegistry:
    def test_has_three_services(self):
        assert 'research' in SERVICE_REGISTRY
        assert 'simulation' in SERVICE_REGISTRY
        assert 'live' in SERVICE_REGISTRY

    def test_each_service_has_tooltip(self):
        for key, svc in SERVICE_REGISTRY.items():
            assert 'tooltip' in svc, f'{key} missing tooltip'
            assert len(svc['tooltip']) > 20, f'{key} tooltip too short'

    def test_each_service_has_required_fields(self):
        required = {'name', 'key', 'tooltip', 'description', 'icon',
                    'status_key', 'channel', 'independent', 'endpoints'}
        for key, svc in SERVICE_REGISTRY.items():
            for field in required:
                assert field in svc, f'{key} missing {field}'

    def test_research_and_simulation_are_independent(self):
        assert SERVICE_REGISTRY['research']['independent'] is True
        assert SERVICE_REGISTRY['simulation']['independent'] is True

    def test_live_is_not_independent(self):
        assert SERVICE_REGISTRY['live']['independent'] is False

    def test_research_does_not_require_broker(self):
        assert SERVICE_REGISTRY['research']['requires_broker'] is False

    def test_live_requires_broker(self):
        assert SERVICE_REGISTRY['live']['requires_broker'] is True


class TestServiceStatus:
    def test_status_with_no_redis(self):
        statuses = get_service_status(None)
        assert len(statuses) == 3
        for key, status in statuses.items():
            assert status['active'] is False

    def test_status_with_redis_data(self):
        redis = fakeredis.FakeRedis()
        redis.set('cache:research:latest', json.dumps({
            'timestamp': '2026-03-15T12:00:00',
        }))

        statuses = get_service_status(redis)
        assert statuses['research']['active'] is True
        assert statuses['research']['last_run'] == '2026-03-15T12:00:00'
        assert statuses['simulation']['active'] is False
