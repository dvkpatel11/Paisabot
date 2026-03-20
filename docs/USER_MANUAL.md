# Paisabot User Manual

**Version 1.0 | Systematic ETF Sector Rotation Bot**

---

## Table of Contents

1. [What Is Paisabot](#1-what-is-paisabot)
2. [Quick Start](#2-quick-start)
3. [Architecture Overview](#3-architecture-overview)
4. [Dashboard Guide](#4-dashboard-guide)
5. [API Reference](#5-api-reference)
6. [Configuration Reference](#6-configuration-reference)
7. [Factor Engine](#7-factor-engine)
8. [Signal Engine](#8-signal-engine)
9. [Portfolio Construction](#9-portfolio-construction)
10. [Risk Controls](#10-risk-controls)
11. [Execution Engine](#11-execution-engine)
12. [Broker Setup](#12-broker-setup)
13. [Operational Modes](#13-operational-modes)
14. [Scheduled Tasks](#14-scheduled-tasks)
15. [Troubleshooting](#15-troubleshooting)
16. [Production Deployment](#16-production-deployment)

---

## 1. What Is Paisabot

Paisabot is a systematic ETF trading strategy bot that detects sector rotation and trending market regimes. It scores a universe of liquid ETFs (SPY, QQQ, XLK, XLE, XLF, XLV, etc.) using 8 quantitative factors, generates buy/sell signals, constructs risk-constrained portfolios, and executes trades through supported brokers.

**What it does:**

- Computes 8 factor scores per ETF (Trend, Volatility, Sentiment, Dispersion, Breadth, Correlation, Liquidity, Slippage)
- Classifies the current market regime (trending, rotation, risk_off, consolidation)
- Generates ranked signals (long / neutral / avoid) for each ETF
- Builds volatility-targeted portfolios with sector and position constraints
- Executes rebalance orders through Alpaca or MetaTrader 5
- Monitors risk continuously with automated kill switches

**What it does not do:**

- Pick individual stocks (ETFs only)
- Guarantee profits (this is a quantitative research tool)
- Run without supervision in live mode (always monitor)

---

## 2. Quick Start

### Prerequisites

- Python 3.12+
- Docker Desktop (for PostgreSQL and Redis)
- Git

### Installation

```bash
# Clone and enter the project
git clone <repo-url> && cd paisabot

# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
```

### Configure .env

Open `.env` and fill in at minimum:

```env
SECRET_KEY=<random-string>
DATABASE_URL=postgresql://paisabot:paisabot@127.0.0.1:5432/paisabot
FERNET_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

> **Windows users:** Use `127.0.0.1` instead of `localhost` in DATABASE_URL. Windows resolves `localhost` to IPv6 (`::1`) but Docker PostgreSQL binds IPv4 only.

### Start Infrastructure

```bash
docker-compose up -d    # Starts PostgreSQL + Redis
```

### Initialize Database

```bash
# Run Alembic migrations (creates all 8 tables)
alembic upgrade head

# Seed default config (68 parameters)
python scripts/seed_config.py

# Populate ETF universe (20 ETFs)
python scripts/universe_setup.py

# Backfill historical price data (optional, requires Alpaca API key)
python scripts/backfill_history.py
```

### Run the Application

```bash
# Terminal 1: Flask web server
flask run

# Terminal 2: Celery background worker
celery -A celery_worker worker --loglevel=info
```

Open your browser to `http://localhost:5000/dashboard`.

---

## 3. Architecture Overview

Paisabot runs as a 7-module pipeline where each module reads from Redis, processes data, and publishes results back to Redis. The Flask web server serves the dashboard and consumes these events in real time via WebSocket.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Market Data │────▶│   Factor    │────▶│   Signal    │────▶│  Portfolio  │
│    Layer     │     │   Engine    │     │   Engine    │     │Construction │
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                    │
                                                                    ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Dashboard   │◀────│  Execution  │◀────│    Risk     │◀────│  Proposed   │
│  & API       │     │   Engine    │     │   Engine    │     │   Orders    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### Module Summary

| # | Module | Location | What It Does |
|---|--------|----------|-------------|
| 1 | Market Data | `app/data/` | Ingests price bars, news, VIX, Reddit sentiment |
| 2 | Factor Engine | `app/factors/` | Computes 8 scores per ETF, all normalized to [0, 1] |
| 3 | Signal Engine | `app/signals/` | Combines factors into composite score, classifies regime, ranks ETFs |
| 4 | Portfolio Construction | `app/portfolio/` | Builds target portfolio with constraints, generates rebalance orders |
| 5 | Risk Engine | `app/risk/` | Pre-trade gate (approve/block) + continuous monitoring |
| 6 | Execution Engine | `app/execution/` | Submits orders to broker, tracks fills, measures slippage |
| 7 | Monitoring | `app/streaming/`, `app/api/` | Real-time dashboard, REST API, admin config UI |

### Inter-Module Communication

All modules communicate through Redis. Two delivery patterns are used:

**Pub/Sub (fire-and-forget)** for dashboard display. Message loss is acceptable:
- `channel:bars`, `channel:factor_scores`, `channel:signals`, `channel:trades`, `channel:fills`

**List Queues (LPUSH/BRPOP)** for critical order flow. Messages are never lost:
- `channel:orders_proposed`, `channel:orders_approved`, `channel:risk_alerts`, `channel:regime_change`

### Database

PostgreSQL stores the permanent audit trail. 8 tables:

| Table | Purpose |
|-------|---------|
| `system_config` | All configurable parameters (cached in Redis) |
| `etf_universe` | Tradable ETF metadata (AUM, ADV, sector, MT5 symbol) |
| `price_bars` | Daily OHLCV bars |
| `factor_scores` | Historical factor scores per symbol per day |
| `signals` | Signal output per cycle (composite score, type, regime) |
| `trades` | Every trade executed (fill price, slippage, mode) |
| `positions` | Open/closed positions with PnL tracking |
| `performance_metrics` | Daily portfolio performance (returns, Sharpe, drawdown) |

---

## 4. Dashboard Guide

Access the dashboard at `http://localhost:5000/dashboard`. All views share a dark terminal aesthetic and update in real time via WebSocket.

### Navigation

| Route | View | Purpose |
|-------|------|---------|
| `/dashboard` | Portfolio Dashboard | Portfolio value, regime, positions, recent trades |
| `/factors` | Factor Heatmap | 8 factor scores per ETF with staleness indicators |
| `/rotation` | Rotation Analysis | Sector rotation detection, correlation heatmap |
| `/execution` | Execution Log | Trade history, slippage analysis, order pipeline |
| `/analytics` | Performance | Cumulative returns, Sharpe, drawdown charts |
| `/alerts` | Risk Alerts | Kill switch status, recent risk events |
| `/pipelines` | Pipeline Status | Health of all 7 modules, queue depths, throughput |
| `/config` | Configuration | All system parameters across 11 tabs |
| `/admin` | Flask-Admin | Direct database access (power-user fallback) |

### Real-Time Updates

The dashboard connects to the server via WebSocket (Socket.IO). Events are automatically pushed when:

| Event | Trigger |
|-------|---------|
| `factor_scores` | Factor recompute completes |
| `signals` | Signal cycle completes |
| `portfolio` | Rebalance executes |
| `risk_alert` | Risk threshold breached |
| `trade` | Order filled |
| `regime_change` | Market regime transitions |
| `system_health` | Module status changes |
| `config_change` | Config parameter updated |

### Pipeline Monitor (`/pipelines`)

Shows a horizontal flow diagram of all 7 modules with:
- **Status dots** (green = ok, yellow = stale, red = error)
- **Metric cards** for throughput, latency, queue depth
- **Kill switch grid** showing which switches are active
- **Events table** with recent system activity

A module is marked "stale" if its last activity exceeds the threshold (10 minutes for market data, 60 minutes for other modules).

---

## 5. API Reference

All endpoints are prefixed with `/api`. Responses are JSON.

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | System health, component status, kill switches |
| GET | `/api/pipelines/status` | All 7 module statuses with queue depths |

### Market Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/scores` | Latest composite scores for all ETFs |
| GET | `/api/scores?preview_weights={...}` | Re-score with hypothetical weights (no save) |
| GET | `/api/factors/<symbol>` | Historical factor scores (last 30 days) |

### Signals & Regime

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/signals` | Latest signals grouped by type (long, neutral, avoid) |
| GET | `/api/regime` | Current regime, confidence, history |

### Portfolio & Trading

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/portfolio` | Open positions, weights, PnL |
| GET | `/api/trades` | Trade log (filterable by `?symbol=SPY`) |
| GET | `/api/risk` | Risk state, kill switches, VaR, drawdown |

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config` | All config parameters |
| PATCH | `/api/config/<category>` | Update parameters in a category |
| PATCH | `/api/config/weights` | Update factor weights (validates sum = 1.0) |
| PATCH | `/api/config/mode` | Change operational mode |

### Weight Preview

To test different factor weights without saving, pass a JSON object as query parameter:

```
GET /api/scores?preview_weights={"weight_trend":0.30,"weight_volatility":0.25,"weight_sentiment":0.10,"weight_breadth":0.15,"weight_dispersion":0.10,"weight_liquidity":0.10}
```

This re-scores the entire universe with the hypothetical weights and returns the result. Nothing is persisted.

---

## 6. Configuration Reference

All parameters live in the `system_config` PostgreSQL table and are cached in Redis `config:*` hashes. The Config UI (`/config`) is the primary interface for changing settings.

### Weights (`config:weights`)

Controls how much each factor contributes to the composite score. Must sum to 1.0.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `weight_trend` | 0.25 | Trend/momentum factor weight |
| `weight_volatility` | 0.20 | Volatility regime weight |
| `weight_sentiment` | 0.15 | News/social sentiment weight |
| `weight_breadth` | 0.15 | Market breadth weight |
| `weight_dispersion` | 0.15 | Cross-sectional dispersion weight |
| `weight_liquidity` | 0.10 | Liquidity/slippage weight |

### Universe (`config:universe`)

Filters for which ETFs are eligible for trading.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_aum_bn` | 2.0 | Minimum AUM in billions |
| `min_avg_daily_vol_m` | 20.0 | Min 30-day avg daily volume ($M) |
| `max_spread_bps` | 10.0 | Max bid-ask spread (basis points) |
| `require_options_market` | true | Must have listed options |
| `min_history_days` | 504 | Min trading days of history (2 years) |
| `excluded_etfs` | (empty) | Comma-separated symbols to exclude |
| `manual_inclusions` | (empty) | Force-include regardless of filters |

### Portfolio (`config:portfolio`)

Controls portfolio construction and rebalancing.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_positions` | 10 | Maximum simultaneous positions |
| `max_position_size` | 0.05 | Max weight per position (5%) |
| `max_sector_exposure` | 0.25 | Max weight per GICS sector (25%) |
| `min_position_size` | 0.01 | Min allocation (1%) |
| `cash_buffer_pct` | 0.05 | Permanent cash allocation (5%) |
| `rebalance_frequency` | daily | daily / weekly / monthly |
| `rebalance_time` | 09:45 | Time for rebalance orders (ET) |
| `turnover_limit_pct` | 0.50 | Max one-way turnover per rebalance |
| `optimization_objective` | max_sharpe | max_sharpe / min_vol / equal_weight / hrp |

### Risk (`config:risk`)

Risk thresholds and automated responses.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_drawdown` | -0.15 | Halt trading if drawdown exceeds this |
| `daily_loss_limit` | -0.03 | Halt for remainder of session |
| `position_stop_loss` | -0.05 | Exit position at -5% from entry |
| `position_trailing_stop` | -0.08 | Exit at -8% from high-water mark |
| `vol_target` | 0.12 | Target annualized portfolio volatility (12%) |
| `vol_scaling_enabled` | true | Scale positions when vol exceeds target |
| `var_confidence` | 0.95 | Value-at-Risk confidence level |
| `var_limit_pct` | 0.02 | Alert if 1-day VaR exceeds 2% |
| `correlation_limit` | 0.85 | Warn if avg pairwise correlation > 0.85 |

### Execution (`config:execution`)

Order submission and broker settings.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `broker` | alpaca_paper | alpaca_paper / alpaca_live / mt5 |
| `order_type` | market | market / limit |
| `limit_slippage_bps` | 5.0 | Limit order offset from mid (bps) |
| `execution_window_minutes` | 30 | Spread execution over N min after open |
| `use_fractional_shares` | true | Enable fractional shares (Alpaca only) |
| `min_order_notional` | 100 | Min order size in dollars |
| `max_order_notional` | 50000 | Max single order size in dollars |
| `max_slippage_bps` | 8.0 | Block order if estimated slippage > this |
| `allow_short` | false | Allow short signals (risk_off only) |
| `mt5_deviation` | 30 | MT5 max price deviation in points |
| `mt5_gateway_url` | http://localhost:8001 | MT5 gateway microservice URL |

### Data (`config:data`)

Data source configuration.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `price_data_provider` | alpaca | alpaca / polygon / databento |
| `news_data_provider` | finnhub | finnhub / newsapi / eodhd |
| `sentiment_model` | finbert | finbert / vader |
| `vix_data_source` | fred | cboe / polygon / fred |

### Scheduling (`config:scheduling`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `bar_fetch_interval_sec` | 60 | Intraday bar refresh (seconds) |
| `intraday_factor_interval_min` | 5 | Fast factor recompute interval |
| `sentiment_update_interval_min` | 15 | Sentiment refresh interval |
| `daily_compute_time` | 16:30 | Full EOD recompute time (ET) |
| `pre_open_check_time` | 09:15 | Pre-market validation time (ET) |
| `market_calendar` | NYSE | Trading calendar for market hours |

### Alerts (`config:alerts`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `alert_channels` | email,slack | Comma-separated channels |
| `webhook_url` | (empty) | Slack/Discord webhook URL |
| `alert_on_trade` | false | Notify on every trade |
| `alert_on_rebalance` | true | Notify on each rebalance |
| `alert_on_regime_change` | true | Notify on regime change |
| `alert_factor_stale_minutes` | 30 | Alert if factor not updated |

---

## 7. Factor Engine

The factor engine computes 8 quantitative scores per ETF, each normalized to [0, 1]. Factors are recomputed on two schedules:

- **Intraday fast** (every 5 min during market hours): Volatility, Liquidity, Slippage
- **Daily EOD** (16:30 ET): All 8 factors

### Factor Catalog

| # | Factor | Weight | What It Measures |
|---|--------|--------|-----------------|
| F01 | Trend | 25% | Momentum and directional strength |
| F02 | Volatility | 20% | Volatility regime (low vol = favorable) |
| F03 | Sentiment | 15% | News and social media sentiment (FinBERT) |
| F04 | Breadth | 15% | Market participation (% above 200-day SMA) |
| F05 | Dispersion | 15% | Cross-sectional return dispersion |
| F06 | Liquidity | 10% | Bid-ask spreads and trading volume |
| F07 | Correlation | 0% (supplementary) | Pairwise correlation structure |
| F08 | Slippage | 0% (supplementary) | Estimated execution cost |

### Composite Score Formula

```
ETF_SCORE = 0.25 * Trend
          + 0.20 * Volatility
          + 0.15 * Sentiment
          + 0.15 * Breadth
          + 0.15 * Dispersion
          + 0.10 * Liquidity
```

Weights are configurable at runtime via the Config UI (`/config` > Weights tab). The weight preview feature lets you test different allocations without saving.

### Normalization

All factors are percentile-ranked against the data available at signal time. This prevents look-ahead bias; the system never uses future data for normalization.

### Staleness

If a factor has not been updated within `alert_factor_stale_minutes` (default: 30), the dashboard shows a staleness warning. Stale factors still contribute their last known score.

---

## 8. Signal Engine

The signal engine transforms factor scores into actionable trading signals.

### Pipeline

```
Factor Scores ──▶ Composite Scorer ──▶ Regime Detector ──▶ Signal Filter ──▶ Signal Ranker
```

### Signal Classification

| Composite Score | Signal Type | Meaning |
|----------------|-------------|---------|
| >= 0.65 | `long` | Buy / hold candidate |
| 0.40 - 0.64 | `neutral` | Hold if held; no new entry |
| < 0.40 | `avoid` | Exit if held |

In **risk_off** regime, the `long` threshold rises to **0.70**.

### Market Regime Detection

The regime detector classifies the current market environment:

| Regime | Description | Effect |
|--------|-------------|--------|
| `trending` | Sustained directional momentum | Normal operation |
| `rotation` | Sector rotation environment | Allow up to 12 long positions |
| `risk_off` | Defensive posture | Cap positions at 5; raise long threshold to 0.70 |
| `consolidation` | Sideways market | Normal operation |

Regime changes require 3 consecutive days at the new regime. Exiting `risk_off` requires confidence >= 0.65.

### Signal Blocking

Signals are blocked (regardless of score) when:pppppppppppppppppppppppppppppppppppppppppppp
- `kill_switch:trading` or `kill_switch:all` is active
- ETF's ADV < minimum threshold
- ETF's spread > maximum threshold
- `kill_switch:maintenance` is active

---

## 9. Portfolio Construction

The portfolio engine transforms signals into concrete allocations.

### Constraints

| Constraint | Value | Configurable |
|-----------|-------|-------------|
| Max positions | 10 (5 in risk_off) | Yes |
| Max position weight | 5% | Yes |
| Max sector exposure | 25% | Yes |
| Min position weight | 1% | Yes |
| Cash buffer | 5% | Yes |
| Max turnover per cycle | 50% | Yes |
| Volatility target | 12% annualized | Yes |

### Optimization Objectives

Set via `config:portfolio.optimization_objective`:

| Objective | Description |
|-----------|-------------|
| `max_sharpe` | Maximize Sharpe ratio (default) |
| `min_vol` | Minimize portfolio volatility |
| `equal_weight` | Equal-weight all long signals |
| `hrp` | Hierarchical Risk Parity |

### Rebalance Flow

1. **Select candidates** from ETFs with `long` signals
2. **Optimize weights** via PyPortfolioOpt
3. **Apply constraints** (position, sector, cash buffer)
4. **Volatility scale** if portfolio vol > target
5. **Generate orders** by diffing current vs. target weights
6. **Skip small legs** below 50 bps (minimum trade threshold)
7. **Submit sells first** (free up cash before buying)

### Rebalance Timing

Signals are generated at market close (T). Rebalance orders execute at T+1 open (default 09:45 ET).

---

## 10. Risk Controls

### Kill Switches

Kill switches are emergency controls stored directly in Redis. Toggle them from the Config UI (`/config` > Kill Switches tab) or the Admin panel.

| Switch | Effect |
|--------|--------|
| `kill_switch:trading` | Block all order submission; keep computing signals |
| `kill_switch:rebalance` | Hold current positions; no new rebalances |
| `kill_switch:sentiment` | Disable sentiment factor; use neutral (0.5) |
| `kill_switch:maintenance` | Read-only mode; compute only |
| `kill_switch:force_liquidate` | Sell ALL open positions immediately (auto-resets) |
| `kill_switch:all` | Halt all operations |

### Automated Risk Monitors

These run continuously and trigger automatically:

| Monitor | Threshold | Action |
|---------|-----------|--------|
| Portfolio drawdown | -15% | Halt trading, send alert |
| Daily loss limit | -3% | Halt for remainder of session |
| Position stop-loss | -5% from entry | Auto-liquidate position |
| Trailing stop | -8% from high-water mark | Auto-liquidate position |
| VaR (95%, 1-day) | > 2% of portfolio | Reduce positions |
| Correlation | > 0.85 avg for 3+ days | Force diversification |
| Liquidity shock | ADV drops > 50% | Suspend new entries for that ETF |

### Pre-Trade Gate

Every order passes through the pre-trade gate before execution. It checks:

1. Is any kill switch active?
2. Will this order breach position or sector limits?
3. Is the ETF's ADV sufficient?
4. Does the regime allow this trade type?
5. Is estimated slippage within bounds?

If any check fails, the order is blocked and logged.

### Drawdown Recovery

After a drawdown halt:
- Requires 5 consecutive "risk_ok" days before resuming
- Re-entry starts at 50% position sizing for 10 days
- Gradually scales back to full sizing

---

## 11. Execution Engine

The execution engine is the **only module that checks operational mode**. All other modules (factors, signals, portfolio, risk) run identically regardless of mode.

### Order Flow

```
channel:orders_approved ──▶ Kill Switch Check ──▶ Mode Gate ──▶ Quote Fetch
       ──▶ Slippage Estimate ──▶ Qty Conversion ──▶ Broker Submit
       ──▶ Fill Monitor ──▶ Slippage Measurement ──▶ Publish Result
```

### Slippage Model

Uses a simplified Almgren-Chriss model:
- Estimates pre-trade slippage based on order size, daily volume, volatility
- Blocks orders if estimated slippage > `max_slippage_bps` (default: 8 bps)
- Measures actual post-trade slippage and logs it
- Capped at 50 bps maximum

### Fill Monitoring

After order submission, the fill monitor polls the broker at 500ms intervals until:
- Order reaches terminal status (filled, cancelled, expired, rejected)
- 60-second timeout (after which it attempts to cancel)

---

## 12. Broker Setup

### Alpaca (Default)

1. Create an account at [alpaca.markets](https://alpaca.markets)
2. Get your API key and secret from the dashboard
3. Add to `.env`:

```env
ALPACA_API_KEY=your-key
ALPACA_SECRET_KEY=your-secret
ALPACA_PAPER=true
```

4. Set `execution.broker` to `alpaca_paper` (or `alpaca_live`) in the Config UI

**Features:** Fractional shares, paper trading, REST + WebSocket, no commission.

### MetaTrader 5 (CMC Markets, Admiral Markets, etc.)

MT5 trades **ETF CFDs** (not spot ETFs). Key differences from Alpaca:

| Aspect | Alpaca | MT5 (CMC Markets) |
|--------|--------|-------------------|
| Instrument | Real ETFs | ETF CFDs |
| Overnight cost | None | Financing charge |
| Spreads | Market | Wider (broker markup) |
| Leverage | 1:1 (or 2:1 margin) | 5:1 to 20:1 |
| Fractional shares | Yes | No (lot-based) |
| Platform | Cloud API | Windows desktop app (IPC) |

**Setup:**

1. Install MetaTrader 5 terminal on Windows
2. Connect to your broker (CMC Markets, Admiral Markets, etc.)
3. Install the Python package: `pip install MetaTrader5`
4. Add to `.env`:

```env
MT5_LOGIN=12345678
MT5_PASSWORD=your-password
MT5_SERVER=CMCMarkets-Demo
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

5. Set `execution.broker` to `mt5` in the Config UI

**Important notes:**
- MT5 Python API works on **Windows only** (uses IPC, not network)
- The MT5 terminal must be **running** on the same machine
- Start with a **demo account** before going live
- Symbol names vary by broker (e.g., `SPY.US`, `#SPY`, `SPY_d`)
- Lot conversion is handled automatically by the MT5Broker class
- All MT5 API calls are thread-safe with automatic reconnection

**ETF availability on MT5:**

Most major ETFs are available as CFDs: SPY, QQQ, IWM, GLD, XLK, XLF, XLV, XLE. Niche sector ETFs may not be available at all brokers. Verify your broker's instrument list.

---

## 13. Operational Modes

Set via Config UI (`/config` > Mode tab) or `PATCH /api/config/mode`.

### Research Mode

- **Execution:** Simulated fills using a cost model (slippage + spread estimate)
- **Data:** Historical database only
- **Use case:** Backtesting, parameter sweeps, strategy development
- **No broker required**

### Simulation Mode (Default)

- **Execution:** No orders submitted; mark-to-market only
- **Data:** Live feeds from data providers
- **Use case:** Live validation before going live; paper trading without broker API
- **No broker required**

### Live Mode

- **Execution:** Real broker orders (Alpaca or MT5)
- **Data:** Live feeds
- **Use case:** Production trading
- **Requires broker credentials configured**

### Mode Switching

| Transition | Requirement |
|-----------|------------|
| Research -> Simulation | No restrictions |
| Simulation -> Live | Admin confirmation in UI |
| Live -> Simulation | Immediately sets `kill_switch:rebalance = 1` |
| Any -> Research | No restrictions |

---

## 14. Scheduled Tasks

### Compute Schedule

| Time (ET) | Task | Frequency |
|-----------|------|-----------|
| 09:15 | Pre-open validation | Daily |
| 09:30-16:00 | Fast factor recompute (Vol, Liquidity) | Every 5 min |
| 09:30-16:00 | Sentiment update | Every 15 min |
| 09:30-16:00 | Price bar ingestion | Every 60 sec |
| 09:45 | Rebalance execution | Daily (configurable) |
| 16:30 | Full EOD factor recompute (all 8 factors) | Daily |

### Background Workers

Celery handles all background tasks. Start with:

```bash
celery -A celery_worker worker --loglevel=info
```

For production, use separate queues:

```bash
celery -A celery_worker worker -Q default,market_data,sentiment --concurrency=4
celery -A celery_worker worker -Q execution --concurrency=1  # single worker for order safety
```

---

## 15. Troubleshooting

### Common Issues

**"Connection refused" on database**
- Ensure Docker is running: `docker-compose ps`
- Windows: Use `127.0.0.1` instead of `localhost` in DATABASE_URL

**"No module named 'MetaTrader5'"**
- MT5 package is Windows-only. On Linux/Mac, tests use a mock module
- Install with: `pip install MetaTrader5`

**Factor scores are stale**
- Check that Celery workers are running
- Check data provider API keys are configured
- View logs: `celery -A celery_worker worker --loglevel=debug`

**Dashboard not updating in real time**
- Check Redis is running: `redis-cli ping`
- Check WebSocket connection in browser dev tools (Network > WS)
- Verify the Redis bridge is active in Flask logs

**Orders stuck in "pending"**
- Check kill switches: `redis-cli keys "kill_switch:*"`
- Check operational mode: `redis-cli hget config:system operational_mode`
- Check broker connectivity in the Execution view

**"Kill switch active" blocking trades**
- View active switches: `/config` > Kill Switches tab
- Clear via UI or: `redis-cli del kill_switch:trading`

### Useful Redis Commands

```bash
# Check operational mode
redis-cli hget config:system operational_mode

# View all kill switches
redis-cli keys "kill_switch:*"

# Check factor weights
redis-cli hgetall config:weights

# View latest composite scores
redis-cli get cache:latest_scores

# Check pipeline status
redis-cli keys "cache:pipeline:*"

# Monitor all pub/sub messages
redis-cli psubscribe "channel:*"

# Check queue depths
redis-cli llen channel:orders_proposed
redis-cli llen channel:orders_approved
```

### Running Tests

```bash
# Full suite (no Docker required — uses SQLite + fakeredis)
pytest

# Single module
pytest tests/test_factors/

# Single test
pytest tests/test_factors/test_volatility.py::test_zscore

# With verbose output
pytest -v

# Show print output
pytest -s
```

---

## 16. Production Deployment

### Server Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB SSD | 50 GB SSD |
| PostgreSQL disk | 50 GB SSD | 200 GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

For MT5: Add a Windows VPS (2 vCPU, 4 GB RAM, 30 GB SSD, Windows Server 2022).

### Key Production Rules

1. **Gunicorn with Eventlet:** Must use exactly 1 worker for Flask-SocketIO. Configure Redis message queue for multi-process support.

```bash
gunicorn -w 1 -k eventlet --bind 0.0.0.0:5000 "app:create_app()"
```

2. **Execution queue:** Run with `--concurrency=1` to prevent parallel order submission (race conditions).

3. **Redis database separation:**
   - DB 0: Celery broker (noeviction)
   - DB 1: Cache (allkeys-lru)
   - DB 2: SocketIO message queue (noeviction)

4. **Nginx:** Reverse proxy with WebSocket upgrade, SSL, rate limiting.

5. **Alembic migrations:** Always run before deploying new app code.

6. **Never use Flask dev server** (`flask run`) in production.

### Docker Compose (Full Stack)

```bash
docker-compose -f docker-compose.prod.yml up -d
```

Services: nginx, web (Gunicorn), celery_worker, celery_beat, flower (task monitor), db (PostgreSQL 16), redis (Redis 7).

### Security Checklist

- [ ] SSH key-only auth (no passwords)
- [ ] Firewall: allow 80/443 only; block 5432, 6379, 5000
- [ ] `.env` file chmod 600
- [ ] API keys encrypted with Fernet in database
- [ ] HTTPS with Let's Encrypt
- [ ] Fail2ban installed
- [ ] Non-root user for all services
- [ ] Database backups daily (14-day retention)

---

*Paisabot is a quantitative research and trading tool. Past performance does not guarantee future results. Always monitor live trading and understand the risks involved.*
