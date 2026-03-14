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
