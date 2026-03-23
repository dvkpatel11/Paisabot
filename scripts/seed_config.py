"""Seed all default system_config parameters.

Usage: python scripts/seed_config.py
Requires: DATABASE_URL environment variable or docker-compose postgres running.

Uses INSERT ... ON CONFLICT DO NOTHING so it's safe to run multiple times.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from app.extensions import db
from app.models.system_config import SystemConfig

# Full parameter catalog from research/admin_config_reference.md
DEFAULTS = [
    # --- weights (dispersion removed, 15% redistributed) ---
    ('weights', 'weight_trend', '0.30', 'float', 'Trend factor weight'),
    ('weights', 'weight_volatility', '0.25', 'float', 'Volatility regime weight'),
    ('weights', 'weight_sentiment', '0.15', 'float', 'Sentiment factor weight'),
    ('weights', 'weight_breadth', '0.15', 'float', 'Market breadth weight'),
    ('weights', 'weight_liquidity', '0.15', 'float', 'Liquidity/slippage weight'),
    ('weights', 'weights_sum_constraint', 'enforce', 'string', 'Validate sum = 1.0 on save'),

    # --- stock weights (fundamentals-heavy composite) ---
    ('weights_stock', 'weight_trend', '0.20', 'float', 'Stock: trend factor weight'),
    ('weights_stock', 'weight_volatility', '0.15', 'float', 'Stock: volatility regime weight'),
    ('weights_stock', 'weight_sentiment', '0.15', 'float', 'Stock: sentiment factor weight'),
    ('weights_stock', 'weight_liquidity', '0.10', 'float', 'Stock: liquidity factor weight'),
    ('weights_stock', 'weight_fundamentals', '0.25', 'float', 'Stock: fundamentals factor weight'),
    ('weights_stock', 'weight_earnings', '0.15', 'float', 'Stock: earnings factor weight'),
    ('weights_stock', 'weights_sum_constraint', 'enforce', 'string', 'Validate sum = 1.0 on save'),

    # --- universe ---
    ('universe', 'min_aum_bn', '2.0', 'float', 'Minimum AUM in billions'),
    ('universe', 'min_avg_daily_vol_m', '20.0', 'float', 'Min 30-day avg daily volume ($M)'),
    ('universe', 'max_spread_bps', '10.0', 'float', 'Max bid-ask spread (bps)'),
    ('universe', 'require_options_market', 'true', 'bool', 'Must have listed options'),
    ('universe', 'min_history_days', '504', 'int', 'Min trading history (2 years)'),
    ('universe', 'excluded_etfs', '', 'string', 'Comma-separated symbols to exclude'),
    ('universe', 'manual_inclusions', '', 'string', 'Force-include regardless of filters'),

    # --- portfolio ---
    ('portfolio', 'max_positions', '10', 'int', 'Maximum simultaneous positions'),
    ('portfolio', 'max_position_size', '0.05', 'float', 'Max weight per position (5%)'),
    ('portfolio', 'max_sector_exposure', '0.25', 'float', 'Max weight per GICS sector (25%)'),
    ('portfolio', 'min_position_size', '0.01', 'float', 'Min allocation (1%)'),
    ('portfolio', 'rebalance_frequency', 'daily', 'string', 'daily / weekly / monthly'),
    ('portfolio', 'rebalance_time', '09:45', 'string', 'Time for rebalance orders (ET)'),
    ('portfolio', 'turnover_limit_pct', '0.50', 'float', 'Max one-way turnover per rebalance'),
    ('portfolio', 'cash_buffer_pct', '0.05', 'float', 'Permanent min cash allocation (5%)'),
    ('portfolio', 'optimization_objective', 'max_sharpe', 'string', 'max_sharpe / min_vol / equal_weight / hrp'),

    # --- risk ---
    ('risk', 'max_drawdown', '-0.15', 'float', 'Halt if portfolio drawdown < this'),
    ('risk', 'daily_loss_limit', '-0.03', 'float', 'Halt if single-day loss < this'),
    ('risk', 'position_stop_loss', '-0.05', 'float', 'Exit position if loss from entry < this'),
    ('risk', 'position_trailing_stop', '-0.08', 'float', 'Exit if decline from HWM < this'),
    ('risk', 'vol_target', '0.12', 'float', 'Target annualized portfolio volatility'),
    ('risk', 'vol_scaling_enabled', 'true', 'bool', 'Scale positions when vol > target'),
    ('risk', 'var_confidence', '0.95', 'float', 'VaR confidence level'),
    ('risk', 'var_limit_pct', '0.02', 'float', 'Alert if 1-day VaR exceeds this'),
    ('risk', 'correlation_limit', '0.85', 'float', 'Warn if avg pairwise correlation exceeds this'),
    ('risk', 'alert_drawdown_warn', '-0.08', 'float', 'Warning alert threshold'),
    ('risk', 'alert_drawdown_critical', '-0.12', 'float', 'Critical alert threshold'),

    # --- execution ---
    ('execution', 'broker', 'alpaca_paper', 'string', 'alpaca_paper / alpaca_live / mt5'),
    ('execution', 'order_type', 'market', 'string', 'market / limit / vwap'),
    ('execution', 'limit_slippage_bps', '5.0', 'float', 'For limit orders: offset from mid (bps)'),
    ('execution', 'execution_window_minutes', '30', 'int', 'Spread execution over N min after open'),
    ('execution', 'use_fractional_shares', 'true', 'bool', 'Enable fractional shares (Alpaca)'),
    ('execution', 'min_order_notional', '100', 'float', 'Min order size in dollars'),
    ('execution', 'max_order_notional', '50000', 'float', 'Max single order size in dollars'),
    ('execution', 'max_slippage_bps', '8.0', 'float', 'Block order if estimated slippage > this'),
    ('execution', 'pre_trade_liquidity_check', 'true', 'bool', 'Verify ADV before submitting'),
    ('execution', 'min_trade_threshold_pct', '0.005', 'float', 'Skip rebalance legs < 50 bps'),
    ('execution', 'allow_short', 'false', 'bool', 'Allow short signals (risk_off only)'),
    ('execution', 'mt5_gateway_url', 'http://localhost:8001', 'string', 'MT5 gateway microservice URL'),
    ('execution', 'mt5_deviation', '30', 'int', 'MT5 max price deviation in points'),
    ('execution', 'mt5_magic_number', '100001', 'int', 'MT5 EA magic number for Paisabot orders'),

    # --- data ---
    ('data', 'price_data_provider', 'alpaca', 'string', 'alpaca / polygon / databento'),
    ('data', 'news_data_provider', 'finnhub', 'string', 'finnhub / newsapi / eodhd'),
    ('data', 'sentiment_model', 'finbert', 'string', 'finbert / vader'),
    ('data', 'options_data_provider', 'cboe', 'string', 'cboe / tradier'),
    ('data', 'vix_data_source', 'fred', 'string', 'fred / cboe'),

    # --- scheduling ---
    ('scheduling', 'bar_fetch_interval_sec', '60', 'int', 'Intraday bar refresh interval'),
    ('scheduling', 'intraday_factor_interval_min', '5', 'int', 'Intraday fast factors interval'),
    ('scheduling', 'sentiment_update_interval_min', '15', 'int', 'Sentiment refresh interval'),
    ('scheduling', 'daily_compute_time', '16:30', 'string', 'EOD full factor recompute (ET)'),
    ('scheduling', 'pre_open_check_time', '09:15', 'string', 'Pre-market validation time (ET)'),
    ('scheduling', 'rebalance_enabled', 'true', 'bool', 'Master toggle for rebalancing'),
    ('scheduling', 'backfill_days_on_start', '30', 'int', 'Historical bars to backfill on cold start'),
    ('scheduling', 'market_calendar', 'NYSE', 'string', 'Trading calendar for market hours'),

    # --- alerts ---
    ('alerts', 'alert_channels', 'email,slack', 'string', 'Comma-separated alert channels'),
    ('alerts', 'webhook_url', '', 'string', 'Slack/Discord webhook URL'),
    ('alerts', 'alert_email', '', 'string', 'Alert recipient email'),
    ('alerts', 'alert_on_trade', 'false', 'bool', 'Notify on every trade'),
    ('alerts', 'alert_on_rebalance', 'true', 'bool', 'Notify on each rebalance'),
    ('alerts', 'alert_on_regime_change', 'true', 'bool', 'Notify on regime change'),
    ('alerts', 'alert_factor_stale_minutes', '30', 'int', 'Alert if factor not updated in N min'),
    ('alerts', 'alert_sharpe_min', '0.5', 'float', 'Alert if rolling Sharpe drops below'),

    # --- system ---
    ('system', 'operational_mode', 'simulation', 'string', 'research / simulation / live'),
]


def seed():
    app = create_app('development')
    with app.app_context():
        count = 0
        for category, key, value, value_type, description in DEFAULTS:
            existing = SystemConfig.query.filter_by(
                category=category, key=key
            ).first()
            if not existing:
                db.session.add(SystemConfig(
                    category=category,
                    key=key,
                    value=value,
                    value_type=value_type,
                    description=description,
                    updated_by='seed',
                ))
                count += 1
        db.session.commit()
        print(f'Seeded {count} config parameters ({len(DEFAULTS)} total, {len(DEFAULTS) - count} already existed)')


if __name__ == '__main__':
    seed()
