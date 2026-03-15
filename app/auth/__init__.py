"""Authentication module using Flask-Login.

Single admin user with credentials from environment variables.
Protects write endpoints and admin routes.
"""
from __future__ import annotations

import os
from functools import wraps

from flask import redirect, url_for, request, jsonify
from flask_login import LoginManager, UserMixin, current_user

login_manager = LoginManager()
login_manager.login_view = 'auth_bp.login_page'


class AdminUser(UserMixin):
    """Single admin user backed by env vars."""

    def __init__(self):
        self.id = 'admin'

    @staticmethod
    def check_credentials(username: str, password: str) -> bool:
        expected_user = os.environ.get('ADMIN_USERNAME', 'admin')
        expected_pass = os.environ.get('ADMIN_PASSWORD', '')
        if not expected_pass:
            return False
        return username == expected_user and password == expected_pass


_admin = AdminUser()


@login_manager.user_loader
def load_user(user_id: str):
    if user_id == 'admin':
        return _admin
    return None


def api_login_required(f):
    """Decorator for API endpoints — returns 401 JSON instead of redirect."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'authentication_required'}), 401
        return f(*args, **kwargs)
    return decorated
