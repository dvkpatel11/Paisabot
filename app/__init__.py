import logging
import os

import redis as redis_lib
import structlog
from flask import Flask

from config import config_map

# Project root (one level above app/)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_REQUIRED_PROD_VARS = [
    'SECRET_KEY',
    'DATABASE_URL',
    'REDIS_URL',
    'FERNET_KEY',
    'ADMIN_PASSWORD',
    'ALPACA_API_KEY',
    'ALPACA_SECRET_KEY',
]

_INSECURE_DEFAULTS = {
    'SECRET_KEY': 'dev-secret-change-me',
    'DATABASE_URL': 'postgresql://paisabot:paisabot@localhost:5432/paisabot',
}


def _validate_env(config_name: str) -> None:
    """Raise on missing/default secrets when running in production."""
    import os
    if config_name != 'production':
        return
    missing = [v for v in _REQUIRED_PROD_VARS if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f'Production startup blocked — missing required env vars: {missing}\n'
            'Set them in your .env or systemd/Docker environment before starting.'
        )
    for var, bad_default in _INSECURE_DEFAULTS.items():
        if os.environ.get(var) == bad_default:
            raise RuntimeError(
                f'Production startup blocked — {var} is still the insecure default value. '
                'Change it before deploying.'
            )


def create_app(config_name: str = 'development') -> Flask:
    _validate_env(config_name)

    app = Flask(
        __name__,
        static_folder=os.path.join(_ROOT, 'static'),
        template_folder=os.path.join(_ROOT, 'templates'),
    )
    app.config.from_object(config_map[config_name])

    # Init extensions
    from app.extensions import db, socketio
    db.init_app(app)
    # Restrict WebSocket connections to explicitly configured origins.
    # '*' would allow any website to subscribe to real-time trading data.
    cors_origins = app.config.get('CORS_ALLOWED_ORIGINS', ['http://localhost:5000'])
    socketio.init_app(
        app,
        message_queue=app.config['REDIS_URL'],
        async_mode='eventlet',
        cors_allowed_origins=cors_origins,
    )

    # Redis
    import app.extensions as ext
    ext.redis_client = redis_lib.from_url(
        app.config['REDIS_URL'],
        decode_responses=True,
    )

    # Flask-Login
    from app.auth import login_manager
    login_manager.init_app(app)

    # Structlog
    _configure_logging()

    # Register blueprints
    from app.auth.views import auth_bp
    app.register_blueprint(auth_bp)

    from app.api import api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    from app.views import views_bp
    app.register_blueprint(views_bp)

    # Flask-Admin
    from app.admin import init_admin
    init_admin(app)

    # SocketIO handlers
    from app.streaming.socket_handler import register_socketio_handlers
    register_socketio_handlers(socketio)

    # Start Redis → SocketIO bridge (only in non-testing)
    if config_name != 'testing':
        from app.streaming.redis_bridge import RedisBridge
        bridge = RedisBridge(ext.redis_client, socketio)
        bridge.start()

    return app


def _configure_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
