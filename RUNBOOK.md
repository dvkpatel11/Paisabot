# Paisabot Operations Runbook

This document covers the five scenarios that require manual operator intervention.
All Redis commands assume default port `6379`; all Flask shell commands assume the
virtual environment is active and `FLASK_APP=app` is set.

---

## Table of Contents

1. [Simulation → Live Validation Checklist](#1-simulation--live-validation-checklist)
2. [Kill Switch Response](#2-kill-switch-response)
3. [Missed Celery Tasks](#3-missed-celery-tasks)
4. [Position Mismatch](#4-position-mismatch)
5. [Broker Disconnect](#5-broker-disconnect)

---

## 1. Simulation → Live Validation Checklist

**Minimum requirement**: 2 consecutive weeks in `simulation` mode with live
Alpaca feeds, zero kill-switch firings, and position-tracker reconciliation
passing before switching to live.

### Pre-flight (one-time)

- [ ] `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are paper-trading credentials.
- [ ] `ALPACA_PAPER=true` in `.env`.
- [ ] All production env vars set and validated:
      ```bash
      flask shell -c "from app import create_app; create_app('production')"
      ```
      Should exit cleanly.  If it raises, fix the missing vars before proceeding.
- [ ] Database migrated to latest revision:
      ```bash
      alembic upgrade head
      ```
- [ ] Seed data loaded:
      ```bash
      python scripts/seed_config.py
      python scripts/universe_setup.py
      ```
- [ ] Run the Alpaca sandbox integration test suite:
      ```bash
      ALPACA_PAPER_KEY=<key> ALPACA_PAPER_SECRET=<secret> \
        pytest tests/test_integration/test_alpaca_sandbox.py -m integration -v
      ```
      **All tests must pass before proceeding.**

### Week 1–2 daily checks (simulation mode)

Each trading day (after 6:30 PM ET when EOD pipeline has run):

- [ ] Celery beat completed all 6 scheduled tasks — check logs:
      ```bash
      redis-cli lrange channel:risk_alerts 0 -1   # should be empty
      grep '"task_name"' /var/log/paisabot/celery.log | tail -20
      ```
- [ ] Factor scores are fresh (TTL > 0):
      ```bash
      redis-cli ttl scores:SPY    # should be 700-900
      redis-cli ttl scores:QQQ
      ```
- [ ] Signals generated and written to DB:
      ```bash
      flask shell -c "
      from app.models.signals import Signal
      from datetime import datetime, timezone, timedelta
      cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
      count = Signal.query.filter(Signal.signal_time > cutoff).count()
      print('Signals today:', count)
      "
      ```
      Expect ≥ 1 row per symbol in the active universe.
- [ ] No kill switches are set:
      ```bash
      redis-cli keys 'kill_switch:*'   # should return (empty list)
      ```
- [ ] Portfolio weights sum to ≤ 1.0:
      ```bash
      flask shell -c "
      from app.models.positions import Position
      from decimal import Decimal
      open_pos = Position.query.filter_by(status='open').all()
      total = sum(float(p.weight or 0) for p in open_pos)
      print('Total weight:', total)
      "
      ```
- [ ] Simulated slippage is within expected range (< 5 bps for SPY):
      Check `/api/trades` for `estimated_slippage_bps` in recent simulation fills.

### End-of-week sign-off

- [ ] No unhandled exceptions in Celery or Flask logs for the week.
- [ ] PnL curve in dashboard is plausible (not flat, not cliff-edge).
- [ ] Factor heatmap shows variation across ETFs (not stuck at 0.5).
- [ ] Run position reconciliation manually (see §4) — zero mismatches.

### Switching from simulation to live

1. Log into the admin UI at `/config`.
2. Navigate to the **Mode** tab.
3. Change `operational_mode` from `simulation` to `live`.
4. The UI will prompt for admin password confirmation.
5. Observe the **Kill Switches** tab — `kill_switch:rebalance` should clear automatically.
6. Watch the next 6 PM pipeline run in the Celery logs. The first live rebalance
   will submit real paper orders to Alpaca. Confirm fills appear in `/api/trades`.

**Rollback**: set `operational_mode` back to `simulation` in the admin UI at any
time.  This immediately sets `kill_switch:rebalance = 1` and stops new orders.

---

## 2. Kill Switch Response

Kill switches are Redis keys `kill_switch:<name>` with value `'1'`.  They are
checked in the execution engine before every order.  The risk engine sets them
automatically on breach; operators can set or clear them manually.

### Detecting which switch fired

```bash
redis-cli keys 'kill_switch:*'
# Example output:
#   kill_switch:rebalance
#   kill_switch:drawdown
```

| Key | Meaning | Auto-set by |
|-----|---------|-------------|
| `kill_switch:rebalance` | Stop all new rebalances | Drawdown -15%; simulation→live toggle |
| `kill_switch:trading` | Stop all order submission | VaR breach; correlation shock |
| `kill_switch:all` | Emergency full halt | Operator or extreme risk event |
| `kill_switch:drawdown` | Portfolio in drawdown guard | Continuous monitor (-15%) |

### Investigating before reset

1. Check the risk alerts queue:
   ```bash
   redis-cli lrange channel:risk_alerts 0 -1
   ```
2. Check structured logs for the breach event:
   ```bash
   grep '"kill_switch"' /var/log/paisabot/celery.log | tail -20
   grep '"drawdown_breach"' /var/log/paisabot/celery.log | tail -5
   ```
3. Check current drawdown in the dashboard at `/` (risk gauges panel) or:
   ```bash
   flask shell -c "
   from app.risk.risk_manager import RiskManager
   import fakeredis
   # Use real Redis in production
   from app.extensions import redis_client
   rm = RiskManager(redis_client=redis_client)
   print(rm.get_current_drawdown())
   "
   ```

### Resetting a kill switch

Only reset after you understand why it fired and the underlying condition has
been resolved (e.g., drawdown recovered, broker reconnected, positions reconciled).

**Via admin UI** (preferred):
1. `/config` → **Kill Switches** tab
2. Toggle the relevant switch off
3. Confirm with admin password

**Via Redis CLI** (emergency):
```bash
redis-cli del kill_switch:rebalance
redis-cli del kill_switch:trading
```

**Note**: `kill_switch:all` requires explicit admin confirmation in the UI.
Never clear it via CLI without understanding the root cause.

---

## 3. Missed Celery Tasks

Celery beat runs 6 scheduled tasks after market close.  A missed task means
factor scores or signals are stale for the day.

### Detecting missed tasks

1. Check Celery beat log for task dispatch:
   ```bash
   grep 'run_trading_pipeline\|compute_all_factors' /var/log/paisabot/celery-beat.log | tail -10
   ```
2. Check task result backend for failures:
   ```bash
   # If using redis result backend:
   redis-cli keys 'celery-task-meta-*' | head -5
   ```
3. Check factor score freshness:
   ```bash
   redis-cli ttl scores:SPY   # -2 means key missing (task never ran)
   ```
4. Check the pipeline status cache:
   ```bash
   redis-cli get cache:pipeline:factor_engine
   redis-cli get cache:pipeline:signal_engine
   ```
   A missing or stale `last_activity` timestamp confirms the task did not run.

### Root causes and fixes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Beat log shows dispatch but no worker log | Worker not running | Restart Celery worker |
| `SoftTimeLimitExceeded` in logs | FinBERT or data fetch took > 20 min | Reduce universe; check data provider latency |
| `ConnectionError` to Redis | Redis restarted | Restart Redis; restart Celery worker |
| `ProgrammingError` (DB) | Migration not applied | Run `alembic upgrade head`; restart worker |
| Task missing from beat schedule | Beat process crashed | Restart Celery beat |

### Manually replaying a missed task

```bash
# Trigger factor computation for today
flask shell -c "
from celery_worker import celery
celery.send_task('app.data.compute_all_factors')
print('Task dispatched')
"

# Trigger full pipeline
flask shell -c "
from celery_worker import celery
celery.send_task('app.pipeline.run_trading_pipeline')
"
```

Wait for completion (up to 20 minutes for EOD factors), then verify:
```bash
redis-cli ttl scores:SPY   # should be ~900
```

### Restarting Celery services

```bash
# Worker
sudo systemctl restart paisabot-celery-worker

# Beat scheduler
sudo systemctl restart paisabot-celery-beat

# Verify
sudo systemctl status paisabot-celery-worker
sudo systemctl status paisabot-celery-beat
```

---

## 4. Position Mismatch

A position mismatch occurs when the DB `positions` table shows a different
quantity or symbol than the broker's actual portfolio.  This can happen after
a partial fill, a crash mid-execution, or a manual trade in the Alpaca UI.

### Detecting a mismatch

The position reconciliation check compares `broker.get_positions()` against
open DB positions.  Run it manually:

```bash
flask shell -c "
from app.execution.alpaca_broker import AlpacaBroker
from app.models.positions import Position
from app.extensions import db
import os

broker = AlpacaBroker(
    api_key=os.environ['ALPACA_API_KEY'],
    secret_key=os.environ['ALPACA_SECRET_KEY'],
    paper=(os.environ.get('ALPACA_PAPER', 'true').lower() == 'true'),
)
broker.connect()

broker_positions = {p['symbol']: p for p in broker.get_positions()}
db_positions = {
    p.symbol: p
    for p in Position.query.filter_by(status='open').all()
}

all_symbols = set(broker_positions) | set(db_positions)
mismatches = []

for sym in all_symbols:
    broker_qty = broker_positions.get(sym, {}).get('qty', 0)
    db_qty = float(db_positions[sym].quantity) if sym in db_positions else 0
    if abs(broker_qty - db_qty) > 0.001:
        mismatches.append({'symbol': sym, 'broker': broker_qty, 'db': db_qty})

if mismatches:
    print('MISMATCHES FOUND:')
    for m in mismatches:
        print(f'  {m[\"symbol\"]}: broker={m[\"broker\"]:.4f}  db={m[\"db\"]:.4f}')
else:
    print('No mismatches — positions reconciled.')
"
```

### Resolution procedure

**Option A: DB is wrong, broker is correct** (most common — e.g., partial fill
was not recorded because the worker crashed)

```bash
flask shell -c "
from app.models.positions import Position
from app.extensions import db
from decimal import Decimal

# Example: broker shows SPY qty=0.022, DB shows qty=0
sym = 'SPY'
broker_qty = 0.022
broker_entry = 451.30

pos = Position.query.filter_by(symbol=sym, status='open', direction='long').first()
if pos is None:
    from datetime import datetime, timezone
    pos = Position(
        symbol=sym, broker='alpaca_paper', direction='long',
        entry_price=Decimal(str(broker_entry)),
        current_price=Decimal(str(broker_entry)),
        quantity=Decimal(str(broker_qty)),
        notional=Decimal(str(round(broker_entry * broker_qty, 2))),
        high_watermark=Decimal(str(broker_entry)),
        unrealized_pnl=Decimal('0'), realized_pnl=Decimal('0'),
        status='open', opened_at=datetime.now(timezone.utc),
    )
    db.session.add(pos)
else:
    pos.quantity = Decimal(str(broker_qty))
    pos.entry_price = Decimal(str(broker_entry))

db.session.commit()
print('Position corrected.')
"
```

**Option B: Broker has no position, DB shows open** (e.g., broker closed it
via a corporate action or manual trade in Alpaca UI)

```bash
flask shell -c "
from app.models.positions import Position
from app.extensions import db
from datetime import datetime, timezone
from decimal import Decimal

sym = 'SPY'
pos = Position.query.filter_by(symbol=sym, status='open').first()
if pos:
    pos.status = 'closed'
    pos.closed_at = datetime.now(timezone.utc)
    pos.close_reason = 'manual_reconciliation'
    pos.quantity = Decimal('0')
    pos.notional = Decimal('0')
    db.session.commit()
    print('Position marked closed.')
"
```

After manual reconciliation, run the detection script above again to confirm
zero mismatches before resuming live trading.

---

## 5. Broker Disconnect

`AlpacaBroker.connect()` is called once at execution engine startup.  If the
connection drops (Alpaca API timeout, network issue, invalid credentials after
key rotation), orders will fail with `status='error', reason='no_broker'`.

### Detecting a disconnect

1. Orders returning `reason='no_broker'` or `reason='quote_failed'` in logs:
   ```bash
   grep 'no_broker\|broker_connect_failed\|quote_failed' /var/log/paisabot/app.log | tail -20
   ```
2. The `/api/health` endpoint will report broker status:
   ```bash
   curl -s http://localhost:5000/api/health | python -m json.tool | grep broker
   ```
3. Check kill switches — a broker disconnect during live trading should trigger
   `kill_switch:trading` automatically via the continuous risk monitor.

### Immediate response

1. Set the kill switch manually if it has not fired automatically:
   ```bash
   redis-cli set kill_switch:trading 1
   ```
   This prevents the execution engine from submitting orders to a broken broker.

2. Verify existing positions are intact in the Alpaca dashboard at
   `https://app.alpaca.markets/paper-trading` (paper) or live portal.

### Reconnection procedure

1. Check if Alpaca's API is reachable:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" https://api.alpaca.markets/v2/clock
   # Expect 200
   ```
2. If the API is up, check for credential rotation in `.env`:
   ```bash
   grep ALPACA .env
   ```
3. Restart the Flask/Gunicorn process (which reinitializes the broker):
   ```bash
   sudo systemctl restart paisabot-web
   sudo systemctl restart paisabot-celery-worker
   ```
4. Verify reconnection via health endpoint:
   ```bash
   curl -s http://localhost:5000/api/health | python -m json.tool
   ```
5. Once reconnected and positions are verified, clear the kill switch via the
   admin UI (`/config` → **Kill Switches**) or:
   ```bash
   redis-cli del kill_switch:trading
   ```

### If reconnection fails repeatedly

- Rotate Alpaca API credentials in the Alpaca dashboard.
- Update `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in `.env`.
- Restart services.
- Re-run the sandbox integration test to confirm the new credentials work:
  ```bash
  ALPACA_PAPER_KEY=<new_key> ALPACA_PAPER_SECRET=<new_secret> \
    pytest tests/test_integration/test_alpaca_sandbox.py::TestConnection -m integration -v
  ```

---

## Quick Reference

```
# Current kill switches
redis-cli keys 'kill_switch:*'

# Set a kill switch
redis-cli set kill_switch:rebalance 1

# Clear a kill switch
redis-cli del kill_switch:rebalance

# Check factor freshness
redis-cli ttl scores:SPY

# Manually trigger EOD pipeline
flask shell -c "from celery_worker import celery; celery.send_task('app.pipeline.run_trading_pipeline')"

# Run position reconciliation
flask shell -c "exec(open('scripts/reconcile_positions.py').read())"

# Alpaca sandbox integration tests
ALPACA_PAPER_KEY=x ALPACA_PAPER_SECRET=y pytest tests/test_integration/test_alpaca_sandbox.py -m integration -v
```
