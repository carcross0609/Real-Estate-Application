"""Ingestion module public interface (§9.3/§19 — other modules import ingestion *only* via
this file). It owns the write path into `properties`/`listings`/`listing_events` and the
read path for listing/price history; nothing else in the platform writes those tables.

Deliberately does **not** import `enrichment` — the dependency runs the other way
(`enrichment.service` calls `resolve_or_create_property` here to find the parcel an assessor
row belongs to), so the boundary stays acyclic. The `pipeline` module (orchestration, not a
peer module) is what fans a mixed batch of deltas to `ingest_listing` here vs.
`enrichment.service`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.errors import NotFoundError
from deallens.modules.admin.models import DqSeverity
from deallens.modules.ingestion import quality
from deallens.modules.ingestion.events import EventEmitter, PropertyResolved
from deallens.modules.ingestion.models import (
    DataSource,
    DataSourceTier,
    Listing,
    ListingEvent,
    ListingEventType,
    ListingStatus,
    Property,
)
from deallens.modules.ingestion.resolution import (
    ResolutionResult,
    apply_property_fields,
    resolve_property,
)
from deallens.modules.ingestion.schemas import ListingDelta, PropertyDelta
from deallens.modules.ingestion.writer import UpsertResult, upsert_listing

_PRICE_EVENTS = (ListingEventType.LISTED, ListingEventType.PRICE_CHANGE)


@dataclass(slots=True)
class IngestSummary:
    listings_upserted: int = 0
    listings_new: int = 0
    stale_skipped: int = 0
    properties_created: int = 0
    dq_flags: int = 0
    change_events: int = 0
    errors: list[str] = field(default_factory=list)


# --- Data-source registry --------------------------------------------------------------


async def register_data_source(
    db: AsyncSession,
    *,
    source_key: str,
    name: str,
    tier: DataSourceTier,
    license_policy: dict[str, Any] | None = None,
) -> DataSource:
    """Idempotently ensure a `data_sources` row exists for `source_key` (upserting the
    license policy). Called at market onboarding (S43) before any poll runs — the
    `data_sources.id` it returns is stamped onto every raw record and ingestion run.
    """
    existing = await db.execute(select(DataSource).where(DataSource.source_key == source_key))
    source = existing.scalars().first()
    if source is None:
        source = DataSource(
            source_key=source_key, name=name, tier=tier, license_policy=license_policy or {}
        )
        db.add(source)
    else:
        source.name = name
        source.tier = tier
        if license_policy is not None:
            source.license_policy = license_policy
    await db.flush()
    return source


async def get_data_source(db: AsyncSession, source_key: str) -> DataSource:
    result = await db.execute(select(DataSource).where(DataSource.source_key == source_key))
    source = result.scalars().first()
    if source is None:
        raise NotFoundError(f"No data source registered for {source_key!r}")
    return source


# --- Property resolution (the enrichment entrypoint) -----------------------------------


async def assign_market(db: AsyncSession, prop: Property) -> None:
    """Set `market_id` by spatial containment — the point-in-market join that decides which
    market's stats/config apply. No-op when the property has no geocode (stays unassigned;
    a DQ flag already records the missing geocode). Reads the *persisted* geometry column
    (not the instance value, which may still be an un-flushed EWKT string) so the PostGIS
    predicate always sees a geometry."""
    if prop.geom is None or prop.market_id is not None:
        return
    from deallens.modules.markets.models import Market

    # The property's persisted geometry as a scalar subquery — keeps `properties` out of the
    # FROM clause (no cartesian join with `markets`) while still using the stored geom, not
    # the possibly-un-flushed instance value.
    point = select(Property.geom).where(Property.id == prop.id).scalar_subquery()
    result = await db.execute(
        select(Market.id).where(func.ST_Contains(Market.boundary, point)).limit(1)
    )
    market_id = result.scalars().first()
    if market_id is not None:
        prop.market_id = market_id


async def resolve_or_create_property(
    db: AsyncSession,
    delta: PropertyDelta,
    *,
    emitter: EventEmitter | None = None,
) -> ResolutionResult:
    """Public resolution entrypoint used by both the listing path and `enrichment.service`.
    Deduplicates to one canonical property (FR-006), merges fresh fields, assigns market, and
    emits `PropertyResolved`. A low-confidence create is DQ-flagged but still returned."""
    result = await resolve_property(db, delta)
    if not result.is_new:
        apply_property_fields(result.property, delta)
    await assign_market(db, result.property)
    await db.flush()

    if result.low_confidence:
        await quality.record_dq_flags(
            db,
            subject_type="property",
            subject_id=result.property.id,
            market_id=result.property.market_id,
            violations=[
                quality.DqViolation(
                    "low_resolution_confidence",
                    DqSeverity.SERVE_WITH_FLAG,
                    {"confidence": str(result.confidence), "method": result.method.value},
                )
            ],
        )
    if emitter is not None:
        from deallens.modules.ingestion.normalize import address_key

        await emitter.emit(PropertyResolved(
            property_id=result.property.id,
            address_key=address_key(delta.address),
            confidence=float(result.confidence),
            is_new=result.is_new,
        ))
    return result


# --- Listing ingest --------------------------------------------------------------------


async def ingest_listing(
    db: AsyncSession,
    delta: ListingDelta,
    *,
    source: DataSource,
    raw_record: Any = None,
    emitter: EventEmitter | None = None,
    reference_year: int | None = None,
) -> UpsertResult:
    """Full single-listing ingest: DQ checks → resolve canonical property → versioned upsert
    + change log → persist DQ flags. This is the unit the pipeline calls per normalized
    listing and that tests exercise directly.
    """
    violations = quality.run_dq_checks(delta, reference_year=reference_year)
    resolution = await resolve_or_create_property(db, delta.property, emitter=emitter)

    result = await upsert_listing(
        db, delta, source=source, resolution=resolution, raw_record=raw_record, emitter=emitter
    )

    # Suppress-severity DQ (e.g. a sold listing with no close price) shouldn't feed comps;
    # the flag is attached to the listing so serialization/comp-selection can honor it.
    if violations and not result.stale:
        await quality.record_dq_flags(
            db,
            subject_type="listing",
            subject_id=result.listing.id,
            market_id=resolution.property.market_id,
            violations=violations,
        )
    return result


async def ingest_property(
    db: AsyncSession,
    delta: PropertyDelta,
    *,
    emitter: EventEmitter | None = None,
) -> ResolutionResult:
    """Ingest a bare property (an ATTOM assessor record with no listing) — resolve-or-create
    and merge structural fields. Enables Analyze-Any-Address (FR-017): a parcel exists in our
    canonical set even when it has never been listed."""
    return await resolve_or_create_property(db, delta, emitter=emitter)


# --- History reads (FR-003) ------------------------------------------------------------


async def get_listing_history(db: AsyncSession, property_id: UUID) -> list[Listing]:
    """Every listing ever recorded for a property, newest first — the relist chain (§14.3).
    DOM-across-relistings and full price history are computed over this set, so the investor
    sees *true* time-on-market, not just the current listing's clock."""
    result = await db.execute(
        select(Listing)
        .where(Listing.property_id == property_id)
        .order_by(Listing.list_date.desc().nullslast(), Listing.created_at.desc())
    )
    return list(result.scalars().all())


async def get_price_history(db: AsyncSession, property_id: UUID) -> list[ListingEvent]:
    """Chronological price points across the property's whole relist chain (FR-003) — the
    series the price-history chart draws. Spans listings so a property relisted at a new price
    shows the full arc, not a reset."""
    result = await db.execute(
        select(ListingEvent)
        .join(Listing, Listing.id == ListingEvent.listing_id)
        .where(Listing.property_id == property_id, ListingEvent.event_type.in_(_PRICE_EVENTS))
        .order_by(ListingEvent.observed_at)
    )
    return list(result.scalars().all())


async def get_active_listings(
    db: AsyncSession, *, market_id: UUID | None = None, limit: int = 100
) -> list[Listing]:
    stmt = select(Listing).where(Listing.status == ListingStatus.ACTIVE)
    if market_id is not None:
        stmt = stmt.join(Property, Property.id == Listing.property_id).where(
            Property.market_id == market_id
        )
    stmt = stmt.order_by(Listing.list_date.desc().nullslast()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
