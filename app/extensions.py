from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
import structlog

db = SQLAlchemy()
socketio = SocketIO()
redis_client = None  # Initialized in create_app
logger = structlog.get_logger()
