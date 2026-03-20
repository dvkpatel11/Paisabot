#!/usr/bin/env python
"""CLI script for running research analysis on arbitrary ETF symbols.

Usage:
    python scripts/run_research.py SPY QQQ XLK XLE XLF
    python scripts/run_research.py --all                  # all is_active ETFs
    python scripts/run_research.py SPY QQQ --portfolio-value 50000
    python scripts/run_research.py SPY QQQ --regime trending
    python scripts/run_research.py SPY QQQ --json         # raw JSON output

This script creates a Flask app context so all DB models and Redis are
available, then runs the ResearchRunner service.
"""
from __future__ import annotations

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(
        description='Run research scoring on ETF symbols',
    )
    parser.add_argument(
        'symbols', nargs='*',
        help='ETF symbols to evaluate (e.g. SPY QQQ XLK)',
    )
    parser.add_argument(
        '--all', action='store_true',
        help='Score all is_active ETFs in the universe',
    )
    parser.add_argument(
        '--portfolio-value', type=float, default=100_000.0,
        help='Hypothetical portfolio value (default: 100000)',
    )
    parser.add_argument(
        '--regime', type=str, default=None,
        help='Override market regime (trending, consolidation, risk_off)',
    )
    parser.add_argument(
        '--json', action='store_true', dest='json_output',
        help='Output raw JSON instead of formatted table',
    )
    args = parser.parse_args()

    # Create Flask app context
    from app import create_app
    app = create_app('development')

    with app.app_context():
        from app.extensions import db, redis_client
        from app.research.research_runner import ResearchRunner

        # Resolve symbols
        symbols = args.symbols
        if args.all:
            from app.models.etf_universe import ETFUniverse
            etfs = ETFUniverse.query.filter_by(is_active=True).all()
            symbols = [e.symbol for e in etfs]
            print(f'Scoring all {len(symbols)} active ETFs: {", ".join(symbols)}')

        if not symbols:
            print('Error: provide symbols or use --all', file=sys.stderr)
            parser.print_help()
            sys.exit(1)

        symbols = [s.upper() for s in symbols]

        # Run research
        runner = ResearchRunner(
            redis_client=redis_client,
            db_session=db.session,
        )
        result = runner.run(
            symbols=symbols,
            portfolio_value=args.portfolio_value,
            regime=args.regime,
        )

        if args.json_output:
            print(json.dumps(result.to_dict(), indent=2, default=str))
            return

        # Formatted output
        print(f'\n{"=" * 60}')
        print(f'Research Run: {result.run_id}')
        print(f'Symbols: {", ".join(result.symbols)}')
        print(f'Regime: {result.regime}')
        print(f'Duration: {result.duration_ms}ms')
        print(f'{"=" * 60}\n')

        # Rankings table
        if result.rankings:
            print('Rankings:')
            print(f'  {"Rank":<6}{"Symbol":<8}{"Composite":<12}{"Trend":<10}'
                  f'{"Vol":<10}{"Sent":<10}{"Breadth":<10}{"Liq":<10}')
            print(f'  {"-" * 66}')
            for r in result.rankings:
                print(
                    f'  {r.get("rank", "-"):<6}'
                    f'{r.get("symbol", "?"):<8}'
                    f'{r.get("composite", 0):<12.4f}'
                    f'{r.get("trend_score", 0):<10.4f}'
                    f'{r.get("volatility_regime", 0):<10.4f}'
                    f'{r.get("sentiment_score", 0):<10.4f}'
                    f'{r.get("breadth_score", 0):<10.4f}'
                    f'{r.get("liquidity_score", 0):<10.4f}'
                )
        else:
            print('No rankings produced.')

        # Hypothetical allocations
        if result.hypothetical_weights:
            print(f'\nHypothetical Allocations (${result.portfolio_value:,.0f}):')
            for sym, w in sorted(
                result.hypothetical_weights.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                print(f'  {sym:<8} {w:>7.2%}  (${w * result.portfolio_value:>10,.2f})')

        if result.expected_vol:
            print(f'\nExpected Vol: {result.expected_vol:.2%}')

        # Orders
        if result.hypothetical_orders:
            print(f'\nHypothetical Orders ({len(result.hypothetical_orders)}):')
            for o in result.hypothetical_orders:
                print(
                    f'  {o["side"].upper():<5} {o["symbol"]:<8} '
                    f'${o["notional"]:>10,.2f}  '
                    f'(delta: {o.get("delta_weight", 0):+.4f})'
                )

        if result.errors:
            print(f'\nWarnings: {", ".join(result.errors)}')

        print()


if __name__ == '__main__':
    main()
