"""REST endpoints for the Simulation Tracker service.

These endpoints are independent of the global ``operational_mode`` —
they manage paper-trading sessions directly.
"""
from __future__ import annotations

import json

from flask import jsonify, request

from app.api import api_bp
from app.auth import api_login_required
from app.extensions import redis_client


@api_bp.route('/simulation/session', methods=['POST'])
@api_login_required
def simulation_create_session():
    """Create a new simulation session.

    Request body (JSON):
        {
            "initial_capital": 100000    // optional, default 100k
        }

    Returns:
        Session state with session_id.
    """
    data = request.get_json(silent=True) or {}
    initial_capital = data.get('initial_capital', 100_000.0)

    from app.simulation.simulation_tracker import SimulationTracker

    tracker = SimulationTracker(redis_client=redis_client)
    state = tracker.create_session(initial_capital=initial_capital)

    return jsonify(state.to_dict()), 201


@api_bp.route('/simulation/execute', methods=['POST'])
@api_login_required
def simulation_execute():
    """Execute orders against the active simulation session.

    Request body (JSON):
        {
            "session_id": "sim_20260315_...",     // optional, uses active
            "orders": [
                {"symbol": "SPY", "side": "buy", "notional": 5000.0, "ref_price": 515.0},
                {"symbol": "XLE", "side": "sell", "notional": 3000.0, "ref_price": 94.0}
            ]
        }

    Returns:
        List of execution results with fill details and cost breakdowns.
    """
    data = request.get_json(silent=True) or {}
    orders = data.get('orders', [])

    if not orders:
        return jsonify({'error': 'orders list is required'}), 400

    from app.simulation.simulation_tracker import SimulationTracker

    tracker = SimulationTracker(redis_client=redis_client)

    session_id = data.get('session_id')
    if session_id:
        state = tracker.load_session(session_id)
    else:
        state = tracker.get_active_session()

    if state is None:
        return jsonify({
            'error': 'no simulation session found — create one first via POST /api/simulation/session',
        }), 404

    results = tracker.execute_batch(state, orders)

    return jsonify({
        'session_id': state.session_id,
        'results': results,
        'portfolio_value': state.portfolio_value,
        'cash': state.cash,
        'n_positions': len(state.positions),
        'realized_pnl': state.realized_pnl,
        'unrealized_pnl': state.unrealized_pnl,
    })


@api_bp.route('/simulation/state', methods=['GET'])
@api_login_required
def simulation_state():
    """Get current state of the active simulation session.

    Query params:
        session_id: specific session (optional, defaults to active).
    """
    from app.simulation.simulation_tracker import SimulationTracker

    tracker = SimulationTracker(redis_client=redis_client)

    session_id = request.args.get('session_id')
    if session_id:
        state = tracker.load_session(session_id)
    else:
        state = tracker.get_active_session()

    if state is None:
        return jsonify({'error': 'no simulation session found'}), 404

    return jsonify(state.to_dict())


@api_bp.route('/simulation/equity', methods=['GET'])
@api_login_required
def simulation_equity():
    """Get the equity curve for a simulation session.

    Query params:
        session_id: specific session (optional, defaults to active).
    """
    from app.simulation.simulation_tracker import SimulationTracker

    tracker = SimulationTracker(redis_client=redis_client)

    session_id = request.args.get('session_id')
    if session_id:
        state = tracker.load_session(session_id)
    else:
        state = tracker.get_active_session()

    if state is None:
        return jsonify({'error': 'no simulation session found'}), 404

    return jsonify({
        'session_id': state.session_id,
        'initial_capital': state.initial_capital,
        'current_value': state.portfolio_value,
        'total_return_pct': round(
            (state.portfolio_value / state.initial_capital - 1) * 100, 2,
        ) if state.initial_capital > 0 else 0,
        'equity_curve': state.equity_curve,
    })


@api_bp.route('/simulation/mark', methods=['POST'])
@api_login_required
def simulation_mark_to_market():
    """Mark all positions to current market prices.

    Request body (JSON):
        {
            "session_id": "sim_20260315_..."    // optional, uses active
        }
    """
    data = request.get_json(silent=True) or {}

    from app.simulation.simulation_tracker import SimulationTracker

    tracker = SimulationTracker(redis_client=redis_client)

    session_id = data.get('session_id')
    if session_id:
        state = tracker.load_session(session_id)
    else:
        state = tracker.get_active_session()

    if state is None:
        return jsonify({'error': 'no simulation session found'}), 404

    state = tracker.mark_to_market(state)

    return jsonify({
        'session_id': state.session_id,
        'portfolio_value': state.portfolio_value,
        'unrealized_pnl': state.unrealized_pnl,
        'realized_pnl': state.realized_pnl,
        'positions': {k: v.to_dict() for k, v in state.positions.items()},
    })
