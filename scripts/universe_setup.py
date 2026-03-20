"""Populate etf_universe table from research/ETF_universe.csv.

All ETFs start as watchlist-only (is_active=True, in_active_set=False).
Use the API or --activate flag to move ETFs into the active trading set.

Usage:
    python scripts/universe_setup.py              # watchlist only
    python scripts/universe_setup.py --activate   # also activate core ETFs
"""
import csv
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from app.extensions import db
from app.models.etf_universe import ETFUniverse

# Core ETFs to activate by default when --activate is passed
DEFAULT_ACTIVE_SET = {
    'SPY', 'QQQ', 'IWM',
    'XLK', 'XLF', 'XLE', 'XLV', 'XLI',
    'XLC', 'XLY', 'XLP', 'XLU', 'XLB',
}

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


def setup(activate_core: bool = False):
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

                should_activate = activate_core and symbol in DEFAULT_ACTIVE_SET
                now = datetime.now(timezone.utc)

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
                    in_active_set=should_activate,
                    active_set_reason='initial_setup' if should_activate else None,
                    active_set_changed_at=now if should_activate else None,
                    added_at=now,
                    mt5_symbol=MT5_SYMBOL_MAP.get(symbol, f'{symbol}.US'),
                )
                db.session.add(etf)
                count += 1

            db.session.commit()
            total = ETFUniverse.query.count()
            active = ETFUniverse.query.filter_by(in_active_set=True).count()
            print(f'Added {count} ETFs ({total} total in universe, {active} in active set)')


if __name__ == '__main__':
    activate = '--activate' in sys.argv
    setup(activate_core=activate)
