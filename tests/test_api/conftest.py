"""API test fixtures — ensure DB tables exist for all API tests."""
import os

import pytest

from app.extensions import db as _db


@pytest.fixture(autouse=True)
def _setup_db(app):
    """Create all tables before each API test, drop after."""
    with app.app_context():
        _db.create_all()
        yield
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture(autouse=True)
def _set_admin_password():
    """Ensure ADMIN_PASSWORD is set so login works in tests."""
    old = os.environ.get('ADMIN_PASSWORD')
    os.environ['ADMIN_PASSWORD'] = 'test-password'
    yield
    if old is None:
        os.environ.pop('ADMIN_PASSWORD', None)
    else:
        os.environ['ADMIN_PASSWORD'] = old


@pytest.fixture
def auth_client(app):
    """Flask test client that is already logged in as admin."""
    client = app.test_client()
    client.post('/login', data={
        'username': 'admin',
        'password': 'test-password',
    }, follow_redirects=True)
    return client
