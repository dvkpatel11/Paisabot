"""REST endpoints for the Research Runner service.

These endpoints are independent of the global ``operational_mode`` —
they call the ResearchRunner service directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import jsonify, request

from app.api import api_bp
from app.auth import api_login_required
from app.extensions import db, redis_client


@api_bp.route('/research/score', methods=['POST'])
@api_login_required
def research_score():
    """Run research scoring on the given symbols.

    Request body (JSON):
        {
            "symbols": ["SPY", "QQQ", "XLK"],
            "portfolio_value": 100000,     // optional, default 100k
            "regime": "trending",          // optional, auto-detected
            "custom_weights": {...}        // optional factor weight overrides
        }

    Returns:
        Full ResearchResult with rankings, allocations, and factor scores.
    """
    data = request.get_json(silent=True) or {}
    symbols = data.get('symbols', [])

    if not symbols:
        return jsonify({'error': 'symbols list is required'}), 400

    symbols = [s.upper().strip() for s in symbols if s.strip()]
    if not symbols:
        return jsonify({'error': 'no valid symbols provided'}), 400

    from app.research.research_runner import ResearchRunner

    runner = ResearchRunner(
        redis_client=redis_client,
        db_session=db.session,
    )
    result = runner.run(
        symbols=symbols,
        portfolio_value=data.get('portfolio_value', 100_000.0),
        regime=data.get('regime'),
        custom_weights=data.get('custom_weights'),
    )

    return jsonify(result.to_dict())


@api_bp.route('/research/latest', methods=['GET'])
@api_login_required
def research_latest():
    """Get the most recent research run result from cache."""
    if redis_client is None:
        return jsonify({'error': 'redis not available'}), 503

    raw = redis_client.get('cache:research:latest')
    if raw is None:
        return jsonify({'error': 'no research results cached'}), 404

    return jsonify(json.loads(raw))


@api_bp.route('/research/symbols', methods=['GET'])
@api_login_required
def research_available_symbols():
    """List all available symbols for research (all is_active ETFs).

    Query params:
        include_inactive: if 'true', include all ETFs in universe.
    """
    from app.models.etf_universe import ETFUniverse

    include_inactive = request.args.get('include_inactive', '').lower() == 'true'

    if include_inactive:
        etfs = ETFUniverse.query.all()
    else:
        etfs = ETFUniverse.query.filter_by(is_active=True).all()

    result = [
        {
            'symbol': e.symbol,
            'name': e.name,
            'sector': e.sector,
            'is_active': e.is_active,
            'in_active_set': e.in_active_set,
        }
        for e in etfs
    ]

    return jsonify(result)
