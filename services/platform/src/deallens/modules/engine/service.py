"""Comps & valuation engine — public interface (§9.3/§19: other modules import engine only
via this file). Turns a subject property into ARV, as-is, and market-rent estimates plus
market appreciation (03 §26.1, §28), persisting the full derivation (comp set + members +
versioned valuation) so any number renders its own math (§26.9).

Reads shared property/listing/market data; writes shared `comp_sets`/`comp_members`/
`valuations` (system-generated, `org_id IS NULL`). Those shared writes require the owner-role
session (RLS forbids the app role from inserting a NULL-org row — see migration 0004 / core/db
`system_session`), so `value_property` / `recompute_if_stale` are worker-path entrypoints.
User pin/exclude (`apply_comp_edits`, FR-015) instead writes an *org-owned* comp set on the
actor's RLS session and returns a transient estimate — it never mutates the shared valuation,
so one tenant's overrides can't move another's number.

Pure math lives in `comps` (selection/adjustment), `valuation` (aggregation/confidence/
intervals), `appreciation` (market-rate selection), and `recompute` (staleness policy); this
file is the orchestration + persistence seam over them and `query` (the PostGIS reads).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.errors import NotFoundError
from deallens.modules.engine import appreciation as appr
from deallens.modules.engine import comps, query, recompute, valuation
from deallens.modules.engine.models import (
    CompCreatedBy,
    CompMember,
    CompSet,
    CompSetKind,
    Valuation,
    ValuationKind,
)
from deallens.modules.engine.schemas import (
    AdjustmentConfig,
    AppreciationOut,
    CompEditRequest,
    CompSelectionParams,
    PropertyValuationOut,
    SelectedComp,
    ValuationOut,
)
from deallens.modules.ingestion.models import Property, PropertyType
from deallens.modules.markets.models import GeoLevel, Market, MarketStat

ENGINE_MODEL_VERSION = "v1"
_MILES_TO_M = Decimal("1609.344")
_ZERO = Decimal("0")
_ONE = Decimal("1")
# Market $/sqft metric for the model-prior fallback (03 §26.1) and appreciation lives in §28.3.
METRIC_MEDIAN_SALE_PPSF = "median_sale_ppsf"
METRIC_MEDIAN_RENT_PPSF = "median_rent_ppsf"

# Rental adjustments are monthly-rent scale, not sale scale — a bed is worth ~$75/mo, not
# $8k. Sale-scale line items would swamp a rent estimate, so rentals get their own config
# (03 §26.1). Condition and lot barely move rent, so they zero out; $/sqft still derives from
# the comp set's own median rent/sqft (config `per_sqft=None`).
_RENTAL_ADJUSTMENTS = AdjustmentConfig(
    bed_value=Decimal("75"),
    bath_value=Decimal("100"),
    garage_space_value=Decimal("40"),
    per_lot_sqft=Decimal("0"),
    pool_value=Decimal("60"),
    condition_grade_value=Decimal("0"),
    sqft_damping=Decimal("0.6"),
)


@dataclass(frozen=True, slots=True)
class _Subject:
    """Everything the estimators need about the subject, resolved once per run."""

    id: UUID
    lon: float | None
    lat: float | None
    property_type: PropertyType | None
    market_id: UUID | None
    market: Market | None
    is_rural: bool
    condition_grade: int | None
    features: comps.SubjectFeatures


# --- Subject & config resolution --------------------------------------------------------


def _representative_grade(condition: Any) -> int | None:
    """A single 1–5 condition grade from the per-system vision grades (03 §27.3): the rounded
    mean of whichever systems have a grade. None when the property has no vision profile — most
    historical comps, and the honest answer for a listing with no usable photos (§27.1).
    """
    if condition is None:
        return None
    grades: list[int] = [
        int(g)
        for g in (
            condition.kitchen_grade,
            condition.bath_grade,
            condition.flooring_grade,
            condition.exterior_grade,
            condition.roof_grade,
        )
        if g is not None
    ]
    if not grades:
        return None
    return round(sum(grades) / len(grades))


async def _load_subject(db: AsyncSession, property_id: UUID) -> _Subject:
    """Resolve the subject property, its market, and its condition grade in one place. Raises
    `NotFoundError` (→ 404) for an unknown id."""
    row = (
        await db.execute(
            select(
                Property,
                func.ST_X(Property.geom),
                func.ST_Y(Property.geom),
            ).where(Property.id == property_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Property not found")
    prop: Property = row[0]
    lon, lat = row[1], row[2]

    market = await db.get(Market, prop.market_id) if prop.market_id else None
    is_rural = bool((market.config.get("comps", {}) if market else {}).get("rural", False))

    from deallens.modules.vision.models import PropertyCondition

    condition = await db.get(PropertyCondition, property_id)
    grade = _representative_grade(condition)

    features = comps.SubjectFeatures(
        sqft=prop.sqft,
        beds=prop.beds,
        baths=prop.baths,
        garage_spaces=prop.garage_spaces,
        lot_sqft=prop.lot_sqft,
        pool=prop.pool,
        year_built=prop.year_built,
        condition_grade=grade,
    )
    return _Subject(
        id=prop.id,
        lon=lon,
        lat=lat,
        property_type=prop.property_type,
        market_id=prop.market_id,
        market=market,
        is_rural=is_rural,
        condition_grade=grade,
        features=features,
    )


def _resolve_params(market: Market | None) -> CompSelectionParams:
    """Comp-selection params = v1 defaults with any per-market override merged over the top
    (03 §26.1 — market-versioned config, not a hardcode). A malformed override falls back to
    defaults rather than failing a valuation."""
    override = (market.config.get("comps", {}) if market else {}) or {}
    knobs = {k: v for k, v in override.items() if k in CompSelectionParams.model_fields}
    try:
        return CompSelectionParams(**knobs)
    except ValueError:
        return CompSelectionParams()


def _resolve_adjustments(kind: CompSetKind, market: Market | None) -> AdjustmentConfig:
    base = _RENTAL_ADJUSTMENTS if kind is CompSetKind.RENTAL else AdjustmentConfig()
    override = (market.config.get("adjustments", {}) if market else {}) or {}
    if not override:
        return base
    merged = base.model_dump()
    merged.update({k: v for k, v in override.items() if k in AdjustmentConfig.model_fields})
    try:
        return AdjustmentConfig(**merged)
    except ValueError:
        return base


def _effective_radius_m(
    params: CompSelectionParams, stage: comps.FallbackStage, rural: bool
) -> float:
    """Search radius in metres for a fallback stage. Base radius is urban/rural per market;
    `WIDEN_RADIUS` (and beyond) stretches toward `max_radius_mi` (03 §26.1)."""
    base_mi = params.radius_mi_rural if rural else params.radius_mi
    radius_mi = params.max_radius_mi if stage >= comps.FallbackStage.WIDEN_RADIUS else base_mi
    return float(radius_mi * _MILES_TO_M)


def _recency_cutoff(params: CompSelectionParams, stage: comps.FallbackStage, now: date) -> date:
    """The earliest acceptable sale/list date for a stage. `WIDEN_RECENCY` (and beyond) uses
    `max_recency_months` (03 §26.1)."""
    months = (
        params.max_recency_months
        if stage >= comps.FallbackStage.WIDEN_RECENCY
        else params.recency_months
    )
    return now - timedelta(days=int(months * 30.4))


# --- Candidate fetch + laddered selection ----------------------------------------------


def _row_to_candidate(row: Any) -> comps.CompCandidate:
    dist = row.distance_m
    return comps.CompCandidate(
        property_id=row.property_id,
        listing_id=row.listing_id,
        observed_value=row.observed_value or _ZERO,
        observed_date=row.observed_date,
        distance_m=Decimal(str(dist)) if dist is not None else None,
        sqft=row.sqft,
        beds=row.beds,
        baths=row.baths,
        garage_spaces=row.garage_spaces,
        lot_sqft=row.lot_sqft,
        pool=row.pool,
        year_built=row.year_built,
    )


async def _fetch_candidates(
    db: AsyncSession,
    subject: _Subject,
    kind: CompSetKind,
    params: CompSelectionParams,
    stage: comps.FallbackStage,
    now: date,
) -> tuple[list[comps.CompCandidate], float]:
    """Run the candidate query for one stage; returns the candidates and the radius used (for
    the params snapshot). Returns an empty list if the subject has no geocode — a property we
    can't place can't be comped by distance (honest gap, not a silent nationwide search)."""
    if subject.lon is None or subject.lat is None:
        return [], 0.0
    radius_m = _effective_radius_m(params, stage, subject.is_rural)
    stmt = query.comp_candidates_select(
        subject_id=subject.id,
        lon=subject.lon,
        lat=subject.lat,
        property_type=subject.property_type,
        subject_sqft=subject.features.sqft,
        subject_beds=subject.features.beds,
        kind=kind,
        same_property_type=params.same_property_type,
        sqft_tolerance=float(params.sqft_tolerance),
        beds_tolerance=params.beds_tolerance,
        radius_m=radius_m,
        recency_cutoff=_recency_cutoff(params, stage, now),
    )
    rows = (await db.execute(stmt)).all()
    return [_row_to_candidate(r) for r in rows], radius_m


async def _select_with_ladder(
    db: AsyncSession,
    subject: _Subject,
    kind: CompSetKind,
    params: CompSelectionParams,
    config: AdjustmentConfig,
    *,
    target_condition_grade: int | None,
    now: date,
) -> tuple[list[comps.ScoredComp], comps.FallbackStage, float]:
    """Walk the fallback ladder (03 §26.1): try base filters, and widen recency → radius until
    the pool clears `min_comps` or the rungs run out. Returns the best scored set found, the
    stage it came from (drives the confidence penalty), and the radius used."""
    stage = comps.FallbackStage.BASE
    best: list[comps.ScoredComp] = []
    best_stage = stage
    best_radius = 0.0
    while True:
        candidates, radius_m = await _fetch_candidates(db, subject, kind, params, stage, now)
        scored = comps.score_and_adjust(
            subject.features,
            candidates,
            params,
            config,
            radius_m=Decimal(str(radius_m)),
            now=now,
            target_condition_grade=target_condition_grade,
        )
        if len(scored) >= len(best):  # keep the widest useful set seen so far
            best, best_stage, best_radius = scored, stage, radius_m
        nxt = comps.next_fallback_stage(stage, len(scored), params)
        if nxt is None or nxt is comps.FallbackStage.MODEL_PRIOR:
            if nxt is comps.FallbackStage.MODEL_PRIOR and len(best) < params.min_comps:
                best_stage = comps.FallbackStage.MODEL_PRIOR
            break
        stage = nxt
    return best, best_stage, best_radius


# --- Persistence ------------------------------------------------------------------------


def _persist_comp_set(
    db: AsyncSession,
    subject: _Subject,
    kind: CompSetKind,
    scored: Sequence[comps.ScoredComp],
    params: CompSelectionParams,
    config: AdjustmentConfig,
    stage: comps.FallbackStage,
    radius_m: float,
    *,
    created_by: CompCreatedBy,
    org_id: UUID | None,
    user_id: UUID | None,
) -> CompSet:
    """Insert a comp set + its members (03 §11.3). `params` records the exact net cast (filters
    + resolved radius + stage + adjustment config) so the set is reproducible and auditable."""
    comp_set = CompSet(
        subject_property_id=subject.id,
        kind=kind,
        created_by=created_by,
        org_id=org_id,
        user_id=user_id,
        params={
            "selection": params.model_dump(mode="json"),
            "adjustments": config.model_dump(mode="json"),
            "resolved": {"radius_m": radius_m, "stage": stage.name, "engine": ENGINE_MODEL_VERSION},
        },
    )
    db.add(comp_set)
    for sc in scored:
        db.add(
            CompMember(
                comp_set=comp_set,
                comp_property_id=sc.candidate.property_id,
                comp_listing_id=sc.candidate.listing_id,
                org_id=org_id,
                distance_m=sc.candidate.distance_m,
                similarity=sc.similarity,
                adjustments={"lines": [a.model_dump(mode="json") for a in sc.adjustments]},
                included=True,
            )
        )
    return comp_set


def _selected_comps(scored: Sequence[comps.ScoredComp]) -> list[SelectedComp]:
    return [
        SelectedComp(
            property_id=sc.candidate.property_id,  # type: ignore[arg-type]
            listing_id=sc.candidate.listing_id,  # type: ignore[arg-type]
            distance_m=sc.candidate.distance_m,
            similarity=sc.similarity,
            observed_value=sc.candidate.observed_value,
            observed_date=sc.candidate.observed_date,
            adjusted_value=sc.adjusted_value,
            adjustments=sc.adjustments,
        )
        for sc in scored
    ]


async def _market_ppsf(db: AsyncSession, subject: _Subject, metric: str) -> Decimal | None:
    """The latest market median $/sqft (sale or rent) for the model-prior fallback (03 §26.1),
    read at the coarsest available (metro) level — a prior only needs to be roughly right."""
    if subject.market_id is None:
        return None
    row = await db.execute(
        select(MarketStat.value)
        .where(MarketStat.market_id == subject.market_id, MarketStat.metric == metric)
        .order_by(MarketStat.period.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


# --- Estimation -------------------------------------------------------------------------


def _scale_estimate(est: valuation.Estimate, factor: Decimal, method: str) -> valuation.Estimate:
    """Scale a value estimate by a constant (the list-to-effective rent factor). Quantiles and
    the mean are homogeneous, so scaling the aggregate == scaling every comp first — cheaper and
    exact (03 §26.1)."""

    def s(v: Decimal | None) -> Decimal | None:
        return None if v is None else (v * factor).quantize(Decimal("0.01"))

    return valuation.Estimate(
        point=s(est.point), p10=s(est.p10), p50=s(est.p50), p90=s(est.p90),
        confidence=est.confidence, method=method, comp_count=est.comp_count,
    )


async def _estimate(
    db: AsyncSession,
    subject: _Subject,
    kind: CompSetKind,
    valuation_kind: ValuationKind,
    *,
    target_condition_grade: int | None,
    now: date,
    persist: bool,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
) -> tuple[ValuationOut, CompSet | None]:
    """The full single-estimator pipeline: ladder-select comps → aggregate → (rent) apply
    list-to-effective → model-prior fallback if thin → optionally persist comp set + valuation.
    Returns the API shape and the persisted comp set (None if not persisted / model-prior)."""
    params = _resolve_params(subject.market)
    config = _resolve_adjustments(kind, subject.market)
    scored, stage, radius_m = await _select_with_ladder(
        db, subject, kind, params, config, target_condition_grade=target_condition_grade, now=now
    )

    est = valuation.estimate_from_comps(scored, target_comps=params.target_comps, stage=stage)
    if kind is CompSetKind.RENTAL and est.point is not None:
        factor = _ONE + subject_list_to_effective_adj(subject)
        est = _scale_estimate(est, factor, est.method)

    comp_set: CompSet | None = None
    used_scored = scored
    if est.point is None or (stage is comps.FallbackStage.MODEL_PRIOR and est.comp_count == 0):
        metric = METRIC_MEDIAN_RENT_PPSF if kind is CompSetKind.RENTAL else METRIC_MEDIAN_SALE_PPSF
        ppsf = await _market_ppsf(db, subject, metric)
        est = valuation.model_prior_estimate(subject.features.sqft, ppsf)
        used_scored = []
    elif persist and scored:
        comp_set = _persist_comp_set(
            db, subject, kind, scored, params, config, stage, radius_m,
            created_by=CompCreatedBy.SYSTEM, org_id=org_id, user_id=user_id,
        )
        # Flush so the client-side `uuid7` default populates `comp_set.id` before the valuation
        # captures it — otherwise the FK persists NULL and the set isn't linked to the number.
        await db.flush()

    if persist:
        db.add(
            Valuation(
                property_id=subject.id,
                kind=valuation_kind,
                point=est.point,
                low=est.p10,
                high=est.p90,
                confidence=est.confidence,
                method=est.method,
                comp_set_id=comp_set.id if comp_set else None,
                model_version=ENGINE_MODEL_VERSION,
            )
        )
        await db.flush()

    out = ValuationOut(
        kind=valuation_kind.value,
        point=est.point,
        low=est.p10,
        high=est.p90,
        p50=est.p50,
        confidence=est.confidence,
        method=est.method,
        model_version=ENGINE_MODEL_VERSION,
        comp_set_id=comp_set.id if comp_set else None,
        comp_count=est.comp_count,
        comps=_selected_comps(used_scored),
    )
    return out, comp_set


def subject_list_to_effective_adj(subject: _Subject) -> Decimal:
    """The market's list-to-effective rent adjustment (03 §26.1), from market config or the v1
    default (−3%). Signed fraction; clamped to a sane [−0.10, 0] so a bad config can't invert
    the correction (asking rent is never *below* effective)."""
    default = Decimal("-0.03")
    raw = (subject.market.config.get("rent", {}) if subject.market else {}).get("list_to_effective")
    try:
        adj = Decimal(str(raw)) if raw is not None else default
    except (ValueError, ArithmeticError):
        adj = default
    if adj > _ZERO:
        return _ZERO
    return max(adj, Decimal("-0.10"))


# --- Appreciation -----------------------------------------------------------------------


def _property_geographies(
    prop_addr: dict[str, Any], market: Market | None, fips: str | None
) -> list[tuple[GeoLevel, str]]:
    """The (level, geo_id) pairs a property belongs to, for market-stat lookup (03 §28.2)."""
    pairs: list[tuple[GeoLevel, str]] = []
    if market is not None:
        pairs.append((GeoLevel.METRO, market.cbsa_code))
    if fips:
        pairs.append((GeoLevel.COUNTY, fips))
    city = prop_addr.get("city")
    if city:
        pairs.append((GeoLevel.CITY, city))
    zip_code = prop_addr.get("zip")
    if zip_code:
        pairs.append((GeoLevel.ZIP, zip_code))
    return pairs


async def estimate_appreciation(db: AsyncSession, *, property_id: UUID) -> AppreciationOut:
    """Market appreciation for a property's finest available geography (03 §28.3/§28.4). Reads
    the appreciation `market_stats` rows for every geography the property sits in and lets
    `appreciation.select_appreciation` pick the finest, most-recent, most-preferred one."""
    prop = await db.get(Property, property_id)
    if prop is None:
        raise NotFoundError("Property not found")
    market = await db.get(Market, prop.market_id) if prop.market_id else None
    pairs = _property_geographies(prop.address_norm or {}, market, prop.fips)
    if market is None or not pairs:
        return AppreciationOut()

    rows = await db.execute(
        select(
            MarketStat.geo_level, MarketStat.geo_id, MarketStat.metric,
            MarketStat.value, MarketStat.period, MarketStat.source,
        ).where(
            MarketStat.market_id == market.id,
            MarketStat.metric.in_([appr.METRIC_YOY, appr.METRIC_3YR]),
            tuple_(MarketStat.geo_level, MarketStat.geo_id).in_(pairs),
        )
    )
    readings = [
        appr.AppreciationReading(
            geo_level=r.geo_level, geo_id=r.geo_id, metric=r.metric,
            value=r.value, period=r.period, source=r.source,
        )
        for r in rows.all()
        if r.value is not None
    ]
    a = appr.select_appreciation(readings)
    return AppreciationOut(
        annual_pct=a.annual_pct,
        period_years=a.period_years,
        geo_level=a.geo_level.value if a.geo_level else None,
        geo_id=a.geo_id,
        source=a.source,
        confidence=a.confidence,
        as_of=a.as_of,
    )


# --- Public entrypoints -----------------------------------------------------------------


async def value_property(
    db: AsyncSession, *, property_id: UUID, now: datetime | None = None
) -> PropertyValuationOut:
    """Compute + persist the full comps-engine bundle for a property (03 §26.1, §28): ARV and
    as-is from sold comps, market rent from rental comps, plus appreciation. Appends versioned
    `valuations` rows (like scores) so history is preserved and the property page reads the
    latest. Runs on the owner-role session (shared writes) — a worker/scheduler entrypoint.
    """
    today = (now or datetime.now(UTC)).date()
    subject = await _load_subject(db, property_id)

    arv_target = _resolve_adjustments(CompSetKind.SALE, subject.market).arv_target_grade
    arv, _ = await _estimate(
        db, subject, CompSetKind.SALE, ValuationKind.ARV,
        target_condition_grade=arv_target, now=today, persist=True,
    )
    as_is, _ = await _estimate(
        db, subject, CompSetKind.SALE, ValuationKind.AS_IS,
        target_condition_grade=subject.condition_grade, now=today, persist=True,
    )
    rent, _ = await _estimate(
        db, subject, CompSetKind.RENTAL, ValuationKind.RENT_LTR,
        target_condition_grade=None, now=today, persist=True,
    )
    appreciation = await estimate_appreciation(db, property_id=property_id)
    return PropertyValuationOut(
        property_id=property_id, arv=arv, as_is=as_is, rent_ltr=rent, appreciation=appreciation
    )


async def get_property_valuation(
    db: AsyncSession, *, property_id: UUID
) -> PropertyValuationOut:
    """Read the latest persisted valuation of each kind for a property — the property-page read
    (no recompute). Returns an empty bundle (nulls) for a property never valued yet."""
    if await db.get(Property, property_id) is None:
        raise NotFoundError("Property not found")
    out = PropertyValuationOut(property_id=property_id)
    for vkind, field in (
        (ValuationKind.ARV, "arv"),
        (ValuationKind.AS_IS, "as_is"),
        (ValuationKind.RENT_LTR, "rent_ltr"),
    ):
        row = await db.execute(
            select(Valuation)
            .where(Valuation.property_id == property_id, Valuation.kind == vkind)
            .order_by(Valuation.computed_at.desc())
            .limit(1)
        )
        v = row.scalar_one_or_none()
        if v is not None:
            setattr(out, field, _valuation_to_out(v))
    out.appreciation = await estimate_appreciation(db, property_id=property_id)
    return out


def _valuation_to_out(v: Valuation) -> ValuationOut:
    return ValuationOut(
        kind=v.kind.value,
        point=v.point,
        low=v.low,
        high=v.high,
        confidence=v.confidence,
        method=v.method,
        model_version=v.model_version,
        comp_set_id=v.comp_set_id,
        computed_at=v.computed_at,
    )


async def recompute_if_stale(
    db: AsyncSession, *, property_id: UUID, now: datetime | None = None
) -> PropertyValuationOut | None:
    """The continuous-improvement entrypoint (03 §9.5): recompute a property's valuation iff the
    staleness policy fires — no prior valuation, a newer comparable sale near it, an aged
    estimate, or an engine-version bump. Returns the fresh bundle when it recomputed, else None.
    Idempotent by construction: with nothing new, it no-ops. Runs on the owner-role session.
    """
    clock = now or datetime.now(UTC)
    subject = await _load_subject(db, property_id)

    latest = (
        await db.execute(
            select(Valuation.computed_at, Valuation.model_version)
            .where(Valuation.property_id == property_id, Valuation.kind == ValuationKind.ARV)
            .order_by(Valuation.computed_at.desc())
            .limit(1)
        )
    ).first()

    newest_event: datetime | None = None
    if subject.lon is not None and subject.lat is not None:
        params = _resolve_params(subject.market)
        radius_m = _effective_radius_m(params, comps.FallbackStage.BASE, subject.is_rural)
        newest_event = (
            await db.execute(
                query.newest_comp_event_select(
                    lon=subject.lon, lat=subject.lat, radius_m=radius_m, kind=CompSetKind.SALE
                )
            )
        ).scalar_one_or_none()

    decision = recompute.needs_recompute(
        latest_computed_at=latest[0] if latest else None,
        latest_model_version=latest[1] if latest else None,
        current_model_version=ENGINE_MODEL_VERSION,
        newest_comp_event_at=newest_event,
        now=clock,
    )
    if not decision:
        return None
    return await value_property(db, property_id=property_id, now=clock)


# --- User pin/exclude (FR-015) ----------------------------------------------------------


async def apply_comp_edits(
    db: AsyncSession,
    *,
    property_id: UUID,
    valuation_kind: ValuationKind,
    edits: CompEditRequest,
    org_id: UUID,
    user_id: UUID,
    now: datetime | None = None,
) -> ValuationOut:
    """Recompute an estimate under a user's pin/exclude overrides (FR-015), persisting an
    *org-owned* comp set (never touching the shared valuation). Pins pull in the user's chosen
    comps even if outside the automatic net; excludes drop comps with a reason. Runs on the
    actor's RLS session — the org-owned set is scoped to the tenant by Postgres.
    """
    today = (now or datetime.now(UTC)).date()
    subject = await _load_subject(db, property_id)
    kind = CompSetKind.RENTAL if valuation_kind is ValuationKind.RENT_LTR else CompSetKind.SALE
    params = _resolve_params(subject.market)
    config = _resolve_adjustments(kind, subject.market)
    if kind is CompSetKind.RENTAL:
        target_grade = None
    elif valuation_kind is ValuationKind.ARV:
        target_grade = config.arv_target_grade
    else:
        target_grade = subject.condition_grade

    scored, stage, radius_m = await _select_with_ladder(
        db, subject, kind, params, config, target_condition_grade=target_grade, now=today
    )
    excluded = set(edits.exclude_property_ids)
    kept = [sc for sc in scored if sc.candidate.property_id not in excluded]

    pinned = await _fetch_pins(db, subject, kind, edits.include_property_ids, kept)
    if pinned:
        pinned_scored = comps.score_and_adjust(
            subject.features, pinned, params, config,
            radius_m=Decimal(str(radius_m or 1.0)), now=today, target_condition_grade=target_grade,
        )
        kept = _dedupe_by_property(kept + pinned_scored)

    est = valuation.estimate_from_comps(kept, target_comps=params.target_comps, stage=stage)
    if kind is CompSetKind.RENTAL and est.point is not None:
        est = _scale_estimate(est, _ONE + subject_list_to_effective_adj(subject), est.method)

    comp_set = _persist_user_comp_set(
        db, subject, kind, kept, excluded, params, config, stage, radius_m,
        org_id=org_id, user_id=user_id, exclude_reason=edits.exclude_reason,
    )
    await db.flush()
    return ValuationOut(
        kind=valuation_kind.value,
        point=est.point,
        low=est.p10,
        high=est.p90,
        p50=est.p50,
        confidence=est.confidence,
        method=f"{est.method} (user-edited)",
        model_version=ENGINE_MODEL_VERSION,
        comp_set_id=comp_set.id,
        comp_count=est.comp_count,
        comps=_selected_comps(kept),
    )


async def _fetch_pins(
    db: AsyncSession,
    subject: _Subject,
    kind: CompSetKind,
    include_ids: list[UUID],
    already: Sequence[comps.ScoredComp],
) -> list[comps.CompCandidate]:
    """Fetch pinned comps not already in the working set (03 §26.1 / FR-015)."""
    have = {sc.candidate.property_id for sc in already}
    want = [pid for pid in include_ids if pid not in have]
    if not want or subject.lon is None or subject.lat is None:
        return []
    rows = await db.execute(
        query.comps_by_ids_select(
            lon=subject.lon, lat=subject.lat, property_ids=list(want), kind=kind
        )
    )
    return [_row_to_candidate(r) for r in rows.all()]


def _dedupe_by_property(scored: list[comps.ScoredComp]) -> list[comps.ScoredComp]:
    seen: set[Any] = set()
    out: list[comps.ScoredComp] = []
    for sc in sorted(scored, key=lambda s: -s.similarity):
        if sc.candidate.property_id in seen:
            continue
        seen.add(sc.candidate.property_id)
        out.append(sc)
    return out


def _persist_user_comp_set(
    db: AsyncSession,
    subject: _Subject,
    kind: CompSetKind,
    kept: Sequence[comps.ScoredComp],
    excluded: set[UUID],
    params: CompSelectionParams,
    config: AdjustmentConfig,
    stage: comps.FallbackStage,
    radius_m: float,
    *,
    org_id: UUID,
    user_id: UUID,
    exclude_reason: str | None,
) -> CompSet:
    comp_set = _persist_comp_set(
        db, subject, kind, kept, params, config, stage, radius_m,
        created_by=CompCreatedBy.USER, org_id=org_id, user_id=user_id,
    )
    # Record the excludes as included=False members so the override is fully reproducible.
    for pid in excluded:
        db.add(
            CompMember(
                comp_set=comp_set,
                comp_property_id=pid,
                org_id=org_id,
                included=False,
                excluded_reason=exclude_reason or "user excluded",
            )
        )
    return comp_set


# Financial-analysis orchestration lives in `analysis.py` (§26.2–§26.8) but is re-exported here
# so `engine.service` stays the module's single public interface (§19). Imported at the bottom to
# avoid a cycle: `analysis` imports this module for the valuation reads it builds inputs from.
from deallens.modules.engine.analysis import (  # noqa: E402
    analyze_property,
    analyze_strategies,
    build_financial_inputs,
    get_analysis,
    resolve_assumptions,
)

__all__ = [
    "ENGINE_MODEL_VERSION",
    "analyze_property",
    "analyze_strategies",
    "apply_comp_edits",
    "build_financial_inputs",
    "estimate_appreciation",
    "get_analysis",
    "get_property_valuation",
    "recompute_if_stale",
    "resolve_assumptions",
    "value_property",
]
