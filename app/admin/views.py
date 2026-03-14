from __future__ import annotations

from flask import redirect, url_for
from flask_admin import Admin, AdminIndexView, BaseView, expose
from flask_admin.contrib.sqla import ModelView

from app.extensions import db, redis_client


class PaisabotAdminIndex(AdminIndexView):
    """Custom admin index showing system summary."""

    @expose('/')
    def index(self):
        from app.models.positions import Position
        from app.models.trades import Trade

        # Quick stats
        open_positions = Position.query.filter_by(status='open').count()
        recent_trades = Trade.query.order_by(Trade.trade_time.desc()).limit(10).all()

        # Kill switch states
        kill_switches = {}
        for switch in ('trading', 'rebalance', 'all', 'force_liquidate'):
            val = redis_client.get(f'kill_switch:{switch}')
            kill_switches[switch] = val == b'1'

        # Operational mode
        mode = redis_client.hget('config:system', 'operational_mode')
        mode = mode.decode() if isinstance(mode, bytes) else (mode or 'unknown')

        return self.render(
            'admin/index.html',
            open_positions=open_positions,
            recent_trades=recent_trades,
            kill_switches=kill_switches,
            operational_mode=mode,
        )


class SystemConfigView(ModelView):
    """Admin CRUD for system_config table with Redis sync on save."""

    column_list = ['category', 'key', 'value', 'value_type', 'description', 'updated_at', 'updated_by']
    column_searchable_list = ['category', 'key', 'description']
    column_filters = ['category', 'value_type', 'updated_by']
    column_editable_list = ['value']
    column_default_sort = [('category', False), ('key', False)]
    page_size = 50

    def after_model_change(self, form, model, is_created):
        """Sync to Redis after any change."""
        redis_client.hset(f'config:{model.category}', model.key, model.value)

    def after_model_delete(self, model):
        """Remove from Redis on delete."""
        redis_client.hdel(f'config:{model.category}', model.key)


class TradeView(ModelView):
    """Read-only admin view for trade audit trail."""

    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        'symbol', 'side', 'order_type', 'requested_notional',
        'fill_price', 'slippage_bps', 'status', 'trade_time', 'regime',
    ]
    column_default_sort = ('trade_time', True)
    column_filters = ['symbol', 'side', 'status', 'regime']
    page_size = 50


class PositionView(ModelView):
    """Admin view for positions."""

    column_list = [
        'symbol', 'direction', 'entry_price', 'current_price',
        'quantity', 'weight', 'unrealized_pnl', 'sector', 'status',
    ]
    column_filters = ['status', 'direction', 'sector']
    column_default_sort = ('opened_at', True)
    can_create = False
    page_size = 50


class ETFUniverseView(ModelView):
    """Admin view for ETF universe management."""

    column_list = [
        'symbol', 'name', 'sector', 'aum_bn', 'avg_daily_vol_m',
        'spread_est_bps', 'liquidity_score', 'is_active',
    ]
    column_editable_list = ['is_active']
    column_filters = ['sector', 'is_active']
    column_searchable_list = ['symbol', 'name']
    column_default_sort = ('symbol', False)


class FactorScoreView(ModelView):
    """Read-only view of factor scores."""

    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        'symbol', 'calc_time', 'trend_score', 'volatility_score',
        'sentiment_score', 'breadth_score', 'dispersion_score',
        'liquidity_score',
    ]
    column_default_sort = ('calc_time', True)
    column_filters = ['symbol']
    page_size = 50


class SignalView(ModelView):
    """Read-only view of signals."""

    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        'symbol', 'signal_time', 'composite_score', 'signal_type',
        'regime', 'regime_confidence', 'block_reason',
    ]
    column_default_sort = ('signal_time', True)
    column_filters = ['symbol', 'signal_type', 'regime']
    page_size = 50


class KillSwitchView(BaseView):
    """Custom view for kill switch management."""

    @expose('/')
    def index(self):
        switches = {}
        for name in ('trading', 'rebalance', 'all', 'force_liquidate', 'sentiment', 'maintenance'):
            val = redis_client.get(f'kill_switch:{name}')
            switches[name] = val == b'1'

        return self.render('admin/killswitch.html', switches=switches)

    @expose('/toggle/<switch_name>', methods=['POST'])
    def toggle(self, switch_name):
        valid = ('trading', 'rebalance', 'all', 'force_liquidate', 'sentiment', 'maintenance')
        if switch_name not in valid:
            return redirect(url_for('.index'))

        current = redis_client.get(f'kill_switch:{switch_name}')
        new_val = '0' if current == b'1' else '1'
        redis_client.set(f'kill_switch:{switch_name}', new_val)

        return redirect(url_for('.index'))


class PerformanceView(ModelView):
    """Read-only view of daily performance metrics."""

    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        'date', 'portfolio_value', 'daily_return', 'cumulative_return',
        'drawdown', 'sharpe_30d', 'volatility_30d', 'var_95', 'regime',
    ]
    column_default_sort = ('date', True)
    page_size = 50


def init_admin(app):
    """Initialize Flask-Admin with all model views."""
    from app.models.etf_universe import ETFUniverse
    from app.models.factor_scores import FactorScore
    from app.models.performance import PerformanceMetric
    from app.models.positions import Position
    from app.models.signals import Signal
    from app.models.system_config import SystemConfig
    from app.models.trades import Trade

    admin = Admin(
        app,
        name='Paisabot Admin',
        template_mode='bootstrap4',
        index_view=PaisabotAdminIndex(),
    )

    admin.add_view(SystemConfigView(SystemConfig, db.session, name='Config', category='System'))
    admin.add_view(KillSwitchView(name='Kill Switches', endpoint='killswitch', category='System'))
    admin.add_view(ETFUniverseView(ETFUniverse, db.session, name='ETF Universe', category='Data'))
    admin.add_view(FactorScoreView(FactorScore, db.session, name='Factor Scores', category='Data'))
    admin.add_view(SignalView(Signal, db.session, name='Signals', category='Data'))
    admin.add_view(TradeView(Trade, db.session, name='Trades', category='Execution'))
    admin.add_view(PositionView(Position, db.session, name='Positions', category='Execution'))
    admin.add_view(PerformanceView(PerformanceMetric, db.session, name='Performance', category='Analytics'))

    return admin
