# Admin Config View — System Configuration Reference

## Overview

All system-level parameters live in the `system_config` PostgreSQL table and are cached in Redis hash keys prefixed `config:*`. The Flask-Admin UI writes to both on save. The factor engine and signal pipeline read from Redis (fast path) with PostgreSQL as the source of truth.

---

## Implementation Pattern

```python
# Flask-Admin view with Redis sync on save
class SystemConfigView(SecureModelView):
    column_list         = ['category', 'key', 'value', 'description', 'updated_at']
    column_filters      = ['category']
    column_editable_list = ['value']

    def on_model_change(self, form, model, is_created):
        redis_client.hset(f'config:{model.category}', model.key, model.value)
        super().on_model_change(form, model, is_created)

    def on_model_delete(self, model):
        redis_client.hdel(f'config:{model.category}', model.key)
        super().on_model_delete(model)
```

---

## Configuration Parameter Catalog

### Category: `weights` — Factor Weights

Redis key: `config:weights`

| Key | Default | Range | Type | Description |
|-----|---------|-------|------|-------------|
| `weight_trend` | 0.25 | 0.0–1.0 | float | Trend factor weight |
| `weight_volatility` | 0.20 | 0.0–1.0 | float | Volatility regime weight |
| `weight_sentiment` | 0.15 | 0.0–1.0 | float | Sentiment factor weight |
| `weight_breadth` | 0.15 | 0.0–1.0 | float | Market breadth weight |
| `weight_dispersion` | 0.15 | 0.0–1.0 | float | Dispersion factor weight |
| `weight_liquidity` | 0.10 | 0.0–1.0 | float | Liquidity/slippage weight |
| `weights_sum_constraint` | `enforce` | `enforce`/`warn` | string | Validate weights sum to 1.0 on save |

**Validation rule**: `weight_trend + weight_volatility + weight_sentiment + weight_breadth + weight_dispersion + weight_liquidity = 1.0` (±0.001 tolerance).

---

### Category: `universe` — ETF Universe Filters

Redis key: `config:universe`

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `min_aum_bn` | 2.0 | float | Minimum AUM in billions |
| `min_avg_daily_vol_m` | 20.0 | float | Minimum 30-day average daily dollar volume ($M) |
| `max_spread_bps` | 10.0 | float | Maximum bid-ask spread in basis points |
| `require_options_market` | true | bool | Must have listed options (for IV / P/C ratio) |
| `min_history_days` | 504 | int | Minimum trading history (2 years = 504 trading days) |
| `excluded_etfs` | `""` | string | Comma-separated symbols to permanently exclude |
| `manual_inclusions` | `""` | string | Force-include specific symbols regardless of filters |

---

### Category: `portfolio` — Portfolio Constraints

Redis key: `config:portfolio`

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `max_positions` | 10 | int | Maximum simultaneous positions |
| `max_position_size` | 0.05 | float | Maximum weight per position (5%) |
| `max_sector_exposure` | 0.25 | float | Maximum weight per GICS sector (25%) |
| `min_position_size` | 0.01 | float | Minimum allocation (avoids micro-positions) |
| `rebalance_frequency` | `daily` | string | `daily` / `weekly` / `monthly` |
| `rebalance_time` | `09:45` | string | Time of day for rebalance orders (ET, HH:MM) |
| `turnover_limit_pct` | 0.50 | float | Max one-way portfolio turnover per rebalance |
| `cash_buffer_pct` | 0.05 | float | Permanent minimum cash allocation |
| `optimization_objective` | `max_sharpe` | string | `max_sharpe` / `min_vol` / `equal_weight` / `hrp` |

---

### Category: `risk` — Risk Parameters

Redis key: `config:risk`

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `max_drawdown` | -0.15 | float | Halt trading if portfolio drawdown < this |
| `daily_loss_limit` | -0.03 | float | Halt if single-day portfolio loss < this |
| `position_stop_loss` | -0.05 | float | Exit position if loss from entry < this |
| `position_trailing_stop` | -0.08 | float | Exit if decline from high-water mark < this |
| `vol_target` | 0.12 | float | Target annualized portfolio volatility (12%) |
| `vol_scaling_enabled` | true | bool | Scale positions down when portfolio vol > vol_target |
| `var_confidence` | 0.95 | float | VaR confidence level |
| `var_limit_pct` | 0.02 | float | Alert if 1-day VaR exceeds 2% of portfolio |
| `correlation_limit` | 0.85 | float | Warn if avg portfolio pairwise correlation exceeds this |
| `alert_drawdown_warn` | -0.08 | float | Warning alert threshold (before halt) |
| `alert_drawdown_critical` | -0.12 | float | Critical alert threshold (before halt) |

---

### Category: `execution` — Execution Settings

Redis key: `config:execution`

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `broker` | `alpaca_paper` | string | `alpaca_paper` / `alpaca_live` / `ib` |
| `order_type` | `market` | string | `market` / `limit` / `vwap` |
| `limit_slippage_bps` | 5.0 | float | For limit orders: offset from mid in bps |
| `execution_window_minutes` | 30 | int | Spread execution over N minutes after open |
| `use_fractional_shares` | true | bool | Enable fractional share orders (Alpaca only) |
| `min_order_notional` | 100 | float | Minimum order size in dollars |
| `max_order_notional` | 50000 | float | Maximum single order size in dollars |
| `pre_trade_liquidity_check` | true | bool | Verify ADV before submitting order |
| `min_trade_threshold_pct` | 0.005 | float | Skip rebalance legs smaller than 50 bps |

---

### Category: `data` — Data Source Settings

Redis key: `config:data`
*API keys stored encrypted using Fernet; masked in admin UI display.*

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `price_data_provider` | `alpaca` | string | `alpaca` / `polygon` / `databento` |
| `news_data_provider` | `finnhub` | string | `finnhub` / `newsapi` / `eodhd` |
| `sentiment_model` | `finbert` | string | `finbert` / `vader` (vader = CPU, no GPU needed) |
| `options_data_provider` | `polygon` | string | `polygon` / `tradier` |
| `vix_data_source` | `cboe` | string | `cboe` (daily file) / `polygon` (real-time) / `fred` |
| `alpaca_api_key` | `""` | secret | Encrypted at rest |
| `alpaca_secret_key` | `""` | secret | Encrypted at rest |
| `polygon_api_key` | `""` | secret | Encrypted at rest |
| `finnhub_api_key` | `""` | secret | Encrypted at rest |
| `newsapi_key` | `""` | secret | Encrypted at rest |
| `databento_api_key` | `""` | secret | Encrypted at rest |

---

### Category: `scheduling` — Job Schedules

Redis key: `config:scheduling`

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `bar_fetch_interval_sec` | 60 | int | Intraday bar refresh interval (seconds) |
| `intraday_factor_interval_min` | 5 | int | Intraday fast factors (vol, liquidity) |
| `sentiment_update_interval_min` | 15 | int | News + Reddit sentiment refresh |
| `daily_compute_time` | `16:30` | string | EOD full factor recompute (ET, HH:MM) |
| `pre_open_check_time` | `09:15` | string | Pre-market data validation time |
| `rebalance_enabled` | true | bool | Master toggle for all rebalancing |
| `backfill_days_on_start` | 30 | int | Historical bars to backfill on cold start |
| `market_calendar` | `NYSE` | string | Trading calendar for market hours check |

---

### Category: `alerts` — Alert Thresholds

Redis key: `config:alerts`

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `alert_channels` | `email,slack` | string | Comma-separated: `email` / `slack` / `webhook` |
| `webhook_url` | `""` | string | Slack/Discord webhook URL |
| `alert_email` | `""` | string | Alert recipient email address |
| `alert_on_trade` | false | bool | Notify on every trade execution |
| `alert_on_rebalance` | true | bool | Notify on each rebalance cycle |
| `alert_on_regime_change` | true | bool | Notify when market regime changes |
| `alert_factor_stale_minutes` | 30 | int | Alert if any factor not updated in N minutes |
| `alert_sharpe_min` | 0.5 | float | Alert if rolling 60-day Sharpe drops below |

---

### Category: `kill_switches` — Trading Halts

Redis keys: direct `kill_switch:*` (not under `config:` prefix for performance)

| Redis Key | Default | Description | Recovery |
|-----------|---------|-------------|----------|
| `kill_switch:all` | `0` | Stop ALL operations | Manual reset by admin |
| `kill_switch:trading` | `0` | Pause order submission; keep computing signals | Manual reset; required after drawdown halt |
| `kill_switch:rebalance` | `0` | Hold current positions; no new rebalances | Manual reset |
| `kill_switch:sentiment` | `0` | Disable sentiment factor; use neutral 0.5 | Manual reset |
| `kill_switch:maintenance` | `0` | Read-only; display and compute only | Manual reset |
| `kill_switch:force_liquidate` | `0` | **DANGER**: Send sell orders for all open positions | Auto-resets to 0 after execution |

**Admin UI kill switch page** should display each switch as a large toggle with:
- Current status (red = active / green = inactive)
- Who last changed it and when
- Confirmation dialog before activating `force_liquidate`

---

## Config Loading Pattern (Application Code)

```python
class ConfigLoader:
    CACHE_TTL = 30  # seconds; short TTL for kill switches

    def __init__(self, redis_client, db_session):
        self.redis = redis_client
        self.db    = db_session

    def get(self, category: str, key: str, default=None):
        """Read from Redis; fall back to DB on cache miss."""
        value = self.redis.hget(f'config:{category}', key)
        if value is not None:
            return value.decode()
        # DB fallback
        row = self.db.query(SystemConfig).filter_by(category=category, key=key).first()
        if row:
            self.redis.hset(f'config:{category}', key, row.value)
            return row.value
        return default

    def get_float(self, category: str, key: str, default: float = 0.0) -> float:
        return float(self.get(category, key, default))

    def get_bool(self, category: str, key: str, default: bool = False) -> bool:
        val = self.get(category, key, str(default))
        return val.lower() in ('1', 'true', 'yes')

    def is_kill_switch_active(self, switch: str) -> bool:
        return self.redis.get(f'kill_switch:{switch}') == b'1'

    def warm_cache(self):
        """Load all config from DB into Redis on application start."""
        rows = self.db.query(SystemConfig).all()
        pipe = self.redis.pipeline()
        for row in rows:
            pipe.hset(f'config:{row.category}', row.key, row.value)
        pipe.execute()
```

---

## Seed Script

Run once to populate the `system_config` table with all defaults:

```bash
python scripts/seed_config.py
```

The seed script inserts every parameter from this reference with its default value. Existing rows are skipped (`INSERT ... ON CONFLICT DO NOTHING`).
