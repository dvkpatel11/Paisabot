"""Celery tasks for the trading pipeline."""
from __future__ import annotations

import structlog

from celery_worker import celery

logger = structlog.get_logger()


@celery.task(name='app.pipeline.run_trading_pipeline', bind=True, max_retries=0)
def run_trading_pipeline(self):
    """Execute the full trading pipeline: signals → portfolio → risk → execution."""
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.extensions import db as _db, redis_client
        from app.utils.config_loader import ConfigLoader
        from app.pipeline.orchestrator import PipelineOrchestrator

        try:
            config = ConfigLoader(redis_client, _db.session)
            orchestrator = PipelineOrchestrator(
                redis_client=redis_client,
                db_session=_db.session,
                config_loader=config,
            )
            result = orchestrator.run()
            logger.info('pipeline_task_complete', **result)
            return result
        except Exception as exc:
            logger.error('pipeline_task_failed', error=str(exc))
            return {'error': str(exc)}
