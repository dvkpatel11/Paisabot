"""Gunicorn configuration for Paisabot production deployment."""
import os

# ---------- Server socket ----------
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# ---------- Worker processes ----------
# eventlet async worker for Flask-SocketIO compatibility
worker_class = 'eventlet'
workers = int(os.environ.get('GUNICORN_WORKERS', '2'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))

# ---------- Timeouts ----------
timeout = 120           # kill worker after 120s of silence
graceful_timeout = 30   # allow 30s for graceful shutdown
keepalive = 5           # keep-alive connections for 5s

# ---------- Logging ----------
accesslog = '-'         # stdout
errorlog = '-'          # stderr
loglevel = os.environ.get('LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" %(D)sms'

# ---------- Process naming ----------
proc_name = 'paisabot'

# ---------- Security ----------
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# ---------- Server hooks ----------
def on_starting(server):
    """Log server startup."""
    server.log.info('Paisabot starting with %d workers', workers)


def post_fork(server, worker):
    """Re-seed random after fork."""
    import random
    random.seed()
