#!/usr/bin/env python
"""Backfill historical daily bars for all active ETFs + VIX.

Usage:
    python scripts/backfill_history.py              # default 756 days (~3 years)
    python scripts/backfill_history.py --days 252   # 1 year
    python scripts/backfill_history.py --symbol SPY # single symbol
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import structlog

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = structlog.get_logger()


def backfill_etfs(days: int, symbol_filter: str | None = None):
    from app import create_app
    app = create_app()

    with app.app_context():
        from app.data.alpaca_provider import AlpacaDataProvider
        from app.data.ingestion import (
            detect_and_fill_gaps,
            ingest_daily_bars,
            update_redis_cache,
        )
        from app.data.vix_provider import VIXProvider
        from app.extensions import redis_client
        from app.models.etf_universe import ETFUniverse

        provider = AlpacaDataProvider(
            api_key=os.environ.get('ALPACA_API_KEY', ''),
            secret_key=os.environ.get('ALPACA_SECRET_KEY', ''),
        )

        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=days)

        # Get active ETFs
        if symbol_filter:
            etfs = ETFUniverse.query.filter_by(
                symbol=symbol_filter.upper(), is_active=True,
            ).all()
        else:
            etfs = ETFUniverse.query.filter_by(is_active=True).all()

        if not etfs:
            logger.warning('no_active_etfs_found')
            print('No active ETFs found. Run universe_setup.py first.')
            return

        symbols = [etf.symbol for etf in etfs]
        total = len(symbols)
        print(f'Backfilling {total} ETFs from {start_date} to {end_date} ({days} days)')

        results = {}
        for i, symbol in enumerate(symbols, 1):
            print(f'  [{i}/{total}] {symbol}...', end=' ', flush=True)
            try:
                df = provider.get_daily_bars(symbol, start_date, end_date)
                if df.empty:
                    print('no data')
                    results[symbol] = 0
                    continue

                inserted = ingest_daily_bars(symbol, df, source='alpaca')
                update_redis_cache(symbol, df, redis_client)

                # Detect and fill gaps with synthetic bars
                gaps_filled = detect_and_fill_gaps(
                    symbol, start_date, end_date,
                )

                results[symbol] = inserted
                print(f'{inserted} bars inserted, {gaps_filled} gaps filled')

            except Exception as exc:
                print(f'ERROR: {exc}')
                results[symbol] = -1
                logger.error(
                    'backfill_symbol_failed',
                    symbol=symbol,
                    error=str(exc),
                )

        # Backfill VIX
        print('\n  Backfilling VIX history...', end=' ', flush=True)
        try:
            vix_provider = VIXProvider(redis_client=redis_client)
            vix_df = vix_provider.get_vix_history(start_date, end_date)
            print(f'{len(vix_df)} data points')
            # Cache latest VIX
            vix_provider.get_latest_vix()
        except Exception as exc:
            print(f'ERROR: {exc}')
            logger.error('vix_backfill_failed', error=str(exc))

        # Summary
        print('\n--- Summary ---')
        success = sum(1 for v in results.values() if v >= 0)
        failed = sum(1 for v in results.values() if v < 0)
        total_bars = sum(v for v in results.values() if v > 0)
        print(f'  Symbols: {success} ok, {failed} failed')
        print(f'  Total bars inserted: {total_bars}')


def main():
    parser = argparse.ArgumentParser(
        description='Backfill historical price bars for ETF universe',
    )
    parser.add_argument(
        '--days', type=int, default=756,
        help='Number of calendar days to backfill (default: 756 = ~3 years)',
    )
    parser.add_argument(
        '--symbol', type=str, default=None,
        help='Backfill a single symbol instead of entire universe',
    )
    args = parser.parse_args()

    backfill_etfs(args.days, args.symbol)


if __name__ == '__main__':
    main()
