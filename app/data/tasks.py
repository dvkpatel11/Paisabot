from __future__ import annotations

from datetime import date, timedelta

import structlog

from celery_worker import celery

logger = structlog.get_logger()


@celery.task(name='app.data.backfill_bars', bind=True, max_retries=2)
def backfill_bars(self, symbol: str, days: int = 756):
    """Backfill historical daily bars for a single symbol."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.alpaca_provider import AlpacaDataProvider
        from app.data.ingestion import ingest_daily_bars, update_redis_cache
        from app.extensions import redis_client
        import os

        try:
            provider = AlpacaDataProvider(
                api_key=os.environ.get('ALPACA_API_KEY', ''),
                secret_key=os.environ.get('ALPACA_SECRET_KEY', ''),
            )
            end_date = date.today() - timedelta(days=1)
            start_date = end_date - timedelta(days=days)

            logger.info(
                'backfill_start',
                symbol=symbol,
                start=str(start_date),
                end=str(end_date),
            )

            df = provider.get_daily_bars(symbol, start_date, end_date)
            if df.empty:
                logger.warning('backfill_no_data', symbol=symbol)
                return {'symbol': symbol, 'inserted': 0}

            inserted = ingest_daily_bars(symbol, df, source='alpaca')
            update_redis_cache(symbol, df, redis_client)

            logger.info(
                'backfill_complete',
                symbol=symbol,
                inserted=inserted,
            )
            return {'symbol': symbol, 'inserted': inserted}

        except Exception as exc:
            logger.error('backfill_failed', symbol=symbol, error=str(exc))
            raise self.retry(exc=exc)


@celery.task(name='app.data.refresh_daily_bars', bind=True, max_retries=3)
def refresh_daily_bars(self, symbol: str):
    """Fetch the latest daily bar for a symbol and ingest it."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.alpaca_provider import AlpacaDataProvider
        from app.data.ingestion import ingest_daily_bars, update_redis_cache
        from app.extensions import redis_client
        import os

        try:
            provider = AlpacaDataProvider(
                api_key=os.environ.get('ALPACA_API_KEY', ''),
                secret_key=os.environ.get('ALPACA_SECRET_KEY', ''),
            )
            end_date = date.today()
            start_date = end_date - timedelta(days=5)  # buffer for weekends

            df = provider.get_daily_bars(symbol, start_date, end_date)
            if df.empty:
                return {'symbol': symbol, 'inserted': 0}

            inserted = ingest_daily_bars(symbol, df, source='alpaca')
            update_redis_cache(symbol, df, redis_client)

            return {'symbol': symbol, 'inserted': inserted}

        except Exception as exc:
            logger.error(
                'refresh_bars_failed', symbol=symbol, error=str(exc),
            )
            raise self.retry(exc=exc)


@celery.task(name='app.data.refresh_vix', bind=True, max_retries=3)
def refresh_vix(self):
    """Refresh the latest VIX value from FRED and cache in Redis."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.vix_provider import VIXProvider
        from app.extensions import redis_client

        try:
            provider = VIXProvider(redis_client=redis_client)
            vix = provider.get_latest_vix()
            logger.info('vix_refreshed', value=vix)
            return {'vix': vix}

        except Exception as exc:
            logger.error('vix_refresh_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(name='app.data.refresh_universe_metadata')
def refresh_universe_metadata():
    """Refresh universe metadata (AUM, ADV) from market data."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.models.etf_universe import ETFUniverse
        from app.data.alpaca_provider import AlpacaDataProvider
        from app.extensions import db as _db
        import os

        try:
            provider = AlpacaDataProvider(
                api_key=os.environ.get('ALPACA_API_KEY', ''),
                secret_key=os.environ.get('ALPACA_SECRET_KEY', ''),
            )

            etfs = ETFUniverse.query.filter_by(is_active=True).all()
            updated = 0

            for etf in etfs:
                quote = provider.get_latest_quote(etf.symbol)
                if quote and quote.get('mid', 0) > 0:
                    spread_bps = quote.get('spread_bps', 0)
                    if spread_bps > 0:
                        etf.spread_est_bps = round(spread_bps, 2)
                        updated += 1

            _db.session.commit()
            logger.info('universe_metadata_refreshed', updated=updated)
            return {'updated': updated}

        except Exception as exc:
            logger.error(
                'universe_metadata_refresh_failed', error=str(exc),
            )
            return {'error': str(exc)}
