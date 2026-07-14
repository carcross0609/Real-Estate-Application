"""Enrichment module public interface (§9.3/§19). Attaches public-records and geo context to
the canonical property that ingestion resolved (§9.4 step 2, FR-005): assessor tax roll,
deed/ownership (absentee + hold-time signals), and spatial layers (flood/schools/crime).

Consumes ingestion's output via `ingestion.service.resolve_or_create_property` — the
dependency points *into* ingestion, never the reverse, so the module graph stays acyclic
(§9.3). Enrichment is driven by ingestion events (`ListingUpserted`/`PropertyResolved`): the
worker reacts to them by calling the Tier-B/D adapters and the `attach_*` functions here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import WKBElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.modules.enrichment.models import (
    GeoLayer,
    GeoLayerKind,
    OwnershipRecord,
    TaxRecord,
)
from deallens.modules.ingestion.schemas import OwnershipDelta, TaxDelta
from deallens.modules.ingestion.service import resolve_or_create_property
from deallens.modules.markets.models import GeoLevel


def _months_between(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _derive_absentee(delta: OwnershipDelta) -> tuple[bool | None, bool | None]:
    """Prefer the source's explicit flags; otherwise infer from owner-occupancy. Absentee
    ownership is a load-bearing motivated-seller signal (03 §25.3), so we keep it explicit
    rather than guessing when the source is silent (both None → unknown, not "not absentee").
    """
    if delta.absentee is not None or delta.owner_occupied is not None:
        absentee = delta.absentee
        owner_occupied = delta.owner_occupied
        if absentee is None and owner_occupied is not None:
            absentee = not owner_occupied
        if owner_occupied is None and absentee is not None:
            owner_occupied = not absentee
        return owner_occupied, absentee
    return None, None


async def attach_ownership(
    db: AsyncSession,
    delta: OwnershipDelta,
    *,
    source_id: UUID | None = None,
    raw_record_id: UUID | None = None,
    as_of: date | None = None,
) -> OwnershipRecord:
    """Resolve the parcel and upsert its current ownership row. Hold time is derived from the
    last sale date against `as_of` (the DB clock) so `ownership_length_months` — the long-hold
    signal — is always current, not frozen at ingest. One current row per property (deeds
    accumulate in `raw_records`; the roll-up here is "who owns it now")."""
    as_of = as_of or datetime.now(UTC).date()
    resolution = await resolve_or_create_property(db, delta.property)
    prop_id = resolution.property.id

    owner_occupied, absentee = _derive_absentee(delta)
    hold_months = delta.ownership_length_months
    if hold_months is None and delta.last_sale_date is not None:
        hold_months = _months_between(delta.last_sale_date, as_of)

    existing = await db.execute(
        select(OwnershipRecord).where(OwnershipRecord.property_id == prop_id)
    )
    record = existing.scalars().first()
    if record is None:
        record = OwnershipRecord(property_id=prop_id)
        db.add(record)

    record.owner_name = delta.owner_name
    record.owner_mailing_address = (
        delta.owner_mailing_address.model_dump() if delta.owner_mailing_address else {}
    )
    record.owner_occupied = owner_occupied
    record.absentee = absentee
    record.ownership_length_months = hold_months
    record.last_sale_price = delta.last_sale_price
    record.last_sale_date = delta.last_sale_date
    record.deed_date = delta.deed_date
    record.deed_type = delta.deed_type
    record.source_id = source_id
    record.raw_record_id = raw_record_id
    await db.flush()
    return record


async def attach_tax(
    db: AsyncSession,
    delta: TaxDelta,
    *,
    source_id: UUID | None = None,
) -> TaxRecord:
    """Resolve the parcel and upsert its tax row for the delta's `tax_year` (unique per
    property-year). The engine reads the latest year for the pro-forma tax line and
    `reassessed_on_sale` to avoid underwriting on the seller's grandfathered basis (03 §26.4).
    """
    resolution = await resolve_or_create_property(db, delta.property)
    prop_id = resolution.property.id

    existing = await db.execute(
        select(TaxRecord).where(
            TaxRecord.property_id == prop_id, TaxRecord.tax_year == delta.tax_year
        )
    )
    record = existing.scalars().first()
    if record is None:
        record = TaxRecord(property_id=prop_id, tax_year=delta.tax_year)
        db.add(record)

    record.assessed_value = delta.assessed_value
    record.land_value = delta.land_value
    record.improvement_value = delta.improvement_value
    record.annual_tax_amount = delta.annual_tax_amount
    record.tax_rate = delta.tax_rate
    record.exemptions = delta.exemptions
    if delta.reassessed_on_sale is not None:
        record.reassessed_on_sale = delta.reassessed_on_sale
    record.source_id = source_id
    await db.flush()
    return record


async def upsert_geo_layer(
    db: AsyncSession,
    *,
    kind: GeoLayerKind,
    attributes: dict[str, Any],
    market_id: UUID | None = None,
    geo_level: GeoLevel | None = None,
    geo_id: str | None = None,
    geom_ewkt: str | None = None,
    source_id: UUID | None = None,
    effective_date: date | None = None,
    method_version: str = "v1",
) -> GeoLayer:
    """Insert-or-replace a spatial/geo-keyed enrichment layer (FEMA flood polygon, county
    crime index, school-zone rating). Layers are area-keyed and joined to a property at read
    time (`layers_for_property`) rather than copied per property, so one FEMA update refreshes
    every property in the zone at once."""
    stmt = select(GeoLayer).where(GeoLayer.kind == kind)
    if geo_level is not None:
        stmt = stmt.where(GeoLayer.geo_level == geo_level)
    if geo_id is not None:
        stmt = stmt.where(GeoLayer.geo_id == geo_id)
    if market_id is not None:
        stmt = stmt.where(GeoLayer.market_id == market_id)
    existing = (await db.execute(stmt.limit(1))).scalars().first()

    layer = existing or GeoLayer(kind=kind)
    layer.market_id = market_id
    layer.geo_level = geo_level
    layer.geo_id = geo_id
    if geom_ewkt is not None:
        layer.geom = geom_ewkt  # type: ignore[assignment]  # geoalchemy2 accepts EWKT
    layer.attributes = attributes
    layer.source_id = source_id
    layer.effective_date = effective_date
    layer.method_version = method_version
    if existing is None:
        db.add(layer)
    await db.flush()
    return layer


async def layers_for_property(
    db: AsyncSession, *, geom: WKBElement, kinds: list[GeoLayerKind] | None = None
) -> list[GeoLayer]:
    """The geo layers covering a property's location — spatial containment for polygon layers.
    This is the read scoring/engine use to pull flood zone, school rating, crime index for a
    specific parcel (03 §28)."""
    stmt = select(GeoLayer).where(
        GeoLayer.geom.isnot(None), func.ST_Contains(GeoLayer.geom, geom)
    )
    if kinds:
        stmt = stmt.where(GeoLayer.kind.in_(kinds))
    result = await db.execute(stmt)
    return list(result.scalars().all())
