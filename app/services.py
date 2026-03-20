"""Service registry — metadata and tooltips for all async services.

Each service operates independently of the global ``operational_mode``
setting.  They can run concurrently, whether the main Flask app is
on or off.

The ``/api/services`` endpoint exposes this registry for the frontend
to build tooltip-enabled service cards.
"""
from __future__ import annotations

SERVICE_REGISTRY = {
    'research': {
        'name': 'Research Runner',
        'key': 'research',
        'tooltip': (
            'Run production-quality factor scoring and portfolio construction '
            'on any ETF list.  Results include per-factor scores, composite '
            'rankings, hypothetical allocations, and cost-model estimates \u2014 '
            'all without touching the live trading pipeline.'
        ),
        'description': 'Score and rank any ETFs with real production factors',
        'icon': 'flask',  # Font Awesome or Lucide icon name
        'status_key': 'cache:research:latest',
        'channel': 'channel:research',
        'independent': True,
        'requires_broker': False,
        'requires_db': True,
        'requires_redis': True,
        'endpoints': {
            'run': 'POST /api/research/score',
            'latest': 'GET /api/research/latest',
        },
    },
    'simulation': {
        'name': 'Simulation Tracker',
        'key': 'simulation',
        'tooltip': (
            'Paper-trade any strategy with realistic cost-model fills. '
            'Tracks hypothetical positions, PnL, and equity curve over time '
            'without placing real orders.  Run alongside live trading for '
            'strategy comparison.'
        ),
        'description': 'Paper-trade with realistic fills and PnL tracking',
        'icon': 'line-chart',
        'status_key': 'sim:active_session',
        'channel': 'channel:simulation',
        'independent': True,
        'requires_broker': False,
        'requires_db': False,
        'requires_redis': True,
        'endpoints': {
            'session': 'POST /api/simulation/session',
            'execute': 'POST /api/simulation/execute',
            'state': 'GET /api/simulation/state',
            'equity': 'GET /api/simulation/equity',
        },
    },
    'live': {
        'name': 'Live Trading',
        'key': 'live',
        'tooltip': (
            'Execute real orders through Alpaca broker with pre-trade risk '
            'checks, slippage monitoring, and fill tracking.  Requires active '
            'broker connection and admin approval.  Kill switches provide '
            'instant emergency shutdown.'
        ),
        'description': 'Real broker execution with risk controls',
        'icon': 'zap',
        'status_key': 'cache:pipeline:latest',
        'channel': 'channel:trades',
        'independent': False,
        'requires_broker': True,
        'requires_db': True,
        'requires_redis': True,
        'endpoints': {
            'status': 'GET /api/pipeline/status',
        },
    },
}


def get_service_status(redis_client) -> dict[str, dict]:
    """Get the runtime status of each service.

    Returns service metadata + live status for dashboard display.
    """
    statuses = {}
    import json

    for key, svc in SERVICE_REGISTRY.items():
        status_data = {
            **svc,
            'active': False,
            'last_run': None,
        }

        if redis_client is not None:
            try:
                raw = redis_client.get(svc['status_key'])
                if raw:
                    status_data['active'] = True
                    try:
                        parsed = json.loads(raw)
                        status_data['last_run'] = parsed.get(
                            'timestamp', parsed.get('started_at'),
                        )
                    except (json.JSONDecodeError, TypeError):
                        pass
            except Exception:
                pass

        statuses[key] = status_data

    return statuses
