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
            'app.data.*': {'queue': 'market_data'},
            'app.factors.sentiment.*': {'queue': 'sentiment'},
        },
        'beat_schedule': {
            'refresh-bars-daily': {
                'task': 'app.data.refresh_all_bars',
                'schedule': crontab(hour=17, minute=0),
            },
            'refresh-vix-daily': {
                'task': 'app.data.refresh_vix',
                'schedule': crontab(hour=17, minute=30),
            },
            'compute-factors-daily': {
                'task': 'app.data.compute_all_factors',
                'schedule': crontab(hour=18, minute=0),
            },
        },
    })
    return celery


celery = make_celery()
