"""Populate stock_universe table with a starter set of liquid US stocks.

Seeds a curated watchlist of ~30 large-cap stocks across sectors.
All stocks start as watchlist-only (is_active=True, in_active_set=False).
Use the API or --activate flag to move stocks into the active trading set.

Usage:
    python scripts/stock_universe_setup.py              # watchlist only
    python scripts/stock_universe_setup.py --activate   # also activate core stocks
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from app.extensions import db
from app.models.stock_universe import StockUniverse


# Starter universe: liquid large-cap stocks across sectors
STOCK_UNIVERSE = [
    # Technology
    {'symbol': 'AAPL', 'name': 'Apple Inc.', 'sector': 'Technology'},
    {'symbol': 'MSFT', 'name': 'Microsoft Corporation', 'sector': 'Technology'},
    {'symbol': 'GOOGL', 'name': 'Alphabet Inc.', 'sector': 'Technology'},
    {'symbol': 'NVDA', 'name': 'NVIDIA Corporation', 'sector': 'Technology'},
    {'symbol': 'META', 'name': 'Meta Platforms Inc.', 'sector': 'Technology'},
    {'symbol': 'AMZN', 'name': 'Amazon.com Inc.', 'sector': 'Consumer Cyclical'},
    {'symbol': 'TSLA', 'name': 'Tesla Inc.', 'sector': 'Consumer Cyclical'},
    # Financials
    {'symbol': 'JPM', 'name': 'JPMorgan Chase & Co.', 'sector': 'Financial Services'},
    {'symbol': 'BAC', 'name': 'Bank of America Corp.', 'sector': 'Financial Services'},
    {'symbol': 'GS', 'name': 'Goldman Sachs Group Inc.', 'sector': 'Financial Services'},
    {'symbol': 'V', 'name': 'Visa Inc.', 'sector': 'Financial Services'},
    # Healthcare
    {'symbol': 'UNH', 'name': 'UnitedHealth Group Inc.', 'sector': 'Healthcare'},
    {'symbol': 'JNJ', 'name': 'Johnson & Johnson', 'sector': 'Healthcare'},
    {'symbol': 'LLY', 'name': 'Eli Lilly and Company', 'sector': 'Healthcare'},
    {'symbol': 'PFE', 'name': 'Pfizer Inc.', 'sector': 'Healthcare'},
    # Energy
    {'symbol': 'XOM', 'name': 'Exxon Mobil Corporation', 'sector': 'Energy'},
    {'symbol': 'CVX', 'name': 'Chevron Corporation', 'sector': 'Energy'},
    # Industrials
    {'symbol': 'CAT', 'name': 'Caterpillar Inc.', 'sector': 'Industrials'},
    {'symbol': 'BA', 'name': 'Boeing Company', 'sector': 'Industrials'},
    {'symbol': 'UPS', 'name': 'United Parcel Service Inc.', 'sector': 'Industrials'},
    # Consumer Staples
    {'symbol': 'PG', 'name': 'Procter & Gamble Co.', 'sector': 'Consumer Defensive'},
    {'symbol': 'KO', 'name': 'Coca-Cola Company', 'sector': 'Consumer Defensive'},
    {'symbol': 'WMT', 'name': 'Walmart Inc.', 'sector': 'Consumer Defensive'},
    # Communication
    {'symbol': 'DIS', 'name': 'Walt Disney Company', 'sector': 'Communication Services'},
    {'symbol': 'NFLX', 'name': 'Netflix Inc.', 'sector': 'Communication Services'},
    # Materials
    {'symbol': 'LIN', 'name': 'Linde plc', 'sector': 'Basic Materials'},
    # Real Estate
    {'symbol': 'AMT', 'name': 'American Tower Corp.', 'sector': 'Real Estate'},
    # Utilities
    {'symbol': 'NEE', 'name': 'NextEra Energy Inc.', 'sector': 'Utilities'},
]

# Core stocks to activate by default when --activate is passed
DEFAULT_ACTIVE_SET = {
    'AAPL', 'MSFT', 'GOOGL', 'NVDA', 'META', 'AMZN', 'TSLA',
    'JPM', 'UNH', 'XOM', 'V', 'LLY',
}


def setup(activate_core: bool = False):
    app = create_app('development')

    with app.app_context():
        count = 0
        for entry in STOCK_UNIVERSE:
            symbol = entry['symbol']
            existing = StockUniverse.query.filter_by(symbol=symbol).first()
            if existing:
                continue

            should_activate = activate_core and symbol in DEFAULT_ACTIVE_SET
            now = datetime.now(timezone.utc)

            stock = StockUniverse(
                symbol=symbol,
                name=entry['name'],
                sector=entry['sector'],
                is_active=True,
                in_active_set=should_activate,
                active_set_reason='initial_setup' if should_activate else None,
                active_set_changed_at=now if should_activate else None,
                added_at=now,
            )
            db.session.add(stock)
            count += 1

        db.session.commit()
        total = StockUniverse.query.count()
        active = StockUniverse.query.filter_by(in_active_set=True).count()
        print(f'Added {count} stocks ({total} total in universe, {active} in active set)')


if __name__ == '__main__':
    activate = '--activate' in sys.argv
    setup(activate_core=activate)
