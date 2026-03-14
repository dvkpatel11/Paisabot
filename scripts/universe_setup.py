"""Populate etf_universe table from research/ETF_universe.csv.

Usage: python scripts/universe_setup.py
"""
import csv
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from app.extensions import db
from app.models.etf_universe import ETFUniverse

# MT5 symbol mapping (broker-dependent, Admiral Markets style)
MT5_SYMBOL_MAP = {
    'SPY': 'SPY.US',
    'QQQ': 'QQQ.US',
    'IWM': 'IWM.US',
    'XLK': 'XLK.US',
    'XLF': 'XLF.US',
    'XLE': 'XLE.US',
    'XLV': 'XLV.US',
    'XLI': 'XLI.US',
    'XLC': 'XLC.US',
    'XLY': 'XLY.US',
    'XLP': 'XLP.US',
    'XLU': 'XLU.US',
    'XLRE': 'XLRE.US',
    'XLB': 'XLB.US',
    'GDX': 'GDX.US',
    'EEM': 'EEM.US',
    'EFA': 'EFA.US',
    'TLT': 'TLT.US',
    'HYG': 'HYG.US',
    'GLD': 'GLD.US',
}


def setup():
    app = create_app('development')
    csv_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'research', 'ETF_universe.csv',
    )

    with app.app_context():
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                symbol = row['ticker'].strip()
                existing = ETFUniverse.query.filter_by(symbol=symbol).first()
                if existing:
                    continue

                etf = ETFUniverse(
                    symbol=symbol,
                    name=row['name'].strip(),
                    sector=row['sector'].strip(),
                    aum_bn=float(row['aum_bn']),
                    avg_daily_vol_m=float(row['avg_daily_vol_m']),
                    spread_est_bps=float(row['spread_est_bps']),
                    liquidity_score=float(row['liquidity_score']),
                    inception_date=date.fromisoformat(row['inception_date'].strip()),
                    options_market=row['options_market'].strip().lower() == 'true',
                    is_active=True,
                    mt5_symbol=MT5_SYMBOL_MAP.get(symbol, f'{symbol}.US'),
                )
                db.session.add(etf)
                count += 1

            db.session.commit()
            total = ETFUniverse.query.count()
            print(f'Added {count} ETFs ({total} total in universe)')


if __name__ == '__main__':
    setup()
