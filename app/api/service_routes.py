"""REST endpoints for the service registry and tooltip metadata.

Exposes service metadata so the frontend can build tooltip-enabled
service cards for Research, Simulation, and Live trading.
"""
from __future__ import annotations

from flask import jsonify

from app.api import api_bp
from app.auth import api_login_required
from app.extensions import redis_client
from app.services import SERVICE_REGISTRY, get_service_status


@api_bp.route('/services', methods=['GET'])
@api_login_required
def list_services():
    """List all async services with tooltips and live status.

    Returns:
        {
            "research": { name, tooltip, description, active, ... },
            "simulation": { ... },
            "live": { ... }
        }
    """
    statuses = get_service_status(redis_client)
    return jsonify(statuses)


@api_bp.route('/services/<service_key>/tooltip', methods=['GET'])
@api_login_required
def service_tooltip(service_key: str):
    """Get tooltip text for a specific service."""
    svc = SERVICE_REGISTRY.get(service_key)
    if svc is None:
        return jsonify({'error': f'unknown service: {service_key}'}), 404

    return jsonify({
        'key': service_key,
        'name': svc['name'],
        'tooltip': svc['tooltip'],
        'description': svc['description'],
    })
