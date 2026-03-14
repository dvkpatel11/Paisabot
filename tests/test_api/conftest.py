"""API test fixtures — ensure DB tables exist for all API tests."""
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
