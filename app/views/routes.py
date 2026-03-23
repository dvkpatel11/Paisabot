from flask import redirect, render_template, url_for
from flask_login import login_required

from app.views import views_bp


@views_bp.route('/')
@login_required
def index():
    return redirect(url_for('views.dashboard'))


@views_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')


@views_bp.route('/bulletin')
@login_required
def bulletin():
    return render_template('bulletin.html')


@views_bp.route('/execution')
@login_required
def execution():
    return render_template('execution.html')


@views_bp.route('/factors')
@login_required
def factors():
    return render_template('factors.html')


@views_bp.route('/rotation')
@login_required
def rotation():
    return render_template('rotation.html')


@views_bp.route('/portfolio')
@login_required
def portfolio():
    return render_template('analytics.html')


@views_bp.route('/analytics')
@login_required
def analytics():
    return render_template('analytics.html')


@views_bp.route('/backtest')
@login_required
def backtest():
    return render_template('backtest.html')


@views_bp.route('/market')
@login_required
def market():
    return render_template('bulletin.html')


@views_bp.route('/pipelines')
@login_required
def pipelines():
    return render_template('pipelines.html')


@views_bp.route('/config')
@login_required
def config():
    return render_template('config.html')


@views_bp.route('/alerts')
@login_required
def alerts():
    return render_template('alerts.html')
