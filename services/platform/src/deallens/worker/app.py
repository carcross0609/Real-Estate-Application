"""The Celery application — broker/backend from settings, task discovery, beat schedule.

Run it with:
    celery -A deallens.worker.app:celery_app worker -l info      # process tasks
    celery -A deallens.worker.app:celery_app beat   -l info      # emit scheduled tasks

`celery_eager` (settings) flips `task_always_eager`: tasks run inline in the caller with no
broker or worker — what a synchronous single-process run and the task tests use, so the same
task code is exercised without standing up Redis.

Every task acquires its DB session through `worker.runtime` (owner-role `system_session`), so
the worker never serves an end-user request and RLS-scoped tables are written only through the
explicit org/user ids the domain events carry.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from deallens.core.config import get_settings
from deallens.core.logging import configure_logging
from deallens.worker.runtime import reset_event_loop
from deallens.worker.schedule import BEAT_SCHEDULE

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery("deallens", broker=settings.broker_url, backend=settings.result_backend)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A poison record must not wedge the queue: tasks are acked after completion and retried
    # with backoff, but a task that keeps failing is dead-lettered by max_retries, not looped.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=30,
    task_max_retries=3,
    # Fair dispatch — one heavy analysis chain shouldn't starve short alert tasks.
    worker_prefetch_multiplier=1,
    task_always_eager=settings.celery_eager,
    task_eager_propagates=settings.celery_eager,
    beat_schedule=BEAT_SCHEDULE,
    # Task modules are imported for their side effect of registering with this app.
    imports=(
        "deallens.worker.tasks.ingestion",
        "deallens.worker.tasks.pipeline",
        "deallens.worker.tasks.analysis",
        "deallens.worker.tasks.alerts",
        "deallens.worker.tasks.scoring",
    ),
)


@worker_process_init.connect
def _init_process(**_: object) -> None:
    """Each forked worker child starts with a clean event loop (see `worker.runtime`)."""
    reset_event_loop()


__all__ = ["celery_app"]
