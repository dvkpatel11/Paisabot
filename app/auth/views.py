"""Login/logout routes."""
from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required

from app.auth import AdminUser

auth_bp = Blueprint('auth_bp', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if AdminUser.check_credentials(username, password):
            user = AdminUser()
            login_user(user)
            next_page = request.args.get('next', '/')
            return redirect(next_page)
        flash('Invalid credentials', 'error')
    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth_bp.login_page'))
