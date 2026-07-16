"""`CeleryEmitter` — the transactional-outbox seam the ingestion writer was built against
(03 §9.4 step 1 "→ outbox event"; `ingestion.events` docstring: "a Redis/Celery … emitter in
the worker … the writer never changes").

`ingestion.pipeline` fans every listing change to an `EventEmitter`; in tests and synchronous
runs that's a `CollectingEmitter`, and in the worker it's this — each domain event becomes a
Celery task so the enrich→analyze→score→match chain runs off the ingestion transaction, not
inside it. Two properties this preserves:

- **Boundary:** it dispatches by task *name* (`send_task`), never importing the task
  functions, so `emitter → tasks → orchestration → services` stays a one-way graph with no
  import cycle.
- **Ordering safety (§9.5):** events carry only ids + what changed, so the consumer re-reads
  current state; a duplicate or out-of-order delivery converges (every downstream write is
  idempotent/versioned). That's why enqueue-per-event is safe even though Celery is at-least-
  once.

Dispatch is best-effort with respect to ingestion: a broker hiccup logs and continues rather
than failing the poll that already persisted the data (the daily/refresh sweeps are the
backstop that re-drives anything a dropped event would have missed).
"""

from __future__ import annotations

from celery import Celery

from deallens.core.logging import get_logger
from deallens.modules.ingestion.events import (
    DomainEvent,
    ListingChanged,
    ListingUpserted,
    PhotosChanged,
    PropertyResolved,
)

_log = get_logger("worker.emitter")

# Task names that consume each event (registered in `worker.tasks.pipeline`). Kept here as the
# single routing table so the event→task mapping is reviewable in one place.
_LISTING_UPSERTED = "deallens.pipeline.on_listing_upserted"
_LISTING_CHANGED = "deallens.pipeline.on_listing_changed"
_PHOTOS_CHANGED = "deallens.pipeline.on_photos_changed"
_PROPERTY_RESOLVED = "deallens.pipeline.on_property_resolved"


class CeleryEmitter:
    """Enqueues one task per ingestion domain event. Satisfies the `EventEmitter` Protocol
    (`async def emit`) so it drops into `run_ingestion_cycle` wherever a `CollectingEmitter`
    goes."""

    def __init__(self, app: Celery) -> None:
        self._app = app

    async def emit(self, event: DomainEvent) -> None:
        task_name, kwargs = self._route(event)
        if task_name is None:
            return
        try:
            # send_task is a fast, non-blocking broker publish; a failure here must not roll
            # back the ingestion write that produced the event.
            self._app.send_task(task_name, kwargs=kwargs)
        except Exception:  # noqa: BLE001 — a broker blip can't fail an ingestion cycle
            _log.exception("event_dispatch_failed", event_name=event.name, task=task_name)

    @staticmethod
    def _route(event: DomainEvent) -> tuple[str | None, dict[str, object]]:
        """Map an event to (task_name, json-safe kwargs). UUIDs become strings so the payload
        is JSON-serializable for the broker."""
        if isinstance(event, ListingUpserted):
            return _LISTING_UPSERTED, {
                "property_id": str(event.property_id),
                "listing_id": str(event.listing_id),
                "is_new": event.is_new,
            }
        if isinstance(event, ListingChanged):
            return _LISTING_CHANGED, {
                "property_id": str(event.property_id),
                "listing_id": str(event.listing_id),
                "event_types": list(event.event_types),
            }
        if isinstance(event, PhotosChanged):
            return _PHOTOS_CHANGED, {
                "listing_id": str(event.listing_id),
                "added": event.added,
                "removed": event.removed,
            }
        if isinstance(event, PropertyResolved):
            # Only a brand-new parcel needs the resolved hook (backfill enrichment); a
            # re-resolve of an existing property is already covered by the listing events.
            if event.is_new:
                return _PROPERTY_RESOLVED, {"property_id": str(event.property_id)}
            return None, {}
        return None, {}


__all__ = ["CeleryEmitter"]
