from flask import redirect, render_template, url_for
from flask_login import login_required

from app.views import views_bp


# ── Index ────────────────────────────────────────────────────────────────
@views_bp.route('/')
@login_required
def index():
    return redirect(url_for('views.dashboard'))


# ── ETF Strategies ───────────────────────────────────────────────────────
@views_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('etf/dashboard.html')


@views_bp.route('/factors')
@login_required
def factors():
    return render_template('etf/factors.html')


@views_bp.route('/rotation')
@login_required
def rotation():
    return render_template('etf/rotation.html')


@views_bp.route('/execution')
@login_required
def execution():
    return render_template('etf/execution.html')


@views_bp.route('/portfolio')
@login_required
def portfolio():
    return render_template('etf/portfolio.html')


@views_bp.route('/analytics')
@login_required
def analytics():
    return render_template('etf/portfolio.html')


@views_bp.route('/backtest')
@login_required
def backtest():
    return render_template('etf/backtest.html')


# ── Equity Research ──────────────────────────────────────────────────────
@views_bp.route('/stocks')
@login_required
def stock_screener():
    return render_template('stocks/screener.html')


@views_bp.route('/stocks/securities')
@views_bp.route('/stocks/securities/<symbol>')
@login_required
def stock_security(symbol=None):
    return render_template('stocks/security.html', symbol=symbol)


@views_bp.route('/stocks/factors')
@login_required
def stock_factors():
    return render_template('stocks/factors.html')


@views_bp.route('/stocks/backtest')
@login_required
def stock_backtest():
    return render_template('stocks/backtest.html')


# ── Market Intelligence ──────────────────────────────────────────────────
@views_bp.route('/intel/news')
@login_required
def intel_news():
    return render_template('intel/news.html')


@views_bp.route('/intel/data')
@login_required
def intel_data():
    return render_template('intel/data.html')


@views_bp.route('/intel/macro')
@login_required
def intel_macro():
    return render_template('intel/macro.html')


# Legacy routes — redirect to new paths
@views_bp.route('/market')
@login_required
def market():
    return redirect(url_for('views.intel_data'))


@views_bp.route('/bulletin')
@login_required
def bulletin():
    return redirect(url_for('views.intel_data'))


# ── System ───────────────────────────────────────────────────────────────
@views_bp.route('/pipelines')
@login_required
def pipelines():
    return render_template('system/pipelines.html')


@views_bp.route('/config')
@login_required
def config():
    return render_template('system/config.html')


@views_bp.route('/alerts')
@login_required
def alerts():
    return render_template('system/alerts.html')
