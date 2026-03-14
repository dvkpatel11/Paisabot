from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
UTC = ZoneInfo('UTC')


def now_et() -> datetime:
    return datetime.now(ET)


def now_utc() -> datetime:
    return datetime.now(UTC)


def is_market_hours() -> bool:
    """True if current ET time is between 09:30 and 16:00 on a weekday."""
    et_now = now_et()
    if et_now.weekday() >= 5:
        return False
    return time(9, 30) <= et_now.time() <= time(16, 0)


def to_et(dt: datetime) -> datetime:
    """Convert a datetime to Eastern Time."""
    return dt.astimezone(ET)


def to_utc(dt: datetime) -> datetime:
    """Convert a datetime to UTC."""
    return dt.astimezone(UTC)
