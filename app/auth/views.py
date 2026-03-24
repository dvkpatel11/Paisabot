"""Login/logout routes — JSON-only (no frontend templates)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, redirect, url_for
from flask_login import login_user, logout_user, login_required

from app.auth import AdminUser

auth_bp = Blueprint('auth_bp', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        # Accept both form-encoded and JSON bodies
        if request.is_json:
            data = request.get_json()
            username = data.get('username', '')
            password = data.get('password', '')
        else:
            username = request.form.get('username', '')
            password = request.form.get('password', '')

        if AdminUser.check_credentials(username, password):
            user = AdminUser()
            login_user(user)
            next_page = request.args.get('next', '/')
            if request.is_json:
                return jsonify(status='ok', redirect=next_page)
            return redirect(next_page)

        if request.is_json:
            return jsonify(error='invalid_credentials'), 401
        return jsonify(error='invalid_credentials', message='POST credentials to this endpoint'), 401

    return jsonify(message='POST username/password to authenticate'), 200


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return jsonify(status='logged_out')
