"""Comp-candidate read path — pure query construction, no execution (03 §26.1).

`comp_candidates_select` compiles the hard filters of a comp search — radius, recency, size
band, class, and the sale-vs-rental listing shape — into one PostGIS `Select` over
`properties ⋈ listings ⋈ data_sources`. Similarity scoring, adjustment, and ranking happen in
`comps.py` on the rows this returns; the split mirrors search (`query` filters, service scores)
so the whole compiler is unit-tested by rendering to SQL text.

Two comp shapes, one query:
- **SALE** (ARV / as-is): closed listings — `status = sold`, a close price and date within the
  recency window, from a sale-carrying tier (MLS / property-data). The money figure is the
  close price; the date is the close date.
- **RENTAL** (market rent): listings from a rental-tier feed (Tier C) with an asking price and
  list date in-window. The money figure is the *asking* rent — `valuation.apply_list_to_effective`
  discounts it to an effective rent downstream (03 §26.1).

`DISTINCT ON (property_id)` keeps the most recent qualifying event per comp property, so a
house sold twice in the window contributes one comp (its latest sale), never two. Comp
condition grades are deliberately absent: historical sold comps predate our vision pipeline, so
the candidate carries `condition_grade = None` and the condition adjustment line simply doesn't
fire — the service supplies the *subject's* grade for the as-is basis.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.sql.elements import ColumnElement

from deallens.modules.engine.models import CompSetKind
from deallens.modules.ingestion.models import (
    DataSource,
    DataSourceTier,
    Listing,
    ListingStatus,
    Property,
    PropertyType,
)

# Tiers that carry a trustworthy *sale* close price (03 §14.1). Rentals come from RENTAL tier.
_SALE_TIERS = (DataSourceTier.MLS, DataSourceTier.PROPERTY_DATA)
# How many nearest candidates to hand the scorer per stage. Generous vs. `max_comps` so
# similarity ranking has room to prefer near-twins over merely in-band comps; the DISTINCT ON
# dedup and the scorer's own `max_comps` cut do the final narrowing.
CANDIDATE_LIMIT = 100


def _subject_point(lon: float, lat: float) -> ColumnElement[Any]:
    """The subject location as a SRID-4326 geometry, from bound numeric params (never string-
    interpolated). Paired with `geography(...)` casts so distances/radii are honest metres."""
    return func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)


def comp_candidates_select(
    *,
    subject_id: object,
    lon: float,
    lat: float,
    property_type: PropertyType | None,
    subject_sqft: int | None,
    subject_beds: int | None,
    kind: CompSetKind,
    same_property_type: bool,
    sqft_tolerance: float,
    beds_tolerance: int | None,
    radius_m: float,
    recency_cutoff: date,
    limit: int = CANDIDATE_LIMIT,
) -> Select[Any]:
    """Build the candidate query for one comp search at one fallback stage. `radius_m` and
    `recency_cutoff` are resolved by the caller per stage (widening loosens both), so this stays
    a pure compiler: same inputs → same SQL. Returns the nearest `limit` distinct comp
    properties with the columns `comps.CompCandidate` needs.
    """
    point = _subject_point(lon, lat)
    distance = func.ST_Distance(func.geography(Property.geom), func.geography(point))

    preds: list[ColumnElement[bool]] = [
        Property.id != subject_id,
        Property.geom.isnot(None),
        func.ST_DWithin(func.geography(Property.geom), func.geography(point), radius_m),
    ]
    if same_property_type and property_type is not None:
        preds.append(Property.property_type == property_type)
    if subject_sqft:
        lo = int(subject_sqft * (1.0 - sqft_tolerance))
        hi = int(subject_sqft * (1.0 + sqft_tolerance))
        preds.append(Property.sqft.between(lo, hi))
    if subject_beds is not None and beds_tolerance is not None:
        preds.append(func.abs(Property.beds - subject_beds) <= beds_tolerance)

    if kind is CompSetKind.SALE:
        value_col = Listing.close_price
        date_col = Listing.close_date
        preds += [
            Listing.status == ListingStatus.SOLD,
            Listing.close_price.isnot(None),
            Listing.close_date.isnot(None),
            Listing.close_date >= recency_cutoff,
            DataSource.tier.in_(_SALE_TIERS),
        ]
    else:  # RENTAL — asking rent from a rental-tier feed
        value_col = Listing.list_price
        date_col = Listing.list_date
        preds += [
            DataSource.tier == DataSourceTier.RENTAL,
            Listing.list_price.isnot(None),
            Listing.list_date.isnot(None),
            Listing.list_date >= recency_cutoff,
        ]

    inner = (
        select(
            Property.id.label("property_id"),
            Listing.id.label("listing_id"),
            value_col.label("observed_value"),
            date_col.label("observed_date"),
            distance.label("distance_m"),
            Property.sqft.label("sqft"),
            Property.beds.label("beds"),
            Property.baths.label("baths"),
            Property.garage_spaces.label("garage_spaces"),
            Property.lot_sqft.label("lot_sqft"),
            Property.pool.label("pool"),
            Property.year_built.label("year_built"),
        )
        .select_from(Property)
        .join(Listing, Listing.property_id == Property.id)
        .join(DataSource, DataSource.id == Listing.source_id)
        .where(and_(*preds))
        # Most recent qualifying event per comp property (03 §26.1 — one comp per house).
        .distinct(Property.id)
        .order_by(Property.id, date_col.desc())
        .subquery("comp_candidate")
    )
    # Outer pass: nearest-first, so a tight `limit` keeps the closest comps, not arbitrary ones.
    return select(inner).order_by(inner.c.distance_m.asc()).limit(limit)


def comps_by_ids_select(
    *,
    lon: float,
    lat: float,
    property_ids: list[object],
    kind: CompSetKind,
) -> Select[Any]:
    """Fetch specific comp properties by id, ignoring the radius/recency/size filters — the
    read behind a user *pin* (FR-015): the user is overriding the engine's selection, so their
    chosen comp is admitted even if it sits outside the automatic net. Still requires a
    qualifying sold/rental listing (a pin needs a real transaction to anchor a value) and still
    computes distance from the subject for display. Most-recent qualifying event per property.
    """
    point = _subject_point(lon, lat)
    distance = func.ST_Distance(func.geography(Property.geom), func.geography(point))

    preds: list[ColumnElement[bool]] = [Property.id.in_(property_ids), Property.geom.isnot(None)]
    if kind is CompSetKind.SALE:
        value_col = Listing.close_price
        date_col = Listing.close_date
        preds += [
            Listing.status == ListingStatus.SOLD,
            Listing.close_price.isnot(None),
            Listing.close_date.isnot(None),
            DataSource.tier.in_(_SALE_TIERS),
        ]
    else:
        value_col = Listing.list_price
        date_col = Listing.list_date
        preds += [
            DataSource.tier == DataSourceTier.RENTAL,
            Listing.list_price.isnot(None),
            Listing.list_date.isnot(None),
        ]

    return (
        select(
            Property.id.label("property_id"),
            Listing.id.label("listing_id"),
            value_col.label("observed_value"),
            date_col.label("observed_date"),
            distance.label("distance_m"),
            Property.sqft.label("sqft"),
            Property.beds.label("beds"),
            Property.baths.label("baths"),
            Property.garage_spaces.label("garage_spaces"),
            Property.lot_sqft.label("lot_sqft"),
            Property.pool.label("pool"),
            Property.year_built.label("year_built"),
        )
        .select_from(Property)
        .join(Listing, Listing.property_id == Property.id)
        .join(DataSource, DataSource.id == Listing.source_id)
        .where(and_(*preds))
        .distinct(Property.id)
        .order_by(Property.id, date_col.desc())
    )


def newest_comp_event_select(
    *, lon: float, lat: float, radius_m: float, kind: CompSetKind
) -> Select[Any]:
    """The most recent qualifying comp *event* near the subject — the max feed timestamp of a
    sold (or rental) listing within `radius_m`. `recompute.needs_recompute` compares this to the
    last valuation's timestamp to fire a `NEW_COMP` refresh: this is the "newly available market
    data" trigger (03 §9.5) made concrete. Returns a scalar `datetime | None`.
    """
    point = _subject_point(lon, lat)
    preds: list[ColumnElement[bool]] = [
        Property.geom.isnot(None),
        func.ST_DWithin(func.geography(Property.geom), func.geography(point), radius_m),
    ]
    if kind is CompSetKind.SALE:
        preds += [Listing.status == ListingStatus.SOLD, DataSource.tier.in_(_SALE_TIERS)]
    else:
        preds += [DataSource.tier == DataSourceTier.RENTAL]
    return (
        select(func.max(Listing.source_ts))
        .select_from(Property)
        .join(Listing, Listing.property_id == Property.id)
        .join(DataSource, DataSource.id == Listing.source_id)
        .where(and_(*preds))
    )


__all__ = [
    "CANDIDATE_LIMIT",
    "comp_candidates_select",
    "comps_by_ids_select",
    "newest_comp_event_select",
]
