"""Entity resolution — resolve a source record to the single canonical property (FR-006,
§14.3). This is the "remove duplicates" guarantee: two feeds (or a relisting months later, or
MLS + ATTOM) describing the same physical parcel must land on *one* `properties` row, or the
same house shows up twice in the Top-25 and comps double-count it.

Resolution ladder, strongest signal first:
  1. **APN + county FIPS** — a parcel number is unique within a county; a hit here is a
     certainty (confidence 1.0). Backed by the partial unique index `uq_properties_fips_apn`.
  2. **Normalized address key** — `address_key` (USPS-standardized line + ZIP, §normalize)
     exact match. Handles "123 N Main St" vs "123 NORTH MAIN STREET" (confidence 0.97).
  3. **Geospatial + attribute similarity** — within 15 m and matching sqft/beds/year, for
     records with a geocode but no APN and a slightly different address string
     (new-construction address churn, unit vs no-unit). Confidence is the computed score;
     below `GEO_MATCH_THRESHOLD` it is treated as a *different* property.
  4. **No match → create.** A new canonical property, confidence reflecting how much
     identifying data we had (APN > full address > sparse). A sparse/low-confidence create is
     DQ-flagged (§14.4) but *still served* — resolution never blocks ingestion (§14.3: a
     listing is served standalone even when unresolved).

`apply_property_fields` is the one place source attributes are written onto a `properties`
row, shared by create and the writer's merge — so "how a delta maps to columns" lives once.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.modules.ingestion.models import Property
from deallens.modules.ingestion.normalize import address_key
from deallens.modules.ingestion.schemas import PropertyDelta

GEO_MATCH_THRESHOLD = 0.70
PROXIMITY_METERS = 15.0

_APN_CONFIDENCE = Decimal("1.000")
_ADDRESS_CONFIDENCE = Decimal("0.970")
_NEW_WITH_APN = Decimal("0.900")
_NEW_WITH_ADDRESS = Decimal("0.800")
_NEW_SPARSE = Decimal("0.500")


class ResolutionMethod(StrEnum):
    APN = "apn"
    ADDRESS_KEY = "address_key"
    GEO_ATTRIBUTE = "geo_attribute"
    CREATED = "created"


@dataclass(slots=True)
class ResolutionResult:
    property: Property
    confidence: Decimal
    is_new: bool
    method: ResolutionMethod
    low_confidence: bool  # true → the writer records a DQ flag (unresolved/ambiguous)


def _ewkt(delta: PropertyDelta) -> str | None:
    if delta.latitude is None or delta.longitude is None:
        return None
    return f"SRID=4326;POINT({delta.longitude} {delta.latitude})"


def apply_property_fields(prop: Property, delta: PropertyDelta) -> None:
    """Write a delta's physical attributes onto a `properties` row. Only overwrites a column
    when the delta carries a non-null value, so a sparse feed never blanks out data a richer
    source already filled (e.g. MLS has no APN; a later ATTOM record backfills it without
    erasing the MLS-provided beds/baths). `attrs` is merged, not replaced, for the same
    reason (long-tail fields from different sources accumulate).
    """
    if delta.apn and not prop.apn:
        prop.apn = delta.apn
    if delta.fips and not prop.fips:
        prop.fips = delta.fips
    # Address + geocode always reflect the most recent normalized value.
    prop.address_norm = delta.address.model_dump()
    ewkt = _ewkt(delta)
    if ewkt is not None:
        prop.geom = ewkt  # type: ignore[assignment]  # geoalchemy2 accepts EWKT on assignment

    for field in (
        "property_type", "beds", "baths", "sqft", "lot_sqft",
        "year_built", "stories", "garage_spaces", "pool", "hoa_monthly", "zoning",
    ):
        value = getattr(delta, field)
        if value is not None:
            setattr(prop, field, value)

    if delta.attrs:
        merged = dict(prop.attrs or {})
        merged.update(delta.attrs)
        merged["address_key"] = address_key(delta.address)
        prop.attrs = merged
    else:
        attrs = dict(prop.attrs or {})
        attrs["address_key"] = address_key(delta.address)
        prop.attrs = attrs


def _attribute_similarity(prop: Property, delta: PropertyDelta) -> float:
    """0..1 agreement on the structural attributes that identify a building. Only attributes
    present on *both* sides count; absent-on-either is neither evidence for nor against, so a
    sparse record isn't penalized into a false non-match.
    """
    scores: list[float] = []
    if prop.sqft and delta.sqft:
        ratio = min(prop.sqft, delta.sqft) / max(prop.sqft, delta.sqft)
        scores.append(1.0 if ratio >= 0.9 else max(0.0, (ratio - 0.5) / 0.4))
    if prop.beds is not None and delta.beds is not None:
        scores.append(1.0 if prop.beds == delta.beds else 0.0)
    if prop.year_built and delta.year_built:
        scores.append(1.0 if abs(prop.year_built - delta.year_built) <= 2 else 0.0)
    if not scores:
        return 0.5  # geocode agreement alone; neutral on attributes
    return sum(scores) / len(scores)


async def _match_by_apn(db: AsyncSession, delta: PropertyDelta) -> Property | None:
    if not (delta.apn and delta.fips):
        return None
    result = await db.execute(
        select(Property).where(Property.apn == delta.apn, Property.fips == delta.fips)
    )
    return result.scalars().first()


async def _match_by_address(db: AsyncSession, delta: PropertyDelta) -> Property | None:
    key = address_key(delta.address)
    # Candidate set narrowed by ZIP (cheap JSONB extract); exact match decided on the
    # computed key in Python so unit/punctuation differences don't miss. A functional index
    # on (address_norm->>'zip') / attrs->>'address_key' is the scale follow-up (§11.6).
    result = await db.execute(
        select(Property).where(Property.address_norm["zip"].astext == delta.address.zip)
    )
    for candidate in result.scalars().all():
        stored = candidate.attrs.get("address_key") if candidate.attrs else None
        if stored == key:
            return candidate
        # Fall back to recomputing from the stored normalized address for rows written before
        # address_key was stamped into attrs.
        if stored is None and candidate.address_norm:
            line = str(candidate.address_norm.get("line1", ""))
            line2 = candidate.address_norm.get("line2")
            recomputed = f"{line}{line2 or ''}".upper()
            recomputed = "".join(ch for ch in recomputed if ch.isalnum() or ch == "#")
            if f"{recomputed}|{candidate.address_norm.get('zip', '')}" == key:
                return candidate
    return None


async def _match_by_geo(
    db: AsyncSession, delta: PropertyDelta
) -> tuple[Property, float] | None:
    point = _ewkt(delta)
    if point is None:
        return None
    geog = cast(Property.geom, Geography)
    target = cast(point, Geography)
    distance = func.ST_Distance(geog, target)
    result = await db.execute(
        select(Property, distance.label("meters"))
        .where(Property.geom.isnot(None), func.ST_DWithin(geog, target, PROXIMITY_METERS))
        .order_by(distance)
        .limit(5)
    )
    best: tuple[Property, float] | None = None
    for candidate, meters in result.all():
        proximity = max(0.0, 1.0 - (float(meters) / PROXIMITY_METERS))
        score = 0.5 * proximity + 0.5 * _attribute_similarity(candidate, delta)
        if best is None or score > best[1]:
            best = (candidate, score)
    return best


async def resolve_property(db: AsyncSession, delta: PropertyDelta) -> ResolutionResult:
    """Find-or-create the canonical property for a delta, deduplicating across sources and
    relistings. On create, the new row is fully populated and flushed (so it has an id the
    writer can attach a listing to); on match, only the caller/writer merges fresh fields via
    `apply_property_fields`.
    """
    apn_match = await _match_by_apn(db, delta)
    if apn_match is not None:
        return ResolutionResult(apn_match, _APN_CONFIDENCE, False, ResolutionMethod.APN, False)

    address_match = await _match_by_address(db, delta)
    if address_match is not None:
        return ResolutionResult(
            address_match, _ADDRESS_CONFIDENCE, False, ResolutionMethod.ADDRESS_KEY, False
        )

    geo = await _match_by_geo(db, delta)
    if geo is not None and geo[1] >= GEO_MATCH_THRESHOLD:
        return ResolutionResult(
            geo[0], Decimal(str(round(geo[1], 3))), False, ResolutionMethod.GEO_ATTRIBUTE, False
        )

    # No match → create. Confidence + low-confidence flag reflect how identifiable it was.
    has_full_address = bool(delta.address.line1 and delta.address.zip)
    if delta.apn and delta.fips:
        confidence, low = _NEW_WITH_APN, False
    elif has_full_address:
        confidence, low = _NEW_WITH_ADDRESS, False
    else:
        confidence, low = _NEW_SPARSE, True

    prop = Property(resolution_confidence=confidence)
    apply_property_fields(prop, delta)
    db.add(prop)
    await db.flush()
    return ResolutionResult(prop, confidence, True, ResolutionMethod.CREATED, low)
