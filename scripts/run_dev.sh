#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Paisabot — Development launcher
# Starts PostgreSQL + Redis (Docker), runs DB migrations, seeds
# config, then launches the Flask dev server and Celery worker.
#
# Usage:  ./scripts/run_dev.sh [--skip-docker] [--skip-migrate] [--skip-seed]
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# ── Parse flags ──────────────────────────────────────────────────
SKIP_DOCKER=false
SKIP_MIGRATE=false
SKIP_SEED=false

for arg in "$@"; do
    case "$arg" in
        --skip-docker)  SKIP_DOCKER=true ;;
        --skip-migrate) SKIP_MIGRATE=true ;;
        --skip-seed)    SKIP_SEED=true ;;
        -h|--help)
            echo "Usage: $0 [--skip-docker] [--skip-migrate] [--skip-seed]"
            echo ""
            echo "  --skip-docker   Don't start/check Docker containers"
            echo "  --skip-migrate  Don't run alembic migrations"
            echo "  --skip-seed     Don't seed config or universe"
            exit 0
            ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

# ── Colors ───────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }

# ── Check .env ───────────────────────────────────────────────────
if [ ! -f .env ]; then
    warn ".env not found — copying from .env.example"
    cp .env.example .env
    warn "Edit .env and fill in your API keys before going live."
fi

# ── Activate venv if present ─────────────────────────────────────
if [ -d "venv" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null || true
    info "Virtual environment activated"
fi

# ── Docker: PostgreSQL + Redis ───────────────────────────────────
if [ "$SKIP_DOCKER" = false ]; then
    if ! command -v docker &>/dev/null; then
        error "Docker not found. Install Docker or run with --skip-docker if services are running externally."
        exit 1
    fi

    info "Starting PostgreSQL and Redis via Docker Compose..."
    docker compose up -d

    # Wait for healthy containers
    echo -n "  Waiting for PostgreSQL..."
    for i in $(seq 1 30); do
        if docker compose exec -T postgres pg_isready -U paisabot &>/dev/null; then
            echo " ready"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo ""
            error "PostgreSQL did not become ready in time"
            exit 1
        fi
        sleep 1
        echo -n "."
    done

    echo -n "  Waiting for Redis..."
    for i in $(seq 1 15); do
        if docker compose exec -T redis redis-cli ping &>/dev/null; then
            echo " ready"
            break
        fi
        if [ "$i" -eq 15 ]; then
            echo ""
            error "Redis did not become ready in time"
            exit 1
        fi
        sleep 1
        echo -n "."
    done

    info "Docker services are up"
else
    warn "Skipping Docker — assuming PostgreSQL and Redis are running"
fi

# ── Database migrations ──────────────────────────────────────────
if [ "$SKIP_MIGRATE" = false ]; then
    info "Running database migrations..."
    alembic upgrade head
    info "Migrations applied"
else
    warn "Skipping migrations"
fi

# ── Seed config + universe ───────────────────────────────────────
if [ "$SKIP_SEED" = false ]; then
    info "Seeding system config..."
    python scripts/seed_config.py

    info "Seeding ETF universe..."
    python scripts/universe_setup.py
else
    warn "Skipping seed"
fi

# ── Trap: clean shutdown ─────────────────────────────────────────
CELERY_PID=""

cleanup() {
    echo ""
    info "Shutting down..."
    if [ -n "$CELERY_PID" ] && kill -0 "$CELERY_PID" 2>/dev/null; then
        kill "$CELERY_PID" 2>/dev/null
        wait "$CELERY_PID" 2>/dev/null || true
        info "Celery worker stopped"
    fi
    info "Done. Docker services are still running — stop with: docker compose down"
}
trap cleanup EXIT INT TERM

# ── Celery worker (background) ───────────────────────────────────
info "Starting Celery worker..."
celery -A celery_worker worker \
    --loglevel=info \
    --concurrency=2 \
    -Q celery,execution,market_data,sentiment \
    &
CELERY_PID=$!
info "Celery worker running (PID $CELERY_PID)"

# ── Flask dev server (foreground) ────────────────────────────────
info "Starting Flask dev server on http://localhost:5000"
echo "────────────────────────────────────────────────────"
python wsgi.py
