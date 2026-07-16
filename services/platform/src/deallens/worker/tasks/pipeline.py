"""Event-consumer tasks — the downstream half of the §9.4 fan-out. `CeleryEmitter` enqueues
these from the ingestion writer's domain events; here each event drives the reaction the
platform's docstrings promise (enrich→analyze→score→match).

Idempotent + at-least-once safe: every handler re-reads current state and every write is
versioned, so a duplicate delivery converges (§9.5). `autoretry_for` gives transient DB/broker
errors a bounded backoff; a persistently bad event dead-letters after `max_retries` rather than
looping, and the daily refresh sweep re-drives anything a dropped event would have missed.
"""

from __future__ import annotations

from uuid import UUID

from deallens.core.logging import get_logger
from deallens.modules.ingestion.models import Listing
from deallens.worker import orchestration
from deallens.worker.app import celery_app
from deallens.worker.runtime import run_async, task_session

_log = get_logger("worker.tasks.pipeline")

_RETRY = {"autoretry_for": (Exception,), "retry_backoff": True, "max_retries": 3}


@celery_app.task(name="deallens.pipeline.on_listing_upserted", **_RETRY)
def on_listing_upserted(property_id: str, listing_id: str, is_new: bool) -> dict[str, object]:
    """A listing was created or a newer version applied → run the full opportunity pass. On an
    *update* (not new) we also compare the score before/after so watchers hear about a material
    move (a first analysis has no prior score to compare)."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            res = await orchestration.analyze_and_score(
                db, property_id=UUID(property_id),
                notify_watchers_on_score_change=not is_new,
            )
            return {
                "property_id": property_id,
                "scored": res.scored,
                "score": str(res.new_score) if res.new_score is not None else None,
                "boxes_matched": res.boxes_matched,
                "notifications": res.notifications,
            }

    return run_async(_run())


@celery_app.task(name="deallens.pipeline.on_listing_changed", **_RETRY)
def on_listing_changed(
    property_id: str, listing_id: str, event_types: list[str]
) -> dict[str, object]:
    """Field-level changes were logged → notify watchers of the ones that warrant an interrupt
    (price/status). Re-scoring is already handled by the accompanying `on_listing_upserted`."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            notified = await orchestration.notify_listing_change(
                db, property_id=UUID(property_id), event_types=event_types
            )
            return {"property_id": property_id, "event_types": event_types,
                    "watchers_notified": notified}

    return run_async(_run())


@celery_app.task(name="deallens.pipeline.on_photos_changed", **_RETRY)
def on_photos_changed(listing_id: str, added: int, removed: int) -> dict[str, object]:
    """The photo set changed → the condition inputs may have moved, so re-run the opportunity
    pass for the owning property (the vision re-pass feeds the C-group factors, §25.3)."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            listing = await db.get(Listing, UUID(listing_id))
            if listing is None:
                return {"listing_id": listing_id, "skipped": "listing_not_found"}
            res = await orchestration.analyze_and_score(
                db, property_id=listing.property_id, notify_watchers_on_score_change=True
            )
            return {"property_id": str(listing.property_id), "scored": res.scored}

    return run_async(_run())


@celery_app.task(name="deallens.pipeline.on_property_resolved", **_RETRY)
def on_property_resolved(property_id: str) -> dict[str, object]:
    """A brand-new canonical parcel was created. Enrichment backfill (geocode/assessor/geo
    layers) hangs off this hook; for now it records the new parcel — the parcel's first listing
    drives analysis via `on_listing_upserted`, so this stays a light marker, not a duplicate
    analysis pass."""
    _log.info("property_resolved", property_id=property_id)
    return {"property_id": property_id, "acknowledged": True}


@celery_app.task(name="deallens.pipeline.analyze_property", **_RETRY)
def analyze_property(property_id: str) -> dict[str, object]:
    """Manual/ad-hoc trigger of the opportunity pass for one property (an admin re-run, a
    backfill). Same chain as the listing-upserted consumer, without the watcher diff."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            res = await orchestration.analyze_and_score(db, property_id=UUID(property_id))
            return {"property_id": property_id, "scored": res.scored,
                    "boxes_matched": res.boxes_matched}

    return run_async(_run())
