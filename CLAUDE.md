# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Paisabot** — a systematic ETF trading strategy bot that detects sector rotation and trending market regimes using 8 factor classes: Volatility, Sentiment, Dispersion, Correlation, Breadth, Trend, Liquidity, and Slippage. Operates primarily on liquid ETFs (SPY, QQQ, XLK, XLE, XLF, XLV, etc.).

Stack: **Python + Flask + PostgreSQL + Redis + WebSocket** (Flask-SocketIO).

Research deliverables are in [research/](research/). Implementation has not started yet.

## Fix First

The gitignore file is misnamed `.gitiginore` (extra `i`). Rename to `.gitignore` before committing any files:
```bash
git mv .gitiginore .gitignore
```

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Fill in API keys

# Database
docker-compose up -d              # starts PostgreSQL + Redis
alembic upgrade head
python scripts/seed_config.py     # seeds default system_config values
python scripts/universe_setup.py  # populates ETF universe table
python scripts/backfill_history.py  # loads historical bars

# Run
flask run                         # development server
celery -A celery_worker worker    # background task worker
```

## Development Commands

```bash
pytest                            # all tests
pytest tests/test_factors/        # single module
pytest tests/test_factors/test_volatility.py::test_zscore  # single test

flask shell                       # REPL with app context

# Redis inspection
redis-cli hgetall config:weights
redis-cli keys "kill_switch:*"
```

## Architecture

See [research/system_architecture.md](research/system_architecture.md) for the canonical design (module definitions, data flow, message bus, operational modes, observability). See [research/architecture_and_stack.md](research/architecture_and_stack.md) for the implementation reference (library versions, Redis key map, file structure, build order).

**7 modules in pipeline order**:

1. **Market Data Layer** (`app/data/`) — Alpaca WebSocket real-time bars; APScheduler REST pollers for news, sentiment, VIX, options; Celery for historical backfill. Outputs to PostgreSQL + Redis cache + `channel:bars` pub/sub
2. **Research & Factor Engine** (`app/factors/`) — 8 classes implementing `FactorBase.compute()`, all returning scores in [0,1]. Daily EOD recompute + intraday fast factors (vol, liquidity) every 5 min. Outputs to `channel:factor_scores`
3. **Signal Engine** (`app/signals/`) — `CompositeScorer` → `RegimeDetector` → `SignalFilter` → `SignalRanker`. Produces long/short candidates + signal confidence. Outputs to `channel:signals`
4. **Portfolio Construction Engine** (`app/portfolio/`) — PyPortfolioOpt with sector (max 25%) and position (max 5%) constraints; volatility targeting to 12% annualized. Pushes proposed orders to `channel:orders_proposed` LIST queue
5. **Risk Engine** (`app/risk/`) — pre-trade gate (approves/blocks orders) + continuous monitor (drawdown -15%, stop-loss -5%/-8% trailing, VaR, correlation shock). Sets `kill_switch:*` keys on breach
6. **Execution Engine** (`app/execution/`) — Alpaca paper/live trading via `alpaca-py`; fill tracking; slippage measurement. Behavior gated by operational mode
7. **Monitoring & Analytics Layer** (`app/streaming/`, `app/api/`, `app/admin/`) — Flask-SocketIO dashboard (PnL, positions, factor heatmap, regime, risk gauges); REST API (`/api/*`); Flask-Admin config UI + kill switch panel

## Operational Modes

Set via `config:system.operational_mode` or `--mode` CLI flag:

| Mode | Execution | Data | Use Case |
|------|-----------|------|---------|
| `research` | Simulated fills via cost model | Historical DB | Backtesting, parameter sweeps |
| `simulation` | No orders; mark-to-market only | Live feeds | Live validation before go-live |
| `live` | Real broker orders | Live feeds | Production trading |

**Switching simulation → live** requires admin confirmation in the UI. Switching live → simulation immediately sets `kill_switch:rebalance = 1`.

## Composite Score

```
ETF_SCORE = 0.25 * Trend
           + 0.20 * Volatility_Regime
           + 0.15 * Sentiment
           + 0.15 * Breadth
           + 0.15 * Dispersion
           + 0.10 * Liquidity
```

Weights are admin-configurable at runtime via `config:weights` Redis hash.

## Config Architecture

All system parameters live in the `system_config` PostgreSQL table and are cached in Redis hashes prefixed `config:*`. Admin UI writes to both; application code reads from Redis (fast path). Kill switches use direct `kill_switch:*` Redis keys (not under `config:`).

See [research/admin_config_reference.md](research/admin_config_reference.md) for the full parameter catalog.

## Research Files

| File | Contents |
|------|---------|
| [research/system_architecture.md](research/system_architecture.md) | **Canonical architecture** — module definitions, data flow, message bus channels, operational modes, observability |
| [research/alpha_factor_library.md](research/alpha_factor_library.md) | **Factor catalog** — all 11 factors with 7 attributes each; registry pattern; how to add a new factor |
| [research/frontend_dashboard.md](research/frontend_dashboard.md) | **Frontend spec** — 6 views, WebSocket events, Flask routes, CSS design system, component library |
| [research/architecture_and_stack.md](research/architecture_and_stack.md) | Implementation reference — libraries, Redis key map, file structure, build order |
| [research/ETF_universe.csv](research/ETF_universe.csv) | Tradable universe with AUM, ADV, spread, liquidity scores |
| [research/factor_models.md](research/factor_models.md) | All factor formulas, lookbacks, normalization, edge cases |
| [research/rotation_detection.md](research/rotation_detection.md) | Regime classification, correlation collapse detection, momentum divergence |
| [research/signal_engine.md](research/signal_engine.md) | Signal pipeline architecture, composite scorer, signal classifier |
| [research/portfolio_construction.md](research/portfolio_construction.md) | PyPortfolioOpt implementation, volatility sizing, rebalance engine |
| [research/risk_framework.md](research/risk_framework.md) | Risk controls, stop-loss ladder, drawdown mitigation strategies |
| [research/backtesting_results.md](research/backtesting_results.md) | Backtest methodology, walk-forward testing, Monte Carlo, cost model |
| [research/admin_config_reference.md](research/admin_config_reference.md) | Every admin config parameter with defaults, ranges, Redis keys |

## Message Bus Rules

Inter-module communication uses Redis exclusively. Two delivery patterns:
- **Pub/sub** (fire-and-forget): `channel:bars`, `channel:quotes`, `channel:factor_scores`, `channel:signals`, `channel:trades`, `channel:fills` — dashboard display only; message loss is acceptable
- **List queue** (`LPUSH`/`BRPOP`): `channel:orders_proposed`, `channel:orders_approved`, `channel:risk_alerts`, `channel:regime_change` — must not lose messages; consumed by exactly one worker

## Key Implementation Notes

- **Timezone**: All DB timestamps in UTC; convert to ET only at display. Use `zoneinfo` (Python 3.9+).
- **Factor normalization**: Always percentile-rank against data available at signal time — never use the full dataset for normalization (look-ahead bias).
- **VIX data**: Use T-1 VIX close when computing signals at T-close (15-min delay on CBOE free feed).
- **FinBERT on CPU**: Batch inference (batch_size=32); do not call per-headline.
- **Redis pub/sub is lossy**: Use `LPUSH/BRPOP` list queues for critical risk alerts; pub/sub only for best-effort dashboard updates.
- **Momentum**: Compute cross-sectionally across the full universe, then percentile-rank. Never compute per-ETF in isolation.
- **Rebalance signal timing**: Generate signals at T-close; execute at T+1 open.
- **Structured logging**: All modules log to `structlog` in JSON format. Minimum required events per module defined in [system_architecture.md](research/system_architecture.md#logging-and-observability). PostgreSQL tables (`factor_scores`, `signals`, `trades`) are the permanent audit trail.
- **Operational mode injection**: The `operational_mode` config key must be checked at the Execution Engine only — factor computation, signal generation, and portfolio construction run identically across all modes.
- **Short signals**: Only active in `risk_off` regime when `config:execution.allow_short = true` and broker supports it. Score threshold < 0.25 + trend_score < 0.30. Disabled by default.
- **Factor library**: 11 factors total — 8 contribute to the composite score (F01–F08), 3 are supplementary (F09 `sector_momentum_rank`, F10 `beta_adjusted_momentum`, F11 `vol_adjusted_position_size`). Adding a new factor requires entries in the library, a new class, FactorRegistry registration, a DB migration, and a dashboard panel. See [alpha_factor_library.md](research/alpha_factor_library.md).
- **Frontend stack**: Jinja2 + Vanilla JS + Plotly.js + Tabulator + Alpine.js + Socket.IO client. Dark terminal aesthetic — CSS variables in `static/css/main.css`. No React/Vue build pipeline. See [frontend_dashboard.md](research/frontend_dashboard.md).
- **Config view** (`/config`): purpose-built operator UI for all system parameters, replacing Flask-Admin as the primary config interface. 11 tabs (Mode, Kill Switches, Weights, Universe, Portfolio, Risk, Execution, Data, Schedule, Alerts, Audit Log). Flask-Admin at `/admin` remains as a power-user/emergency DB fallback. All writes go through `PATCH /api/config/<category>` → PostgreSQL + Redis sync + `config_change` WebSocket broadcast.
- **Weight preview**: `/api/scores?preview_weights={...}` returns re-scored rankings without saving — powers the live preview panel in the Config Weights tab.
- **API key security**: secrets stored Fernet-encrypted in `system_config`; displayed masked in UI (`••••5f3a`); reveal requires re-auth; `[Test]` button pings live provider.
