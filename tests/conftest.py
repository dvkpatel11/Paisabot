import pytest
import fakeredis

from app import create_app
from app.extensions import db as _db
from app.utils.config_loader import ConfigLoader


@pytest.fixture(scope='session')
def app():
    """Create application for testing."""
    app = create_app('testing')
    with app.app_context():
        yield app


@pytest.fixture(scope='function')
def db_session(app):
    """Create a fresh database session for each test with rollback."""
    with app.app_context():
        _db.create_all()
        yield _db.session
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture(scope='function')
def redis_mock():
    """Fake Redis for testing without a real Redis server."""
    return fakeredis.FakeRedis()


@pytest.fixture(scope='function')
def config_loader(db_session, redis_mock):
    """ConfigLoader with fake Redis and real DB session."""
    return ConfigLoader(redis_mock, db_session)


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()
