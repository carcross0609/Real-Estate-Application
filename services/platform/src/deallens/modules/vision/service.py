"""Vision module public interface (§9.3/§19 — other modules import vision only via this file).

Orchestrates the §27 pipeline: run the selected model provider over a listing's photos, persist
each `photo_analyses` row, deterministically aggregate them into a `property_conditions` row, and
price the rehab (§27.4). Reads/serves the property-level condition + rehab, and exposes the
Group-C scoring factors (§25.3) the investment score consumes.

Vision writes shared reference data (`photo_analyses`, `property_conditions` — no RLS, §11.1), so
the analyze path is a worker/owner entrypoint. The pure math is in `aggregate` (roll-up), `rehab`
(cost model), `factors` (scoring bridge), and `provider` (the pluggable model); this file is the
DB seam over them. Model choice is `pipeline_version` — persisted per photo so a newer model is
diffed on the eval set before promotion (§13.4), and re-running a version appends history rather
than mutating it (`photo_analyses` is versioned, §11.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.errors import NotFoundError
from deallens.modules.ingestion.models import Listing, ListingPhoto, Property
from deallens.modules.markets.models import Market
from deallens.modules.vision import aggregate, factors, rehab
from deallens.modules.vision.models import PhotoAnalysis, PropertyCondition, RoomType
from deallens.modules.vision.provider import PhotoInput, VisionProvider, get_provider
from deallens.modules.vision.schemas import (
    ConditionCoverage,
    ConditionFactors,
    PhotoPerception,
    PropertyConditionOut,
    RedFlag,
    RehabBreakdown,
    RehabCostConfig,
)

_ZERO = Decimal("0")
# aggregate system → property_conditions grade column.
_SYSTEM_COLUMNS = {
    "kitchen": "kitchen_grade",
    "bath": "bath_grade",
    "flooring": "flooring_grade",
    "exterior": "exterior_grade",
    "roof": "roof_grade",
    "landscaping": "landscaping_grade",
}


def _resolve_cost_config(market: Market | None) -> RehabCostConfig:
    """The rehab unit-cost table for a property's market (§27.4), market-overridable. A market may
    override the contingency and red-flag exposures via `markets.config["rehab_costs"]`; the
    system table (which needs local contractor calibration) is kept unless fully replaced."""
    config = rehab.default_cost_config()
    if market is None:
        return config
    override = market.config.get("rehab_costs")
    if isinstance(override, dict) and override:
        merged: dict[str, Any] = config.model_dump(mode="json")
        merged.update(override)
        return RehabCostConfig.model_validate(merged)
    return config


async def _load_photos(db: AsyncSession, listing_id: UUID) -> Sequence[ListingPhoto]:
    rows = await db.execute(
        select(ListingPhoto)
        .where(ListingPhoto.listing_id == listing_id)
        .order_by(ListingPhoto.position)
    )
    return list(rows.scalars().all())


async def _persist_photo_analysis(
    db: AsyncSession, *, photo_id: UUID, perception: PhotoPerception
) -> None:
    """Upsert a `photo_analyses` row for (photo, pipeline_version). Re-running the same version
    updates in place; a *new* version appends (the unique constraint is on the pair), preserving
    the version history the eval/audit diff reads (§27.6)."""
    existing = await db.execute(
        select(PhotoAnalysis).where(
            PhotoAnalysis.photo_id == photo_id,
            PhotoAnalysis.pipeline_version == perception.pipeline_version,
        )
    )
    row = existing.scalars().first()
    if row is None:
        row = PhotoAnalysis(photo_id=photo_id, pipeline_version=perception.pipeline_version)
        db.add(row)
    row.model_id = perception.model_id
    row.room_type = RoomType(perception.room_type) if perception.room_type else None
    row.condition_grade = perception.condition_grade
    row.findings = perception.findings.model_dump(mode="json")
    row.red_flags = [f.model_dump(mode="json") for f in perception.red_flags]
    row.quality = perception.quality.model_dump(mode="json")
    row.confidence = Decimal(str(perception.confidence))
    row.tokens_in = perception.tokens_in
    row.tokens_out = perception.tokens_out
    row.cost_usd = perception.cost_usd


def _breakdown_to_columns(bd: RehabBreakdown) -> dict[str, Decimal]:
    """The §27.4 breakdown → the `property_conditions` repair columns (pre-contingency component
    sums; the engine applies its own §26.2 contingency, so we store components, not the padded
    total — storing the padded figure would double-count contingency downstream)."""
    return {
        "cosmetic_repair_low": bd.cosmetic_low,
        "cosmetic_repair_high": bd.cosmetic_high,
        "major_repair_low": bd.major_low,
        "major_repair_high": bd.major_high,
    }


async def analyze_listing(
    db: AsyncSession,
    *,
    listing_id: UUID,
    pipeline_version: str | None = None,
    provider: VisionProvider | None = None,
    hints: dict[UUID, dict[str, Any]] | None = None,
    persist: bool = True,
) -> PropertyConditionOut:
    """Run the vision pipeline for a listing (§27): perceive each photo via the selected provider,
    persist the `photo_analyses`, aggregate into a `property_conditions` row, and price the rehab.
    `hints` supplies per-photo structured signals to the (deterministic) provider; a real model
    ignores them and reads pixels. Returns the property condition + rehab bundle.

    A listing with no photos still produces a row — an all-unknown, zero-confidence condition with
    the degraded banner (§27.5), never a blank or a defaulted "average" (§27.1)."""
    listing = await db.get(Listing, listing_id)
    if listing is None:
        raise NotFoundError("Listing not found")
    prop = await db.get(Property, listing.property_id)
    if prop is None:
        raise NotFoundError("Property not found")
    market = await db.get(Market, prop.market_id) if prop.market_id else None

    vp = provider or get_provider(pipeline_version)
    photo_hints = hints or {}
    photos = await _load_photos(db, listing_id)

    perceptions: list[PhotoPerception] = []
    for photo in photos:
        perception = await vp.analyze(
            PhotoInput(
                photo_id=photo.id,
                image_ref=photo.s3_key or photo.source_url,
                position=photo.position,
                hints=photo_hints.get(photo.id, {}),
            )
        )
        perceptions.append(perception)
        if persist:
            await _persist_photo_analysis(db, photo_id=photo.id, perception=perception)

    agg = aggregate.aggregate_condition(perceptions)
    cost_config = _resolve_cost_config(market)
    breakdown = rehab.estimate_rehab(
        agg.grades, agg.red_flags, sqft=prop.sqft,
        baths=int(prop.baths) if prop.baths is not None else None,
        confidence=agg.confidence, config=cost_config,
    )

    if persist:
        await _persist_condition(
            db, property_id=prop.id, listing_id=listing_id, aggregate=agg,
            breakdown=breakdown, pipeline_version=vp.pipeline_version,
        )
        await db.flush()

    return _to_out(
        property_id=prop.id, pipeline_version=vp.pipeline_version, aggregate=agg,
        breakdown=breakdown,
    )


async def _persist_condition(
    db: AsyncSession,
    *,
    property_id: UUID,
    listing_id: UUID,
    aggregate: aggregate.ConditionAggregate,
    breakdown: RehabBreakdown,
    pipeline_version: str,
) -> None:
    """Upsert the single current `property_conditions` row for a property (PK = property_id)."""
    existing = await db.get(PropertyCondition, property_id)
    row = existing or PropertyCondition(property_id=property_id)
    if existing is None:
        db.add(row)
    row.as_of_listing_id = listing_id
    row.pipeline_version = pipeline_version
    for system, column in _SYSTEM_COLUMNS.items():
        setattr(row, column, aggregate.grades.get(system))
    row.renovation_difficulty = aggregate.renovation_difficulty
    row.red_flags = [f.model_dump(mode="json") for f in aggregate.red_flags]
    for column, value in _breakdown_to_columns(breakdown).items():
        setattr(row, column, value)
    row.confidence = aggregate.confidence
    row.coverage = aggregate.coverage.model_dump(mode="json")


def _to_out(
    *,
    property_id: UUID,
    pipeline_version: str,
    aggregate: aggregate.ConditionAggregate,
    breakdown: RehabBreakdown,
) -> PropertyConditionOut:
    return PropertyConditionOut(
        property_id=str(property_id),
        pipeline_version=pipeline_version,
        kitchen_grade=aggregate.grades.get("kitchen"),
        bath_grade=aggregate.grades.get("bath"),
        flooring_grade=aggregate.grades.get("flooring"),
        exterior_grade=aggregate.grades.get("exterior"),
        roof_grade=aggregate.grades.get("roof"),
        landscaping_grade=aggregate.grades.get("landscaping"),
        renovation_difficulty=aggregate.renovation_difficulty,
        red_flags=aggregate.red_flags,
        coverage=aggregate.coverage,
        confidence=aggregate.confidence,
        rehab=breakdown,
    )


async def get_property_condition(
    db: AsyncSession, *, property_id: UUID
) -> PropertyConditionOut | None:
    """The persisted property condition + a recomputed rehab breakdown (§27.3). The rehab line
    items are recomputed deterministically from the stored grades so "show the math" renders
    without re-storing the whole breakdown. None when the property has never been analyzed."""
    row = await db.get(PropertyCondition, property_id)
    if row is None:
        return None
    prop = await db.get(Property, property_id)
    market = await db.get(Market, prop.market_id) if prop and prop.market_id else None

    grades = {system: getattr(row, column) for system, column in _SYSTEM_COLUMNS.items()}
    red_flags = [RedFlag.model_validate(f) for f in row.red_flags]
    breakdown = rehab.estimate_rehab(
        grades, red_flags, sqft=prop.sqft if prop else None,
        baths=int(prop.baths) if prop and prop.baths is not None else None,
        confidence=row.confidence, config=_resolve_cost_config(market),
    )
    return PropertyConditionOut(
        property_id=str(property_id),
        pipeline_version=row.pipeline_version,
        kitchen_grade=row.kitchen_grade, bath_grade=row.bath_grade,
        flooring_grade=row.flooring_grade, exterior_grade=row.exterior_grade,
        roof_grade=row.roof_grade, landscaping_grade=row.landscaping_grade,
        renovation_difficulty=row.renovation_difficulty, red_flags=red_flags,
        coverage=ConditionCoverage.model_validate(row.coverage) if row.coverage else (
            ConditionCoverage()
        ),
        confidence=row.confidence, rehab=breakdown,
    )


async def condition_scoring_factors(
    db: AsyncSession,
    *,
    property_id: UUID,
    arv: Decimal | None,
    list_price: Decimal | None,
) -> ConditionFactors | None:
    """The Group-C factors for the investment score (§25.3), computed from the stored condition +
    rehab against the comps engine's ARV and the list price the caller supplies. None when the
    property has no condition analysis (scoring then treats condition as thin-data, not zero)."""
    condition = await get_property_condition(db, property_id=property_id)
    if condition is None:
        return None
    rehab_mid = condition.rehab.total_mid if condition.rehab else None
    return factors.condition_factors(
        rehab_mid=rehab_mid, arv=arv, list_price=list_price,
        renovation_difficulty=condition.renovation_difficulty,
        red_flags=condition.red_flags, confidence=condition.confidence,
    )


__all__ = [
    "analyze_listing",
    "condition_scoring_factors",
    "get_property_condition",
]
