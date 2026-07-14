"""Versioned upsert + change detection (§9.4 step 1, §9.5, §14.4) — where a resolved delta
becomes canonical rows and a change log.

Two correctness properties the whole pipeline leans on live here:

- **Versioned upsert (§9.5).** Every write is conditional on the source `ModificationTimestamp`
  (`Listing.source_ts`): a delta whose `source_ts` is not newer than what's stored is a
  no-op. Feeds deliver out-of-order and duplicate events; this makes stale/duplicate delivery
  correct-by-construction instead of last-write-wins-corrupts. The DB unique constraint
  `(source_id, source_listing_key)` is the backstop against a concurrent double-insert; a
  per-property Redis lock (§9.5) serializes analysis downstream (the worker's job, not here).

- **Field-level change log (§14.4).** On update, a typed diff produces immutable
  `listing_events` rows — this *is* the price/status history the product charts (FR-003), and
  the trigger set for re-scoring (a price cut re-scores; a remarks edit re-parses signals).

`diff_listing`/`diff_photos` are pure so the change semantics are unit-tested without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.modules.ingestion.events import (
    EventEmitter,
    ListingChanged,
    ListingUpserted,
    PhotosChanged,
)
from deallens.modules.ingestion.models import (
    DataSource,
    Listing,
    ListingEvent,
    ListingEventType,
    ListingPhoto,
    ListingStatus,
    Property,
    RawRecord,
)
from deallens.modules.ingestion.resolution import ResolutionResult, apply_property_fields
from deallens.modules.ingestion.schemas import ListingDelta, PhotoDelta

# Statuses that mean "not currently for sale"; a transition *out* of one of these back to
# active is a distinct BACK_ON_MARKET signal (investors watch for it — a relist attempt).
_INACTIVE_STATUSES = frozenset(
    {ListingStatus.WITHDRAWN, ListingStatus.EXPIRED, ListingStatus.PENDING, ListingStatus.SOLD}
)


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    event_type: ListingEventType
    old: dict[str, Any] | None
    new: dict[str, Any] | None


@dataclass(slots=True)
class UpsertResult:
    listing: Listing
    is_new: bool
    stale: bool
    changes: list[ChangeRecord] = field(default_factory=list)
    photos_added: int = 0
    photos_removed: int = 0
    relisted_from: Any = None  # prior listing id when this is a detected relist


def _jsonable(value: Any) -> Any:
    """Coerce a changed value into a JSONB-safe scalar for the event `old`/`new` payload."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, ListingStatus):
        return value.value
    return value


def diff_listing(old: Listing, delta: ListingDelta) -> list[ChangeRecord]:
    """Typed field-level diff → the change events this update records. Pure; `old` is read but
    not mutated. Photos are diffed separately (`diff_photos`) — they're a set, not a scalar.
    """
    changes: list[ChangeRecord] = []

    if delta.list_price is not None and old.list_price != delta.list_price:
        changes.append(ChangeRecord(
            ListingEventType.PRICE_CHANGE,
            {"list_price": _jsonable(old.list_price)},
            {"list_price": _jsonable(delta.list_price)},
        ))

    if old.status != delta.status:
        changes.append(ChangeRecord(
            ListingEventType.STATUS_CHANGE,
            {"status": _jsonable(old.status)},
            {"status": _jsonable(delta.status)},
        ))
        if old.status in _INACTIVE_STATUSES and delta.status in (
            ListingStatus.ACTIVE, ListingStatus.COMING_SOON
        ):
            changes.append(ChangeRecord(
                ListingEventType.BACK_ON_MARKET,
                {"status": _jsonable(old.status)},
                {"status": _jsonable(delta.status)},
            ))

    if delta.remarks is not None and (old.remarks or "") != delta.remarks:
        changes.append(ChangeRecord(ListingEventType.REMARKS_CHANGE, None, None))

    return changes


def diff_photos(
    existing_hashes: set[bytes], delta_photos: list[PhotoDelta]
) -> tuple[list[PhotoDelta], set[bytes]]:
    """Content-hash set diff (§14.4): which photos are new, which vanished. Feeds resend the
    full media set each update, so absence from the delta means removed."""
    incoming = {p.content_hash: p for p in delta_photos}
    added = [p for h, p in incoming.items() if h not in existing_hashes]
    removed = existing_hashes - set(incoming)
    return added, removed


def _apply_listing_fields(
    listing: Listing, delta: ListingDelta, source: DataSource, raw_record: RawRecord | None
) -> None:
    listing.status = delta.status
    if delta.list_price is not None:
        listing.list_price = delta.list_price
    if delta.close_price is not None:
        listing.close_price = delta.close_price
    if delta.list_date is not None:
        listing.list_date = delta.list_date
    if delta.close_date is not None:
        listing.close_date = delta.close_date
    if delta.dom_current is not None:
        listing.dom_current = delta.dom_current
    if delta.remarks is not None:
        listing.remarks = delta.remarks
    listing.attribution = delta.attribution.model_dump(exclude_none=True)
    listing.photo_count = len(delta.photos)
    listing.source_ts = delta.source_ts
    if raw_record is not None:
        listing.raw_record_id = raw_record.id


async def _existing_listing(db: AsyncSession, source_id: Any, key: str) -> Listing | None:
    result = await db.execute(
        select(Listing).where(
            Listing.source_id == source_id, Listing.source_listing_key == key
        )
    )
    return result.scalars().first()


async def _apply_photo_diff(
    db: AsyncSession, listing: Listing, delta: ListingDelta
) -> tuple[int, int]:
    existing = await db.execute(
        select(ListingPhoto).where(ListingPhoto.listing_id == listing.id)
    )
    existing_rows = list(existing.scalars().all())
    existing_by_hash = {row.content_hash: row for row in existing_rows}
    added, removed = diff_photos(set(existing_by_hash), delta.photos)

    for photo in added:
        db.add(ListingPhoto(
            listing_id=listing.id,
            position=photo.position,
            source_url=photo.source_url,
            content_hash=photo.content_hash,
            phash=photo.phash,
            width=photo.width,
            height=photo.height,
        ))
    for hash_ in removed:
        await db.delete(existing_by_hash[hash_])
    if added or removed:
        await db.flush()
    return len(added), len(removed)


async def _detect_relist(db: AsyncSession, property_id: Any, new_listing_id: Any) -> Listing | None:
    """A new listing on a property that already had a *different* listing is a relist (§14.3
    step 3). Returns the most recent prior listing so the caller chains DOM/price-history
    across it via a RELISTED_LINK event."""
    result = await db.execute(
        select(Listing)
        .where(Listing.property_id == property_id, Listing.id != new_listing_id)
        .order_by(Listing.list_date.desc().nullslast(), Listing.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def upsert_listing(
    db: AsyncSession,
    delta: ListingDelta,
    *,
    source: DataSource,
    resolution: ResolutionResult,
    raw_record: RawRecord | None = None,
    emitter: EventEmitter | None = None,
) -> UpsertResult:
    """Apply one resolved `ListingDelta`. Idempotent and order-independent: a stale delta
    no-ops; a duplicate re-applies the same rows without creating history noise. Emits domain
    events for the downstream pipeline (§9.4). `resolution.property` is the canonical property
    the listing attaches to (already flushed with an id).
    """
    prop: Property = resolution.property
    apply_property_fields(prop, delta.property)  # merge fresh structural fields (§resolution)

    existing = await _existing_listing(db, source.id, delta.source_listing_key)

    # --- Versioned upsert guard (§9.5) -------------------------------------------------
    if (
        existing is not None
        and delta.source_ts is not None
        and existing.source_ts is not None
        and existing.source_ts >= delta.source_ts
    ):
        return UpsertResult(listing=existing, is_new=False, stale=True)

    if existing is None:
        listing = Listing(
            property_id=prop.id,
            source_id=source.id,
            source_listing_key=delta.source_listing_key,
            status=delta.status,
        )
        _apply_listing_fields(listing, delta, source, raw_record)
        db.add(listing)
        await db.flush()

        db.add(ListingEvent(
            listing_id=listing.id,
            event_type=ListingEventType.LISTED,
            new={"status": delta.status.value, "list_price": _jsonable(delta.list_price)},
            source_ts=delta.source_ts,
        ))
        photos_added, _ = await _apply_photo_diff(db, listing, delta)

        relist = await _detect_relist(db, prop.id, listing.id)
        if relist is not None:
            db.add(ListingEvent(
                listing_id=listing.id,
                event_type=ListingEventType.RELISTED_LINK,
                new={
                    "prior_listing_id": str(relist.id),
                    "prior_source_listing_key": relist.source_listing_key,
                },
                source_ts=delta.source_ts,
            ))
        await db.flush()

        result = UpsertResult(
            listing=listing, is_new=True, stale=False, photos_added=photos_added,
            relisted_from=relist.id if relist is not None else None,
        )
        await _emit(emitter, result, source, prop, is_new=True)
        return result

    # --- Update path: diff, apply, log -------------------------------------------------
    changes = diff_listing(existing, delta)
    _apply_listing_fields(existing, delta, source, raw_record)
    for change in changes:
        db.add(ListingEvent(
            listing_id=existing.id,
            event_type=change.event_type,
            old=change.old,
            new=change.new,
            source_ts=delta.source_ts,
        ))
    await db.flush()
    photos_added, photos_removed = await _apply_photo_diff(db, existing, delta)

    result = UpsertResult(
        listing=existing, is_new=False, stale=False, changes=changes,
        photos_added=photos_added, photos_removed=photos_removed,
    )
    await _emit(emitter, result, source, prop, is_new=False)
    return result


async def _emit(
    emitter: EventEmitter | None,
    result: UpsertResult,
    source: DataSource,
    prop: Property,
    *,
    is_new: bool,
) -> None:
    if emitter is None:
        return
    await emitter.emit(ListingUpserted(
        listing_id=result.listing.id,
        property_id=prop.id,
        source_key=source.source_key,
        is_new=is_new,
        source_ts=result.listing.source_ts,
    ))
    if result.changes:
        await emitter.emit(ListingChanged(
            listing_id=result.listing.id,
            property_id=prop.id,
            event_types=[c.event_type.value for c in result.changes],
            source_ts=result.listing.source_ts,
        ))
    if result.photos_added or result.photos_removed:
        await emitter.emit(PhotosChanged(
            listing_id=result.listing.id,
            added=result.photos_added,
            removed=result.photos_removed,
        ))
