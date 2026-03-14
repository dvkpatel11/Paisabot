# Architecture & Technology Stack

## System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL DATA SOURCES                              │
│  Alpaca (bars/quotes/WS) │ Polygon (ticks/options) │ Finnhub (news)       │
│  CBOE (VIX) │ FRED (macro) │ Reddit/PRAW │ NewsAPI │ Tradier (options)    │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │    DATA INGESTION        │
              │  WebSocket Consumer      │  ← Alpaca/Polygon real-time
              │  REST Poller (APScheduler│  ← Batch / periodic
              │  Celery Task Queue       │  ← Heavy historical backfill
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  PostgreSQL + Redis       │
              │  price_bars, ohlcv       │  ← Persistent store
              │  Redis L1 cache          │  ← Fast reads, TTL-keyed
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  FACTOR ENGINE           │
              │  8 factor classes        │  ← Batch daily + intraday
              │  FactorRegistry          │  ← Orchestrates all factors
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  SIGNAL PIPELINE         │
              │  CompositeScorer         │
              │  RegimeDetector          │
              │  SignalFilter            │
              └────┬────────────┬────────┘
                   │            │
        ┌──────────▼──┐  ┌──────▼─────────────┐
        │  PORTFOLIO   │  │  RISK MANAGER       │
        │  Constructor │  │  DrawdownMonitor    │
        │  Rebalancer  │  │  StopLossEngine     │
        └──────┬───────┘  │  VarCalculator      │
               │          └──────────────────────┘
        ┌──────▼───────┐
        │  EXECUTION   │
        │  AlpacaBroker│  ← Paper trading (Alpaca paper API)
        │  OrderManager│
        └──────┬───────┘
               │
┌──────────────▼──────────────────────────────────────────────┐
│                      FLASK APPLICATION                        │
│                                                               │
│  /admin/*          Flask-Admin config views                  │
│  /api/*            REST API (scores, portfolio, health)       │
│  /dashboard        WebSocket namespace (/dashboard)          │
│                                                               │
│  Redis Pub/Sub Bridge → Flask-SocketIO → Browser clients     │
└──────────────────────────────────────────────────────────────┘
```

---

## Python Dependencies

### Core Runtime
```
flask==3.0.x
flask-socketio==5.3.x
eventlet==0.36.x              # WebSocket greenthreads
flask-admin==2.0.x
flask-sqlalchemy==3.1.x
sqlalchemy==2.0.x
alembic==1.13.x
psycopg2-binary==2.9.x
redis==5.0.x
pydantic==2.7.x
python-dotenv==1.0.x
cryptography==42.x            # Fernet for API key encryption
```

### Data & Market Feed
```
alpaca-py==0.30.x             # Live/paper trading + WebSocket bars
polygon-api-client==1.14.x    # Full tick data + options
databento==0.40.x             # Institutional L1/L2 (optional)
yfinance==0.2.x               # Research ONLY — not production
requests==2.31.x
httpx==0.27.x                 # Async HTTP
aiohttp==3.9.x                # Used with Alpaca async WebSocket
pandas-datareader==0.10.x     # FRED VIX pull
```

### Analysis & Factors
```
pandas==2.2.x
numpy==1.26.x
pandas-ta==0.3.x              # 130+ TA indicators
ta==0.11.x                    # Lightweight TA alternative
arch==6.3.x                   # GARCH volatility models
scipy==1.13.x                 # Stats, percentile rank, sigmoid
statsmodels==0.14.x           # ADF, ACF, diagnostics
```

### NLP / Sentiment
```
transformers==4.40.x          # HuggingFace FinBERT
torch==2.3.x                  # PyTorch backend for FinBERT
vaderSentiment==3.3.x         # CPU fallback for Reddit/Twitter
newsapi-python==0.2.x         # NewsAPI.org client
praw==7.7.x                   # Reddit PRAW
finnhub-python==2.4.x         # Finnhub news + sentiment
```

### Portfolio Optimization
```
pyportfolioopt==1.5.x         # Efficient frontier, Black-Litterman
riskfolio-lib==6.x            # HRP, CVaR, risk parity
skfolio==0.4.x                # sklearn-compatible portfolio selection
cvxpy==1.4.x                  # Custom convex optimization
```

### Backtesting & Performance
```
vectorbt==0.26.x              # Vectorized backtesting + Monte Carlo
backtrader==1.9.x             # Event-driven validation
bt==1.0.x                     # ETF rotation backtesting
quantstats==0.0.x             # Tearsheet generation
```

### Scheduling & Workers
```
apscheduler==3.10.x           # In-process scheduler (dev)
celery==5.3.x                 # Distributed task queue (prod)
```

---

## Data Sources & API Reference

| Provider | Data | Free Tier | Paid | Best For |
|----------|------|-----------|------|---------|
| Alpaca Markets | OHLCV bars, quotes, WebSocket, paper trading | Unlimited paper; SIP real-time ~$9/mo | $9–$99/mo | Primary price feed + execution |
| Polygon.io | Full tick, options chains, WebSocket | 5 req/min | $199–$499/mo | Tick data, options IV, news |
| Databento | L1/L2/L3, nanosecond-precision | $125 free credits | Usage-based | Institutional tick data |
| Finnhub | News, sentiment, economic calendar | 60 req/min | $50–$300/mo | News sentiment + calendar |
| CBOE | VIX historical CSV | Free download | — | VIX daily history |
| FRED (St. Louis Fed) | VIX (`VIXCLS`), macro data | Free, 120 req/min | — | Daily VIX, economic series |
| NewsAPI.org | 100+ news sources | 100 req/day | From $449/mo | FinBERT input headlines |
| Reddit / PRAW | WSB, investing subreddits | Free (rate-limited) | — | Social sentiment |
| Tradier | Options chains, IV | Free sandbox | $10–$35/mo | Options data in dev |
| FMP (Financial Modeling Prep) | ETF AUM, holdings, fund flows | 5 req/min free | $15–$150/mo | ETF fundamentals |
| ApeWisdom | Reddit ticker mentions (pre-aggregated) | Free public API | — | Quick mention counts |

---

## Project File Structure

```
paisabot/
├── .env                         # API keys, DB URL (never commit)
├── .env.example                 # Template
├── config.py                    # Flask config classes (Dev/Prod/Test)
├── requirements.txt
├── docker-compose.yml           # Flask + PostgreSQL + Redis
├── alembic.ini
│
├── app/
│   ├── __init__.py              # Flask app factory
│   ├── extensions.py            # db, redis, socketio, scheduler instances
│   │
│   ├── models/                  # SQLAlchemy models
│   │   ├── etf_universe.py
│   │   ├── price_bars.py
│   │   ├── factor_scores.py
│   │   ├── signals.py
│   │   ├── positions.py
│   │   ├── trades.py
│   │   ├── system_config.py     # + ConfigLoader helper
│   │   └── performance.py
│   │
│   ├── data/                    # Data providers + ingestion jobs
│   │   ├── base.py              # DataProvider ABC
│   │   ├── alpaca_provider.py
│   │   ├── polygon_provider.py
│   │   ├── finnhub_provider.py
│   │   ├── cboe_provider.py
│   │   ├── reddit_provider.py
│   │   ├── ingestion_jobs.py    # APScheduler tasks
│   │   └── websocket_listener.py
│   │
│   ├── factors/                 # 8 factor implementations
│   │   ├── base.py              # FactorBase ABC
│   │   ├── volatility.py
│   │   ├── sentiment.py
│   │   ├── dispersion.py
│   │   ├── correlation.py
│   │   ├── breadth.py
│   │   ├── trend.py
│   │   ├── liquidity.py
│   │   ├── slippage.py
│   │   └── factor_registry.py  # Orchestrates all factors
│   │
│   ├── signals/
│   │   ├── composite_scorer.py
│   │   ├── regime_detector.py
│   │   └── signal_generator.py
│   │
│   ├── portfolio/
│   │   ├── constructor.py       # PyPortfolioOpt wrapper
│   │   ├── sizer.py             # Volatility-scaled sizing
│   │   ├── constraints.py
│   │   └── rebalancer.py
│   │
│   ├── risk/
│   │   ├── monitor.py           # Drawdown + VaR
│   │   ├── stop_loss.py
│   │   └── alerts.py            # Slack/email/webhook
│   │
│   ├── execution/
│   │   ├── broker_base.py
│   │   ├── alpaca_broker.py
│   │   ├── order_manager.py
│   │   └── slippage_tracker.py
│   │
│   ├── admin/                   # Flask-Admin views
│   │   ├── __init__.py
│   │   ├── views.py
│   │   ├── forms.py
│   │   └── templates/admin/
│   │       ├── index.html
│   │       └── killswitch.html
│   │
│   ├── api/                     # REST blueprint
│   │   ├── routes.py
│   │   └── serializers.py
│   │
│   ├── streaming/               # WebSocket + Redis bridge
│   │   ├── socketio_server.py
│   │   ├── redis_bridge.py
│   │   └── publishers.py
│   │
│   ├── backtesting/
│   │   ├── engine.py
│   │   ├── walk_forward.py
│   │   ├── monte_carlo.py
│   │   ├── cost_model.py
│   │   └── tearsheet.py
│   │
│   └── utils/
│       ├── redis_helpers.py
│       ├── normalization.py
│       ├── time_utils.py        # ET timezone, market hours
│       ├── logging_config.py
│       └── encryption.py       # Fernet for API keys
│
├── migrations/                  # Alembic versions
├── celery_worker.py
├── scheduler.py
├── wsgi.py
│
├── tests/
│   ├── conftest.py
│   ├── test_factors/
│   ├── test_portfolio/
│   ├── test_signals/
│   └── test_backtesting/
│
├── notebooks/                   # Research only
│   ├── factor_research.ipynb
│   └── backtest_analysis.ipynb
│
└── scripts/
    ├── backfill_history.py
    ├── universe_setup.py
    └── seed_config.py
```

---

## Redis Key Map

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `ohlcv:{symbol}:{date}` | Hash | 24h | Latest bar cache |
| `factor:{symbol}:{factor}` | String | 1h | Individual factor scores |
| `scores:{symbol}` | Hash | 15min | All 8 factors per ETF |
| `cache:latest_scores` | JSON String | 5min | All ETF composite scores |
| `cache:portfolio:current` | JSON String | 5min | Current positions snapshot |
| `config:weights` | Hash | No expiry | Factor weights (admin-set) |
| `config:risk` | Hash | No expiry | Risk params (admin-set) |
| `config:portfolio` | Hash | No expiry | Portfolio constraints |
| `config:universe` | Hash | No expiry | Universe filters |
| `config:execution` | Hash | No expiry | Execution settings |
| `config:data` | Hash | No expiry | Data source config |
| `config:scheduling` | Hash | No expiry | Job schedule config |
| `config:alerts` | Hash | No expiry | Alert thresholds |
| `kill_switch:*` | String | No expiry | `1` = active halt |
| `liquidity_shock:{symbol}` | String | 24h | ETF flagged for shock |
| `etf:{symbol}` | Hash | 1h | ETF metadata (ADV, spread) |
| `sentiment:{symbol}:{date}` | Hash | 24h | Daily sentiment cache |
| `channel:bars` | Pub/Sub | — | Real-time bar events |
| `channel:factor_scores` | Pub/Sub | — | Updated scores → dashboard |
| `channel:risk_alerts` | Pub/Sub | — | Risk breaches → dashboard |

---

## WebSocket Streaming Pattern

```python
# Redis → Flask-SocketIO bridge (runs in a daemon thread)
def redis_listener(socketio, redis_client):
    pubsub = redis_client.pubsub()
    pubsub.subscribe('channel:factor_scores', 'channel:risk_alerts')

    for message in pubsub.listen():
        if message['type'] != 'message':
            continue
        channel = message['channel'].decode().split(':')[1]  # 'factor_scores'
        data    = json.loads(message['data'])
        # Broadcast to all connected /dashboard clients
        socketio.emit(channel, data, namespace='/dashboard')
```

**Important**: Redis pub/sub is lossy — messages published with no subscriber are dropped. Use a Redis list (`LPUSH` / `BRPOP`) for critical risk alerts that must not be lost.

---

## Recommended Build Order

| Week | Focus | Output |
|------|-------|--------|
| 1 | DB models + migrations + seed scripts | Schema running locally |
| 2 | Alpaca REST provider + bar ingestion | Price data in DB |
| 3 | Trend + Volatility factors | First two factor scores |
| 4 | Composite scorer + signal generator | Ranked ETF signals |
| 5 | `bt` backtest (Trend + Vol only) | Baseline backtest results |
| 6 | Flask-Admin config view + kill switches | Admin UI operational |
| 7 | Flask-SocketIO + Redis bridge + dashboard | Live score display |
| 8 | Remaining 6 factors (one at a time, validate each) | Full factor suite |
| 9 | Risk manager + stop-loss + alerts | Risk controls live |
| 10 | Alpaca paper trading execution | Paper trading loop |
| 11 | Sentiment factor (FinBERT + Reddit) | Most complex; do last |
| 12 | Monte Carlo + walk-forward + tearsheet | Research deliverables |

---

## Developer Skills Required

### Must Have
- **Async Python**: `asyncio`, `async/await`, `aiohttp` — required for WebSocket consumers
- **Financial math**: log returns, rolling stats, GARCH basics, covariance matrix estimation
- **SQL**: time-series table design, PostgreSQL partitioning, SQLAlchemy 2.0
- **Redis**: pub/sub, hash/list/string data structures, TTL strategy, pipeline batching
- **Portfolio math**: MVO, Ledoit-Wolf shrinkage, sector constraints with cvxpy

### Important
- **NLP/ML basics**: HuggingFace `pipeline` API, FinBERT batching, `torch.no_grad()`
- **Options theory**: implied vol vs realized vol, put/call ratio as sentiment indicator
- **Flask ecosystem**: app factory pattern, Flask-Admin ModelView, Flask-SocketIO
- **Backtesting rigor**: look-ahead bias avoidance, IS vs OOS split, walk-forward testing
- **DevOps**: Docker, docker-compose, environment variable management

### Common Pitfalls
1. **Timezone handling**: All DB timestamps in UTC; convert to ET only at display
2. **API rate limits**: Wrap every API client with a `RateLimiter` class
3. **FinBERT on CPU**: Always batch (batch_size=32); ~2–5s for 200 headlines on CPU
4. **VIX data delay**: Use T-1 VIX close when computing signals at T-close
5. **Look-ahead in normalization**: Percentile rank must only use data available at signal time — never use the full dataset
6. **Redis pub/sub is lossy**: Use list queues for critical alerts
7. **ETF NAV deviation**: ETF price ≠ NAV intraday; use mid-price, not last trade, in slippage models
