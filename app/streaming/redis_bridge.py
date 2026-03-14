from __future__ import annotations

import json
import threading

import structlog

logger = structlog.get_logger()

# Maps Redis channel → SocketIO event name
CHANNEL_EVENT_MAP = {
    'channel:factor_scores': 'factor_scores',
    'channel:signals': 'signals',
    'channel:portfolio': 'portfolio',
    'channel:risk_alerts': 'risk_alert',
    'channel:trades': 'trade',
    'channel:fills': 'trade',
    'channel:regime_change': 'regime_change',
    'channel:system_health': 'system_health',
    'channel:config_change': 'config_change',
}


class RedisBridge:
    """Daemon thread that relays Redis pub/sub messages to SocketIO clients.

    Subscribes to all dashboard-relevant channels and emits events
    to connected clients on the /dashboard namespace.

    Redis pub/sub is lossy by design — if no clients are connected,
    messages are simply dropped.
    """

    def __init__(self, redis_client, socketio, namespace='/dashboard'):
        self._redis = redis_client
        self._socketio = socketio
        self._namespace = namespace
        self._thread = None
        self._running = False
        self._log = logger.bind(component='redis_bridge')

    def start(self) -> None:
        """Start the bridge in a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name='redis-bridge',
        )
        self._thread.start()
        self._log.info('redis_bridge_started', channels=list(CHANNEL_EVENT_MAP.keys()))

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._log.info('redis_bridge_stopped')

    def _listen_loop(self) -> None:
        """Subscribe to all channels and relay messages to SocketIO."""
        pubsub = self._redis.pubsub()
        pubsub.subscribe(*CHANNEL_EVENT_MAP.keys())

        try:
            while self._running:
                message = pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    continue

                channel = message['channel']
                if isinstance(channel, bytes):
                    channel = channel.decode()

                event_name = CHANNEL_EVENT_MAP.get(channel)
                if not event_name:
                    continue

                try:
                    data = json.loads(message['data'])
                except (json.JSONDecodeError, TypeError):
                    continue

                self._socketio.emit(
                    event_name,
                    data,
                    namespace=self._namespace,
                )

        except Exception as exc:
            self._log.error('redis_bridge_error', error=str(exc))
        finally:
            try:
                pubsub.unsubscribe()
                pubsub.close()
            except Exception:
                pass
