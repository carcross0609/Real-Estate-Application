"""Domain events emitted by the ingestion writer (§9.4 step 1 "→ outbox event").

Ingestion's contract with the rest of the pipeline is these events, not direct calls: the
writer emits `ListingUpserted`/`ListingChanged`/`PropertyResolved`/`PhotosChanged`, and
enrich → vision → engine → score → fan-out react (§9.3 — modules communicate via events, not
reach-ins). Keeping the emitter behind a Protocol means the transport is swappable: an
in-process `CollectingEmitter` for tests and synchronous runs, and a Redis/Celery or
transactional-outbox emitter in the worker (the §9.4 outbox) — the writer never changes.

Events are intentionally thin — ids + what changed, never whole rows — so a consumer always
re-reads current state (avoids acting on a stale snapshot when events arrive out of order,
§9.5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from deallens.core.logging import get_logger

_log = get_logger("ingestion.events")


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base for every ingestion event. `source_ts` (the feed ModificationTimestamp that
    produced the change) travels with the event so a debounced/late consumer can order or
    discard it (§9.5)."""

    name: str = field(init=False, default="domain_event")


@dataclass(frozen=True, slots=True)
class PropertyResolved(DomainEvent):
    property_id: UUID
    address_key: str
    confidence: float
    is_new: bool
    name: str = field(init=False, default="PropertyResolved")


@dataclass(frozen=True, slots=True)
class ListingUpserted(DomainEvent):
    """A listing was created or a newer version applied. `is_new` distinguishes a brand-new
    listing (full analysis) from a fresh version of an existing one (re-analysis)."""

    listing_id: UUID
    property_id: UUID
    source_key: str
    is_new: bool
    source_ts: datetime | None = None
    name: str = field(init=False, default="ListingUpserted")


@dataclass(frozen=True, slots=True)
class ListingChanged(DomainEvent):
    """Field-level changes were recorded as `listing_events`. `event_types` mirrors the typed
    change log so a consumer can react selectively (a price cut re-scores; a remarks edit
    re-parses seller signals)."""

    listing_id: UUID
    property_id: UUID
    event_types: Sequence[str]
    source_ts: datetime | None = None
    name: str = field(init=False, default="ListingChanged")


@dataclass(frozen=True, slots=True)
class PhotosChanged(DomainEvent):
    """The photo set changed by content-hash diff — the trigger for the vision pre-pass and
    re-analysis (§9.4 step 3)."""

    listing_id: UUID
    added: int
    removed: int
    name: str = field(init=False, default="PhotosChanged")


@runtime_checkable
class EventEmitter(Protocol):
    async def emit(self, event: DomainEvent) -> None: ...


class CollectingEmitter:
    """Accumulates events in memory. The pipeline returns its collected events so a caller
    (or test) can assert on exactly what the run produced; also the substrate a synchronous
    single-process run uses before Redis/outbox exists."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def emit(self, event: DomainEvent) -> None:
        self.events.append(event)


class LoggingEmitter:
    """Structured-log emitter — the always-on observability tap (§18). Composes with a real
    transport via `CompositeEmitter` so every emitted event is also traceable in logs."""

    async def emit(self, event: DomainEvent) -> None:
        payload = {
            k: (str(v) if isinstance(v, UUID) else v)
            for k, v in asdict(event).items()
            if k != "name"
        }
        _log.info("domain_event", event_name=event.name, **payload)


class CompositeEmitter:
    """Fan one event out to several emitters (e.g. log + enqueue). A failing emitter must not
    swallow the others; failures are logged and the fan-out continues (an alerting emitter
    being down can't block ingestion)."""

    def __init__(self, *emitters: EventEmitter) -> None:
        self._emitters = emitters

    async def emit(self, event: DomainEvent) -> None:
        for emitter in self._emitters:
            try:
                await emitter.emit(event)
            except Exception:  # noqa: BLE001 — one emitter's failure can't stop ingestion
                _log.exception("event_emitter_failed", event_name=event.name)
