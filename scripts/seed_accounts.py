"""Seed default accounts for ETF and Stock modes.

Creates one account per asset class with default capital allocation.

Usage:
    python scripts/seed_accounts.py
    python scripts/seed_accounts.py --capital 200000   # custom starting capital
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from app.extensions import db
from app.models.account import Account


DEFAULT_ACCOUNTS = [
    {
        'name': 'ETF Portfolio',
        'asset_class': 'etf',
        'max_positions': 20,
        'max_position_pct': 0.05,
        'max_sector_pct': 0.25,
        'vol_target': 0.12,
    },
    {
        'name': 'Stock Portfolio',
        'asset_class': 'stock',
        'max_positions': 30,
        'max_position_pct': 0.05,
        'max_sector_pct': 0.20,
        'vol_target': 0.15,
    },
]


def seed(capital: float = 100_000):
    app = create_app('development')

    with app.app_context():
        count = 0
        for acct_def in DEFAULT_ACCOUNTS:
            existing = Account.query.filter_by(
                name=acct_def['name'],
                asset_class=acct_def['asset_class'],
            ).first()
            if existing:
                print(f'  Account "{acct_def["name"]}" already exists, skipping')
                continue

            now = datetime.now(timezone.utc)
            account = Account(
                name=acct_def['name'],
                asset_class=acct_def['asset_class'],
                initial_capital=capital,
                cash_balance=capital,
                high_watermark=capital,
                max_positions=acct_def['max_positions'],
                max_position_pct=acct_def['max_position_pct'],
                max_sector_pct=acct_def['max_sector_pct'],
                vol_target=acct_def['vol_target'],
                created_at=now,
            )
            db.session.add(account)
            count += 1

        db.session.commit()
        total = Account.query.count()
        print(f'Seeded {count} accounts ({total} total)')


if __name__ == '__main__':
    capital_amount = 100_000
    if '--capital' in sys.argv:
        idx = sys.argv.index('--capital')
        if idx + 1 < len(sys.argv):
            capital_amount = float(sys.argv[idx + 1])

    seed(capital=capital_amount)
