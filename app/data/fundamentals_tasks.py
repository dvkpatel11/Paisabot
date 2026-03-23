"""Celery tasks for stock fundamental data ingestion.

Tasks:
  - refresh_stock_fundamentals: Update fundamentals for all active stocks
  - refresh_earnings_calendar: Update earnings dates for the next 30 days
  - refresh_single_stock_fundamentals: Update fundamentals for one stock
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog

from celery_worker import celery

logger = structlog.get_logger()


@celery.task(
    name='app.data.refresh_stock_fundamentals',
    bind=True,
    max_retries=1,
    soft_time_limit=600,
    time_limit=660,
)
def refresh_stock_fundamentals(self):
    """Refresh fundamental data for all active stocks with stale data.

    Fetches profiles, ratios, income growth, and earnings surprises
    from FMP and updates the stock_universe table + Redis cache.

    Rate limit: FMP free tier = 250 req/day. Each stock uses ~5 requests.
    Batches to max 40 stocks per run (200 requests, leaving headroom).
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.fmp_provider import FMPProvider
        from app.data.fundamentals_ingestion import (
            get_stale_stocks,
            update_stock_fundamentals,
        )
        from app.extensions import redis_client
        import os

        try:
            api_key = os.environ.get('FMP_API_KEY', '')
            if not api_key:
                logger.warning('fmp_api_key_missing')
                return {'error': 'FMP_API_KEY not set'}

            provider = FMPProvider(api_key=api_key)

            # Get stocks needing refresh (max 40 per run)
            stale = get_stale_stocks(max_age_days=7)
            batch = stale[:40]

            if not batch:
                logger.info('no_stale_stocks')
                return {'updated': 0, 'total_stale': 0}

            logger.info(
                'fundamentals_refresh_start',
                batch_size=len(batch),
                total_stale=len(stale),
            )

            updated = 0
            errors = 0
            for symbol in batch:
                try:
                    fundamentals = provider.get_full_fundamentals(symbol)
                    if fundamentals:
                        success = update_stock_fundamentals(
                            symbol, fundamentals, redis_client,
                        )
                        if success:
                            updated += 1
                except Exception as exc:
                    logger.error(
                        'single_stock_fundamentals_failed',
                        symbol=symbol,
                        error=str(exc),
                    )
                    errors += 1

            logger.info(
                'fundamentals_refresh_complete',
                updated=updated,
                errors=errors,
                remaining_stale=len(stale) - len(batch),
            )
            return {
                'updated': updated,
                'errors': errors,
                'remaining_stale': len(stale) - len(batch),
            }

        except Exception as exc:
            logger.error('fundamentals_refresh_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(
    name='app.data.refresh_earnings_calendar',
    bind=True,
    max_retries=2,
)
def refresh_earnings_calendar(self):
    """Refresh earnings calendar for next 30 days.

    Updates next_earnings_date on StockUniverse and caches
    days-to-earnings in Redis for the risk gate.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.fmp_provider import FMPProvider
        from app.data.fundamentals_ingestion import update_earnings_calendar
        from app.extensions import redis_client
        import os

        try:
            api_key = os.environ.get('FMP_API_KEY', '')
            if not api_key:
                logger.warning('fmp_api_key_missing')
                return {'error': 'FMP_API_KEY not set'}

            provider = FMPProvider(api_key=api_key)

            today = date.today()
            earnings = provider.get_earnings_calendar(
                from_date=today,
                to_date=today + timedelta(days=30),
            )

            updated = update_earnings_calendar(earnings, redis_client)

            logger.info(
                'earnings_calendar_refreshed',
                entries_fetched=len(earnings),
                stocks_updated=updated,
            )
            return {
                'entries_fetched': len(earnings),
                'stocks_updated': updated,
            }

        except Exception as exc:
            logger.error('earnings_calendar_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(
    name='app.data.refresh_single_stock_fundamentals',
    bind=True,
    max_retries=2,
)
def refresh_single_stock_fundamentals(self, symbol: str):
    """Refresh fundamental data for a single stock.

    Used for on-demand refresh from the UI or when a new stock
    is added to the universe.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.fmp_provider import FMPProvider
        from app.data.fundamentals_ingestion import update_stock_fundamentals
        from app.extensions import redis_client
        import os

        try:
            api_key = os.environ.get('FMP_API_KEY', '')
            if not api_key:
                return {'error': 'FMP_API_KEY not set'}

            provider = FMPProvider(api_key=api_key)
            fundamentals = provider.get_full_fundamentals(symbol)

            if not fundamentals:
                return {'symbol': symbol, 'updated': False, 'reason': 'no_data'}

            success = update_stock_fundamentals(
                symbol, fundamentals, redis_client,
            )
            return {'symbol': symbol, 'updated': success}

        except Exception as exc:
            logger.error(
                'single_fundamentals_failed',
                symbol=symbol,
                error=str(exc),
            )
            raise self.retry(exc=exc)


@celery.task(name='app.data.refresh_all_stock_bars', bind=True, max_retries=1)
def refresh_all_stock_bars(self):
    """Refresh daily bars for all active stocks in the universe."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.models.stock_universe import StockUniverse
        from app.data.tasks import refresh_daily_bars

        try:
            stocks = StockUniverse.query.filter_by(is_active=True).all()
            symbols = [s.symbol for s in stocks]
            logger.info('refresh_all_stock_bars_start', count=len(symbols))

            for symbol in symbols:
                refresh_daily_bars.delay(symbol, asset_class='stock')

            return {'dispatched': len(symbols)}
        except Exception as exc:
            logger.error('refresh_all_stock_bars_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(name='app.data.compute_stock_factors', bind=True, max_retries=1)
def compute_stock_factors(self):
    """Compute factor scores and generate signals for all active stocks."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.extensions import db as _db, redis_client
        from app.factors.factor_registry import FactorRegistry
        from app.signals.signal_generator import SignalGenerator
        from app.models.stock_universe import StockUniverse
        import json

        try:
            stocks = StockUniverse.query.filter_by(
                is_active=True, in_active_set=True,
            ).all()
            symbols = [s.symbol for s in stocks]
            logger.info('compute_stock_factors_start', count=len(symbols))

            # Compute factors with stock asset class
            registry = FactorRegistry(
                redis_client=redis_client,
                db_session=_db.session,
                asset_class='stock',
            )
            scores = registry.compute_all(symbols)

            # Cache latest scores
            redis_client.set(
                'cache:scores:stock:latest',
                json.dumps({
                    sym: {k: round(v, 4) for k, v in factors.items()}
                    for sym, factors in scores.items()
                }),
                ex=3600,
            )

            # Generate signals
            generator = SignalGenerator(
                redis_client=redis_client,
                db_session=_db.session,
                asset_class='stock',
            )
            signals = generator.run(symbols)

            logger.info(
                'compute_stock_factors_complete',
                symbols=len(symbols),
                signals=len(signals) if signals else 0,
            )
            return {
                'symbols': len(symbols),
                'signals': len(signals) if signals else 0,
            }

        except Exception as exc:
            logger.error('compute_stock_factors_failed', error=str(exc))
            raise self.retry(exc=exc)
