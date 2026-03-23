"""Celery tasks for the hardened trading pipeline.

The pipeline runs as a Celery **chain** of 5 isolated stages:

    stage_load_data → stage_portfolio → stage_risk_gate → stage_execute → stage_record

Each stage has its own retry policy and timeout.  If any stage raises,
the ``pipeline_error_handler`` callback fires:

* Logs the failure with stage context.
* Sets ``kill_switch:rebalance = 1`` if the failure was in execution.
* Publishes an error event to ``channel:risk_alerts`` for the dashboard.

Dual pipeline support:
  - ``launch_pipeline()``       → ETF pipeline (asset_class='etf')
  - ``launch_stock_pipeline()`` → Stock pipeline (asset_class='stock')

Usage::

    from app.pipeline.tasks import launch_pipeline, launch_stock_pipeline
    launch_pipeline.delay()          # ETF — fire-and-forget from Celery Beat
    launch_stock_pipeline.delay()    # Stock
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from celery import chain
from celery_worker import celery

logger = structlog.get_logger()


# ─── helpers ──────────────────────────────────────────────────────────

def _make_orchestrator(asset_class: str = 'etf'):
    """Create a PipelineOrchestrator inside an app context.

    Returns (app_ctx, orchestrator, redis_client) so the caller can
    use the app context.
    """
    from app import create_app
    app = create_app()
    ctx = app.app_context()
    ctx.push()

    from app.extensions import db as _db, redis_client
    from app.utils.config_loader import ConfigLoader
    from app.pipeline.orchestrator import PipelineOrchestrator

    config = ConfigLoader(redis_client, _db.session)
    orchestrator = PipelineOrchestrator(
        redis_client=redis_client,
        db_session=_db.session,
        config_loader=config,
        asset_class=asset_class,
    )
    return ctx, orchestrator, redis_client


def _teardown(ctx):
    """Pop the Flask app context."""
    try:
        ctx.pop()
    except Exception:
        pass


# ─── stage 1: load data ──────────────────────────────────────────────

@celery.task(
    name='app.pipeline.stage_load_data',
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    soft_time_limit=60,
    time_limit=90,
)
def stage_load_data(self, asset_class: str = 'etf'):
    """Fetch signals, positions, prices, regime, drawdown.

    Returns a JSON-serializable dict consumed by stage_portfolio.
    """
    ctx, orchestrator, _ = _make_orchestrator(asset_class)
    try:
        return orchestrator.load_data()
    except Exception as exc:
        logger.error('stage_load_data_failed', error=str(exc), asset_class=asset_class)
        raise self.retry(exc=exc)
    finally:
        _teardown(ctx)


# ─── stage 2: portfolio construction ─────────────────────────────────

@celery.task(
    name='app.pipeline.stage_portfolio',
    bind=True,
    max_retries=1,
    default_retry_delay=10,
    soft_time_limit=120,
    time_limit=150,
)
def stage_portfolio(self, pipeline_data: dict):
    """Run PortfolioManager and produce rebalance orders."""
    if pipeline_data.get('status') == 'stopped':
        return pipeline_data

    asset_class = pipeline_data.get('asset_class', 'etf')
    ctx, orchestrator, _ = _make_orchestrator(asset_class)
    try:
        return orchestrator.portfolio(pipeline_data)
    except Exception as exc:
        logger.error('stage_portfolio_failed', error=str(exc), asset_class=asset_class)
        raise self.retry(exc=exc)
    finally:
        _teardown(ctx)


# ─── stage 3: risk gate ──────────────────────────────────────────────

@celery.task(
    name='app.pipeline.stage_risk_gate',
    bind=True,
    max_retries=1,
    default_retry_delay=5,
    soft_time_limit=30,
    time_limit=60,
)
def stage_risk_gate(self, pipeline_data: dict):
    """Run pre-trade risk gate; split orders into approved / blocked."""
    if pipeline_data.get('status') == 'stopped':
        return pipeline_data

    asset_class = pipeline_data.get('asset_class', 'etf')
    ctx, orchestrator, _ = _make_orchestrator(asset_class)
    try:
        return orchestrator.risk_gate(pipeline_data)
    except Exception as exc:
        logger.error('stage_risk_gate_failed', error=str(exc), asset_class=asset_class)
        raise self.retry(exc=exc)
    finally:
        _teardown(ctx)


# ─── stage 4: execution ──────────────────────────────────────────────

@celery.task(
    name='app.pipeline.stage_execute',
    bind=True,
    max_retries=0,            # NEVER retry execution — risk of double-fills
    soft_time_limit=300,
    time_limit=360,
)
def stage_execute(self, pipeline_data: dict):
    """Submit approved orders to the broker.

    No retry.  On failure the chain's ``link_error`` callback sets
    ``kill_switch:rebalance`` and alerts the dashboard.
    """
    if pipeline_data.get('status') == 'stopped':
        return pipeline_data

    asset_class = pipeline_data.get('asset_class', 'etf')
    ctx, orchestrator, _ = _make_orchestrator(asset_class)
    try:
        return orchestrator.execute(pipeline_data)
    except Exception as exc:
        logger.critical('stage_execute_failed', error=str(exc), asset_class=asset_class)
        raise  # propagate — do NOT retry
    finally:
        _teardown(ctx)


# ─── stage 5: record / publish ───────────────────────────────────────

@celery.task(
    name='app.pipeline.stage_record',
    bind=True,
    max_retries=2,
    default_retry_delay=3,
    soft_time_limit=30,
    time_limit=60,
)
def stage_record(self, pipeline_data: dict):
    """Publish pipeline summary to Redis + dashboard."""
    asset_class = pipeline_data.get('asset_class', 'etf')
    ctx, orchestrator, _ = _make_orchestrator(asset_class)
    try:
        return orchestrator.record(pipeline_data)
    except Exception as exc:
        logger.error('stage_record_failed', error=str(exc), asset_class=asset_class)
        raise self.retry(exc=exc)
    finally:
        _teardown(ctx)


# ─── error callback ──────────────────────────────────────────────────

@celery.task(name='app.pipeline.pipeline_error_handler')
def pipeline_error_handler(request, exc, traceback):
    """Called when any stage in the chain raises an unrecoverable error.

    * Logs with CRITICAL severity.
    * Sets ``kill_switch:rebalance = 1`` to block future rebalances
      until an operator investigates.
    * Caches error to ``cache:pipeline:latest`` for dashboard visibility.
    * Publishes to ``channel:risk_alerts`` for live dashboard.
    """
    failed_task = getattr(request, 'task', 'unknown')
    task_id = getattr(request, 'id', 'unknown')

    logger.critical(
        'pipeline_chain_failed',
        failed_task=failed_task,
        task_id=task_id,
        error=str(exc),
    )

    # Best-effort Redis operations — don't let a Redis failure mask the
    # original error.
    try:
        import redis as _redis_lib
        import os

        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
        r = _redis_lib.from_url(redis_url, decode_responses=True)

        # Defensive kill switch — block automated rebalances until operator
        # reviews the failure.
        r.set('kill_switch:rebalance', '1')

        now = datetime.now(timezone.utc).isoformat()

        # Cache error for dashboard / API
        error_payload = json.dumps({
            'status': 'error',
            'stage': failed_task,
            'task_id': task_id,
            'error': str(exc),
            'timestamp': now,
        })
        r.set('cache:pipeline:latest', error_payload, ex=3600)

        # Publish alert for live dashboard
        r.publish('channel:risk_alerts', json.dumps({
            'alert_type': 'pipeline_failure',
            'severity': 'critical',
            'stage': failed_task,
            'error': str(exc),
            'timestamp': now,
        }))

    except Exception as redis_exc:
        logger.error(
            'pipeline_error_handler_redis_failed',
            redis_error=str(redis_exc),
            original_error=str(exc),
        )


# ─── chain launchers ─────────────────────────────────────────────────

@celery.task(name='app.pipeline.launch_pipeline')
def launch_pipeline():
    """Build and dispatch the ETF pipeline chain.

    Called by Celery Beat at 18:15 ET daily.  Can also be called
    manually from ``flask shell``::

        from app.pipeline.tasks import launch_pipeline
        launch_pipeline.delay()
    """
    logger.info('pipeline_chain_launching', asset_class='etf')

    pipeline_chain = chain(
        stage_load_data.s('etf'),
        stage_portfolio.s(),
        stage_risk_gate.s(),
        stage_execute.s(),
        stage_record.s(),
    )

    result = pipeline_chain.apply_async(
        link_error=pipeline_error_handler.s(),
    )

    logger.info('pipeline_chain_dispatched', chain_id=result.id, asset_class='etf')
    return {'chain_id': result.id, 'asset_class': 'etf', 'status': 'dispatched'}


@celery.task(name='app.pipeline.launch_stock_pipeline')
def launch_stock_pipeline():
    """Build and dispatch the Stock pipeline chain.

    Called by Celery Beat at 18:20 ET daily (after ETF pipeline).
    """
    logger.info('pipeline_chain_launching', asset_class='stock')

    pipeline_chain = chain(
        stage_load_data.s('stock'),
        stage_portfolio.s(),
        stage_risk_gate.s(),
        stage_execute.s(),
        stage_record.s(),
    )

    result = pipeline_chain.apply_async(
        link_error=pipeline_error_handler.s(),
    )

    logger.info('pipeline_chain_dispatched', chain_id=result.id, asset_class='stock')
    return {'chain_id': result.id, 'asset_class': 'stock', 'status': 'dispatched'}


# ─── legacy compat (kept for tests that import the old name) ─────────

run_trading_pipeline = launch_pipeline
