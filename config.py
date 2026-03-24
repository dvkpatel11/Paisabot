import os
from dotenv import load_dotenv

load_dotenv()


class BaseConfig:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'postgresql://paisabot:paisabot@localhost:5432/paisabot'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/1')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2')
    FERNET_KEY = os.environ.get('FERNET_KEY', '')
    # Comma-separated list of origins allowed to open WebSocket connections.
    # Set CORS_ALLOWED_ORIGINS=https://yourdomain.com in production .env
    _cors_raw = os.environ.get('CORS_ALLOWED_ORIGINS', 'http://localhost:5000')
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_raw.split(',') if o.strip()]

    # CSRF — enabled globally; API blueprint exempted in app factory
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour token validity

    # Session cookie security defaults (overridden per-environment)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = False  # overridden in production
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour (dev)


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TESTING = False


class TestingConfig(BaseConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False  # disable CSRF in test runner
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'TEST_DATABASE_URL',
        'sqlite://'  # in-memory SQLite for tests without Docker
    )
    # Keep a single connection for in-memory SQLite so tables created on one
    # connection are visible to others within the same test.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': __import__('sqlalchemy.pool', fromlist=['StaticPool']).StaticPool,
        'connect_args': {'check_same_thread': False},
    }
    REDIS_URL = os.environ.get('TEST_REDIS_URL', 'redis://localhost:6379/15')


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True  # require HTTPS for cookies
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 28800  # 8 hours (trading day)


config_map = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
}
