"""Tests for view routes — template rendering."""
import pytest


@pytest.fixture
def client(app):
    return app.test_client()


class TestViewRoutes:
    def test_index_redirects_to_dashboard(self, client):
        resp = client.get('/')
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']

    def test_dashboard_renders(self, client):
        resp = client.get('/dashboard')
        assert resp.status_code == 200
        assert b'Dashboard' in resp.data

    def test_factors_renders(self, client):
        resp = client.get('/factors')
        assert resp.status_code == 200

    def test_rotation_renders(self, client):
        resp = client.get('/rotation')
        assert resp.status_code == 200

    def test_execution_renders(self, client):
        resp = client.get('/execution')
        assert resp.status_code == 200

    def test_analytics_renders(self, client):
        resp = client.get('/analytics')
        assert resp.status_code == 200

    def test_alerts_renders(self, client):
        resp = client.get('/alerts')
        assert resp.status_code == 200

    def test_pipelines_renders(self, client):
        resp = client.get('/pipelines')
        assert resp.status_code == 200
        assert b'Pipelines' in resp.data

    def test_config_renders(self, client):
        resp = client.get('/config')
        assert resp.status_code == 200

    def test_pipelines_has_nav_link(self, client):
        """Pipelines link appears in navigation."""
        resp = client.get('/pipelines')
        assert b'Pipelines' in resp.data
