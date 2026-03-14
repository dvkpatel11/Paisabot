# Production Flask Setup Research

**Purpose:** Covers Gunicorn configuration, Nginx reverse proxy, systemd process management, Docker Compose stack, PostgreSQL tuning, Redis configuration, and Celery production setup for Paisabot.

---

## B.1 — WSGI Server (Gunicorn + Eventlet)

Flask-SocketIO requires long-lived connections for WebSocket and long-polling. Gunicorn with eventlet worker is the standard production choice.

**Critical rule:** With Flask-SocketIO, use **exactly 1 worker process per Gunicorn instance**. SocketIO connection state cannot be shared across multiple workers unless you configure a Redis message queue.

**`gunicorn.conf.py`:**

```python
# gunicorn.conf.py — Production config for Flask-SocketIO + eventlet

# Binding
bind        = "0.0.0.0:5000"
backlog     = 2048

# Worker config — CRITICAL: 1 worker for SocketIO
workers     = 1
worker_class = "eventlet"
worker_connections = 1000    # max concurrent connections per worker

# Timeouts
timeout      = 120    # seconds
keepalive    = 5
graceful_timeout = 30

# Requests
max_requests       = 1000    # recycle worker after N requests (prevents memory leaks)
max_requests_jitter = 100    # random jitter to avoid thundering herd

# Process naming
proc_name   = "paisabot"

# Logging
accesslog   = "/var/log/paisabot/gunicorn_access.log"
errorlog    = "/var/log/paisabot/gunicorn_error.log"
loglevel    = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(D)s'

# Security
limit_request_line   = 8190
limit_request_fields = 100

# Preload app before forking — saves memory, faster startup
preload_app = True

# Use False when managed by systemd (systemd handles daemon behavior)
daemon      = False
```

**Flask-SocketIO initialization with Redis message queue:**

```python
# app.py
import eventlet
eventlet.monkey_patch()   # MUST be first import before Flask/SocketIO

from flask import Flask
from flask_socketio import SocketIO

app    = Flask(__name__)
socketio = SocketIO(
    app,
    message_queue="redis://localhost:6379/2",  # required for multi-process
    async_mode="eventlet",
    cors_allowed_origins=["https://yourdomain.com"],
    ping_timeout=60,
    ping_interval=25,
)
```

---

## B.2 — Nginx Reverse Proxy

```nginx
# /etc/nginx/sites-available/paisabot

map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

limit_req_zone $binary_remote_addr zone=api_limit:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=5r/m;

upstream gunicorn_backend {
    ip_hash;                         # sticky sessions for SocketIO
    server 127.0.0.1:5000;
    keepalive 32;
}

server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com www.yourdomain.com;

    # SSL/TLS — Let's Encrypt via Certbot
    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;

    # Static files — served directly by Nginx
    location /static/ {
        alias /var/www/paisabot/static/;
        expires 7d;
        add_header Cache-Control "public, no-transform";
        gzip_static on;
    }

    # SocketIO endpoint — WebSocket upgrade
    location /socket.io/ {
        proxy_pass         http://gunicorn_backend/socket.io/;
        proxy_http_version 1.1;
        proxy_buffering    off;
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection $connection_upgrade;
        proxy_set_header   Host       $host;
        proxy_set_header   X-Real-IP  $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;   # 24 hours — keep WS alive
        proxy_send_timeout 86400s;
    }

    # API endpoints — with rate limiting
    location /api/ {
        limit_req zone=api_limit burst=60 nodelay;
        proxy_pass         http://gunicorn_backend;
        proxy_http_version 1.1;
        proxy_set_header   Connection "";
        proxy_set_header   Host       $host;
        proxy_set_header   X-Real-IP  $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }

    # Auth endpoints — stricter rate limit
    location ~ ^/(login|register|api/auth) {
        limit_req zone=auth_limit burst=10 nodelay;
        proxy_pass         http://gunicorn_backend;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    # All other Flask routes
    location / {
        proxy_pass         http://gunicorn_backend;
        proxy_http_version 1.1;
        proxy_set_header   Connection "";
        proxy_set_header   Host       $host;
        proxy_set_header   X-Real-IP  $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    # Deny access to sensitive files
    location ~ /\.(env|git|htaccess) {
        deny all;
        return 404;
    }

    gzip on;
    gzip_types text/plain text/css application/json application/javascript;
    gzip_min_length 1000;

    access_log /var/log/nginx/paisabot_access.log;
    error_log  /var/log/nginx/paisabot_error.log;
}
```

**SSL certificate via Certbot:**

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
sudo certbot renew --dry-run   # test auto-renewal
```

---

## B.3 — Process Management (Systemd)

```ini
# /etc/systemd/system/paisabot-web.service
[Unit]
Description=Paisabot Flask/Gunicorn Web Server
After=network.target postgresql.service redis.service

[Service]
User=paisabot
Group=paisabot
WorkingDirectory=/opt/paisabot
EnvironmentFile=/opt/paisabot/.env.production
ExecStart=/opt/paisabot/venv/bin/gunicorn \
    --config /opt/paisabot/gunicorn.conf.py \
    "app:create_app()"
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure
RestartSec=5s
StandardOutput=append:/var/log/paisabot/web.log
StandardError=append:/var/log/paisabot/web.err.log
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/paisabot-celery-worker.service
[Unit]
Description=Paisabot Celery Worker
After=network.target redis.service postgresql.service

[Service]
User=paisabot
Group=paisabot
WorkingDirectory=/opt/paisabot
EnvironmentFile=/opt/paisabot/.env.production
ExecStart=/opt/paisabot/venv/bin/celery \
    -A app.celery \
    worker \
    --loglevel=info \
    --concurrency=4 \
    --queues=default,market_data,execution,sentiment \
    --hostname=worker@%%h
ExecStop=/opt/paisabot/venv/bin/celery \
    -A app.celery \
    control shutdown
Restart=on-failure
RestartSec=10s
StandardOutput=append:/var/log/paisabot/celery_worker.log
StandardError=append:/var/log/paisabot/celery_worker.err.log
KillMode=mixed
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/paisabot-celery-beat.service
[Unit]
Description=Paisabot Celery Beat Scheduler
After=network.target redis.service

[Service]
User=paisabot
Group=paisabot
WorkingDirectory=/opt/paisabot
EnvironmentFile=/opt/paisabot/.env.production
ExecStart=/opt/paisabot/venv/bin/celery \
    -A app.celery \
    beat \
    --loglevel=info \
    --scheduler redbeat.RedBeatScheduler
Restart=on-failure
RestartSec=10s
StandardOutput=append:/var/log/paisabot/celery_beat.log
StandardError=append:/var/log/paisabot/celery_beat.err.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable paisabot-web paisabot-celery-worker paisabot-celery-beat
sudo systemctl start  paisabot-web paisabot-celery-worker paisabot-celery-beat
```

**Log rotation (`/etc/logrotate.d/paisabot`):**

```ini
/var/log/paisabot/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    sharedscripts
    postrotate
        systemctl kill -s HUP paisabot-web.service
    endscript
}
```

---

## B.4 — Docker Production Setup

**`docker-compose.yml`:**

```yaml
version: "3.9"

services:

  nginx:
    image: nginx:1.25-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
      - ./static:/var/www/paisabot/static:ro
      - ./logs/nginx:/var/log/nginx
    depends_on:
      web:
        condition: service_healthy
    restart: unless-stopped

  web:
    build:
      context: .
      dockerfile: Dockerfile
    command: >
      gunicorn
        --worker-class eventlet
        -w 1
        --bind 0.0.0.0:5000
        --timeout 120
        --max-requests 1000
        --max-requests-jitter 100
        "app:create_app()"
    env_file:
      - .env.production
    environment:
      - FLASK_ENV=production
    volumes:
      - ./logs/web:/var/log/paisabot
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    restart: unless-stopped

  celery_worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: >
      celery -A app.celery worker
        --loglevel=info
        --concurrency=4
        --queues=default,market_data,execution,sentiment
        --hostname=worker@%h
    env_file:
      - .env.production
    volumes:
      - ./logs/celery:/var/log/paisabot
    depends_on:
      redis:
        condition: service_healthy
      db:
        condition: service_healthy
    restart: unless-stopped

  celery_beat:
    build:
      context: .
      dockerfile: Dockerfile
    command: >
      celery -A app.celery beat
        --loglevel=info
        --scheduler redbeat.RedBeatScheduler
    env_file:
      - .env.production
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

  flower:
    build:
      context: .
      dockerfile: Dockerfile
    command: >
      celery -A app.celery flower
        --port=5555
        --broker_api=redis://redis:6379/0
        --basic_auth=${FLOWER_USER}:${FLOWER_PASSWORD}
    env_file:
      - .env.production
    ports:
      - "127.0.0.1:5555:5555"    # Only expose locally; Nginx proxies externally
    depends_on:
      - redis
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres/init:/docker-entrypoint-initdb.d:ro
    environment:
      POSTGRES_DB:       ${POSTGRES_DB}
      POSTGRES_USER:     ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    command: >
      postgres
        -c shared_buffers=512MB
        -c effective_cache_size=2GB
        -c work_mem=32MB
        -c maintenance_work_mem=256MB
        -c max_connections=100
        -c wal_buffers=16MB
        -c checkpoint_completion_target=0.9
        -c random_page_cost=1.1
        -c effective_io_concurrency=200

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
      - ./redis/redis.conf:/usr/local/etc/redis/redis.conf:ro
    command: redis-server /usr/local/etc/redis/redis.conf
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    restart: unless-stopped

volumes:
  postgres_data:
    driver: local
  redis_data:
    driver: local
```

**`Dockerfile`:**

```dockerfile
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y \
    gcc g++ libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r paisabot && useradd -r -g paisabot paisabot

WORKDIR /opt/paisabot

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=paisabot:paisabot . .

RUN mkdir -p /var/log/paisabot && \
    chown -R paisabot:paisabot /var/log/paisabot

USER paisabot
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1
```

---

## B.5 — PostgreSQL Production Configuration

**SQLAlchemy pool settings in `app.py`:**

```python
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size':     10,      # persistent connections in pool
    'max_overflow':  20,      # extra connections above pool_size
    'pool_timeout':  30,      # seconds to wait for connection
    'pool_recycle':  3600,    # recycle connections older than 1 hour
    'pool_pre_ping': True,    # test connection health before use (essential)
}
```

`pool_size + max_overflow` must be less than PostgreSQL `max_connections`.

**`postgresql.conf` tuning — 4 CPU, 8 GB RAM, SSD:**

```ini
# Memory
shared_buffers            = 2GB            # 25% of RAM
effective_cache_size      = 6GB            # 75% of RAM (planner hint)
work_mem                  = 32MB           # per sort/hash operation
maintenance_work_mem      = 256MB          # VACUUM, CREATE INDEX

# Write performance
wal_buffers               = 64MB
checkpoint_completion_target = 0.9
checkpoint_timeout        = 15min
max_wal_size              = 2GB

# I/O (for SSDs)
effective_io_concurrency  = 200
random_page_cost          = 1.1

# Connections
max_connections           = 100

# Parallel query
max_worker_processes      = 4
max_parallel_workers      = 4
max_parallel_workers_per_gather = 2

# Logging
log_min_duration_statement = 1000   # log queries > 1 second
log_lock_waits             = on
```

**Time-series table design (OHLCV storage):**

```sql
-- BRIN indexes for time-ordered data (100x smaller than B-tree)
CREATE TABLE ohlcv_bars (
    id          BIGSERIAL,
    symbol      VARCHAR(20) NOT NULL,
    timeframe   VARCHAR(5)  NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    open        NUMERIC(12,4),
    high        NUMERIC(12,4),
    low         NUMERIC(12,4),
    close       NUMERIC(12,4),
    volume      BIGINT,
    tick_volume BIGINT
) PARTITION BY RANGE (ts);

CREATE INDEX ON ohlcv_bars USING BRIN (ts);
CREATE INDEX ON ohlcv_bars (symbol, timeframe, ts DESC);

-- Monthly partitions
CREATE TABLE ohlcv_bars_2025_01 PARTITION OF ohlcv_bars
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
```

**Alembic workflow:**

```bash
alembic revision --autogenerate -m "add mt5_ticket to positions table"
alembic upgrade head                                  # staging
DATABASE_URL=postgresql://... alembic upgrade head    # production
alembic downgrade -1                                  # emergency rollback
```

Always deploy migration **before** deploying new app code.

---

## B.6 — Redis Production Configuration

**`redis/redis.conf`:**

```conf
bind 127.0.0.1
port 6379
tcp-keepalive 300

maxmemory 2gb
maxmemory-policy allkeys-lru   # for cache DB; use noeviction for Celery DB

# RDB + AOF persistence
save 900 1
save 300 10
save 60 1000
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec

requirepass YourStrongRedisPassword
rename-command FLUSHDB ""       # disable destructive commands
rename-command FLUSHALL ""
rename-command DEBUG ""

slowlog-log-slower-than 10000   # microseconds
slowlog-max-len 128
```

**Redis database separation (critical):**

```
DB 0 — Celery broker (queues)      → maxmemory-policy: noeviction
DB 1 — Cache (market data, ticks)  → maxmemory-policy: allkeys-lru
DB 2 — SocketIO message queue      → maxmemory-policy: noeviction
DB 3 — RedBeat scheduler state     → maxmemory-policy: noeviction
DB 4 — Rate limiting counters      → maxmemory-policy: volatile-lru
```

Celery queue keys must **never** be evicted. Cache keys can be evicted under memory pressure. Never share these on the same Redis database with the same `maxmemory-policy`.

**Redis connection pool:**

```python
import redis

redis_pool = redis.ConnectionPool(
    host="localhost",
    port=6379,
    db=1,                          # cache DB
    password="YourStrongRedisPassword",
    max_connections=20,
    socket_connect_timeout=5,
    socket_timeout=5,
    health_check_interval=30,
    decode_responses=True,
)
redis_client = redis.Redis(connection_pool=redis_pool)
```

---

## B.7 — Celery Production Configuration

**`celery_config.py`:**

```python
import os

broker_url            = os.environ['CELERY_BROKER_URL']
result_backend        = os.environ['CELERY_RESULT_BACKEND']
result_expires        = 3600

task_serializer        = "json"
result_serializer      = "json"
accept_content         = ["json"]
timezone               = "America/New_York"
enable_utc             = True

worker_concurrency     = 4
worker_prefetch_multiplier = 1     # don't prefetch in trading systems
task_acks_late         = True      # ack after task completes, not when received
task_reject_on_worker_lost = True  # re-queue if worker dies mid-task

# Task routing — separate queues by type
task_default_queue     = "default"
task_queues = {
    "default":     {"exchange": "default",     "routing_key": "default"},
    "execution":   {"exchange": "execution",   "routing_key": "execution"},
    "market_data": {"exchange": "market_data", "routing_key": "market_data"},
    "sentiment":   {"exchange": "sentiment",   "routing_key": "sentiment"},
}
task_routes = {
    "app.tasks.execution.*":   {"queue": "execution"},
    "app.tasks.market_data.*": {"queue": "market_data"},
    "app.tasks.sentiment.*":   {"queue": "sentiment"},
    "app.tasks.factors.*":     {"queue": "default"},
}

task_default_retry_delay = 5
task_max_retries         = 3
broker_transport_options = {
    "visibility_timeout": 3600,
    "max_retries": 5,
}
broker_connection_retry_on_startup = True

task_soft_time_limit   = 300    # raises SoftTimeLimitExceeded
task_time_limit        = 360    # hard kill

# RedBeat for Redis-backed beat schedule
beat_scheduler         = "redbeat.RedBeatScheduler"
redbeat_redis_url      = os.environ['CELERY_BROKER_URL']
redbeat_key_prefix     = "paisabot:beat:"
redbeat_lock_timeout   = 5 * 60

beat_schedule = {
    "fetch-market-data-every-minute": {
        "task":     "app.tasks.market_data.fetch_all_symbols",
        "schedule": 60.0,
        "options":  {"queue": "market_data"},
    },
    "run-factor-calculation": {
        "task":     "app.tasks.factors.compute_all_factors",
        "schedule": 300.0,
        "options":  {"queue": "default"},
    },
    "run-sentiment-update": {
        "task":     "app.tasks.sentiment.update_all",
        "schedule": 900.0,
        "options":  {"queue": "sentiment"},
    },
    "sync-mt5-positions": {
        "task":     "app.tasks.execution.sync_positions",
        "schedule": 30.0,
        "options":  {"queue": "execution"},
    },
    "risk-check": {
        "task":     "app.tasks.execution.run_risk_checks",
        "schedule": 60.0,
        "options":  {"queue": "execution"},
    },
}
```

**Starting workers with queue separation:**

```bash
# Execution — 1 concurrent process (no parallelism for order safety)
celery -A app.celery worker -Q execution --concurrency=1 --hostname=exec@%h

# Market data — higher concurrency for I/O-bound fetches
celery -A app.celery worker -Q market_data --concurrency=8 --hostname=mktdata@%h

# Sentiment + slow tasks
celery -A app.celery worker -Q sentiment,slow --concurrency=4 --hostname=slow@%h

# General
celery -A app.celery worker -Q default --concurrency=4 --hostname=default@%h
```

---

## B.8 — Server Requirements

**Linux server (Flask/Celery/PostgreSQL/Redis):**

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk (OS + App) | 20 GB SSD | 50 GB SSD |
| Disk (PostgreSQL data) | 50 GB SSD | 200 GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

**Windows VPS for MT5 (separate machine):**

| Resource | Minimum |
|---|---|
| CPU | 2 vCPU |
| RAM | 4 GB |
| Disk | 30 GB SSD |
| OS | Windows Server 2022 or Windows 10/11 |

Cloud provider recommendations: AWS (EC2 t3.medium for both), DigitalOcean Droplet + Vultr Windows VPS, Azure B2s.

---

## B.9 — Security Hardening

**Server hardening:**

```bash
# Disable root SSH login and password auth
sudo sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# Install fail2ban
sudo apt install fail2ban

# Automatic security updates
sudo apt install unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades

# Run as non-root user
sudo useradd -r -m -s /bin/bash paisabot
chmod 600 /opt/paisabot/.env.production
chmod 700 /opt/paisabot
chown -R paisabot:paisabot /opt/paisabot
```

**UFW firewall:**

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_ADMIN_IP to any port 22
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 6379    # Redis — never expose to internet
sudo ufw deny 5432    # PostgreSQL — never expose to internet
sudo ufw deny 5000    # Gunicorn — Nginx proxies it
sudo ufw deny 5555    # Flower — Nginx proxies it
# Allow MT5 Windows VPS to reach Redis
sudo ufw allow from MT5_WINDOWS_VPS_IP to any port 6379
sudo ufw enable
```

**Application security (Flask-Talisman):**

```python
from flask_talisman import Talisman

Talisman(app,
    force_https=True,
    strict_transport_security=True,
    session_cookie_secure=True,
    session_cookie_http_only=True,
    content_security_policy={
        'default-src': "'self'",
        'script-src': ["'self'", "'unsafe-inline'"],
        'style-src':  ["'self'", "'unsafe-inline'"],
        'connect-src': ["'self'", "wss://yourdomain.com"],
    }
)
```

---

## B.10 — Monitoring

**Health check endpoint:**

```python
# app/routes/health.py
@health_bp.route('/health')
def health_check():
    status = {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

    try:
        db.session.execute(text("SELECT 1"))
        status["db"] = "ok"
    except Exception:
        status["db"]     = "error"
        status["status"] = "degraded"

    try:
        redis_client.ping()
        status["redis"] = "ok"
    except Exception:
        status["redis"]  = "error"
        status["status"] = "degraded"

    try:
        inspect = celery.control.inspect(timeout=2.0)
        workers = inspect.ping()
        status["celery"] = "ok" if workers else "no_workers"
    except Exception:
        status["celery"] = "error"

    code = 200 if status["status"] == "ok" else 503
    return jsonify(status), code
```

**Database backup (cron daily at 2 AM):**

```bash
# /usr/local/bin/backup_postgres.sh
BACKUP_DIR=/var/backups/paisabot/postgres
DATE=$(date +%Y%m%d_%H%M%S)
pg_dump -U paisabot_user -Fc paisabot_db > "$BACKUP_DIR/paisabot_${DATE}.dump"
find "$BACKUP_DIR" -name "*.dump" -mtime +14 -delete
```

---

## Key Architecture Decisions

1. **Flask-SocketIO must use exactly 1 Gunicorn worker.** Configure Redis `message_queue` so Celery workers can emit SocketIO events from separate processes.

2. **Redis database separation is critical.** Celery broker queues (DB0) must use `maxmemory-policy noeviction` so tasks are never evicted. Cache data (DB1) uses `allkeys-lru`. Never share these on the same Redis database.

3. **Celery execution queue uses `--concurrency=1`.** Trade execution is stateful; parallel order placement against the same account causes race conditions in position sizing. All other queues can have higher concurrency.

4. **Use RedBeat instead of the file-based Celery Beat scheduler.** RedBeat stores schedule state in Redis, enabling schedule changes at runtime and surviving worker restarts without a state file.

5. **Never use Flask's development server in production.** It is single-threaded, has no process management, and exposes an interactive debugger (remote code execution vulnerability) in debug mode.
