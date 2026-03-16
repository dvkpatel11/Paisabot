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
    """Refresh VIX latest value + 252-day history from FRED, cache in Redis.

    Populates both `vix:latest` and `vix:history_252` so the volatility
    factor can percentile-rank without hitting FRED on every compute cycle.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.vix_provider import VIXProvider
        from app.extensions import redis_client

        try:
            provider = VIXProvider(redis_client=redis_client)
            result = provider.refresh()
            logger.info('vix_refreshed', **result)
            return result

        except Exception as exc:
            logger.error('vix_refresh_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(name='app.data.refresh_cboe_put_call', bind=True, max_retries=3)
def refresh_cboe_put_call(self):
    """Refresh CBOE equity put/call ratio and cache in Redis.

    Fetches aggregate P/C ratio from CBOE (with FRED fallback),
    computes 10-day MA, and populates per-symbol Redis keys
    so the sentiment factor's options_score component is live.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.data.cboe_provider import CBOEPutCallProvider
        from app.extensions import redis_client

        try:
            provider = CBOEPutCallProvider(redis_client=redis_client)
            result = provider.refresh()
            logger.info('cboe_pc_refreshed', **result)
            return result

        except Exception as exc:
            logger.error('cboe_pc_refresh_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(name='app.data.refresh_all_bars', bind=True, max_retries=1)
def refresh_all_bars(self):
    """Refresh daily bars for all active ETFs in the universe."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.models.etf_universe import ETFUniverse

        try:
            etfs = ETFUniverse.query.filter_by(is_active=True).all()
            symbols = [e.symbol for e in etfs]
            logger.info('refresh_all_bars_start', count=len(symbols))

            for symbol in symbols:
                refresh_daily_bars.delay(symbol)

            return {'dispatched': len(symbols)}
        except Exception as exc:
            logger.error('refresh_all_bars_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(name='app.data.compute_all_factors', bind=True, max_retries=1)
def compute_all_factors(self):
    """Compute factor scores and generate signals for all active ETFs."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.extensions import db as _db, redis_client
        from app.factors.factor_registry import FactorRegistry
        from app.signals.signal_generator import SignalGenerator
        from app.models.etf_universe import ETFUniverse
        import json

        try:
            etfs = ETFUniverse.query.filter_by(
                is_active=True, in_active_set=True,
            ).all()
            symbols = [e.symbol for e in etfs]
            logger.info('compute_all_factors_start', count=len(symbols))

            # Compute factors
            registry = FactorRegistry(
                redis_client=redis_client,
                db_session=_db.session,
            )
            scores = registry.compute_all(symbols)

            # Cache latest scores for API
            redis_client.set(
                'cache:scores:latest',
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
            )
            signals = generator.run(symbols)

            logger.info(
                'compute_all_factors_complete',
                symbols=len(symbols),
                signals=len(signals) if signals else 0,
            )
            return {
                'symbols': len(symbols),
                'signals': len(signals) if signals else 0,
            }

        except Exception as exc:
            logger.error('compute_all_factors_failed', error=str(exc))
            raise self.retry(exc=exc)


@celery.task(name='app.data.refresh_universe_metadata')
def refresh_universe_metadata():
    """Refresh universe metadata (spreads, ADV) and cache to Redis."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.models.etf_universe import ETFUniverse
        from app.models.price_bars import PriceBar
        from app.data.alpaca_provider import AlpacaDataProvider
        from app.extensions import db as _db, redis_client
        from sqlalchemy import func, desc
        import os

        try:
            provider = AlpacaDataProvider(
                api_key=os.environ.get('ALPACA_API_KEY', ''),
                secret_key=os.environ.get('ALPACA_SECRET_KEY', ''),
            )

            etfs = ETFUniverse.query.filter_by(is_active=True).all()
            updated_spread = 0
            updated_adv = 0

            for etf in etfs:
                # Update spread from live quotes
                quote = provider.get_latest_quote(etf.symbol)
                if quote and quote.get('mid', 0) > 0:
                    spread_bps = quote.get('spread_bps', 0)
                    if spread_bps > 0:
                        etf.spread_est_bps = round(spread_bps, 2)
                        updated_spread += 1
                        # Cache spread to Redis
                        redis_client.set(
                            f'etf:{etf.symbol}:spread_bps',
                            str(round(spread_bps, 2)),
                            ex=86400,
                        )

                # Compute 30-day ADV from price_bars
                bars = (
                    PriceBar.query
                    .filter_by(symbol=etf.symbol, timeframe='1d')
                    .order_by(desc(PriceBar.timestamp))
                    .limit(30)
                    .all()
                )
                if bars:
                    total_dollar_vol = sum(
                        float(b.close) * int(b.volume) for b in bars
                    )
                    adv_m = round(total_dollar_vol / len(bars) / 1_000_000, 2)
                    redis_client.set(
                        f'etf:{etf.symbol}:adv_30d_m',
                        str(adv_m),
                        ex=86400,
                    )
                    updated_adv += 1

            _db.session.commit()
            logger.info(
                'universe_metadata_refreshed',
                updated_spread=updated_spread,
                updated_adv=updated_adv,
            )
            return {
                'updated_spread': updated_spread,
                'updated_adv': updated_adv,
            }

        except Exception as exc:
            logger.error(
                'universe_metadata_refresh_failed', error=str(exc),
            )
            return {'error': str(exc)}


@celery.task(name='app.data.record_daily_performance')
def record_daily_performance():
    """Compute and persist daily performance metrics."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.extensions import db as _db, redis_client
        from app.risk.performance_recorder import PerformanceRecorder

        try:
            recorder = PerformanceRecorder(_db.session, redis_client)
            result = recorder.record_daily()
            logger.info('daily_performance_recorded', **result)
            return result
        except Exception as exc:
            logger.error('daily_performance_failed', error=str(exc))
            return {'error': str(exc)}
