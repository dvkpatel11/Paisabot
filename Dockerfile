# ---------- Stage 1: build ----------
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements-prod.txt

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim

# System deps for psycopg2-binary, eventlet
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -r paisabot && useradd -r -g paisabot -d /app paisabot

WORKDIR /app
COPY . .
RUN chown -R paisabot:paisabot /app

USER paisabot

# Environment defaults — override via docker-compose or .env
ENV FLASK_CONFIG=production \
    PYTHONUNBUFFERED=1 \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    PORT=5000

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Gunicorn with eventlet async worker
CMD ["sh", "-c", "gunicorn --config gunicorn.conf.py wsgi:app"]
