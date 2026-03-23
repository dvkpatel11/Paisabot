"""Ingest fundamental data from FMP into StockUniverse + Redis cache.

Updates stock metadata (ratios, earnings, growth) and caches key
fundamentals in Redis for fast factor computation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import structlog

from app.extensions import db
from app.models.stock_universe import StockUniverse

logger = structlog.get_logger()


def update_stock_fundamentals(
    symbol: str,
    fundamentals: dict,
    redis_client=None,
) -> bool:
    """Update StockUniverse row with fetched fundamental data.

    Args:
        symbol: Stock ticker
        fundamentals: Dict from FMPProvider.get_full_fundamentals()
        redis_client: Redis client for caching

    Returns:
        True if updated, False if stock not found or error.
    """
    stock = StockUniverse.query.filter_by(symbol=symbol).first()
    if stock is None:
        logger.warning('stock_not_in_universe', symbol=symbol)
        return False

    # ── update DB fields ─────────────────────────────────────────
    field_map = {
        'market_cap_bn': 'market_cap_bn',
        'beta': 'beta',
        'pe_ratio': 'pe_ratio',
        'forward_pe': 'forward_pe',
        'pb_ratio': 'pb_ratio',
        'ps_ratio': 'ps_ratio',
        'roe': 'roe',
        'debt_to_equity': 'debt_to_equity',
        'profit_margin': 'profit_margin',
        'dividend_yield': 'dividend_yield',
        'revenue_growth_yoy': 'revenue_growth_yoy',
        'earnings_growth_yoy': 'earnings_growth_yoy',
        'last_earnings_surprise': 'last_earnings_surprise',
        'earnings_surprise_3q_avg': 'earnings_surprise_3q_avg',
    }

    updated_fields = 0
    for src_key, db_field in field_map.items():
        value = fundamentals.get(src_key)
        if value is not None:
            setattr(stock, db_field, value)
            updated_fields += 1

    # Update sector/industry if provided and not already set
    if fundamentals.get('sector') and stock.sector == 'Unknown':
        stock.sector = fundamentals['sector']
    if fundamentals.get('industry') and not stock.industry:
        stock.industry = fundamentals['industry']

    stock.fundamentals_updated_at = datetime.now(timezone.utc)
    db.session.commit()

    # ── cache to Redis ───────────────────────────────────────────
    if redis_client:
        import json

        cache_data = {}
        for key in field_map:
            val = fundamentals.get(key)
            if val is not None:
                cache_data[key] = str(val)

        if cache_data:
            redis_key = f'fundamentals:{symbol}'
            redis_client.hset(redis_key, mapping=cache_data)
            redis_client.expire(redis_key, 86400)  # 24h TTL

    logger.info(
        'stock_fundamentals_updated',
        symbol=symbol,
        fields_updated=updated_fields,
    )
    return True


def update_earnings_calendar(
    earnings: list[dict],
    redis_client=None,
) -> int:
    """Update next_earnings_date for stocks in the universe.

    Args:
        earnings: List of dicts from FMPProvider.get_earnings_calendar()
        redis_client: Redis client for caching

    Returns:
        Number of stocks updated.
    """
    today = date.today()
    updated = 0

    for entry in earnings:
        symbol = entry.get('symbol', '')
        earn_date_str = entry.get('date', '')
        if not symbol or not earn_date_str:
            continue

        try:
            earn_date = date.fromisoformat(earn_date_str)
        except ValueError:
            continue

        stock = StockUniverse.query.filter_by(symbol=symbol).first()
        if stock is None:
            continue

        # Update next/last earnings date
        if earn_date >= today:
            stock.next_earnings_date = earn_date
        else:
            stock.last_earnings_date = earn_date

        # Cache earnings proximity in Redis for risk gate
        if redis_client and earn_date >= today:
            days_to_earnings = (earn_date - today).days
            redis_client.set(
                f'earnings:{symbol}:next_date', earn_date_str, ex=86400,
            )
            redis_client.set(
                f'earnings:{symbol}:days_to', str(days_to_earnings), ex=86400,
            )

        updated += 1

    if updated:
        db.session.commit()

    logger.info('earnings_calendar_updated', stocks_updated=updated)
    return updated


def get_stale_stocks(max_age_days: int = 7) -> list[str]:
    """Return symbols whose fundamentals are older than max_age_days.

    Prioritizes stocks that have never been updated.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    stale = StockUniverse.query.filter(
        StockUniverse.is_active == True,  # noqa: E712
        db.or_(
            StockUniverse.fundamentals_updated_at.is_(None),
            StockUniverse.fundamentals_updated_at < cutoff,
        ),
    ).order_by(
        # Never-updated first, then oldest
        StockUniverse.fundamentals_updated_at.asc().nullsfirst(),
    ).all()

    return [s.symbol for s in stale]
