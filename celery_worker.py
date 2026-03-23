import os

from celery import Celery
from celery.schedules import crontab


def make_celery(app=None):
    celery = Celery('paisabot')
    celery.config_from_object({
        'broker_url': os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/1'),
        'result_backend': os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2'),
        'task_serializer': 'json',
        'result_serializer': 'json',
        'accept_content': ['json'],
        'timezone': 'America/New_York',
        'enable_utc': True,
        'task_acks_late': True,
        'task_reject_on_worker_lost': True,
        'worker_prefetch_multiplier': 1,
        'task_soft_time_limit': 300,
        'task_time_limit': 360,
        'task_default_retry_delay': 5,
        'task_max_retries': 3,
        'task_routes': {
            'app.execution.*': {'queue': 'execution'},
            'app.pipeline.stage_execute': {'queue': 'execution'},
            'app.data.*': {'queue': 'market_data'},
            'app.factors.sentiment.*': {'queue': 'sentiment'},
        },
        # Per-task time-limit overrides for long-running EOD jobs.
        # Pipeline stages have individual limits on their task decorators.
        'task_annotations': {
            'app.data.compute_all_factors': {
                'soft_time_limit': 1200,
                'time_limit': 1320,
            },
        },
        'beat_schedule': {
            # ── EOD data refresh ───────────────────────────────────
            'refresh-bars-daily': {
                'task': 'app.data.refresh_all_bars',
                'schedule': crontab(hour=17, minute=0),
            },
            'refresh-universe-metadata-daily': {
                'task': 'app.data.refresh_universe_metadata',
                'schedule': crontab(hour=17, minute=15),
            },
            'refresh-vix-daily': {
                'task': 'app.data.refresh_vix',
                'schedule': crontab(hour=17, minute=30),
            },
            'refresh-cboe-put-call-daily': {
                'task': 'app.data.refresh_cboe_put_call',
                'schedule': crontab(hour=17, minute=45),
            },
            # ── Factor computation ─────────────────────────────────
            'compute-factors-daily': {
                'task': 'app.data.compute_all_factors',
                'schedule': crontab(hour=18, minute=0),
            },
            # ── Trading pipeline (chained stages) ──────────────────
            'launch-pipeline-daily': {
                'task': 'app.pipeline.launch_pipeline',
                'schedule': crontab(hour=18, minute=15),
            },
            # ── Post-trade ─────────────────────────────────────────
            'record-performance-daily': {
                'task': 'app.data.record_daily_performance',
                'schedule': crontab(hour=18, minute=30),
            },
            # ── Continuous risk monitoring ──────────────────────────
            'continuous-risk-monitor': {
                'task': 'app.risk.run_continuous_monitor',
                'schedule': crontab(minute='*/5'),
            },
            # ── Stock data & fundamentals ────────────────────────────
            'refresh-stock-bars-daily': {
                'task': 'app.data.refresh_all_stock_bars',
                'schedule': crontab(hour=17, minute=5),
            },
            'refresh-stock-fundamentals-daily': {
                'task': 'app.data.refresh_stock_fundamentals',
                'schedule': crontab(hour=17, minute=20),
            },
            'refresh-earnings-calendar-daily': {
                'task': 'app.data.refresh_earnings_calendar',
                'schedule': crontab(hour=17, minute=35),
            },
            'compute-stock-factors-daily': {
                'task': 'app.data.compute_stock_factors',
                'schedule': crontab(hour=18, minute=5),
            },
            'launch-stock-pipeline-daily': {
                'task': 'app.pipeline.launch_stock_pipeline',
                'schedule': crontab(hour=18, minute=20),
            },
        },
    })
    return celery


celery = make_celery()

# Import task modules AFTER celery instance is created so @celery.task
# decorators register with this app. tasks.py does `from celery_worker
# import celery` — so celery must exist before this import runs.
import app.data.tasks  # noqa: E402, F401
import app.data.fundamentals_tasks  # noqa: E402, F401
import app.pipeline.tasks  # noqa: E402, F401
import app.risk.tasks  # noqa: E402, F401
