# Frontend Dashboard Specification

## Overview

The Paisabot frontend is a **single-page Flask application** with real-time WebSocket updates. It uses a dark terminal aesthetic (Bloomberg-style) with a monospace font and high-contrast tables. All live data is pushed from the server via Flask-SocketIO — no polling from the client.

The UI has two distinct surfaces:

| Surface | Technology | Purpose |
|---------|-----------|---------|
| **Trading Dashboard** | Jinja2 templates + Vanilla JS + Plotly.js + Socket.IO client | Real-time monitoring, analytics, controls, and system configuration |
| **Flask-Admin** | Flask-Admin (CRUD auto-generated) | Low-level DB editing, universe table management, advanced overrides |

The dashboard's **Config view (`/config`)** is the primary operator interface for system parameters. Flask-Admin at `/admin` remains available as a power-user/emergency fallback for direct DB access. All config writes from either surface go through the same backend: PostgreSQL `system_config` → Redis `config:*` hash sync.

---

## Technology Choices

### Frontend Libraries (served via CDN or local static)

| Library | Version | Purpose |
|---------|---------|---------|
| `socket.io-client` | 4.x | WebSocket connection to Flask-SocketIO server |
| `Plotly.js` | 5.x | All charts (line, heatmap, bar, gauge) — dark theme built-in |
| `Chart.js` | 4.x | Lightweight alternative for simple bar/line charts |
| `Tabulator` | 6.x | Real-time sortable/filterable tables (ETF universe, factor scores, orders) |
| `Alpine.js` | 3.x | Lightweight reactive bindings for toggle states, modals (no full framework needed) |
| `Font: JetBrains Mono` | — | Monospace for all numbers and tables |

**Why not React/Vue?** Flask + Jinja2 + Alpine.js is sufficient for a monitoring dashboard and avoids a full JS build pipeline. Plotly.js handles all complex charting.

### CSS Design System

```css
:root {
  --bg-primary:     #0d1117;   /* deep black-grey */
  --bg-secondary:   #161b22;   /* panel background */
  --bg-tertiary:    #21262d;   /* table row hover */
  --border:         #30363d;   /* panel borders */
  --text-primary:   #e6edf3;   /* main text */
  --text-secondary: #8b949e;   /* labels, captions */
  --green:          #3fb950;   /* positive PnL, uptrend */
  --red:            #f85149;   /* negative PnL, downtrend, alerts */
  --yellow:         #d29922;   /* warnings */
  --blue:           #58a6ff;   /* links, highlights */
  --orange:         #e3b341;   /* neutral/moderate signals */
  --font-mono:      'JetBrains Mono', 'Courier New', monospace;
}
```

All Plotly charts use `template='plotly_dark'` with background overridden to `var(--bg-secondary)`.

---

## Flask Routes

```python
# app/api/routes.py — REST API
GET  /api/scores          → latest composite scores for all ETFs
GET  /api/portfolio       → current positions, weights, PnL
GET  /api/signals         → latest signal output (long/short/neutral)
GET  /api/regime          → current regime + confidence
GET  /api/risk            → VaR, drawdown, exposures, stop status
GET  /api/health          → all component statuses
GET  /api/trades          → recent trade log (last 100)
GET  /api/factors/{symbol} → factor score history for one ETF (query: ?days=30)
GET  /api/backtest/results       → latest backtest tearsheet data
POST /api/backtest/run          → trigger backtest (research mode only)

# Config API — all write to PostgreSQL + sync Redis
GET  /api/config                → all config categories as nested JSON
GET  /api/config/<category>     → single category (e.g., "weights", "risk")
PATCH /api/config/<category>    → update one or more keys in a category
POST  /api/config/reset/<category> → reset category to seed defaults
GET  /api/config/audit          → config change audit log (last 200 entries)
PATCH /api/config/weights       → dedicated weights endpoint (validates sum = 1.0)
PATCH /api/config/mode          → set operational_mode (research/simulation/live)
PATCH /api/control/<switch>     → toggle kill switch (trading/rebalance/all/etc.)
POST  /api/control/force_liquidate → force-liquidate all positions (danger)

# app/views/routes.py — Dashboard pages
GET  /                    → redirect to /dashboard
GET  /dashboard           → Dashboard Home (View 1)
GET  /factors             → Factor & Signal Explorer (View 2)
GET  /rotation            → Sector Rotation View (View 3)
GET  /execution           → Execution & Risk Control View (View 4)
GET  /analytics           → Analytics / Backtesting View (View 5)
GET  /alerts              → Alerts & Notifications View (View 6)
GET  /config              → System Configuration View (View 7)
```

---

## WebSocket Events (server → client)

All events are emitted on the `/dashboard` Socket.IO namespace. The client connects once on page load and receives all relevant events regardless of which view is active.

```javascript
const socket = io('/dashboard');

socket.on('connect',       () => console.log('Connected'));
socket.on('factor_scores', data => updateFactorTable(data));
socket.on('signals',       data => updateSignalRanking(data));
socket.on('portfolio',     data => updatePortfolioPanels(data));
socket.on('risk_alert',    data => showAlert(data));
socket.on('trade',         data => appendTradeLog(data));
socket.on('system_health', data => updateHealthPanel(data));
socket.on('regime_change', data => updateRegimeIndicator(data));
```

| Event | Frequency | Payload Summary |
|-------|-----------|----------------|
| `factor_scores` | Every 5 min + on change | `{symbol: {factor_name: score, ...}}` for all ETFs |
| `signals` | Every signal cycle | `{long: [], short: [], neutral: [], regime, confidence}` |
| `portfolio` | On rebalance / every 1 min | `{positions: {}, weights: {}, pnl: float, drawdown: float}` |
| `risk_alert` | On trigger | `{type, value, threshold, action, timestamp}` |
| `trade` | On fill | `{symbol, side, fill_price, notional, slippage_bps, timestamp}` |
| `system_health` | Every 30 sec | `{component: status}` for all 7 modules |
| `regime_change` | On regime transition | `{from_regime, to_regime, confidence, factors_snapshot}` |
| `config_change` | On any config save | `{category, key, old_value, new_value, changed_by}` |

---

## View 1 — Dashboard Home (`/dashboard`)

**Purpose**: Immediate situational awareness — portfolio status, market regime, and full ETF universe at a glance.

### Layout (3-column grid)

```
┌─────────────────────┬───────────────────┬──────────────────────┐
│  PORTFOLIO SUMMARY  │  MARKET REGIME    │  SYSTEM HEALTH       │
│  PnL / Cash / DD    │  Trending badge   │  Module status dots  │
├─────────────────────┴───────────────────┴──────────────────────┤
│  ETF UNIVERSE TABLE  (full width)                               │
│  Symbol | Price | Trend | Vol | Breadth | Liq | Score | Signal │
└─────────────────────────────────────────────────────────────────┘
```

### Portfolio Summary Panel

```
┌─────────────────────────────────────┐
│  Portfolio Value:   $103,412.50      │
│  Daily PnL:         +$824.30 (+0.8%)│  ← green
│  Cumulative Return: +3.41%           │
│  Cash:              $5,240 (5.1%)    │
│  Drawdown:          -2.1% / -15% max│  ← progress bar, red if >10%
│  Open Positions:    8 / 10 max       │
│  Regime:            ● ROTATION       │  ← colored badge
└─────────────────────────────────────┘
```

Data source: `GET /api/portfolio` on load; updated by `portfolio` WebSocket event.

### Market Regime Indicator

Large colored badge with confidence percentage:

| Regime | Color | Badge Text |
|--------|-------|-----------|
| `trending` | Green | ● TRENDING (71%) |
| `rotation` | Blue | ◎ ROTATION (68%) |
| `risk_off` | Red | ▼ RISK-OFF (82%) |
| `consolidation` | Yellow | — CONSOLIDATION (55%) |

Below the badge: mini-history of last 10 regime states as colored dots (sparkline of regime transitions).

### System Health Panel

Grid of module status indicators:

```
Market Data Layer    ● LIVE    last bar: 0s ago
Factor Engine        ● OK      last run: 3m ago
Signal Engine        ● OK      last run: 3m ago
Portfolio Engine     ● OK      last run: 3m ago
Risk Engine          ● OK      monitoring
Execution Engine     ● PAPER   last trade: 12m ago
Dashboard            ● LIVE    8 clients
```

Each dot: green = OK, yellow = degraded (stale > 5 min), red = error / disconnected.

### ETF Universe Table

Live-updating Tabulator table. Sortable by any column. Color-coded cells.

| Column | Source | Color Logic |
|--------|--------|-------------|
| Symbol | static | — |
| Price | Redis `ohlcv:{sym}` | — |
| Change % | Redis | green if > 0, red if < 0 |
| Trend Score | Redis `scores:{sym}` | gradient: red (0) → yellow (0.5) → green (1.0) |
| Vol Regime | Redis `scores:{sym}` | green = low vol, red = high vol |
| Breadth | Redis `scores:{sym}` | gradient |
| Liquidity | Redis `scores:{sym}` | gradient |
| ETF Score | Redis `cache:latest_scores` | bold; gradient |
| Signal | Redis latest signal | `LONG` (green), `NEUTRAL` (grey), `AVOID` (red) |

```javascript
// Tabulator real-time update
socket.on('factor_scores', data => {
    Object.entries(data).forEach(([symbol, scores]) => {
        table.updateData([{symbol, ...scores}]);
    });
});
```

---

## View 2 — Factor & Signal Explorer (`/factors`)

**Purpose**: Deep dive into factor scores per ETF, signal rankings, and historical factor trends.

### Layout

```
┌───────────────────────────────┬──────────────────────────────┐
│  FACTOR SCORES TABLE          │  SIGNAL RANKING              │
│  ETF × Factor heatmap         │  Long / Short / Neutral      │
├───────────────────────────────┴──────────────────────────────┤
│  HISTORICAL FACTOR CHART  (ETF selector + factor selector)    │
└──────────────────────────────────────────────────────────────┘
```

### Factor Scores Heatmap Table

Full factor matrix — rows are ETFs, columns are factor names, cells are color-coded scores.

```
         Trend  Vol   Senti  Disp  Corr  Bread  Liq   SCORE
XLK      0.81  0.72  0.63   0.72  0.58  0.77  0.94   0.74
QQQ      0.79  0.70  0.61   0.72  0.58  0.77  0.97   0.72
XLV      0.68  0.75  0.55   0.54  0.52  0.71  0.91   0.65
XLE      0.31  0.48  0.42   0.65  0.55  0.44  0.88   0.47
```

Cell background: `hsl(120, 60%, {score*40 + 15}%)` — low score = dim red, high score = bright green. Monospace font for all numbers.

### Signal Ranking Panel

Three columns: Long Candidates | Neutral | Short Candidates

```
  LONG CANDIDATES        NEUTRAL              SHORT CANDIDATES
  ──────────────         ──────────────       ──────────────
  1. XLK  0.74 ▲         XLF  0.56 →          XLE  0.31 ▼
  2. QQQ  0.72 ▲         XLI  0.53 →          XLRE 0.28 ▼
  3. XLV  0.65 ▲         XLY  0.51 →
  4. XLC  0.66 ▲
```

Arrows indicate trend direction (EMA-20 vs EMA-50). Updated by `signals` WebSocket event.

### Historical Factor Chart

Plotly line chart. ETF dropdown (default: SPY) + factor multi-select checkboxes. Displays normalized score over selectable time window (7d / 30d / 90d / 1y).

```python
# Backend endpoint
@api_bp.route('/api/factors/<symbol>')
def factor_history(symbol):
    days = request.args.get('days', 30, type=int)
    rows = db.query(FactorScore).filter_by(symbol=symbol) \
              .filter(FactorScore.calc_time >= datetime.utcnow() - timedelta(days=days)) \
              .order_by(FactorScore.calc_time).all()
    return jsonify({
        'symbol': symbol,
        'dates':  [r.calc_time.isoformat() for r in rows],
        'factors': {
            name: [getattr(r, f'{name}_score') for r in rows]
            for name in FACTOR_NAMES
        }
    })
```

---

## View 3 — Sector Rotation View (`/rotation`)

**Purpose**: Visualize sector dynamics — which sectors are leading, where capital is rotating to, and when rotation conditions are met.

### Layout

```
┌───────────────────────┬──────────────────────────────────────┐
│  SECTOR SCOREBOARD    │  CORRELATION vs DISPERSION CHART     │
│  Momentum ranking     │  Scatter: corr_index vs dispersion   │
├───────────────────────┴──────────────────────────────────────┤
│  REGIME HISTORY TIMELINE            │  ROTATION ALERTS       │
└─────────────────────────────────────┴──────────────────────-─┘
```

### Sector Scoreboard

Horizontal bar chart (Plotly) sorted by `sector_momentum_rank`. Bars colored green (top quartile) to red (bottom quartile).

```
XLK  Technology        ████████████████  0.89
XLV  Health Care       ████████████      0.71
XLC  Comm. Services    ██████████        0.63
XLF  Financials        ████████          0.54
XLI  Industrials       ██████            0.44
XLY  Cons. Discr.      ████              0.33
XLE  Energy            ███               0.24
```

Below the chart: "Top 3 Sectors: XLK, XLV, XLC" | "Bottom 3: XLE, XLRE, XLB"

### Correlation vs Dispersion Chart

Plotly scatter plot. X-axis = `correlation_index` (inverted: right = low correlation), Y-axis = `dispersion_score`. Plot each of the last 60 trading days as a dot, today highlighted in bright blue. Quadrant labels:

```
High Dispersion
Low Correlation     │   High Dispersion
= ROTATION ✓        │   High Correlation
                    │   = FEAR DISPERSION ✗
────────────────────┼────────────────────
Low Dispersion      │   Low Dispersion
Low Correlation     │   High Correlation
= CONSOLIDATION     │   = RISK-OFF
```

### Regime History Timeline

Plotly horizontal bar chart showing regime color bands over time. Each day is a colored segment: green = trending, blue = rotation, red = risk-off, grey = consolidation.

### Rotation Alerts Feed

Real-time scrolling alert log. Populated by `risk_alert` and `regime_change` WebSocket events.

```
[16:32:05]  ● REGIME CHANGE: consolidation → rotation (confidence: 0.68)
[16:30:00]  ● CORRELATION COLLAPSE: z-score = -2.3 (avg corr dropped to 0.31)
[15:45:12]  ◎ DISPERSION: 84th percentile (high rotation signal strength)
[09:31:00]  ● MARKET OPEN: Regime = rotation, 4 long candidates selected
```

---

## View 4 — Execution & Risk Control (`/execution`)

**Purpose**: Monitor live orders and fills, review portfolio exposure, track risk metrics, and provide manual override controls.

### Layout

```
┌────────────────────────┬───────────────────────────────────┐
│  RISK METRICS          │  EXPOSURE & ALLOCATION CHART      │
│  DD / VaR / Vol / Beta │  Sector bars vs 25% limit         │
├────────────────────────┴───────────────────────────────────┤
│  OPEN POSITIONS TABLE  (entry, current, PnL, stop dist)    │
├─────────────────────────────────────────────────────────────┤
│  ORDER / EXECUTION LOG          │  CONTROLS                 │
└─────────────────────────────────┴──────────────────────────┘
```

### Risk Metrics Panel

```
  Current Drawdown   -2.1%    ████░░░░░░░░░░░  (max: -15%)
  Daily VaR (95%)     0.8%    ████░░░░░░░░░░░  (limit: 2%)
  Port. Volatility   10.4%    ████████░░░░░░░  (target: 12%)
  Portfolio Beta      0.74
  Avg Corr (60d)      0.48    ● OK (limit: 0.85)
```

Progress bars fill and color-shift from green → yellow → red as values approach limits. Drawdown gauge uses a Plotly indicator with color bands.

### Exposure & Allocation Chart

Horizontal grouped bar chart. Two bars per sector: current weight (filled) and max limit line (dashed at 25%). Positions over-limit render in red.

### Open Positions Table

```
Symbol  Entry    Current  Chg%   Weight  P&L     Stop Dist  Stop Level
XLK     $212.40  $218.90  +3.1%  4.8%   +$312   -5.2%      $207.78
XLV     $143.20  $141.80  -1.0%  4.5%   -$63    -4.1%      $136.04
QQQ     $448.10  $451.20  +0.7%  4.7%   +$147   -4.6%      $426.70
```

Stop distance cell: green if > 3% away, yellow if 1–3%, red if < 1% (approaching stop).

### Order/Execution Log

```
16:32:11  FILLED   XLC  BUY   $73.42  200 shares  $14,684  slippage: 0.3 bps
16:32:09  SUBMIT   XLC  BUY   mkt     ~200 shares  ~$14,680
16:32:05  FILLED   XLE  SELL  $87.31  170 shares  $14,843  slippage: 0.8 bps
09:45:00  REBALANCE  8 orders generated
```

Updated by `trade` WebSocket event. Scrollable, last 50 entries visible.

### Manual Controls Panel

```
┌────────────────────────────────────────────────────────┐
│  OPERATIONAL MODE:  [ RESEARCH ]  [ SIMULATION ]  [ LIVE ]
│
│  KILL SWITCHES:
│  [ ● PAUSE TRADING ]        kill_switch:trading
│  [ ● PAUSE REBALANCING ]    kill_switch:rebalance
│  [ ● DISABLE SENTIMENT ]    kill_switch:sentiment
│  [ ● MAINTENANCE MODE ]     kill_switch:maintenance
│
│  DANGER ZONE:
│  [ ⚠ FORCE LIQUIDATE ALL ]  requires confirmation modal
└────────────────────────────────────────────────────────┘
```

Each control calls a `PATCH /api/control/{switch}` endpoint that writes to Redis and the audit log. Kill switch buttons require a 2-second hold-to-confirm to prevent accidental clicks. `FORCE LIQUIDATE` shows a modal with typed confirmation (`type "LIQUIDATE" to confirm`).

```python
# Flask endpoint for kill switch control
@api_bp.route('/api/control/<switch>', methods=['PATCH'])
@login_required
def set_kill_switch(switch):
    allowed = ['trading', 'rebalance', 'sentiment', 'maintenance']
    if switch not in allowed:
        abort(403)
    value = request.json.get('active', False)
    redis_client.set(f'kill_switch:{switch}', '1' if value else '0')
    _audit_log(switch, value, current_user.username)
    return jsonify({'switch': switch, 'active': value})
```

---

## View 5 — Analytics / Backtesting (`/analytics`)

**Purpose**: Review strategy performance, understand factor attribution, and run simulation/backtest experiments.

### Layout

```
┌────────────────────────────────────────────────────────────┐
│  PERFORMANCE CHART  (cumulative returns vs SPY benchmark)   │
├──────────────────────────┬─────────────────────────────────┤
│  PERFORMANCE METRICS     │  FACTOR ATTRIBUTION CHART       │
│  Sharpe / MDD / Win rate │  Stacked bar: factor contrib.   │
├──────────────────────────┴─────────────────────────────────┤
│  SIMULATION CONTROLS                                        │
└────────────────────────────────────────────────────────────┘
```

### Performance Chart

Plotly line chart with two traces:
- Strategy cumulative returns (blue line)
- SPY benchmark (grey dashed line)
- Drawdown shaded area below the strategy line (red fill, semi-transparent)

X-axis: date range selector (1M / 3M / 6M / 1Y / All). Y-axis: % return.

### Performance Metrics Table

```
Metric                  Strategy    Benchmark (SPY)
──────────────────────  ────────    ───────────────
CAGR                      18.4%          12.1%
Sharpe Ratio               1.72           1.01
Sortino Ratio              2.14           1.28
Max Drawdown              -9.3%         -14.2%
Win Rate                  58.2%
Calmar Ratio               1.98
Beta                       0.62
Alpha (annual)            +8.1%
Information Ratio          0.89
Avg Monthly Return        +1.41%          +0.93%
```

Data source: `GET /api/backtest/results` → served from most recent `quantstats` tearsheet data stored in DB.

### Factor Attribution Chart

Plotly stacked bar chart. One bar per month. Each bar segment = contribution of one factor to portfolio return. Hovering a segment shows: factor name, weight at that time, and return contribution in bps.

```python
# Attribution calculation (approximate — regression-based)
def compute_factor_attribution(returns_df, factor_scores_df, weights):
    # Regress portfolio returns against factor score changes × weights
    ...
```

### Simulation Controls

```
┌───────────────────────────────────────────────────────┐
│  BACKTEST / PAPER TRADE CONTROLS
│
│  Date Range:   [ 2022-01-01 ] to [ 2024-12-31 ]
│  Initial Capital: [ $100,000 ]
│  Mode:         ( ) Paper  (●) Backtest
│
│  Factor Weights (drag sliders or type):
│  trend_score        [ ████████████░░ 0.25 ]
│  volatility_regime  [ ██████████░░░░ 0.20 ]
│  sentiment_score    [ ███████░░░░░░░ 0.15 ]
│  breadth_score      [ ███████░░░░░░░ 0.15 ]
│  dispersion_score   [ ███████░░░░░░░ 0.15 ]
│  liquidity_score    [ █████░░░░░░░░░ 0.10 ]
│  Sum: 1.00 ✓
│
│  [ RUN BACKTEST ]   (research mode only)
│  [ APPLY WEIGHTS TO LIVE CONFIG ]   (requires confirmation)
└───────────────────────────────────────────────────────┘
```

Factor weight sliders auto-normalize to sum to 1.0 as values are dragged. "Apply to Live Config" calls `PATCH /api/config/weights` which writes to both Redis and PostgreSQL `system_config`.

---

## View 6 — Alerts & Notifications (`/alerts`)

**Purpose**: Centralized alert log with filtering and notification management.

### Layout

```
┌──────────────────────────────────────────────────────────────┐
│  FILTER: [ ALL ] [ RISK ] [ REGIME ] [ FACTOR ] [ EXECUTION ] │
├──────────────────────────────────────────────────────────────┤
│  ALERT LOG (newest first, paginated)                          │
│  Unread badge count in nav                                    │
└──────────────────────────────────────────────────────────────┘
```

### Alert Log

Each entry: timestamp | severity icon | category | message | affected ETF (if any)

```
[2026-03-14 16:32:05]  🔴 RISK      REGIME CHANGE: consolidation → rotation (conf: 0.68)
[2026-03-14 16:30:00]  🔴 RISK      CORRELATION COLLAPSE: z=-2.3, avg corr=0.31
[2026-03-14 15:45:00]  🟡 FACTOR    XLE: sentiment_score stale (42 min, threshold: 30)
[2026-03-14 14:20:00]  🟡 EXECUTION Slippage: XLK BUY 2.1 bps (estimate was 0.5 bps)
[2026-03-14 09:32:00]  🔵 REGIME    Market open: regime=rotation, 4 long candidates
[2026-03-13 16:30:00]  🟡 RISK      Drawdown warning: -8.1% (critical: -12%)
```

Severity icons: 🔴 critical, 🟡 warning, 🔵 info.

Alert types pushed by `risk_alert` WebSocket event and stored in DB. Unread count badge in the navigation sidebar.

### Notification Configuration

Table of alert types with toggle switches:

```
Alert Type                      Channel(s)           Enabled
───────────────────────────────  ────────────────     ───────
Rotation regime detected         Slack, Email         ✓
Risk-off regime triggered        Slack, Email         ✓
Drawdown warning (-8%)           Slack                ✓
Drawdown critical (-12%)         Slack, Email         ✓
Factor staleness (> 30 min)      Email                ✓
Position stop-loss triggered     Slack, Email         ✓
Execution slippage > 3 bps       Email                ○
Mode change (live ↔ simulation)  Slack, Email         ✓
Kill switch activated            Slack, Email, SMS    ✓
```

These settings map directly to the `config:alerts` Redis hash (see [admin_config_reference.md](admin_config_reference.md)).

---

## View 7 — System Configuration (`/config`)

**Purpose**: The operator's primary interface for all system-level parameters. Grouped by category, with live validation, an audit trail, and instant effect — every save writes to PostgreSQL and syncs Redis without requiring a restart.

This view replaces Flask-Admin as the day-to-day config interface. Flask-Admin remains at `/admin` for emergency direct-DB access.

### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  OPERATIONAL MODE  ←  top-of-page; always visible               │
│  [ RESEARCH ]  [ SIMULATION ]  [ ● LIVE ]                       │
├──────────────────────────────────────────────────────────────────┤
│  KILL SWITCHES  ←  second row; always visible                   │
│  [● PAUSE TRADING] [○ PAUSE REBALANCE] [○ HALT ALL] [⚠ LIQUIDATE]│
├──────────────────────────────────────────────────────────────────┤
│  CATEGORY TABS                                                   │
│  [Weights][Universe][Portfolio][Risk][Execution][Data][Schedule] │
│  [Alerts][Audit Log]                                             │
├──────────────────────────────────────────────────────────────────┤
│  ACTIVE CATEGORY PANEL          │  LIVE PREVIEW / VALIDATION     │
│  (form controls)                │  (impact summary)              │
└─────────────────────────────────┴────────────────────────────────┘
```

### Section 1 — Operational Mode (always visible, top of page)

Three mutually exclusive pill buttons. The active mode is highlighted. Switching modes shows a confirmation modal (see transition guard rules in [system_architecture.md](system_architecture.md)).

```html
<div class="mode-selector" x-data="{mode: '{{ current_mode }}'}">
  <button :class="{active: mode==='research'}"   @click="confirmModeChange('research')">
    RESEARCH
  </button>
  <button :class="{active: mode==='simulation'}" @click="confirmModeChange('simulation')">
    SIMULATION
  </button>
  <button :class="{active: mode==='live'}"       @click="confirmModeChange('live')">
    LIVE
  </button>
</div>
```

Mode badge colors: Research = grey, Simulation = blue, Live = green (flashes amber when first activated).

**Confirmation modal for switching to Live**:
```
⚠ Switch to LIVE mode?

This will enable real order submission to your configured broker.
Current broker: alpaca_paper

Current drawdown:  -2.1%
kill_switch:trading: OFF

Type "GO LIVE" to confirm:  [ ________________ ]
[ Cancel ]  [ Confirm ]
```

---

### Section 2 — Kill Switches (always visible, below mode)

Four toggle buttons rendered as large, high-contrast controls. Active state = red with pulsing border.

```
┌──────────────────┬──────────────────┬────────────────┬──────────────────┐
│ ● PAUSE TRADING  │ ○ PAUSE REBALANCE│ ○ HALT ALL     │ ⚠ FORCE LIQUIDATE│
│ Orders blocked   │ Hold positions   │ All ops stop   │ Sell everything  │
│ (trading = 1)    │ (rebalance = 1)  │ (all = 1)      │ DANGER ZONE      │
└──────────────────┴──────────────────┴────────────────┴──────────────────┘
```

`FORCE LIQUIDATE` is disabled unless mode is `live` or `simulation`. It requires a typed confirmation (`LIQUIDATE`) in a modal.

WebSocket: when any kill switch changes, `config_change` event is emitted to all connected clients so every open browser tab immediately reflects the new state.

```javascript
socket.on('config_change', ({category, key, new_value}) => {
    if (category === 'kill_switch') {
        updateKillSwitchUI(key, new_value === '1');
    }
});
```

---

### Section 3 — Tab: Factor Weights

The most frequently adjusted category. Sliders auto-normalize: dragging one slider redistributes the remainder proportionally across the others.

```
┌─────────────────────────────────────────────────────────────────┐
│  FACTOR WEIGHTS
│  All weights must sum to 1.00
│
│  trend_score        ●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━○  0.25
│  volatility_regime  ●━━━━━━━━━━━━━━━━━━━━━━━━━━━━○      0.20
│  sentiment_score    ●━━━━━━━━━━━━━━━━━━━━━━○            0.15
│  breadth_score      ●━━━━━━━━━━━━━━━━━━━━━━○            0.15
│  dispersion_score   ●━━━━━━━━━━━━━━━━━━━━━━○            0.15
│  liquidity_score    ●━━━━━━━━━━━━━━━━○                  0.10
│                                          Sum: 1.00 ✓
│
│  PREVIEW: Composite score shift for universe
│  XLK: 0.74 → 0.74 (no change)
│  XLE: 0.47 → 0.44 (↓ if sentiment weight raised)
│
│  [ RESET TO DEFAULTS ]        [ SAVE WEIGHTS ]
└─────────────────────────────────────────────────────────────────┘
```

**Live preview panel** (right column): as the user drags sliders, the preview re-calls `GET /api/scores` with `?preview_weights=...` and shows the top-5 ETF score deltas. This gives instant feedback on how the weight change would shift the current ranking — without saving anything yet.

```python
# Preview endpoint — read-only, no DB write
@api_bp.route('/api/scores')
def get_scores():
    preview_weights = request.args.get('preview_weights')  # JSON string
    if preview_weights:
        weights = json.loads(preview_weights)
        # Validate: keys, range, sum
        _validate_weights(weights)
        scores = scorer.rank_universe(latest_factor_scores, weights=weights)
    else:
        scores = json.loads(redis_client.get('cache:latest_scores') or '{}')
    return jsonify(scores)
```

**Save handler** (writes to DB + Redis + broadcasts `config_change`):
```python
@api_bp.route('/api/config/weights', methods=['PATCH'])
@login_required
def update_weights():
    weights = request.json  # {factor_name: float}
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        return jsonify({'error': f'Weights sum to {total:.4f}, must equal 1.0'}), 400

    for key, value in weights.items():
        _upsert_config('weights', f'weight_{key}', str(value))
        redis_client.hset('config:weights', f'weight_{key}', str(value))

    _audit_log('weights', weights, current_user.username)
    socketio.emit('config_change', {
        'category': 'weights', 'new_value': weights,
        'changed_by': current_user.username
    }, namespace='/dashboard')
    return jsonify({'status': 'ok', 'weights': weights})
```

---

### Section 4 — Tab: ETF Universe Filters

```
┌─────────────────────────────────────────────────────────────────┐
│  ETF UNIVERSE FILTERS
│
│  Minimum AUM ($B)          [ 2.0  ]   (current: $2B threshold)
│  Min Avg Daily Volume ($M) [ 20.0 ]
│  Max Bid-Ask Spread (bps)  [ 10.0 ]
│  Min History (trading days)[ 504  ]
│  Require Options Market    [✓]
│
│  Excluded ETFs (comma-separated):
│  [ __________________ ]   e.g. "SOXL,TQQQ"
│
│  Manual Inclusions:
│  [ __________________ ]   Force-include regardless of filters
│
│  CURRENT UNIVERSE PREVIEW
│  ┌──────────────────────────────────────────┐
│  │  20 ETFs pass current filters            │
│  │  [View Table]  ← opens modal with list  │
│  └──────────────────────────────────────────┘
│
│  [ SAVE UNIVERSE CONFIG ]   [ RE-RUN UNIVERSE SCREEN NOW ]
└─────────────────────────────────────────────────────────────────┘
```

"Re-run Universe Screen Now" calls `POST /api/universe/refresh` which triggers the `universe_setup` job to re-evaluate all ETFs against the new filters and update `etf_universe.is_active`. Result count updates instantly.

---

### Section 5 — Tab: Portfolio Constraints

Two-column form. Left = numeric inputs, right = descriptions.

```
┌────────────────────────────────┬────────────────────────────────┐
│  Max Positions         [ 10 ]  │  Max simultaneous open ETFs    │
│  Max Position Size     [5.0%]  │  Hard cap per single position  │
│  Max Sector Exposure   [25%]   │  Max weight per GICS sector    │
│  Min Position Size     [1.0%]  │  Avoids micro-allocations      │
│  Cash Buffer           [5.0%]  │  Always-held minimum cash      │
│  Turnover Limit        [50%]   │  Max one-way turnover/rebalance│
│                                │                                │
│  Rebalance Frequency           │                                │
│  ( ) Daily  (●) Weekly         │  Trigger: first open day       │
│  ( ) Monthly                   │  of the period                 │
│                                │                                │
│  Rebalance Time        [09:45] │  ET, HH:MM                     │
│                                │                                │
│  Optimization Objective        │                                │
│  (●) Max Sharpe                │                                │
│  ( ) Min Volatility            │                                │
│  ( ) Equal Weight              │                                │
│  ( ) HRP (Risk Parity)         │                                │
└────────────────────────────────┴────────────────────────────────┘

  [ SAVE PORTFOLIO CONFIG ]
```

---

### Section 6 — Tab: Risk Parameters

Risk parameters are the most safety-critical configs. Fields with values near their limits are highlighted amber.

```
┌─────────────────────────────────────────────────────────────────┐
│  RISK PARAMETERS
│
│  ── Drawdown Controls ────────────────────────────────────────
│  Max Drawdown Halt (%)         [ -15.0 ]  Trading halted at this level
│  Daily Loss Limit (%)          [  -3.0 ]  Halt for session
│  Drawdown Warning (%)          [  -8.0 ]  Alert only
│  Drawdown Critical (%)         [ -12.0 ]  Alert + escalate
│
│  ── Position Stops ───────────────────────────────────────────
│  Position Stop-Loss (%)        [  -5.0 ]  Exit from entry price
│  Trailing Stop (%)             [  -8.0 ]  Exit from high-water mark
│
│  ── Volatility Targeting ─────────────────────────────────────
│  Vol Target (annualized %)     [  12.0 ]
│  Vol Scaling Enabled           [✓]
│
│  ── Portfolio Risk Limits ────────────────────────────────────
│  VaR Confidence Level          [  0.95 ]
│  VaR Limit (% of portfolio)    [   2.0 ]
│  Correlation Limit             [  0.85 ]  Alert if avg corr exceeds
│
│  CURRENT STATUS
│  ┌──────────────────────────────────────────────────────┐
│  │ Drawdown:   -2.1%    ████░░░░░░░░░░  (warn at -8%)   │
│  │ Daily Loss:  0.0%    ░░░░░░░░░░░░░░  (limit: -3%)    │
│  │ Port. Vol:  10.4%    ████████░░░░░░  (target: 12%)   │
│  └──────────────────────────────────────────────────────┘
│
│  [ SAVE RISK CONFIG ]
└─────────────────────────────────────────────────────────────────┘
```

The "Current Status" mini-panel is live (updated by `portfolio` WebSocket event) so operators can see exactly how far the current state is from each limit before adjusting thresholds.

---

### Section 7 — Tab: Execution Settings

```
┌─────────────────────────────────────────────────────────────────┐
│  EXECUTION SETTINGS
│
│  Broker                        [ alpaca_paper ▼ ]
│    alpaca_paper / alpaca_live / ib
│
│  Order Type                    [ market ▼ ]
│    market / limit / vwap
│
│  Limit Order Offset (bps)      [  5.0 ]   (for limit orders only)
│  Execution Window (minutes)    [    30]   Spread orders after open
│  Use Fractional Shares         [✓]
│  Pre-Trade Liquidity Check     [✓]
│
│  Min Order Notional ($)        [   100]
│  Max Order Notional ($)        [ 50000]
│  Min Rebalance Threshold (bps) [     5]   Skip legs below this delta
│  Max Slippage Gate (bps)       [     8]   Block order if est > this
│
│  Allow Short Selling           [ ]    (requires broker margin account)
│
│  [ SAVE EXECUTION CONFIG ]
└─────────────────────────────────────────────────────────────────┘
```

When "Allow Short Selling" is toggled on, a warning banner appears: "Short selling requires a margin-enabled account. Confirm your broker config supports short orders before enabling."

---

### Section 8 — Tab: Data Sources

API keys are stored encrypted (Fernet) in the database. They are displayed masked in the UI: `sk-••••••••••••••••••••••5f3a`. The "Reveal" button shows the key in-context (requires re-authentication).

```
┌─────────────────────────────────────────────────────────────────┐
│  DATA SOURCE SETTINGS
│
│  ── Primary Price Provider ─────────────────────────────────
│  (●) Alpaca     ( ) Polygon     ( ) Databento
│
│  Alpaca API Key     [ ••••••••••••••••••••••5f3a ] [Reveal] [Test]
│  Alpaca Secret Key  [ ••••••••••••••••••••••8b2c ] [Reveal]
│
│  ── News / Sentiment ───────────────────────────────────────
│  (●) Finnhub     ( ) NewsAPI     ( ) EODHD
│  Finnhub API Key    [ ••••••••••••••••••••••1a9f ] [Reveal] [Test]
│
│  Sentiment Model
│  (●) FinBERT (GPU/CPU)     ( ) VADER (CPU only, faster)
│
│  ── Options Data ───────────────────────────────────────────
│  (●) Polygon     ( ) Tradier
│  Polygon API Key    [ ••••••••••••••••••••••3d7e ] [Reveal] [Test]
│
│  ── VIX Source ─────────────────────────────────────────────
│  (●) CBOE daily file     ( ) Polygon real-time     ( ) FRED
│
│  ── Other Keys ─────────────────────────────────────────────
│  Reddit Client ID   [ ••••••••••••••••••••••7f1b ] [Reveal]
│  Reddit Secret      [ ••••••••••••••••••••••c4e2 ] [Reveal]
│  NewsAPI Key        [ ──────── not set ────────── ] [Set]
│  Databento Key      [ ──────── not set ────────── ] [Set]
│
│  [ SAVE DATA CONFIG ]
└─────────────────────────────────────────────────────────────────┘
```

**[Test] button** — calls `POST /api/config/test_connection/<provider>` which attempts a live ping to that API and returns latency + status:
```
✓ Alpaca connected  (latency: 42ms)  Account: PAPER  Balance: $100,000
✗ NewsAPI failed:   401 Unauthorized — check API key
```

**Key storage** — saving a new API key calls `PATCH /api/config/data` which encrypts the value with Fernet before writing to `system_config.value`. The Redis cache for data config stores the encrypted ciphertext; decryption happens only at the data provider instantiation layer.

---

### Section 9 — Tab: Scheduling

```
┌─────────────────────────────────────────────────────────────────┐
│  SCHEDULING
│
│  ── Intraday Jobs ──────────────────────────────────────────
│  Bar Fetch Interval (seconds)          [  60 ]
│  Fast Factor Update (minutes)          [   5 ]  vol, liquidity
│  Sentiment Update (minutes)            [  15 ]
│
│  ── Daily Jobs ─────────────────────────────────────────────
│  EOD Full Recompute Time (ET)          [16:30]
│  Pre-Market Data Check Time (ET)       [09:15]
│  Rebalance Time (ET)                   [09:45]
│
│  ── Cold Start ─────────────────────────────────────────────
│  History Backfill on Start (days)      [  30 ]
│
│  ── Master Toggles ─────────────────────────────────────────
│  Rebalancing Enabled                   [✓]
│  Sentiment Collection Enabled          [✓]
│  Options Data Collection Enabled       [✓]
│
│  NEXT SCHEDULED JOBS
│  ┌─────────────────────────────────────────────┐
│  │ Fast factors:   in 3m 14s                   │
│  │ Sentiment:      in 11m 42s                  │
│  │ EOD recompute:  today at 16:30 ET           │
│  │ Rebalance:      tomorrow 09:45 ET           │
│  └─────────────────────────────────────────────┘
│
│  [ SAVE SCHEDULE CONFIG ]   [ TRIGGER JOB NOW ▼ ]
│    (dropdown: Fast Factors / Sentiment / EOD Recompute / Universe Screen)
└─────────────────────────────────────────────────────────────────┘
```

"Trigger Job Now" dropdown calls `POST /api/jobs/trigger/<job_name>` which enqueues the job immediately via Celery/APScheduler without disrupting the normal schedule.

---

### Section 10 — Tab: Alerts

(Same notification table as View 6 but rendered as editable form with Save button, not just display.)

Adds two extra fields not in the Alerts view:

```
  Slack Webhook URL        [ https://hooks.slack.com/services/... ]  [Test]
  Alert Email Address      [ trading@example.com ]

  [ SAVE ALERT CONFIG ]
```

---

### Section 11 — Tab: Audit Log

Full searchable log of every config change made through any surface (dashboard, Flask-Admin, API, or automated system action).

```
┌────────────────────────────────────────────────────────────────┐
│  FILTER: [ All Categories ▼ ]  [ All Users ▼ ]  [ Last 7d ▼ ] │
│  [ Search key... ________________ ]                             │
├────────────────────────────────────────────────────────────────┤
│  Time                  │ User      │ Category  │ Key           │ Old      │ New    │
│  2026-03-14 14:32:05   │ operator  │ weights   │ weight_trend  │ 0.25     │ 0.30   │
│  2026-03-14 14:32:05   │ operator  │ weights   │ weight_liq... │ 0.10     │ 0.05   │
│  2026-03-14 09:31:00   │ system    │ kill_sw.. │ trading       │ 1        │ 0      │
│  2026-03-13 16:30:22   │ system    │ kill_sw.. │ trading       │ 0        │ 1      │
│  2026-03-13 16:30:00   │ scheduler │ system    │ op_mode       │ sim..    │ sim..  │
└────────────────────────────────────────────────────────────────┘
│  [ Export CSV ]
```

Data source: `GET /api/config/audit`. Changes by `system` = automated (risk halt); changes by `scheduler` = job-triggered; changes by named user = manual. This log is append-only — no records are ever deleted.

---

### Config View Backend

```python
# app/api/config_routes.py

@api_bp.route('/api/config/<category>', methods=['GET'])
@login_required
def get_config(category):
    rows = db.query(SystemConfig).filter_by(category=category).all()
    result = {}
    for row in rows:
        # Mask secrets
        if row.value_type == 'secret':
            result[row.key] = '••••' + row.value[-4:] if len(row.value) > 4 else '••••'
        else:
            result[row.key] = row.value
    return jsonify(result)


@api_bp.route('/api/config/<category>', methods=['PATCH'])
@login_required
def update_config(category):
    updates = request.json  # {key: new_value}
    protected = {'operational_mode', 'force_liquidate'}

    for key, new_value in updates.items():
        if key in protected:
            abort(403, f'{key} must be set via dedicated endpoint')

        row = db.query(SystemConfig).filter_by(category=category, key=key).first()
        old_value = row.value if row else None

        if row is None:
            row = SystemConfig(category=category, key=key)
            db.add(row)

        # Encrypt secrets before storing
        if row.value_type == 'secret':
            new_value = fernet.encrypt(new_value.encode()).decode()

        row.value      = str(new_value)
        row.updated_by = current_user.username
        row.updated_at = datetime.utcnow()

        # Sync Redis
        redis_client.hset(f'config:{category}', key, str(new_value))

        # Audit log
        db.add(ConfigAuditLog(
            category=category, key=key,
            old_value=old_value, new_value=str(new_value),
            changed_by=current_user.username,
        ))

    db.commit()

    # Broadcast to all dashboard clients
    socketio.emit('config_change', {
        'category': category,
        'updates': list(updates.keys()),
        'changed_by': current_user.username,
    }, namespace='/dashboard')

    return jsonify({'status': 'ok', 'category': category, 'updated': list(updates.keys())})


@api_bp.route('/api/config/test_connection/<provider>', methods=['POST'])
@login_required
def test_connection(provider):
    results = DataProviderTestService.test(provider)
    return jsonify(results)  # {status, latency_ms, message}


@api_bp.route('/api/jobs/trigger/<job_name>', methods=['POST'])
@login_required
def trigger_job(job_name):
    allowed = ['fast_factors', 'sentiment', 'eod_recompute', 'universe_screen']
    if job_name not in allowed:
        abort(400)
    celery_app.send_task(f'tasks.{job_name}')
    return jsonify({'status': 'queued', 'job': job_name})
```

---

### Config View Frontend (config.js key patterns)

```javascript
// Weight sliders — auto-normalize on drag
document.querySelectorAll('.weight-slider').forEach(slider => {
    slider.addEventListener('input', () => {
        const sliders  = document.querySelectorAll('.weight-slider');
        const changed  = slider;
        const newVal   = parseFloat(changed.value);
        const rest     = [...sliders].filter(s => s !== changed);
        const remaining = 1.0 - newVal;
        const oldRestSum = rest.reduce((s, s2) => s + parseFloat(s2.value), 0);

        rest.forEach(s => {
            const proportion = oldRestSum > 0 ? parseFloat(s.value) / oldRestSum : 1 / rest.length;
            s.value = (remaining * proportion).toFixed(3);
        });

        updateWeightSum();
        requestWeightPreview();  // debounced 300ms
    });
});

// Debounced preview fetch
const requestWeightPreview = debounce(() => {
    const weights = getWeightsFromSliders();
    fetch(`/api/scores?preview_weights=${JSON.stringify(weights)}`)
        .then(r => r.json())
        .then(data => renderWeightPreview(data));
}, 300);

// Kill switch toggle — hold-to-confirm (2 seconds)
document.querySelectorAll('.kill-switch-btn').forEach(btn => {
    let holdTimer;
    btn.addEventListener('mousedown', () => {
        holdTimer = setTimeout(() => activateKillSwitch(btn.dataset.switch), 2000);
        btn.classList.add('holding');
    });
    btn.addEventListener('mouseup', () => {
        clearTimeout(holdTimer);
        btn.classList.remove('holding');
    });
});

// Receive config change from any other connected client
socket.on('config_change', ({category, updates, changed_by}) => {
    showToast(`Config updated: ${category} by ${changed_by}`);
    if (category === 'weights') reloadWeightSliders();
    if (category === 'kill_switch') reloadKillSwitches();
});
```

---

## Navigation & Layout Shell

### Global Navigation Sidebar

```
╔══════════════════╗
║  PAISABOT  v1.0  ║
║  ● SIMULATION    ║  ← operational mode badge; click → /config#mode
╠══════════════════╣
║  Dashboard     ⌂ ║  /dashboard
║  Factors       📊 ║  /factors
║  Rotation      ⟳  ║  /rotation
║  Execution     ⚡ ║  /execution
║  Analytics     📈 ║  /analytics
║  Alerts     🔔 3  ║  /alerts  ← unread count badge
║  Config        ⚙  ║  /config  ← system parameters
╠══════════════════╣
║  Flask Admin   ↗  ║  /admin   ← opens in new tab
╠══════════════════╣
║  [PAUSE]  [HALT] ║  ← quick kill switches always visible
╚══════════════════╝
```

The two quick kill switch buttons in the nav are always visible on every page. `[PAUSE]` = `kill_switch:rebalance`; `[HALT]` = `kill_switch:trading`. Both render in red and flash when active. Clicking the mode badge in the nav navigates directly to `/config` with the mode tab pre-selected.

### Page Header Bar

Every page has a persistent top bar showing:

```
[Regime: ◎ ROTATION 68%]  [DD: -2.1%]  [Port: +3.4%]  [Mode: SIMULATION]  [Clock: 14:32:05 ET]
```

The clock updates every second via `setInterval`. All other values update from WebSocket events.

---

## Flask Application Structure for Frontend

```
app/
├── templates/
│   ├── base.html              # navigation shell, SocketIO client init, CSS vars
│   ├── dashboard/
│   │   ├── home.html          # View 1
│   │   ├── factors.html       # View 2
│   │   ├── rotation.html      # View 3
│   │   ├── execution.html     # View 4
│   │   ├── analytics.html     # View 5
│   │   ├── alerts.html        # View 6
│   │   └── config.html        # View 7 — system config (tabbed)
│   └── admin/                 # Flask-Admin custom templates
│       ├── index.html
│       └── killswitch.html
│
├── static/
│   ├── css/
│   │   ├── main.css           # CSS variables, global dark theme
│   │   ├── tables.css         # Tabulator custom dark theme overrides
│   │   ├── charts.css         # Plotly container sizing
│   │   └── config.css         # Slider, toggle, tab styles for View 7
│   └── js/
│       ├── socket.js          # SocketIO connection + event dispatch
│       ├── dashboard.js       # View 1 update handlers
│       ├── factors.js         # View 2 + Tabulator heatmap
│       ├── rotation.js        # View 3 + scatter chart
│       ├── execution.js       # View 4 + kill switch controls
│       ├── analytics.js       # View 5 + slider controls
│       ├── alerts.js          # View 6 + notification settings
│       └── config.js          # View 7: weight sliders, kill switches,
│                              #   mode selector, API key masking,
│                              #   audit log, job trigger, preview fetch
│
└── views/
    └── routes.py              # GET routes for all dashboard pages
```

---

## SocketIO Server-Side Setup

```python
# app/__init__.py (app factory)
from flask_socketio import SocketIO

socketio = SocketIO(
    app,
    message_queue='redis://localhost:6379',  # multi-process support
    async_mode='eventlet',
    cors_allowed_origins='*',
    logger=False,
    engineio_logger=False,
)

# app/streaming/socketio_server.py
@socketio.on('connect', namespace='/dashboard')
def on_connect():
    # Push current state snapshot immediately on connect
    scores    = json.loads(redis_client.get('cache:latest_scores') or '{}')
    portfolio = json.loads(redis_client.get('cache:portfolio:current') or '{}')
    regime    = redis_client.get('cache:current_regime')
    emit('factor_scores', scores)
    emit('portfolio', portfolio)
    if regime:
        emit('regime_change', json.loads(regime))

@socketio.on('disconnect', namespace='/dashboard')
def on_disconnect():
    pass  # connection cleanup handled automatically
```

---

## Design Principles

1. **No page reloads** — all data updates via WebSocket events; page navigations use `history.pushState` or simply load a new Jinja2 page (acceptable for a monitoring tool)
2. **Degrade gracefully** — if WebSocket disconnects, all panels show "⚠ DISCONNECTED" and attempt reconnect every 5 seconds
3. **No layout thrash** — tables update cells in place (Tabulator `updateData`); charts use `Plotly.update` / `Plotly.extendTraces` rather than full re-renders
4. **Monospace everywhere** — all prices, scores, percentages, and timestamps use `font-family: var(--font-mono)` for alignment
5. **Color convention** — green/red for directional values only; never use green/red for non-directional data
6. **Admin is separate** — Flask-Admin lives at `/admin` with its own auth; it shares the same dark CSS overrides but is not part of the SPA navigation
