from __future__ import annotations

import structlog
from flask_socketio import Namespace, emit

logger = structlog.get_logger()


class DashboardNamespace(Namespace):
    """SocketIO namespace for the monitoring dashboard.

    Clients connect to /dashboard and receive real-time events:
    - factor_scores, signals, portfolio, risk_alert
    - trade, regime_change, system_health, config_change

    All events are pushed server→client via the RedisBridge.
    Client→server messages are handled here (subscriptions, pings).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._log = logger.bind(component='dashboard_ws')

    def on_connect(self):
        self._log.info('client_connected')
        emit('connected', {'status': 'ok'})

    def on_disconnect(self):
        self._log.info('client_disconnected')

    def on_ping(self, data=None):
        emit('pong', {'status': 'ok'})

    def on_subscribe(self, data):
        """Client requests subscription to specific event types.

        Currently all events go to all connected clients.
        This is a placeholder for per-client filtering.
        """
        channels = data.get('channels', []) if data else []
        self._log.info('client_subscribe', channels=channels)
        emit('subscribed', {'channels': channels})


def register_socketio_handlers(socketio):
    """Register the dashboard namespace with Flask-SocketIO."""
    socketio.on_namespace(DashboardNamespace('/dashboard'))
