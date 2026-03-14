import logging

import redis as redis_lib
import structlog
from flask import Flask

from config import config_map


def create_app(config_name: str = 'development') -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_map[config_name])

    # Init extensions
    from app.extensions import db, socketio
    db.init_app(app)
    socketio.init_app(
        app,
        message_queue=app.config['REDIS_URL'],
        async_mode='eventlet',
        cors_allowed_origins='*',
    )

    # Redis
    import app.extensions as ext
    ext.redis_client = redis_lib.from_url(
        app.config['REDIS_URL'],
        decode_responses=False,
    )

    # Structlog
    _configure_logging()

    # Register blueprints
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
