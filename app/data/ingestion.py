from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import structlog
from sqlalchemy import and_

from app.extensions import db
from app.models.price_bars import PriceBar

logger = structlog.get_logger()


def ingest_daily_bars(
    symbol: str,
    df: pd.DataFrame,
    source: str = 'alpaca',
    asset_class: str = 'etf',
) -> int:
    """Bulk-insert daily bars into price_bars table.

    Uses PostgreSQL ON CONFLICT DO NOTHING to skip duplicates.
    Returns the number of rows inserted.
    """
    if df.empty:
        return 0

    rows = []
    for _, row in df.iterrows():
        rows.append({
            'symbol': symbol,
            'timeframe': '1d',
            'timestamp': row['timestamp'],
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': int(row['volume']) if pd.notna(row['volume']) else 0,
            'vwap': float(row['vwap']) if pd.notna(row.get('vwap')) else None,
            'trade_count': (
                int(row['trade_count'])
                if pd.notna(row.get('trade_count'))
                else None
            ),
            'is_synthetic': False,
            'source': source,
            'asset_class': asset_class,
        })

    if not rows:
        return 0

    dialect = db.engine.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(PriceBar).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=['symbol', 'timeframe', 'timestamp'],
        )
        result = db.session.execute(stmt)
        db.session.commit()
        inserted = result.rowcount
    else:
        # Fallback for SQLite / other dialects: skip duplicates manually
        inserted = 0
        for row_data in rows:
            existing = PriceBar.query.filter_by(
                symbol=row_data['symbol'],
                timeframe=row_data['timeframe'],
                timestamp=row_data['timestamp'],
            ).first()
            if existing is None:
                db.session.add(PriceBar(**row_data))
                inserted += 1
        db.session.commit()
    logger.info(
        'bars_ingested',
        symbol=symbol,
        total=len(rows),
        inserted=inserted,
        source=source,
    )
    return inserted


def detect_gaps(
    symbol: str,
    start_date: date,
    end_date: date,
    trading_calendar: list[date] | None = None,
) -> list[date]:
    """Detect missing trading days for a symbol in the given range.

    If trading_calendar is not provided, uses a simple weekday heuristic
    (excludes Sat/Sun but not market holidays).
    """
    existing = (
        db.session.query(db.func.date(PriceBar.timestamp))
        .filter(
            and_(
                PriceBar.symbol == symbol,
                PriceBar.timeframe == '1d',
                PriceBar.timestamp >= datetime.combine(
                    start_date, datetime.min.time()
                ).replace(tzinfo=timezone.utc),
                PriceBar.timestamp <= datetime.combine(
                    end_date, datetime.min.time()
                ).replace(tzinfo=timezone.utc),
            )
        )
        .all()
    )
    existing_dates = set()
    for row in existing:
        val = row[0]
        if isinstance(val, str):
            val = date.fromisoformat(val)
        elif isinstance(val, datetime):
            val = val.date()
        existing_dates.add(val)

    if trading_calendar is not None:
        expected_dates = set(trading_calendar)
    else:
        # Simple weekday heuristic
        expected_dates = set()
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:  # Mon-Fri
                expected_dates.add(current)
            current += timedelta(days=1)

    missing = sorted(expected_dates - existing_dates)
    if missing:
        logger.info(
            'gaps_detected',
            symbol=symbol,
            count=len(missing),
            first=str(missing[0]),
            last=str(missing[-1]),
        )
    return missing


def fill_gaps_with_synthetic(
    symbol: str,
    gap_dates: list[date],
    asset_class: str = 'etf',
) -> int:
    """Fill missing dates with synthetic bars (carry-forward close).

    For each gap date, finds the most recent real bar and creates a
    synthetic bar with OHLC = prior close, volume = 0.
    """
    if not gap_dates:
        return 0

    filled = 0
    for gap_date in gap_dates:
        # Find most recent real bar before this date
        prior_bar = (
            PriceBar.query.filter(
                and_(
                    PriceBar.symbol == symbol,
                    PriceBar.timeframe == '1d',
                    PriceBar.timestamp < datetime.combine(
                        gap_date, datetime.min.time()
                    ).replace(tzinfo=timezone.utc),
                    PriceBar.is_synthetic == False,  # noqa: E712
                )
            )
            .order_by(PriceBar.timestamp.desc())
            .first()
        )

        if prior_bar is None:
            logger.warning(
                'no_prior_bar_for_synthetic',
                symbol=symbol,
                date=str(gap_date),
            )
            continue

        carry_close = float(prior_bar.close)
        ts = datetime.combine(gap_date, datetime.min.time()).replace(
            tzinfo=timezone.utc,
        )

        synthetic = PriceBar(
            symbol=symbol,
            timeframe='1d',
            timestamp=ts,
            open=carry_close,
            high=carry_close,
            low=carry_close,
            close=carry_close,
            volume=0,
            is_synthetic=True,
            source='synthetic',
            asset_class=asset_class,
        )
        db.session.add(synthetic)
        filled += 1

    if filled:
        db.session.commit()
        logger.info(
            'synthetic_bars_filled',
            symbol=symbol,
            count=filled,
        )

    return filled


def detect_and_fill_gaps(
    symbol: str,
    start_date: date,
    end_date: date,
    trading_calendar: list[date] | None = None,
    asset_class: str = 'etf',
) -> int:
    """Detect missing trading days and fill with synthetic bars."""
    gaps = detect_gaps(symbol, start_date, end_date, trading_calendar)
    if not gaps:
        return 0
    return fill_gaps_with_synthetic(symbol, gaps, asset_class=asset_class)


def update_redis_cache(
    symbol: str,
    df: pd.DataFrame,
    redis_client,
    ttl: int = 86400,
) -> int:
    """Cache bar data in Redis as ohlcv:{symbol}:{date} hashes.

    Returns number of keys set.
    """
    if df.empty or redis_client is None:
        return 0

    count = 0
    for _, row in df.iterrows():
        ts = row['timestamp']
        if hasattr(ts, 'date'):
            date_str = str(ts.date())
        else:
            date_str = str(ts)[:10]

        key = f'ohlcv:{symbol}:{date_str}'
        mapping = {
            'open': str(row['open']),
            'high': str(row['high']),
            'low': str(row['low']),
            'close': str(row['close']),
            'volume': str(int(row['volume'])) if pd.notna(row['volume']) else '0',
        }
        if pd.notna(row.get('vwap')):
            mapping['vwap'] = str(row['vwap'])

        redis_client.hset(key, mapping=mapping)
        redis_client.expire(key, ttl)
        count += 1

    logger.info('redis_cache_updated', symbol=symbol, keys=count)
    return count
