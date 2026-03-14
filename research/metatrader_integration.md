# MetaTrader 5 Integration Research

**Purpose:** Covers MT5 Python API, ETF CFD availability, Windows constraint solutions, MT5Broker class design, lot conversion, position sync, and Flask integration patterns.

---

## A.1 — MT5 Python Package: Install, Initialize, Connect

**Installation**

```bash
pip install MetaTrader5
# Current stable version: 5.0.5640 (February 2026)
# Optional but recommended:
pip install pandas numpy matplotlib
```

**Critical platform constraint:** The `MetaTrader5` package publishes **Windows x86-64 wheel files only**. It will not install on Linux or macOS natively. This is not a soft limitation — the PyPI package has no Linux wheel. See section A.3 for Linux workarounds.

**How the API works — IPC bridge, not network:**

The MT5 Python package communicates with the MetaTrader 5 desktop terminal via **Windows inter-process communication (IPC/named pipes / shared memory)**. It does not use TCP sockets or a REST API. The Python process must run **on the same Windows machine** as the MT5 terminal. The terminal does not need to be visually in the foreground, but it must be running as a Windows process.

**Initialization and login sequence:**

```python
import MetaTrader5 as mt5

# Option 1: Basic initialize (uses last-used account in terminal)
mt5.initialize()

# Option 2: Initialize pointing at terminal executable
mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe")

# Option 3: Full inline login (recommended for automation)
if not mt5.initialize(
    path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
    login=12345678,           # integer account number
    password="YourPassword",
    server="BrokerName-Server",
    timeout=60000,            # ms; default 60000
    portable=False
):
    print(f"initialize() failed, error: {mt5.last_error()}")
    quit()

# Explicit login after initialize (if credentials not passed to initialize):
authorized = mt5.login(
    login=12345678,
    password="YourPassword",
    server="BrokerName-Server",
    timeout=60000
)
if not authorized:
    print(f"Login failed, error: {mt5.last_error()}")
    mt5.shutdown()
    quit()

# Always shut down cleanly
mt5.shutdown()
```

**Key functions reference:**

| Function | Purpose | Returns |
|---|---|---|
| `mt5.initialize(path, login, password, server, timeout)` | Open IPC connection to terminal | `True` / `False` |
| `mt5.login(login, password, server, timeout)` | Authenticate to a trading account | `True` / `False` |
| `mt5.shutdown()` | Close terminal connection | `None` |
| `mt5.last_error()` | Get last error code and message | `(code, description)` tuple |
| `mt5.account_info()` | Full account snapshot | namedtuple |
| `mt5.terminal_info()` | Terminal status, build, path | namedtuple |
| `mt5.symbol_info("SPY.US")` | Symbol contract specs | namedtuple or `None` |
| `mt5.symbol_info_tick("SPY.US")` | Latest bid/ask tick | namedtuple or `None` |
| `mt5.symbol_select("SPY.US", True)` | Add symbol to MarketWatch | `True` / `False` |
| `mt5.copy_rates_from(sym, tf, dt, n)` | Get N bars from date | numpy array or `None` |
| `mt5.copy_rates_from_pos(sym, tf, pos, n)` | Get N bars from index | numpy array or `None` |
| `mt5.copy_rates_range(sym, tf, dt_from, dt_to)` | Get bars in date range | numpy array or `None` |
| `mt5.copy_ticks_from(sym, dt, n, flags)` | Get N ticks from date | numpy array or `None` |
| `mt5.copy_ticks_range(sym, dt_from, dt_to, flags)` | Get ticks in range | numpy array or `None` |
| `mt5.order_send(request_dict)` | Submit trade request | `MqlTradeResult` namedtuple |
| `mt5.order_check(request_dict)` | Validate order without sending | `MqlTradeCheckResult` namedtuple |
| `mt5.positions_get(symbol=None, group=None, ticket=None)` | Open positions | tuple of namedtuples or `None` |
| `mt5.orders_get(symbol=None, group=None, ticket=None)` | Active pending orders | tuple of namedtuples or `None` |
| `mt5.history_deals_get(dt_from, dt_to)` | Historical executed deals | tuple of namedtuples or `None` |

**`account_info()` fields relevant to trading:**

```python
info = mt5.account_info()
info.balance        # float — cash balance
info.equity         # float — balance + unrealized P&L
info.margin         # float — currently used margin
info.margin_free    # float — available margin for new trades
info.margin_level   # float — equity/margin ratio in %
info.leverage       # int — e.g. 10, 20, 100
info.currency       # str — e.g. "USD"
info.profit         # float — total unrealized P&L
info.login          # int — account number
info.server         # str — broker server name
info.name           # str — account holder name
info.trade_allowed  # bool — trading allowed flag
```

**Getting OHLCV bars:**

```python
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd

# Get last 500 1-minute bars
rates = mt5.copy_rates_from_pos("SPY.US", mt5.TIMEFRAME_M1, 0, 500)
# Returns numpy array with columns: time, open, high, low, close,
#                                    tick_volume, spread, real_volume

df = pd.DataFrame(rates)
df['time'] = pd.to_datetime(df['time'], unit='s')

# Get bars by date range
from_date = datetime(2025, 1, 1)
to_date   = datetime(2025, 3, 1)
rates = mt5.copy_rates_range("SPY.US", mt5.TIMEFRAME_D1, from_date, to_date)
```

**Timeframe constants:**

`TIMEFRAME_M1, M2, M3, M4, M5, M6, M10, M12, M15, M20, M30, H1, H2, H3, H4, H6, H8, H12, D1, W1, MN1`

**Real-time price feeds — polling, NOT callbacks:**

The MT5 Python API is **fully synchronous and polling-based**. There are no event callbacks, WebSocket feeds, or subscription mechanisms. To simulate real-time data you must poll:

```python
import time, threading
import MetaTrader5 as mt5

def price_poll_loop(symbol, interval_sec=1.0):
    while True:
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            process_tick(tick.bid, tick.ask, tick.time)
        time.sleep(interval_sec)

t = threading.Thread(target=price_poll_loop, args=("SPY.US",), daemon=True)
t.start()
```

Recommended polling interval: **250ms–1s** for ticks, **1s** for rates.

**Order types and `order_send()` usage:**

```python
# TRADE_ACTION values:
# mt5.TRADE_ACTION_DEAL    — immediate market execution
# mt5.TRADE_ACTION_PENDING — place a pending order
# mt5.TRADE_ACTION_SLTP    — modify SL/TP of open position
# mt5.TRADE_ACTION_MODIFY  — modify a pending order
# mt5.TRADE_ACTION_REMOVE  — cancel a pending order

# ORDER_TYPE values:
# mt5.ORDER_TYPE_BUY            — market buy
# mt5.ORDER_TYPE_SELL           — market sell
# mt5.ORDER_TYPE_BUY_LIMIT      — buy when price drops to limit
# mt5.ORDER_TYPE_SELL_LIMIT     — sell when price rises to limit
# mt5.ORDER_TYPE_BUY_STOP       — buy when price rises to stop
# mt5.ORDER_TYPE_SELL_STOP      — buy when price drops to stop

# Market buy example:
symbol = "SPY.US"
mt5.symbol_select(symbol, True)  # ensure it's in MarketWatch
tick   = mt5.symbol_info_tick(symbol)
info   = mt5.symbol_info(symbol)

request = {
    "action":    mt5.TRADE_ACTION_DEAL,
    "symbol":    symbol,
    "volume":    0.10,                     # lots (see lot conversion below)
    "type":      mt5.ORDER_TYPE_BUY,
    "price":     tick.ask,
    "deviation": 20,                       # max slippage in points
    "magic":     100001,                   # EA identifier
    "comment":   "paisabot-market-buy",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC, # or RETURN / FOK
}

result = mt5.order_send(request)
if result.retcode != mt5.TRADE_RETCODE_DONE:
    print(f"Order failed: {result.retcode}, {result.comment}")
else:
    print(f"Order placed: ticket={result.order}")
```

**`ORDER_FILLING` types:**
- `ORDER_FILLING_FOK` — Fill or Kill (all or nothing)
- `ORDER_FILLING_IOC` — Immediate or Cancel (partial fills allowed)
- `ORDER_FILLING_RETURN` — Return partial volume as residual (most common for CFDs)

Check `symbol_info().filling_mode` bitmask for broker-supported modes.

---

## A.2 — ETF Availability on MT5 Brokers

**US equity ETFs on MT5 are traded as CFDs** (Contracts for Difference), not direct equity ownership. Key implications:

- No ownership of underlying shares; no dividends (swap credits/debits may approximate).
- Margin-based trading; leverage available (typically 5:1 to 20:1 for ETF CFDs on retail accounts).
- Subject to broker overnight financing charges (swap rates).

**MT5 brokers known to offer US equity ETF CFDs:**

| Broker | ETFs Available | Notes |
|---|---|---|
| Admiral Markets (Admirals) | SPY, QQQ, XLK, GLD, IWM, and ~400 others | Best coverage for US ETF CFDs |
| IC Markets | US equity CFDs including major ETFs | Primarily forex/index focused |
| Pepperstone | CFDs on US ETFs via MT5 | Tiered leverage for retail |
| XM Group | US stock/ETF CFDs | Wide instrument range |
| RoboForex | US stocks and ETFs as CFDs | Good coverage |
| OANDA | US equity CFDs on MT5 | Regulated, solid reliability |

Always verify the exact instrument list directly with the broker. Coverage changes — niche ETFs (XLE, XLK) may not be available at every broker.

**Symbol naming conventions (vary by broker):**

```
SPY.US        — Most common (Admiral Markets style)
#SPY          — Hash prefix convention
SPY_d         — CFD suffix convention
SPY           — Some brokers use bare ticker
SPYUSD        — With currency pair suffix
```

**Discover available symbols programmatically:**

```python
symbols = mt5.symbols_get(group="*SPY*")
for s in symbols:
    print(s.name, s.description, s.trade_contract_size)
```

**Trading hours for ETF CFDs:**
- Regular session: **09:30–16:00 ET (14:30–21:00 UTC)**
- MT5 uses server time, often UTC+2 or UTC+3 (EET/EEST)

Verify with `mt5.symbol_info_session_trade()` for exact broker-specific hours.

**Margin and leverage for ETF CFDs:**

| Account Type | Typical Leverage | Margin Requirement |
|---|---|---|
| Retail EU/UK (ESMA-regulated) | 5:1 max | 20% |
| Retail AU/non-EU | 10:1–20:1 | 5–10% |
| Professional/Institutional | 20:1–100:1 | 1–5% |

Minimum lot size for ETF CFDs is typically 0.01 lots (step 0.01). Contract size varies by broker — always read `symbol_info().trade_contract_size`.

---

## A.3 — MT5 Limitations Relevant to This System

**1. Windows-only constraint — the core architectural problem**

Options for a Linux production server:

**Option A: Dedicated Windows VM/VPS — RECOMMENDED**

```
[Linux Production Server]                    [Windows VM / Windows VPS]
  Flask + Gunicorn                   ←→       MT5 Terminal (running)
  Celery Workers                    REST        Python MT5 gateway service
  PostgreSQL                        API         (pushes data to Redis/DB)
  Redis
  Nginx
```

Run a lightweight Windows Server VM (e.g., Azure B2s, AWS t3.medium Windows). On the Windows VM, run a small Python Flask/FastAPI microservice wrapping MT5 calls. The Linux stack communicates via internal REST API or Redis pub/sub.

**Option B: Wine on Linux (Works but fragile)**

```bash
# On Ubuntu 22.04
sudo apt install wine64 xvfb
Xvfb :99 -screen 0 1024x768x16 &
DISPLAY=:99 wine mt5setup.exe
DISPLAY=:99 wine python.exe -m pip install MetaTrader5
DISPLAY=:99 wine python.exe mt5_collector.py
```

Not officially supported by MetaQuotes. Terminal disconnects under Wine are harder to diagnose. Use only if a Windows VPS is not viable.

**Option C: Docker with Wine (Advanced)**

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y wine64 xvfb winbind
COPY mt5setup.exe /tmp/
RUN Xvfb :99 & DISPLAY=:99 wine /tmp/mt5setup.exe /S
```

Requires privileged mode. Not production-grade without extensive testing. Use Option A for production.

**2. Latency: MT5 Python API is synchronous and blocking**

- **Never call MT5 directly from a Flask route handler.** The blocking call will hold the eventlet green thread.
- All MT5 calls must happen in **Celery worker processes** or a **dedicated daemon thread with a queue interface**.
- Use a `threading.RLock()` around all MT5 API calls:

```python
import threading, MetaTrader5 as mt5

_mt5_lock = threading.RLock()

def safe_mt5_call(fn, *args, **kwargs):
    with _mt5_lock:
        return fn(*args, **kwargs)

tick = safe_mt5_call(mt5.symbol_info_tick, "SPY.US")
```

**3. MT5 as market data provider**

MT5 can provide: real-time bid/ask/last tick, historical OHLCV bars years back.

MT5 **cannot** provide: fundamentals, earnings, options chain, news feeds, SEC filings.

Strategy: MT5 as **primary real-time price source** (polling tick data every 500ms), with Polygon.io as backup/supplementary feed.

---

## A.4 — MT5Broker Class (Drop-in Replacement for AlpacaBroker)

```python
import threading
import MetaTrader5 as mt5


def notional_to_lots(notional_usd: float, symbol: str) -> float:
    """Convert dollar notional to MT5 lots, respecting volume_step and min/max."""
    info  = mt5.symbol_info(symbol)
    tick  = mt5.symbol_info_tick(symbol)
    price = tick.ask
    contract_size = info.trade_contract_size   # e.g. 100 shares/lot
    raw_lots = notional_usd / (price * contract_size)
    step  = info.volume_step
    lots  = round(round(raw_lots / step) * step, 8)
    lots  = max(info.volume_min, min(info.volume_max, lots))
    return lots

# Example: buy $10,000 of SPY at $530/share, contract_size=100
# raw_lots = 10000 / (530 * 100) = 0.1887 → rounded to 0.19 lots


class MT5Broker:
    def __init__(self, login: int, password: str, server: str,
                 terminal_path: str):
        self._login    = login
        self._password = password
        self._server   = server
        self._path     = terminal_path
        self._lock     = threading.RLock()
        self._connected = False

    def connect(self) -> bool:
        with self._lock:
            ok = mt5.initialize(
                path=self._path,
                login=self._login,
                password=self._password,
                server=self._server,
                timeout=60_000
            )
            self._connected = ok
            return ok

    def disconnect(self):
        mt5.shutdown()
        self._connected = False

    def get_account(self) -> dict:
        with self._lock:
            info = mt5.account_info()
            return {
                "balance":     info.balance,
                "equity":      info.equity,
                "margin_free": info.margin_free,
                "leverage":    info.leverage,
                "currency":    info.currency,
            }

    def get_positions(self) -> list[dict]:
        with self._lock:
            positions = mt5.positions_get()
            if not positions:
                return []
            return [
                {
                    "ticket":        p.ticket,
                    "symbol":        p.symbol,
                    "volume":        p.volume,
                    "price_open":    p.price_open,
                    "price_current": p.price_current,
                    "profit":        p.profit,
                    "type":          "long" if p.type == 0 else "short",
                    "magic":         p.magic,
                }
                for p in positions
            ]

    def place_market_order(self, symbol: str, side: str,
                           notional_usd: float) -> dict:
        with self._lock:
            lots  = notional_to_lots(notional_usd, symbol)
            tick  = mt5.symbol_info_tick(symbol)
            otype = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
            price = tick.ask if side == "buy" else tick.bid
            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       lots,
                "type":         otype,
                "price":        price,
                "deviation":    30,
                "magic":        100001,
                "comment":      f"paisabot-{side}",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            return {
                "retcode":  result.retcode,
                "order_id": result.order,
                "volume":   result.volume,
                "price":    result.price,
                "comment":  result.comment,
                "success":  result.retcode == mt5.TRADE_RETCODE_DONE,
            }

    def close_position(self, ticket: int, symbol: str,
                       volume: float) -> dict:
        with self._lock:
            pos_type = mt5.positions_get(ticket=ticket)[0].type
            otype    = mt5.ORDER_TYPE_SELL if pos_type == 0 else mt5.ORDER_TYPE_BUY
            tick     = mt5.symbol_info_tick(symbol)
            price    = tick.bid if otype == mt5.ORDER_TYPE_SELL else tick.ask
            request  = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       volume,
                "type":         otype,
                "price":        price,
                "position":     ticket,
                "deviation":    30,
                "magic":        100001,
                "comment":      "paisabot-close",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            return {"success": result.retcode == mt5.TRADE_RETCODE_DONE,
                    "retcode": result.retcode}
```

**Position reconciliation with internal DB (Celery task, every 30s):**

```python
@celery.task
def sync_mt5_positions():
    mt5_positions = broker.get_positions()
    db_positions  = Position.query.filter_by(status='open').all()

    mt5_tickets   = {p['ticket'] for p in mt5_positions}
    db_tickets    = {p.mt5_ticket for p in db_positions}

    # Close DB records for positions no longer in MT5
    for p in db_positions:
        if p.mt5_ticket not in mt5_tickets:
            p.status = 'closed'
            p.closed_at = datetime.utcnow()

    # Update or insert positions from MT5
    for mp in mt5_positions:
        existing = Position.query.filter_by(mt5_ticket=mp['ticket']).first()
        if existing:
            existing.current_price  = mp['price_current']
            existing.unrealized_pnl = mp['profit']
        else:
            db.session.add(Position(
                mt5_ticket    = mp['ticket'],
                symbol        = mp['symbol'],
                volume        = mp['volume'],
                entry_price   = mp['price_open'],
                current_price = mp['price_current'],
                direction     = mp['type'],
                status        = 'open',
            ))
    db.session.commit()
```

---

## E.1 — MT5 + Flask Integration Patterns

**Pattern 1 (Recommended): Dedicated MT5 Gateway Microservice on Windows**

```
[Linux Flask/Celery Server]
        │
        │  REST / Redis pub-sub
        ▼
[Windows VPS: MT5 Gateway Service]
  ┌─────────────────────────────────────────────────┐
  │  FastAPI / Flask gateway (Windows, Python 3.12) │
  │  + MetaTrader5 package                          │
  │  + threading.RLock() around all mt5.* calls     │
  │  + polling thread for tick data → Redis         │
  │  + order endpoint for execution                 │
  └─────────────────────────────────────────────────┘
        │
        │  IPC (named pipe / shared memory)
        ▼
[MT5 Terminal (Windows)]
```

The gateway service handles: initialization + reconnection, thread-safe MT5 wrapping, publishing tick data to Redis channels, receiving order requests via REST, and position sync to Redis.

**Pattern 2: MT5 in Celery Worker (Windows-only stack)**

```python
# tasks/mt5_tasks.py
import MetaTrader5 as mt5
import threading
from celery.signals import worker_process_init, worker_process_shutdown

_mt5_lock  = threading.RLock()
_connected = False

@worker_process_init.connect
def init_mt5(**kwargs):
    global _connected
    _connected = mt5.initialize(
        login=int(os.environ['MT5_LOGIN']),
        password=os.environ['MT5_PASSWORD'],
        server=os.environ['MT5_SERVER'],
        timeout=60000
    )
    if not _connected:
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

@worker_process_shutdown.connect
def shutdown_mt5(**kwargs):
    mt5.shutdown()

@celery.task(bind=True, max_retries=3, default_retry_delay=5)
def fetch_tick(self, symbol: str) -> dict:
    with _mt5_lock:
        tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise self.retry(exc=RuntimeError(f"tick fetch failed: {mt5.last_error()}"))
    return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}
```

`worker_process_init` fires per-process. Each worker process gets its own MT5 connection. One terminal, multiple Python processes connecting simultaneously — this works.

---

## E.2 — MT5 Disconnect Handling and Reconnection

```python
import time, logging, threading
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

class MT5Connection:
    MAX_RETRIES = 10
    RETRY_DELAY = 5.0   # seconds

    def __init__(self, login, password, server, path):
        self.login    = login
        self.password = password
        self.server   = server
        self.path     = path
        self._lock    = threading.RLock()

    def ensure_connected(self) -> bool:
        with self._lock:
            info = mt5.terminal_info()
            if info is not None and info.connected:
                return True
            return self._reconnect()

    def _reconnect(self) -> bool:
        mt5.shutdown()
        for attempt in range(self.MAX_RETRIES):
            logger.warning(f"MT5 reconnect attempt {attempt + 1}/{self.MAX_RETRIES}")
            ok = mt5.initialize(
                path=self.path,
                login=self.login,
                password=self.password,
                server=self.server,
                timeout=30_000
            )
            if ok:
                logger.info("MT5 reconnected successfully")
                return True
            time.sleep(self.RETRY_DELAY * (1.5 ** attempt))  # exponential backoff
        logger.error("MT5 reconnection failed after max retries")
        return False

    def safe_call(self, fn, *args, **kwargs):
        with self._lock:
            self.ensure_connected()
            result = fn(*args, **kwargs)
            if result is None:
                err = mt5.last_error()
                if err[0] in (-10004, -10006):   # NOT_CONNECTED, DISCONNECTED
                    if self._reconnect():
                        result = fn(*args, **kwargs)
            return result
```

---

## E.3 — MT5 Data Flow Architecture

```
                    ┌──────────────────────────────────────────┐
                    │         MT5 Terminal (Windows)            │
                    │  ┌────────┐  ┌────────┐  ┌────────────┐ │
                    │  │ Broker │  │  OHLCV │  │ Order Book │ │
                    │  │ Server │  │  Data  │  │   (Depth)  │ │
                    └──┴────────┴──┴────────┴──┴────────────┘─┘
                              │
                    ┌─────────▼───────────────┐
                    │   MT5 Gateway Service    │
                    │   (Windows Python)       │
                    │                          │
                    │  Poll loop (500ms):      │
                    │   symbol_info_tick() ──→ Redis PUBLISH "ticks:SPY"
                    │   copy_rates_from_pos()─→ Redis HSET "bars:SPY:1m"
                    │                          │
                    │  Order API endpoint:     │
                    │   POST /order ──────────→ order_send()
                    │   GET  /positions ──────→ positions_get()
                    └──────────────────────────┘
                              │
                    ┌─────────▼──────────────────────────────────────┐
                    │              Redis (Linux server)               │
                    │  Channels: ticks:SPY, ticks:QQQ, ticks:XLK    │
                    │  Keys:     bars:SPY:1m, bars:SPY:5m, ...       │
                    │  Queues:   Celery task queues                   │
                    └──────────┬─────────────────────────────────────┘
                               │
              ┌────────────────┼──────────────────┐
              ▼                ▼                   ▼
      [Celery Workers]   [Flask/SocketIO]   [PostgreSQL]
       Factor calc        Push updates       Store OHLCV
       Signal gen         to dashboard       Store trades
       Order dispatch                        Store P&L
```

---

## E.4 — Fallback to Alpaca/Polygon When MT5 Disconnects

```python
# services/market_data.py
from enum import Enum

class DataSource(Enum):
    MT5     = "mt5"
    POLYGON = "polygon"
    ALPACA  = "alpaca"

class MarketDataService:
    def __init__(self, mt5_gateway, polygon_client, alpaca_client):
        self._mt5     = mt5_gateway
        self._polygon = polygon_client
        self._alpaca  = alpaca_client
        self._source  = DataSource.MT5

    def get_latest_bar(self, symbol: str) -> dict | None:
        # Try primary (MT5)
        if self._source == DataSource.MT5:
            try:
                bar = self._mt5.get_latest_bar(symbol)
                if bar:
                    return bar
                self._fallback_to_polygon(symbol)
            except Exception as e:
                logger.warning(f"MT5 data failure: {e}; falling back to Polygon")
                self._fallback_to_polygon(symbol)

        # Polygon fallback
        if self._source == DataSource.POLYGON:
            try:
                return self._polygon.get_latest_bar(symbol)
            except Exception as e:
                logger.warning(f"Polygon failure: {e}; falling back to Alpaca")
                self._source = DataSource.ALPACA

        # Alpaca last resort
        try:
            return self._alpaca.get_latest_bar(symbol)
        except Exception as e:
            logger.error(f"All data sources failed for {symbol}: {e}")
            return None

    def _fallback_to_polygon(self, symbol: str):
        self._source = DataSource.POLYGON
        alert_service.send_slack(
            f"WARNING: MT5 data unavailable for {symbol}. Using Polygon fallback.",
            level="WARNING"
        )
```

---

## Key Architecture Decisions

1. **MT5 Windows constraint is the biggest architectural issue.** Use a Windows VPS running a lightweight Python gateway service that wraps MT5 calls. The Linux Flask/Celery stack communicates with it via REST or Redis. This cleanly separates concerns.

2. **MT5 Python API is poll-only, not event-driven.** Build a dedicated polling loop (500ms for ticks) in the MT5 gateway that pushes data to Redis. Flask/Celery consumes from Redis, not from MT5 directly.

3. **Flask-SocketIO must use exactly 1 Gunicorn worker.** Configure a Redis message queue so Celery workers can emit SocketIO events from separate processes.

4. **Celery execution queue should use `--concurrency=1`.** Trade execution is stateful; parallel order placement against the same account causes race conditions. All other queues can have higher concurrency.

5. **Never put a live MT5 account into production without testing the full demo cycle first.** Integration test suite must exercise: initialize → login → symbol_info → copy_rates → order_check → order_send (demo) → positions_get → close → shutdown — with reconnect testing.
