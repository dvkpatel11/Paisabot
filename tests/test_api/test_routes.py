"""Tests for API routes.

Uses the Flask test client with SQLite in-memory DB.
"""
import json

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


# ── health ─────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get('/api/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert 'components' in data
        assert 'kill_switches' in data
        assert 'timestamp' in data

    def test_health_has_redis_status(self, client):
        resp = client.get('/api/health')
        data = resp.get_json()
        assert 'redis' in data['components']

    def test_health_has_database_status(self, client):
        resp = client.get('/api/health')
        data = resp.get_json()
        assert 'database' in data['components']


# ── scores ─────────────────────────────────────────────────────────

class TestScoresEndpoint:
    def test_scores_empty(self, client):
        resp = client.get('/api/scores')
        assert resp.status_code == 200

    def test_scores_preview_invalid_json(self, client):
        resp = client.get('/api/scores?preview_weights=not_json')
        assert resp.status_code == 400


# ── signals ────────────────────────────────────────────────────────

class TestSignalsEndpoint:
    def test_signals_returns_groups(self, client):
        resp = client.get('/api/signals')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'long' in data
        assert 'neutral' in data
        assert 'avoid' in data


# ── regime ─────────────────────────────────────────────────────────

class TestRegimeEndpoint:
    def test_regime_returns_data(self, client):
        resp = client.get('/api/regime')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'history' in data


# ── portfolio ──────────────────────────────────────────────────────

class TestPortfolioEndpoint:
    def test_portfolio_returns_positions(self, client):
        resp = client.get('/api/portfolio')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'positions' in data


# ── risk ───────────────────────────────────────────────────────────

class TestRiskEndpoint:
    def test_risk_returns_kill_switches(self, client):
        resp = client.get('/api/risk')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'kill_switches' in data


# ── trades ─────────────────────────────────────────────────────────

class TestTradesEndpoint:
    def test_trades_empty(self, client):
        resp = client.get('/api/trades')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_trades_limit(self, client):
        resp = client.get('/api/trades?limit=5')
        assert resp.status_code == 200

    def test_trades_symbol_filter(self, client):
        resp = client.get('/api/trades?symbol=SPY')
        assert resp.status_code == 200


# ── factors ────────────────────────────────────────────────────────

class TestFactorsEndpoint:
    def test_factors_returns_structure(self, client):
        resp = client.get('/api/factors/SPY')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['symbol'] == 'SPY'
        assert 'factors' in data
        assert 'dates' in data['factors']
        assert 'trend' in data['factors']


# ── config ─────────────────────────────────────────────────────────

class TestConfigEndpoints:
    def test_get_all_config(self, client):
        resp = client.get('/api/config')
        assert resp.status_code == 200

    def test_get_category(self, client):
        resp = client.get('/api/config/system')
        assert resp.status_code == 200

    def test_patch_config(self, client, app):
        with app.app_context():
            resp = client.patch(
                '/api/config/test_cat',
                data=json.dumps({'test_key': 'test_val'}),
                content_type='application/json',
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['updated'] == 1

    def test_patch_empty_body(self, client):
        resp = client.patch(
            '/api/config/test_cat',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_get_audit(self, client):
        resp = client.get('/api/config/audit')
        assert resp.status_code == 200


# ── config/weights ─────────────────────────────────────────────────

class TestWeightsEndpoint:
    def test_weights_sum_validation(self, client):
        resp = client.patch(
            '/api/config/weights',
            data=json.dumps({'trend': 0.5, 'volatility': 0.3}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'sum to 1.0' in resp.get_json()['error']

    def test_weights_valid(self, client, app):
        with app.app_context():
            resp = client.patch(
                '/api/config/weights',
                data=json.dumps({'trend': 0.5, 'volatility': 0.5}),
                content_type='application/json',
            )
            assert resp.status_code == 200


# ── config/mode ────────────────────────────────────────────────────

class TestModeEndpoint:
    def test_invalid_mode(self, client):
        resp = client.patch(
            '/api/config/mode',
            data=json.dumps({'mode': 'invalid'}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_set_simulation(self, client, app):
        with app.app_context():
            resp = client.patch(
                '/api/config/mode',
                data=json.dumps({'mode': 'simulation'}),
                content_type='application/json',
            )
            assert resp.status_code == 200
            assert resp.get_json()['mode'] == 'simulation'

    def test_live_requires_confirmation(self, client, app):
        with app.app_context():
            # Set to simulation first
            client.patch(
                '/api/config/mode',
                data=json.dumps({'mode': 'simulation'}),
                content_type='application/json',
            )
            # Try live without confirmation
            resp = client.patch(
                '/api/config/mode',
                data=json.dumps({'mode': 'live'}),
                content_type='application/json',
            )
            assert resp.status_code == 400
            assert 'confirm' in resp.get_json()['error']


# ── control ────────────────────────────────────────────────────────

class TestControlEndpoints:
    def test_toggle_kill_switch(self, client):
        resp = client.patch(
            '/api/control/trading',
            data=json.dumps({'active': True}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.get_json()['active'] is True

    def test_invalid_switch(self, client):
        resp = client.patch(
            '/api/control/invalid_switch',
            data=json.dumps({'active': True}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_force_liquidate_requires_confirm(self, client):
        resp = client.post(
            '/api/control/force_liquidate',
            data=json.dumps({'confirm': 'wrong'}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_force_liquidate_valid(self, client):
        resp = client.post(
            '/api/control/force_liquidate',
            data=json.dumps({'confirm': 'LIQUIDATE_ALL'}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'liquidation_triggered'


# ── universe ───────────────────────────────────────────────────────

class TestUniverseEndpoint:
    def test_universe_returns_list(self, client):
        resp = client.get('/api/universe')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)


# ── backtest ───────────────────────────────────────────────────────

class TestBacktestEndpoint:
    def test_backtest_results(self, client):
        resp = client.get('/api/backtest/results')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'dates' in data
        assert 'portfolio_value' in data


# ── pipelines ─────────────────────────────────────────────────────

class TestPipelineStatusEndpoint:
    def test_pipeline_status_returns_modules(self, client):
        resp = client.get('/api/pipelines/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'modules' in data
        assert len(data['modules']) == 7

    def test_pipeline_status_module_structure(self, client):
        resp = client.get('/api/pipelines/status')
        data = resp.get_json()
        mod = data['modules'][0]
        assert 'id' in mod
        assert 'name' in mod
        assert 'index' in mod
        assert 'status' in mod
        assert 'items_processed' in mod

    def test_pipeline_status_has_kill_switches(self, client):
        resp = client.get('/api/pipelines/status')
        data = resp.get_json()
        assert 'kill_switches' in data
        assert 'trading' in data['kill_switches']

    def test_pipeline_status_has_operational_mode(self, client):
        resp = client.get('/api/pipelines/status')
        data = resp.get_json()
        assert 'operational_mode' in data

    def test_pipeline_status_has_timestamp(self, client):
        resp = client.get('/api/pipelines/status')
        data = resp.get_json()
        assert 'timestamp' in data

    def test_pipeline_module_ids(self, client):
        resp = client.get('/api/pipelines/status')
        data = resp.get_json()
        ids = [m['id'] for m in data['modules']]
        assert 'market_data' in ids
        assert 'factor_engine' in ids
        assert 'signal_engine' in ids
        assert 'portfolio_engine' in ids
        assert 'risk_engine' in ids
        assert 'execution_engine' in ids
        assert 'dashboard' in ids

    def test_pipeline_cached_status(self, client, app):
        """Test that cached pipeline status is returned."""
        with app.app_context():
            from app.extensions import redis_client
            redis_client.setex(
                'cache:pipeline:factor_engine',
                300,
                json.dumps({
                    'status': 'ok',
                    'items_processed': 42,
                    'compute_time_ms': 1200,
                    'last_activity': '2026-03-14T12:00:00+00:00',
                }),
            )

            resp = client.get('/api/pipelines/status')
            data = resp.get_json()
            factor_mod = next(m for m in data['modules'] if m['id'] == 'factor_engine')
            assert factor_mod['items_processed'] == 42
            assert factor_mod['compute_time_ms'] == 1200

    def test_pipeline_queue_depth(self, client, app):
        """Test queue depth for list-based channels."""
        with app.app_context():
            from app.extensions import redis_client
            # Clear first, then push known items
            redis_client.delete('channel:orders_proposed')
            redis_client.lpush('channel:orders_proposed', 'order1', 'order2', 'order3')

            resp = client.get('/api/pipelines/status')
            data = resp.get_json()
            portfolio_mod = next(m for m in data['modules'] if m['id'] == 'portfolio_engine')
            assert portfolio_mod['queue_depth'] == 3
