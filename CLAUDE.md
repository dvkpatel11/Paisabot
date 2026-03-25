# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Paisabot** — a systematic multi-asset trading strategy bot that detects sector rotation and trending market regimes. Supports two asset classes as first-class citizens:

- **ETF mode** — 7 factors (Trend, Volatility, Sentiment, Correlation, Breadth, Liquidity, Slippage) across liquid ETFs (SPY, QQQ, XLK, XLE, XLF, XLV, etc.)
- **Stock mode** — 6 factors (Trend, Volatility, Sentiment, Liquidity, Fundamentals, Earnings) across individual equities

Stack: **Python + Flask + PostgreSQL + Redis + WebSocket** (Flask-SocketIO) backend. **React 18 + TypeScript + Vite + TailwindCSS** frontend SPA.

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt  # core + CPU torch + transformers
# pip install -r requirements-prod.txt  # GPU/CUDA for FinBERT (Linux prod)
cp .env.example .env              # Fill in API keys

# Database
docker compose up -d              # starts PostgreSQL + Redis (infra only)
alembic upgrade head
python scripts/seed_config.py     # seeds default system_config values
python scripts/universe_setup.py  # populates ETF universe table
python scripts/backfill_history.py  # loads historical bars

# Run backend
flask run                         # development server
celery -A celery_worker worker    # background task worker

# Run frontend
cd frontend
npm install
npm run dev                       # Vite dev server
```

## Development Commands

```bash
pytest                            # all tests (78 test files)
pytest tests/test_factors/        # single module
pytest tests/test_factors/test_volatility.py::test_zscore  # single test

flask shell                       # REPL with app context

# Redis inspection
redis-cli hgetall config:weights
redis-cli keys "kill_switch:*"
```

## Architecture

See [research/system_architecture.md](research/system_architecture.md) for the canonical design. See [research/architecture_and_stack.md](research/architecture_and_stack.md) for the implementation reference.

### Backend Modules (pipeline order)

1. **Market Data Layer** (`app/data/`) — Alpaca WebSocket real-time bars; Finnhub news; Reddit sentiment via PRAW; CBOE VIX + put/call ratio; yfinance fallback; FMP fundamentals. Celery for historical backfill. Outputs to PostgreSQL + Redis cache + `channel:bars` pub/sub
2. **Factor Engine** (`app/factors/`) — `FactorBase.compute()` returning scores in [0,1]. Asset-class-aware via `FactorRegistry`. Daily EOD recompute + intraday fast factors (vol, liquidity) every 5 min. Outputs to `channel:factor_scores`
3. **Signal Engine** (`app/signals/`) — `CompositeScorer` → `RegimeDetector` → `SignalFilter` → `SignalGenerator`. Produces long/short candidates + signal confidence. 4 regimes: trending, rotation, risk_off, consolidation. Outputs to `channel:signals`
4. **Portfolio Construction Engine** (`app/portfolio/`) — PyPortfolioOpt (max_sharpe, min_vol, HRP) with Ledoit-Wolf covariance shrinkage; sector (max 25%) and position (max 5%) constraints; volatility targeting to 12% annualized; turnover limit 50%. Pushes proposed orders to `channel:orders_proposed` LIST queue
5. **Risk Engine** (`app/risk/`) — pre-trade gate (approves/blocks orders) + continuous monitor every 5 min (drawdown -15% halt, stop-loss -5%/-8% trailing, VaR 95%, correlation shock with consecutive-day tracking). Sets `kill_switch:*` keys on breach
6. **Execution Engine** (`app/execution/`) — Alpaca paper/live trading via `alpaca-py`; MT5 broker class (Windows-only, defined but untested); fill tracking; Almgren-Chriss slippage estimation + post-trade measurement. Behavior gated by operational mode
7. **Streaming & API Layer** (`app/streaming/`, `app/api/`) — RedisBridge relays pub/sub → Socket.IO `/dashboard` namespace; 5 REST API route modules (40+ endpoints); Flask-Login auth

### Pipeline Orchestration (`app/pipeline/`)

5-stage Celery chain triggered EOD (6:15pm ET for ETFs, 6:20pm for stocks):
```
stage_load_data → stage_portfolio → stage_risk_gate → stage_execute → stage_record
```
Both ETF and stock pipelines run identical chain structure, parameterized by `asset_class`. On error, sets `kill_switch:rebalance = 1` and publishes to `channel:risk_alerts`.

### Additional Modules

- **Backtesting** (`app/backtesting/`) — `VectorizedBacktester` with vol targeting, drawdown halt, trailing stops, sector constraints, turnover limits, Almgren-Chriss slippage model, SPY benchmark (alpha/beta/IR/tracking error), Sortino + Calmar ratios
- **Research** (`app/research/`) — Research mode runner for historical analysis
- **Simulation** (`app/simulation/`) — Paper trading simulation tracker
- **Auth** (`app/auth/`) — Flask-Login single admin user (env-driven credentials)

### Frontend (`frontend/`)

React 18 SPA with TypeScript, Vite build, TailwindCSS styling. Key dependencies:
- **State**: Zustand
- **Routing**: TanStack Router
- **Tables**: TanStack Table
- **Charts**: Plotly.js + lightweight-charts (TradingView)
- **WebSocket**: socket.io-client
- **UI**: Lucide icons, cmdk command palette

Components: Shell, Topbar, Sidebar, StatusBar, CommandPalette, AlertsPanel, ScoreBar, PnlText, RegimeBadge.

### Database Models (13 tables)

`SystemConfig`, `PriceBar`, `FactorScore`, `Signal`, `ETFUniverse`, `StockUniverse`, `Account`, `Position`, `Trade`, `Quote`, `PerformanceMetric`, `SentimentRaw`, `OptionsChain`

Asset class column (`etf`/`stock`) on: `PriceBar`, `FactorScore`, `Signal`, `Position`, `Trade`.

## API Routes

5 route modules under `app/api/`:

| Module | Endpoints |
|--------|-----------|
| `routes.py` (2038 lines) | `/api/health`, `/api/scores`, `/api/signals`, `/api/regime`, `/api/factors/<symbol>`, `/api/portfolio`, `/api/risk`, `/api/trades`, `/api/config/*`, `/api/control/*`, `/api/universe`, `/api/data/*`, `/api/pipeline/*`, `/api/backtest/*`, `/api/market/vix`, `/api/pipelines/status`, `/api/sentiment/feed`, `/api/rotation/correlation` |
| `stock_routes.py` (957 lines) | `/api/stock-universe`, `/api/stock-universe/<symbol>`, stock fundamentals, earnings, options |
| `simulation_routes.py` (181 lines) | Simulation mode endpoints |
| `research_routes.py` (102 lines) | Research mode endpoints |
| `service_routes.py` (45 lines) | Utility/health endpoints |

All write endpoints (`PATCH`, `POST`, `DELETE`) require `@api_login_required`.

## WebSocket Events

10 events via RedisBridge → Socket.IO `/dashboard` namespace:

| Channel | Event | Payload |
|---------|-------|---------|
| `channel:bars` | `price_update` | `{symbol: {o,h,l,c,v}}` |
| `channel:factor_scores` | `factor_scores` | `{symbol: {trend, vol, sentiment, ...}}` |
| `channel:signals` | `signals` | `{symbol: {composite_score, signal_type, regime}}` |
| `channel:portfolio` | `portfolio` | `{positions, weights, total_value, cash}` |
| `channel:risk_alerts` | `risk_alert` | `{type, level, metric, threshold, timestamp}` |
| `channel:trades` | `trade` | `{symbol, side, qty, fill_price, slippage_bps}` |
| `channel:regime_change` | `regime_change` | `{regime, confidence, timestamp}` |
| `channel:system_health` | `system_health` | `{module, status, items_processed}` |
| `channel:config_change` | `config_change` | `{category, key, old_value, new_value}` |
| `channel:kill_switch` | `kill_switch` | `{switch, active, timestamp}` |

## Operational Modes

Set via `config:system.operational_mode` or `--mode` CLI flag:

| Mode | Execution | Data | Use Case |
|------|-----------|------|---------|
| `research` | Simulated fills via cost model | Historical DB | Backtesting, parameter sweeps |
| `simulation` | No orders; mark-to-market only | Live feeds | Live validation before go-live |
| `live` | Real broker orders | Live feeds | Production trading |

**Switching simulation → live** requires admin confirmation. Switching live → simulation immediately sets `kill_switch:rebalance = 1`.

## Composite Scores

### ETF Default Weights
```
ETF_SCORE = 0.30 * Trend
           + 0.25 * Volatility_Regime
           + 0.15 * Sentiment
           + 0.15 * Breadth
           + 0.15 * Liquidity
```

Dispersion excluded from active composite (class remains in `app/factors/dispersion.py` for research). Weights admin-configurable at runtime via `config:weights` Redis hash.

### Stock Default Weights
```
STOCK_SCORE = 0.25 * Fundamentals
             + 0.20 * Trend
             + 0.15 * Volatility_Regime
             + 0.15 * Sentiment
             + 0.15 * Earnings
             + 0.10 * Liquidity
```

Configurable via `config:weights:stock` Redis hash.

## Factor Registry

| Factor | ETF | Stock | Notes |
|--------|-----|-------|-------|
| Trend (F01) | 0.30 | 0.20 | MA crossover + cross-sectional momentum percentile |
| Volatility (F02) | 0.25 | 0.15 | 20-day realized vol + VIX percentile + GARCH ratio |
| Sentiment (F03) | 0.15 | 0.15 | FinBERT 35% + Reddit VADER 25% + options P/C 25% + flow 15% |
| Dispersion (F04) | — | — | Excluded from composite, kept for research |
| Correlation (F05) | — | — | 60-day rolling pairwise correlation (ETF supplementary) |
| Breadth (F06) | 0.15 | — | % of universe above 20-day MA |
| Liquidity (F07) | 0.15 | 0.10 | ADV ratio percentile rank |
| Slippage (F08) | — | — | Bid-ask spread estimate (ETF supplementary) |
| Fundamentals (F09) | — | 0.25 | PE/PB/ROE/debt-to-equity percentile rank |
| Earnings (F10) | — | 0.15 | Days-to-earnings + surprise magnitude |

Supplementary: `beta_adjusted_momentum`, `vol_adjusted_position_size`.

## Celery Beat Schedule (EOD after market close)

```
5:00pm  refresh_all_bars          (ETF OHLCV)
5:05pm  refresh_all_stock_bars    (stock OHLCV)
5:15pm  refresh_universe_metadata
5:20pm  refresh_stock_fundamentals
5:30pm  refresh_vix
5:35pm  refresh_earnings_calendar
5:45pm  refresh_cboe_put_call
6:00pm  compute_all_factors       (ETF)
6:05pm  compute_stock_factors     (stock)
6:15pm  launch_pipeline           (ETF — 5-stage Celery chain)
6:20pm  launch_stock_pipeline     (stock — 5-stage Celery chain)
6:30pm  record_daily_performance

Every 5min:  run_continuous_risk_monitor
```

## Config Architecture

All system parameters live in `system_config` PostgreSQL table, cached in Redis hashes prefixed `config:*`. API writes to both (`PATCH /api/config/<category>` → PostgreSQL + Redis sync + `config_change` WebSocket broadcast). Kill switches use direct `kill_switch:*` Redis keys.

**Weight preview**: `/api/scores?preview_weights={...}` returns re-scored rankings without saving.

**API key security**: secrets stored Fernet-encrypted in `system_config`; displayed masked in UI.

See [research/admin_config_reference.md](research/admin_config_reference.md) for the full parameter catalog.

## Message Bus Rules

Inter-module communication uses Redis exclusively. Two delivery patterns:
- **Pub/sub** (fire-and-forget): `channel:bars`, `channel:quotes`, `channel:factor_scores`, `channel:signals`, `channel:trades`, `channel:fills` — dashboard display only; message loss is acceptable
- **List queue** (`LPUSH`/`BRPOP`): `channel:orders_proposed`, `channel:orders_approved`, `channel:risk_alerts`, `channel:regime_change` — must not lose messages; consumed by exactly one worker

## Research Files

| File | Contents |
|------|---------|
| [research/system_architecture.md](research/system_architecture.md) | Canonical architecture — module definitions, data flow, message bus, operational modes, observability |
| [research/alpha_factor_library.md](research/alpha_factor_library.md) | Factor catalog — all factors with attributes; registry pattern; how to add a new factor |
| [research/architecture_and_stack.md](research/architecture_and_stack.md) | Implementation reference — libraries, Redis key map, file structure, build order |
| [research/ETF_universe.csv](research/ETF_universe.csv) | Tradable universe with AUM, ADV, spread, liquidity scores |
| [research/factor_models.md](research/factor_models.md) | All factor formulas, lookbacks, normalization, edge cases |
| [research/rotation_detection.md](research/rotation_detection.md) | Regime classification, correlation collapse detection, momentum divergence |
| [research/signal_engine.md](research/signal_engine.md) | Signal pipeline architecture, composite scorer, signal classifier |
| [research/portfolio_construction.md](research/portfolio_construction.md) | PyPortfolioOpt implementation, volatility sizing, rebalance engine |
| [research/risk_framework.md](research/risk_framework.md) | Risk controls, stop-loss ladder, drawdown mitigation strategies |
| [research/backtesting_results.md](research/backtesting_results.md) | Backtest methodology, walk-forward testing, Monte Carlo, cost model |
| [research/admin_config_reference.md](research/admin_config_reference.md) | Every admin config parameter with defaults, ranges, Redis keys |
| [research/production_setup.md](research/production_setup.md) | Gunicorn + eventlet, Nginx, systemd, Docker Compose, PostgreSQL/Redis tuning |
| [research/metatrader_integration.md](research/metatrader_integration.md) | MT5 Python API, ETF CFD availability, Windows constraint, MT5Broker class |

## Key Implementation Notes

- **Timezone**: All DB timestamps in UTC; convert to ET only at display. Use `zoneinfo` (Python 3.9+).
- **Factor normalization**: Always percentile-rank against data available at signal time — never use the full dataset (look-ahead bias). Uses `cross_sectional_percentile_rank()` from `app/utils/normalization.py`.
- **VIX data**: Use T-1 VIX close when computing signals at T-close (15-min delay on CBOE free feed).
- **FinBERT**: Fully wired with lazy-loaded singleton, batch_size=32, torch no_grad inference, VADER fallback on error.
- **Redis pub/sub is lossy**: Use `LPUSH/BRPOP` list queues for critical risk alerts; pub/sub only for best-effort dashboard updates.
- **Momentum**: Compute cross-sectionally across the full universe, then percentile-rank. Never compute per-ETF in isolation.
- **Rebalance signal timing**: Generate signals at T-close; execute at T+1 open.
- **Structured logging**: All modules log to `structlog` in JSON format. PostgreSQL tables (`factor_scores`, `signals`, `trades`) are the permanent audit trail.
- **Operational mode injection**: The `operational_mode` config key must be checked at the Execution Engine only — factor computation, signal generation, and portfolio construction run identically across all modes.
- **Short signals**: Only active in `risk_off` regime when `config:execution.allow_short = true` and broker supports it. Score threshold < 0.25 + trend_score < 0.30. Disabled by default.
- **Slippage model**: Almgren-Chriss (temporary + permanent impact) for pre-trade estimation; post-trade measurement via `SlippageTracker.measure_posttrade()`.
- **Portfolio optimization**: Ledoit-Wolf covariance shrinkage; supports max_sharpe, min_vol, equal_weight, HRP objectives. 5% risk-free rate in optimizer.
- **Correlation monitoring**: Date-stamped consecutive breach counter (prevents double-counting intraday), gradual decrement hysteresis, pair-level identification.
- **Weight preview**: `/api/scores?preview_weights={...}` returns re-scored rankings without saving.
- **API key security**: secrets stored Fernet-encrypted in `system_config`; displayed masked in UI (`••••5f3a`).

## Known Issues

- **Pipeline mid-flight status**: `publish_pipeline_status()` function exists in `publishers.py` but is never called from the pipeline orchestrator. Status only available after chain completion, not during stage execution.
- **Per-client SocketIO filtering**: `on_subscribe()` handler accepts channel lists but all events broadcast to all clients (placeholder logic only).
- **Stop-loss engine bug**: `_check_long()` in `stop_loss_engine.py` references undefined variables (`direction`, `thresholds`). Will raise `NameError` if executed via this code path.
- **Production Sharpe ratio**: `performance_recorder.py` does not subtract risk-free rate (backtester does correctly at 4.5%).
