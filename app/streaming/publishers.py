from __future__ import annotations

import json

import structlog

logger = structlog.get_logger()


def publish_event(redis_client, channel: str, data: dict) -> None:
    """Publish a JSON event to a Redis pub/sub channel (lossy, best-effort)."""
    if redis_client is None:
        return
    try:
        redis_client.publish(channel, json.dumps(data, default=str))
    except Exception as exc:
        logger.error('publish_failed', channel=channel, error=str(exc))


def publish_factor_scores(redis_client, scores: dict) -> None:
    publish_event(redis_client, 'channel:factor_scores', scores)


def publish_signals(redis_client, signals: dict) -> None:
    publish_event(redis_client, 'channel:signals', signals)


def publish_portfolio(redis_client, portfolio: dict) -> None:
    publish_event(redis_client, 'channel:portfolio', portfolio)


def publish_risk_alert(redis_client, alert: dict) -> None:
    publish_event(redis_client, 'channel:risk_alerts', alert)


def publish_trade(redis_client, trade: dict) -> None:
    publish_event(redis_client, 'channel:trades', trade)


def publish_regime_change(redis_client, regime_data: dict) -> None:
    publish_event(redis_client, 'channel:regime_change', regime_data)


def publish_system_health(redis_client, health: dict) -> None:
    publish_event(redis_client, 'channel:system_health', health)


def publish_config_change(redis_client, change: dict) -> None:
    publish_event(redis_client, 'channel:config_change', change)


def publish_pipeline_status(redis_client, module_id: str, status: dict) -> None:
    """Publish pipeline module status to Redis cache + pub/sub.

    Args:
        redis_client: Redis connection.
        module_id: Module identifier (e.g. 'factor_engine', 'signal_engine').
        status: Dict with keys: status, items_processed, compute_time_ms, extra.
    """
    import json as _json
    from datetime import datetime, timezone

    status['last_activity'] = datetime.now(timezone.utc).isoformat()
    status.setdefault('status', 'ok')

    # Cache for API polling (TTL 5 min)
    cache_key = f'cache:pipeline:{module_id}'
    try:
        redis_client.setex(cache_key, 300, _json.dumps(status, default=str))
    except Exception:
        pass

    # Also broadcast for live dashboard
    publish_event(redis_client, 'channel:system_health', {
        'module': module_id,
        **status,
    })
