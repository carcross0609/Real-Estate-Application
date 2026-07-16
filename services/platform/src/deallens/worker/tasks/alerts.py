"""Alert-delivery tasks — dispatch queued notifications and roll matches into digests.

The buy-box *matching* runs inline in the analysis fan-out (`orchestration.analyze_and_score`
→ `alerts.service.match_property`), so a new opportunity alerts the moment it's scored. These
scheduled tasks are the delivery side:
- `dispatch_pending` (every couple minutes) is the backstop that sends anything left QUEUED —
  an in-app feed row, or an instant send that failed and needs a retry.
- `run_digests` (hourly/daily) rolls the QUEUED matches for boxes at that latency into one
  digest per user, so an hourly/daily subscriber gets "12 new matches", not twelve pings.
"""

from __future__ import annotations

from deallens.core.logging import get_logger
from deallens.modules.alerts import service as alerts_service
from deallens.modules.identity.models import AlertLatency
from deallens.worker.app import celery_app
from deallens.worker.runtime import run_async, task_session

_log = get_logger("worker.tasks.alerts")


@celery_app.task(
    name="deallens.alerts.dispatch_pending",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def dispatch_pending() -> dict[str, object]:
    """Send every QUEUED notification through its channel."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            sent = await alerts_service.dispatch_pending(db)
            return {"dispatched": sent}

    return run_async(_run())


@celery_app.task(
    name="deallens.alerts.run_digests",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def run_digests(latency: str) -> dict[str, object]:
    """Roll queued matches for boxes at `latency` (hourly|daily) into one digest per user."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            digests = await alerts_service.build_digests(db, latency=AlertLatency(latency))
            return {"latency": latency, "digests": digests}

    return run_async(_run())
