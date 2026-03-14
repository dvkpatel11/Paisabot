import json

from app.extensions import redis_client


def publish_event(channel: str, data: dict):
    """Publish to a pub/sub channel (fire-and-forget, lossy)."""
    redis_client.publish(channel, json.dumps(data))


def push_to_queue(queue_name: str, data: dict):
    """LPUSH for reliable list queues (must not lose messages)."""
    redis_client.lpush(queue_name, json.dumps(data))


def pop_from_queue(queue_name: str, timeout: int = 0) -> dict | None:
    """BRPOP for consuming from list queues."""
    result = redis_client.brpop(queue_name, timeout=timeout)
    if result:
        return json.loads(result[1])
    return None


def cache_set(key: str, data: dict, ttl_seconds: int = 300):
    """Set a JSON-serialized cache entry with TTL."""
    redis_client.set(key, json.dumps(data), ex=ttl_seconds)


def cache_get(key: str) -> dict | None:
    """Get a JSON-serialized cache entry."""
    raw = redis_client.get(key)
    return json.loads(raw) if raw else None


def hash_set(key: str, mapping: dict, ttl_seconds: int | None = None):
    """Set multiple fields in a Redis hash."""
    pipe = redis_client.pipeline()
    for field, value in mapping.items():
        pipe.hset(key, field, json.dumps(value) if isinstance(value, (dict, list)) else str(value))
    if ttl_seconds:
        pipe.expire(key, ttl_seconds)
    pipe.execute()


def hash_get(key: str, field: str) -> str | None:
    """Get a single field from a Redis hash."""
    val = redis_client.hget(key, field)
    return val.decode() if val else None


def hash_getall(key: str) -> dict:
    """Get all fields from a Redis hash."""
    raw = redis_client.hgetall(key)
    return {k.decode(): v.decode() for k, v in raw.items()}
