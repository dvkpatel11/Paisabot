from __future__ import annotations

import structlog

logger = structlog.get_logger()


class ConfigLoader:
    """Reads config from Redis (fast path), falls back to DB on cache miss.

    All system parameters live in the system_config PostgreSQL table and are
    cached in Redis hashes prefixed config:*. Kill switches use direct
    kill_switch:* Redis keys (not under config:).
    """

    CACHE_TTL = 30  # seconds — short TTL for kill switches

    def __init__(self, redis_client, db_session):
        self.redis = redis_client
        self.db = db_session

    def get(self, category: str, key: str, default=None) -> str | None:
        """Read from Redis; fall back to DB on cache miss."""
        value = self.redis.hget(f'config:{category}', key)
        if value is not None:
            return value.decode() if isinstance(value, bytes) else value

        # DB fallback
        from app.models.system_config import SystemConfig
        row = self.db.query(SystemConfig).filter_by(
            category=category, key=key
        ).first()
        if row:
            self.redis.hset(f'config:{category}', key, row.value)
            return row.value
        return default

    def get_float(self, category: str, key: str, default: float = 0.0) -> float:
        val = self.get(category, key)
        if val is None:
            return default
        return float(val)

    def get_int(self, category: str, key: str, default: int = 0) -> int:
        val = self.get(category, key)
        if val is None:
            return default
        return int(float(val))

    def get_bool(self, category: str, key: str, default: bool = False) -> bool:
        val = self.get(category, key)
        if val is None:
            return default
        return str(val).lower() in ('1', 'true', 'yes')

    def is_kill_switch_active(self, switch: str) -> bool:
        """Check kill switch Redis key (not under config:)."""
        val = self.redis.get(f'kill_switch:{switch}')
        return val == b'1'

    def set_kill_switch(self, switch: str, active: bool):
        """Set or clear a kill switch."""
        self.redis.set(f'kill_switch:{switch}', '1' if active else '0')
        logger.info(
            'kill_switch_changed',
            switch=switch,
            active=active,
        )

    def warm_cache(self):
        """Load all config from DB into Redis on application start."""
        from app.models.system_config import SystemConfig
        rows = self.db.query(SystemConfig).all()
        pipe = self.redis.pipeline()
        for row in rows:
            pipe.hset(f'config:{row.category}', row.key, row.value)
        pipe.execute()
        logger.info('config_cache_warmed', count=len(rows))

    def set(self, category: str, key: str, value: str, updated_by: str = 'system'):
        """Write to both PostgreSQL and Redis."""
        from app.models.system_config import SystemConfig
        row = self.db.query(SystemConfig).filter_by(
            category=category, key=key
        ).first()
        if row:
            row.value = value
            row.updated_by = updated_by
        else:
            row = SystemConfig(
                category=category,
                key=key,
                value=value,
                updated_by=updated_by,
            )
            self.db.add(row)
        self.db.commit()
        self.redis.hset(f'config:{category}', key, value)
