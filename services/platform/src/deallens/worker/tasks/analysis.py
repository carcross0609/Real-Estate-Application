"""Analysis-refresh tasks — the staleness backstop for the valuation engine (03 §26.1).

Most re-analysis is event-driven: a comp closing near a property fires `on_listing_upserted`
for the sold listing and, through the fan-out, re-drives affected valuations. `refresh_stale_
valuations` is the daily sweep that catches the *silent* staleness the event stream can't —
an estimate that simply aged past the refresh window with no new event (STALE_AGE), or an
engine-version bump that should re-value everything under it. It enqueues one `recompute_
property` per candidate so a slow property can't hold up the batch; each recompute no-ops when
nothing was actually stale, so the sweep is cheap on a quiet day.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from deallens.core.logging import get_logger
from deallens.modules.ingestion.models import Listing, ListingStatus, Property
from deallens.worker import orchestration
from deallens.worker.app import celery_app
from deallens.worker.runtime import run_async, task_session

_log = get_logger("worker.tasks.analysis")

# Cap the daily sweep so one tick enqueues a bounded batch; the next day's tick picks up the
# rest. Actively-listed properties are prioritized — a stale number on a live deal matters most.
_SWEEP_LIMIT = 5000


@celery_app.task(name="deallens.analysis.refresh_stale_valuations")
def refresh_stale_valuations() -> dict[str, object]:
    """Beat sweep: enqueue a staleness recompute for each actively-listed property."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            rows = await db.execute(
                select(Property.id)
                .join(Listing, Listing.property_id == Property.id)
                .where(Listing.status == ListingStatus.ACTIVE)
                .distinct()
                .limit(_SWEEP_LIMIT)
            )
            ids = [r[0] for r in rows.all()]
            for pid in ids:
                celery_app.send_task(
                    "deallens.analysis.recompute_property", kwargs={"property_id": str(pid)}
                )
        return {"enqueued": len(ids)}

    return run_async(_run())


@celery_app.task(
    name="deallens.analysis.recompute_property",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def recompute_property(property_id: str) -> dict[str, object]:
    """Recompute one property iff the staleness policy fires; re-score + re-match when it does."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            res = await orchestration.refresh_if_stale(db, property_id=UUID(property_id))
            if res is None:
                return {"property_id": property_id, "recomputed": False}
            return {
                "property_id": property_id,
                "recomputed": True,
                "score": str(res.new_score) if res.new_score is not None else None,
                "boxes_matched": res.boxes_matched,
            }

    return run_async(_run())
