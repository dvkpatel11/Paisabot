from flask import redirect, render_template, url_for

from app.views import views_bp


@views_bp.route('/')
def index():
    return redirect(url_for('views.dashboard'))


@views_bp.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')


@views_bp.route('/factors')
def factors():
    return render_template('factors.html')


@views_bp.route('/rotation')
def rotation():
    return render_template('rotation.html')


@views_bp.route('/execution')
def execution():
    return render_template('execution.html')


@views_bp.route('/analytics')
def analytics():
    return render_template('analytics.html')


@views_bp.route('/alerts')
def alerts():
    return render_template('alerts.html')


@views_bp.route('/pipelines')
def pipelines():
    return render_template('pipelines.html')


@views_bp.route('/config')
def config():
    return render_template('config.html')
