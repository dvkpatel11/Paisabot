# System Architecture

This is the canonical system design document. It defines module boundaries, data flow, inter-module communication, operational modes, and observability requirements. For implementation details (library versions, Redis key catalog, file structure, build order) see [architecture_and_stack.md](architecture_and_stack.md).

---

## Module Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                     EXTERNAL DATA SOURCES                            │
│  Alpaca │ Polygon │ Databento │ Finnhub │ CBOE │ Reddit │ NewsAPI    │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  MODULE 1 — MARKET DATA LAYER                                        │
│  Ingest, normalize, and publish all market data                      │
│  WebSocket consumer + REST pollers + Celery backfill workers         │
│  Outputs to: PostgreSQL (persistent) + Redis cache + message bus     │
└──────────────────────┬───────────────────────────────────────────────┘
                       │  Redis channel: channel:bars, channel:quotes
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  MODULE 2 — RESEARCH & FACTOR ENGINE                                 │
│  Compute 8 factor scores per ETF                                     │
│  Batch daily (EOD) + intraday fast factors every 5 min               │
│  Outputs factor scores to Redis + DB                                 │
└──────────────────────┬───────────────────────────────────────────────┘
                       │  Redis channel: channel:factor_scores
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  MODULE 3 — SIGNAL ENGINE                                            │
│  CompositeScorer → RegimeDetector → SignalFilter → SignalRanker      │
│  Produces: long candidates, short candidates, signal confidence      │
└──────────────────────┬───────────────────────────────────────────────┘
                       │  Redis channel: channel:signals
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  MODULE 4 — PORTFOLIO CONSTRUCTION ENGINE                            │
│  PyPortfolioOpt with sector/position/vol constraints                 │
│  Produces: proposed portfolio weights + rebalance order list         │
└────────┬─────────────┴──────────────────────────────────────────────┘
         │                         ↑ risk check feedback
         │  Redis channel:          │
         │  channel:orders_proposed │
         ▼                         │
┌────────────────────┐  ┌──────────┴─────────────────────────────────┐
│  MODULE 5          │  │  MODULE 6 — RISK ENGINE                     │
│  EXECUTION ENGINE  │  │  Pre-trade approval gate                    │
│  AlpacaBroker      │  │  Drawdown monitor (continuous)              │
│  Order routing     │  │  Stop-loss scanner (continuous)             │
│  Fill tracking     │  │  VaR + correlation shock detection          │
│  Slippage logging  │  │  Kill switch enforcement                    │
└────────┬───────────┘  └─────────────────────────────────────────────┘
         │  Redis channel: channel:trades, channel:fills
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  MODULE 7 — MONITORING & ANALYTICS LAYER                             │
│  Flask application + WebSocket dashboard + REST API                  │
│  Flask-Admin config/kill-switch UI                                   │
│  Consumes all Redis channels; persists metrics to DB                 │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Module Definitions

### Module 1 — Market Data Layer

**Responsibility**: Ingest all external data, normalize it to a standard schema, and make it available to downstream modules via Redis cache and pub/sub channels.

**Inputs**:

| Data Type | Provider | Delivery |
|-----------|----------|---------|
| ETF OHLCV bars (1-min, daily) | Alpaca, Polygon | WebSocket (real-time) + REST (batch) |
| Order book depth (L1/L2) | Alpaca quotes, Databento | WebSocket |
| Options chains + IV | Polygon, Tradier | REST every 5 min |
| VIX / volatility index | CBOE (daily file), FRED (`VIXCLS`) | REST daily |
| News headlines | Finnhub, NewsAPI | REST every 15 min |
| Social sentiment | Reddit/PRAW, StockTwits | REST every 15 min |
| Macro indicators | FRED API | REST daily |

**Normalization rules**:
- All timestamps converted to UTC before storage
- Missing bars filled with prior close (OHLCV carry-forward); flagged with `is_synthetic=true`
- Delayed or stale feeds detected when timestamp age > configurable `alert_factor_stale_minutes`
- All prices stored as `DECIMAL(12,4)`; volumes as `BIGINT`

**Outputs**:
- PostgreSQL: `price_bars`, `quotes`, `options_chains`, `sentiment_raw` tables
- Redis: TTL-keyed hash caches per symbol
- Pub/sub: `channel:bars`, `channel:quotes` (best-effort real-time events)

**Implementation reference**: `app/data/` — `alpaca_provider.py`, `polygon_provider.py`, `ingestion_jobs.py`, `websocket_listener.py`

---

### Module 2 — Research & Factor Engine

**Responsibility**: Compute all 8 factor scores per ETF on schedule. Each factor is an independent class implementing a standard interface, enabling isolated testing and replacement.

**Factor interface**:
```python
class FactorBase(ABC):
    @abstractmethod
    def compute(self, symbols: list[str]) -> dict[str, float]:
        """Returns {symbol: normalized_score_in_[0,1]}"""
```

**Factors computed**:

| Factor | Class | Update Cadence |
|--------|-------|---------------|
| Volatility regime | `VolatilityFactor` | Every 5 min (VIX); GARCH daily |
| Sentiment score | `SentimentFactor` | Every 15 min |
| Dispersion score | `DispersionFactor` | Daily EOD |
| Sector correlation | `CorrelationFactor` | Daily EOD |
| Market breadth | `BreadthFactor` | Daily EOD |
| Trend strength | `TrendFactor` | Daily EOD |
| Liquidity score | `LiquidityFactor` | Every 5 min |
| Slippage estimate | `SlippageFactor` | Pre-trade (on-demand) |

**Example output** (per ETF, stored in Redis hash `scores:{symbol}`):
```
symbol:           XLK
trend_score:      0.81
sentiment_score:  0.63
volatility_score: 0.45
dispersion_score: 0.72
breadth_score:    0.77
correlation_score:0.58
liquidity_score:  0.94
slippage_score:   0.91
calc_time:        2026-03-14T16:30:00Z
```

**Normalization**: All scores use percentile rank, z-score sigmoid, or min-max cap (see [factor_models.md](factor_models.md)). All values are [0, 1]. Higher is always more favorable.

**Outputs**:
- Redis: `scores:{symbol}` hash (15-min TTL), `factor:{symbol}:{factor}` strings (1h TTL)
- PostgreSQL: `factor_scores` table (permanent record)
- Pub/sub: `channel:factor_scores` (triggers Signal Engine)

**Implementation reference**: `app/factors/`, `app/factors/factor_registry.py`
**Factor catalog** (all 11 factors, 7 attributes each, extensibility guide): [alpha_factor_library.md](alpha_factor_library.md)
**Detailed formulas**: [factor_models.md](factor_models.md)

---

### Module 3 — Signal Engine

**Responsibility**: Convert factor scores into ranked trade signals. Applies the composite weighting model, detects regime, enforces hard filters, and classifies each ETF as a long candidate, short candidate, or avoid.

**Processing stages**:

1. **CompositeScorer** — weighted sum of factor scores (weights from `config:weights` Redis hash)
2. **RegimeDetector** — classifies market as `trending` / `rotation` / `risk_off` / `consolidation` using breadth, correlation, dispersion, and trend scores
3. **SignalFilter** — enforces kill switches, liquidity gates, and spread hard limits
4. **SignalRanker** — sorts by composite score; assigns signal type

**Composite score formula**:
```
ETF_SCORE = 0.25 * Trend
           + 0.20 * Volatility_Regime
           + 0.15 * Sentiment
           + 0.15 * Breadth
           + 0.15 * Dispersion
           + 0.10 * Liquidity
```

**Signal classification**:

| Score | Signal Type | Action |
|-------|------------|--------|
| ≥ 0.65 | `long` | Eligible for long allocation |
| 0.40–0.64 | `neutral` | Hold only; no new entry |
| < 0.40 | `avoid` | Exit if held |

**Short candidates**: In `risk_off` regime, ETFs with composite score < 0.25 and negative momentum (trend_score < 0.30) may be flagged as short candidates. Short execution requires `allow_short = true` in `config:execution` and broker support. Default: disabled.

**Output per signal cycle**:
```python
{
  'long_candidates':  ['XLK', 'XLV', 'XLC'],       # score >= 0.65
  'short_candidates': ['XLE'],                       # score < 0.25, risk_off only
  'neutral':          ['XLF', 'XLI'],
  'regime':           'rotation',
  'regime_confidence': 0.71,
  'signal_confidence': {symbol: composite_score},    # full ranking
  'calc_time':        '2026-03-14T16:30:05Z',
}
```

**Implementation reference**: `app/signals/`
**Detailed design**: [signal_engine.md](signal_engine.md)
**Regime logic**: [rotation_detection.md](rotation_detection.md)

---

### Module 4 — Portfolio Construction Engine

**Responsibility**: Transform signal output into a concrete portfolio allocation respecting all position, sector, and volatility constraints.

**Inputs**: long candidates list + current positions + portfolio value + `config:portfolio` settings

**Processing stages**:

1. **Candidate selection**: top-N from long candidates (N = `max_positions`, reduced in risk-off)
2. **Optimization**: PyPortfolioOpt — default objective `max_sharpe` with Ledoit-Wolf covariance
3. **Constraint enforcement**: max 5% per position, max 25% per sector, max 50% turnover
4. **Volatility scaling**: scale weights down proportionally when portfolio vol > `vol_target` (12%)
5. **Order generation**: diff current vs target weights; skip legs < 50 bps

**Hard constraints**:

| Parameter | Value | Config Key |
|-----------|-------|-----------|
| Max positions | 10 | `portfolio.max_positions` |
| Max position size | 5% | `portfolio.max_position_size` |
| Max sector exposure | 25% | `portfolio.max_sector_exposure` |
| Cash buffer | 5% | `portfolio.cash_buffer_pct` |
| Max turnover per cycle | 50% | `portfolio.turnover_limit_pct` |

**Output**:
```python
{
  'target_weights':     {'XLK': 0.048, 'XLV': 0.045, 'XLC': 0.042, ...},
  'proposed_orders':    [{'symbol': 'XLK', 'side': 'buy', 'notional': 2400.0}, ...],
  'expected_vol':       0.11,          # annualized
  'expected_sharpe':    1.8,
  'sector_exposures':   {'Technology': 0.23, 'Health Care': 0.18, ...},
}
```

Before orders are sent to the Execution Engine, they pass through a **Risk Engine pre-trade gate**.

**Implementation reference**: `app/portfolio/`
**Detailed design**: [portfolio_construction.md](portfolio_construction.md)

---

### Module 5 — Execution Engine

**Responsibility**: Submit orders to the broker, track fill status, measure execution quality, and log slippage.

**Inputs**: approved order list from Risk Engine pre-trade gate

**Processing**:
1. Pre-trade slippage estimate (Almgren-Chriss model) — abort if estimated slippage > `execution.max_slippage_bps`
2. Order submission to Alpaca (paper or live, per `execution.broker` config)
3. Fill monitoring via order status polling / WebSocket fills feed
4. Post-trade slippage measurement: `(fill_price - mid_at_submission) / mid * 10_000`
5. Trade record persisted to `trades` table

**Execution considerations**:
- Default order type: `market` (configurable to `limit` or `vwap`)
- Execution window: spread buy/sell orders over first 30 min after open (configurable)
- Sell orders submitted first (free up cash before buying)
- Fractional shares enabled by default for dollar-notional orders

**Operational mode behavior**:
- **Research**: No orders submitted; fills simulated using transaction cost model
- **Simulation**: No orders submitted; live prices used for mark-to-market PnL tracking
- **Live**: Real orders submitted to configured broker

**Outputs**:
- PostgreSQL: `trades` table
- Redis: `channel:trades`, `channel:fills` pub/sub
- Slippage stats published to monitoring dashboard

**Implementation reference**: `app/execution/`

---

### Module 6 — Risk Engine

**Responsibility**: Continuously protect the portfolio with two distinct functions:
1. **Pre-trade gate**: approves or blocks proposed orders from Portfolio Construction
2. **Continuous monitor**: watches live portfolio for breach conditions; issues kill actions

**Pre-trade checks** (run before any order list enters Execution):
- Portfolio-level: would this rebalance push drawdown below limit?
- Position-level: is concentration within bounds post-trade?
- Liquidity: is current-session ADV sufficient for proposed notional?
- Regime override: are short orders blocked in non-risk-off regime?

**Continuous monitors** (run every tick / every 1 min):

| Monitor | Trigger | Action |
|---------|---------|--------|
| Drawdown | Portfolio value drop > 15% from peak | Set `kill_switch:trading = 1`; alert |
| Daily loss | Single-day portfolio loss > 3% | Halt for remainder of session; alert |
| Position stop-loss | Position drop > 5% from entry | Liquidate position immediately |
| Trailing stop | Position drop > 8% from high-water mark | Liquidate position immediately |
| Correlation shock | Avg pairwise correlation > 0.85 for 3+ days | Alert; force diversification |
| VaR breach | 1-day 95% VaR > 2% of portfolio | Alert; scale positions |
| Liquidity shock | Current ADV < 50% of 30-day ADV | Suspend new entries for that ETF |
| Factor staleness | Any factor score > 30 min old | Alert; block new entries until refreshed |

**Kill actions available**:
```
kill_switch:trading      → block all order submission
kill_switch:rebalance    → hold current positions; no new rebalancing
kill_switch:all          → halt all system operations
kill_switch:force_liquidate → sell all open positions immediately
```

All kill switches are settable by the Risk Engine automatically (drawdown halt) or manually via the Admin UI.

**Implementation reference**: `app/risk/`
**Detailed framework**: [risk_framework.md](risk_framework.md)

---

### Module 7 — Monitoring & Analytics Layer

**Responsibility**: Provide real-time visibility into all system components via a browser dashboard, REST API, and admin configuration interface.

**Components**:

| Sub-component | Technology | Path |
|---------------|-----------|------|
| WebSocket dashboard | Flask-SocketIO + browser JS | `/dashboard` namespace |
| Redis bridge | Background thread; Redis sub → `socketio.emit()` | `app/streaming/redis_bridge.py` |
| REST API | Flask Blueprint | `/api/*` |
| Admin config UI | Flask-Admin | `/admin/*` |
| Kill switch panel | Custom Flask-Admin view | `/admin/killswitch` |

**Dashboard metrics** (real-time, pushed via WebSocket):

| Panel | Metrics |
|-------|---------|
| Portfolio PnL | Intraday PnL, cumulative return, drawdown gauge |
| Current positions | Symbol, weight, entry price, unrealized PnL, stop distance |
| Factor scores | Per-ETF heatmap of all 8 factors; staleness indicators |
| Market regime | Current regime + confidence score + regime history |
| Risk exposures | VaR, sector exposures, portfolio beta, correlation heatmap |
| Signal ranking | Full ETF universe ranked by composite score; long/short/neutral labels |
| Execution log | Recent trades, fill prices, slippage vs estimate |
| System health | Data feed status, last factor compute time, worker queue depth, Redis latency |

**REST API endpoints** (`/api/`):

| Endpoint | Method | Response |
|----------|--------|----------|
| `/api/scores` | GET | Latest composite scores for all ETFs |
| `/api/portfolio` | GET | Current positions, weights, PnL |
| `/api/signals` | GET | Latest signal output |
| `/api/regime` | GET | Current regime + confidence |
| `/api/risk` | GET | Current VaR, drawdown, exposures |
| `/api/health` | GET | System component status |
| `/api/trades` | GET | Recent trade log |
| `/api/backtest` | POST | Trigger backtest run (Research mode only) |

**WebSocket events** (server → client, namespace `/dashboard`):

| Event | Trigger | Payload |
|-------|---------|---------|
| `factor_scores` | Factor recompute complete | `{symbol: {factor: score}}` |
| `signals` | Signal cycle complete | `{long: [], short: [], regime: str}` |
| `portfolio` | Rebalance complete | `{weights: {}, pnl: float}` |
| `risk_alert` | Risk threshold breach | `{type: str, value: float, action: str}` |
| `trade` | Order filled | `{symbol, side, fill_price, slippage_bps}` |
| `system_health` | Any component status change | `{component: str, status: str}` |

**Implementation reference**: `app/streaming/`, `app/api/`, `app/admin/`
**Frontend specification**: [frontend_dashboard.md](frontend_dashboard.md) — all 6 views, WebSocket event handling, Flask routes, CSS design system, component library choices

---

## Inter-Module Communication (Message Bus)

All modules communicate via **Redis pub/sub channels**. This decouples producers from consumers and allows the dashboard to subscribe to all events without any module needing direct knowledge of the UI.

| Channel | Producer | Consumers | Delivery |
|---------|----------|-----------|---------|
| `channel:bars` | Market Data Layer | Factor Engine (fast factors) | Best-effort |
| `channel:quotes` | Market Data Layer | Execution Engine, Risk Engine | Best-effort |
| `channel:factor_scores` | Factor Engine | Signal Engine, Dashboard | Best-effort |
| `channel:signals` | Signal Engine | Portfolio Construction, Dashboard | Best-effort |
| `channel:orders_proposed` | Portfolio Construction | Risk Engine (pre-trade gate) | Queued (list) |
| `channel:orders_approved` | Risk Engine | Execution Engine | Queued (list) |
| `channel:trades` | Execution Engine | Risk Engine, Dashboard | Best-effort |
| `channel:fills` | Execution Engine | Risk Engine, Dashboard | Best-effort |
| `channel:risk_alerts` | Risk Engine | Dashboard, Alert Manager | Queued (list) |
| `channel:regime_change` | Signal Engine | Portfolio Construction, Dashboard | Queued (list) |

**Critical rule**: Use `LPUSH` / `BRPOP` (Redis list queue) for channels that must not lose messages (`channel:orders_proposed`, `channel:orders_approved`, `channel:risk_alerts`, `channel:regime_change`). Use pub/sub (fire-and-forget) only for dashboard display events (`channel:bars`, `channel:factor_scores`, `channel:trades`).

---

## Operational Modes

The system supports three modes, set via `config:system.operational_mode` (or `--mode` CLI flag). The mode gates the Execution Engine and changes the behavior of the entire pipeline.

### Research Mode

Used for backtesting, parameter optimization, and factor development.

| Behavior | Detail |
|---------|--------|
| Data source | Historical data from PostgreSQL / flat files; no live feed |
| Execution | Simulated fills using `TransactionCostModel`; no broker connection |
| Risk engine | Full risk calculation against simulated P&L; kill switches inactive |
| Backtest API | `/api/backtest` endpoint active |
| Factor engine | Can be run with arbitrary historical date ranges |
| Dashboard | Shows backtest PnL curve and factor score history |

```python
# Operational mode check pattern used throughout the codebase
if config.get('system', 'operational_mode') == 'research':
    fills = cost_model.simulate_fills(orders, prices_df)
else:
    fills = broker.submit_orders(orders)
```

### Simulation Mode

Runs the full live pipeline — real data, real signal generation, real portfolio construction — but submits no orders to the broker.

| Behavior | Detail |
|---------|--------|
| Data source | Live feeds (Alpaca WebSocket, Finnhub, etc.) |
| Execution | No orders submitted; uses last-price for mark-to-market |
| Risk engine | Full risk monitoring against simulated positions |
| Kill switches | Active; but `force_liquidate` is a no-op |
| Dashboard | Shows live factor scores, simulated PnL, signal ranking |

Use case: validate a new strategy configuration or new factors in a live environment before committing real capital.

### Live Trading Mode

Full production operation. All modules active. Requires explicit admin enablement.

| Behavior | Detail |
|---------|--------|
| Data source | Live feeds |
| Execution | Real orders via configured broker (`alpaca_paper` or `alpaca_live`) |
| Risk engine | Full enforcement; all kill switches operational |
| Mode change | Requires admin to flip `config:system.operational_mode` AND disable `kill_switch:trading` |
| Audit | All state changes logged with user, timestamp, and before/after values |

**Transition guard**: Switching from Simulation → Live requires confirmation in the Admin UI. Switching from Live → Simulation immediately sets `kill_switch:rebalance = 1` (no new orders during transition).

---

## Full Data Flow

```
Market Data Feed (Alpaca WS / Polygon / CBOE / Finnhub / Reddit)
        │
        ▼
Data Normalization (UTC timestamps, missing bar fill, staleness flag)
        │
        ├──→ PostgreSQL: price_bars, quotes, options_chains, sentiment_raw
        └──→ Redis: ohlcv cache + channel:bars pub/sub
        │
        ▼
Factor Engine (8 factor classes via FactorRegistry)
        │
        ├──→ PostgreSQL: factor_scores table
        └──→ Redis: scores:{symbol} hash + channel:factor_scores pub/sub
        │
        ▼
Signal Engine (CompositeScorer → RegimeDetector → SignalFilter → SignalRanker)
        │
        ├──→ PostgreSQL: signals table
        └──→ Redis: cache:latest_scores + channel:signals pub/sub
        │
        ▼
Portfolio Construction Engine (PyPortfolioOpt + ConstraintEnforcer)
        │
        └──→ Redis: channel:orders_proposed LIST (queued)
        │
        ▼
Risk Engine — Pre-Trade Gate
        │  (block if kill switch active, drawdown at limit,
        │   or concentration would be breached)
        │
        └──→ Redis: channel:orders_approved LIST (queued)
        │
        ▼
Execution Engine (AlpacaBroker → order routing)
        │
        ├──→ Broker (Alpaca paper / live)
        └──→ Redis: channel:fills + channel:trades pub/sub
        │   PostgreSQL: trades table
        │
        ▼
Risk Engine — Post-Trade Monitor (continuous)
        │
        ├──→ Redis: channel:risk_alerts LIST (queued)
        │   kill_switch:* keys on breach
        │
        ▼
Dashboard (Flask-SocketIO)
        │  (subscribes to all channels; pushes to browser clients)
        │
        └──→ Browser: real-time PnL, factor heatmap, positions, regime,
                      risk gauges, signal ranking, execution log
```

---

## Logging and Observability

Every module must emit structured logs using `structlog` (or `loguru`). Logs are the primary post-trade debugging tool and must be complete enough to replay any system decision.

**Minimum required log events per module**:

| Module | Required Log Events |
|--------|-------------------|
| Market Data | Feed connected/disconnected, missing bar detected, stale data alert, backfill complete |
| Factor Engine | Factor compute start/end, score per ETF, normalization window size, factor skipped (insufficient data) |
| Signal Engine | Composite score per ETF, regime classification + confidence, signals blocked by filter, kill switch state |
| Portfolio Construction | Candidate list, optimization objective, resulting weights, turnover calculated, orders generated |
| Execution Engine | Order submitted (symbol, side, notional, order_type), fill received (fill_price, slippage_bps), order cancelled |
| Risk Engine | Pre-trade decision (approved/blocked + reason), continuous monitor trigger (breach type, value, threshold), kill switch activated/deactivated |
| Admin / Config | Config value changed (category, key, old_value, new_value, changed_by) |

**Log structure** (JSON-formatted for log aggregation):
```json
{
  "timestamp": "2026-03-14T16:30:05.123Z",
  "level": "info",
  "module": "factor_engine",
  "event": "factor_computed",
  "symbol": "XLK",
  "factor": "trend",
  "raw_value": 0.0842,
  "normalized_score": 0.81,
  "lookback_days": 252,
  "operational_mode": "simulation"
}
```

**Retention**: Application logs → rolling file (7-day local) + optional forwarding to log aggregator. All PostgreSQL tables (factor_scores, signals, trades, performance_metrics) serve as the permanent structured audit trail.

**Post-trade analysis queries** (examples):
```sql
-- Reconstruct signal state at any past time
SELECT * FROM signals WHERE signal_time::date = '2026-02-01' ORDER BY composite_score DESC;

-- Measure slippage vs estimate over past month
SELECT symbol, AVG(slippage_bps) as avg_slippage
FROM trades
WHERE trade_time > NOW() - INTERVAL '30 days'
GROUP BY symbol ORDER BY avg_slippage DESC;

-- Track regime history
SELECT signal_time, regime, MAX(composite_score)
FROM signals GROUP BY signal_time, regime ORDER BY signal_time;
```

---

## System Goals and Design Principles

| Goal | How It Is Achieved |
|------|------------------|
| **Modularity** | Each of the 7 modules has a defined interface and communicates only via Redis channels or shared DB tables — no direct imports across module boundaries |
| **Scalability** | Factor Engine workers can be scaled horizontally with Celery; Flask-SocketIO uses `message_queue='redis://'` for multi-process WebSocket support |
| **Transparency** | Every factor score, signal, and trade is persisted with a timestamp; full audit trail queryable from PostgreSQL |
| **Risk control** | Risk Engine has two enforcement points (pre-trade gate + continuous monitor); kill switches settable by both automated triggers and human admin |
| **Reproducibility** | Research Mode runs the exact same factor code against historical data; operational mode is injected as config, not baked into module logic |

---

## Cross-Reference Index

| Topic | Document |
|-------|---------|
| All 11 factor definitions (7 attributes each), registry, extensibility | [alpha_factor_library.md](alpha_factor_library.md) |
| Factor formulas, lookbacks, normalization, edge cases | [factor_models.md](factor_models.md) |
| Regime detection, rotation confidence score | [rotation_detection.md](rotation_detection.md) |
| Signal engine implementation details | [signal_engine.md](signal_engine.md) |
| Portfolio construction code, PyPortfolioOpt | [portfolio_construction.md](portfolio_construction.md) |
| Risk controls, stop-loss ladder | [risk_framework.md](risk_framework.md) |
| Backtesting methodology, Monte Carlo | [backtesting_results.md](backtesting_results.md) |
| Admin config parameter catalog | [admin_config_reference.md](admin_config_reference.md) |
| Frontend views, WebSocket events, Flask routes, CSS design system | [frontend_dashboard.md](frontend_dashboard.md) |
| Python libraries, Redis key map, file structure, build order | [architecture_and_stack.md](architecture_and_stack.md) |
| ETF universe with AUM, ADV, spread | [ETF_universe.csv](ETF_universe.csv) |
