"""Comp selection & adjustment — pure, Decimal, no I/O (03 §26.1).

Two jobs, both deterministic and unit-testable without a database:

- **Similarity.** Score how comparable a candidate is to the subject on a 0–1 scale — a
  weighted blend of size, bed/bath, distance, recency, and vintage closeness. This weight
  drives both *which* comps are kept (top-N by similarity) and *how much* each pulls the
  estimate (03 §26.1 "weight by similarity"). A comp that clears the hard filters but sits at
  the edge of every band should count for less than a near-twin next door, and it does.

- **Adjustment grid.** Move each comp's observed price toward the subject line by line — the
  $/sqft delta, bed/bath, garage, lot, pool, and condition adjustments of the §26.1 table.
  The *adjusted* value is "what this comp implies the subject is worth"; `valuation.py`
  aggregates those into the point + interval.

Hard filtering (radius / recency / sqft band / class) is the database's job in `query.py`;
this module scores and adjusts what the query already narrowed. The one exception is the
fallback ladder (`next_fallback_stage`): the *policy* of how far to widen is pure and lives
here, while the re-query it triggers is the service's job.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from deallens.modules.engine.schemas import (
    AdjustmentConfig,
    CompAdjustment,
    CompSelectionParams,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class FallbackStage(enum.IntEnum):
    """The comp-search widening ladder (03 §26.1). Ordered so a higher stage is a weaker,
    lower-confidence net; `valuation.confidence` penalizes each rung. `MODEL_PRIOR` is not a
    comp query at all — it is the last resort when even the widest net stays under `min_comps`.
    """

    BASE = 0  # base filters: urban radius, recency window, ±sqft band, same class
    WIDEN_RECENCY = 1  # stretch the recency window (older sales admitted)
    WIDEN_RADIUS = 2  # stretch the search radius (farther comps admitted)
    MODEL_PRIOR = 3  # no usable comps → market $/sqft prior, low confidence


# Similarity sub-score weights (sum to 1). Size and location dominate — an appraiser's first
# two questions ("how big, how close") — with recency, rooms, and vintage filling in.
_W_SQFT = Decimal("0.30")
_W_DISTANCE = Decimal("0.25")
_W_RECENCY = Decimal("0.20")
_W_BEDS = Decimal("0.10")
_W_BATHS = Decimal("0.10")
_W_YEAR = Decimal("0.05")

# Full-penalty spans for the room/vintage sub-scores (a difference this large scores ~0).
_BED_SPAN = Decimal("2")
_BATH_SPAN = Decimal("2")
_YEAR_SPAN = Decimal("40")


@dataclass(frozen=True, slots=True)
class SubjectFeatures:
    """The subject property's comparison attributes. `condition_grade` (1–5) is the vision
    aggregate (03 §27.3) used for the as-is condition line; ARV normalizes to a target grade
    instead, so the subject grade is optional for an ARV run.
    """

    sqft: int | None = None
    beds: int | None = None
    baths: Decimal | None = None
    garage_spaces: int | None = None
    lot_sqft: int | None = None
    pool: bool | None = None
    year_built: int | None = None
    condition_grade: int | None = None


@dataclass(frozen=True, slots=True)
class CompCandidate:
    """One comparable pulled by `query.py`: its comparison attributes, the observed money
    figure (sale close price for ARV/as-is, asking rent for rentals), and how far / how long
    ago. `distance_m`/`observed_date` come from PostGIS + the listing; the rest from the
    canonical property. Any field may be None — the grid simply skips a line it can't compute.
    """

    property_id: object  # UUID, kept opaque so this module stays free of DB types
    listing_id: object | None = None
    observed_value: Decimal = _ZERO
    observed_date: date | None = None
    distance_m: Decimal | None = None
    sqft: int | None = None
    beds: int | None = None
    baths: Decimal | None = None
    garage_spaces: int | None = None
    lot_sqft: int | None = None
    pool: bool | None = None
    year_built: int | None = None
    condition_grade: int | None = None


@dataclass(frozen=True, slots=True)
class ScoredComp:
    """A candidate after scoring + adjustment: the similarity weight and the adjusted value the
    estimator consumes, plus the line-item ledger for "show the math" (§26.9).
    """

    candidate: CompCandidate
    similarity: Decimal
    adjusted_value: Decimal
    adjustments: list[CompAdjustment] = field(default_factory=list)


def _clamp01(x: Decimal) -> Decimal:
    if x < _ZERO:
        return _ZERO
    return _ONE if x > _ONE else x


def _linear_closeness(diff: Decimal, span: Decimal) -> Decimal:
    """1 at no difference, decaying linearly to 0 at `span`. Pure and monotone — no exp() so
    the whole score stays exact Decimal (no float drift in a stored `similarity`)."""
    if span <= _ZERO:
        return _ONE
    return _clamp01(_ONE - abs(diff) / span)


def _days_between(a: date, b: date) -> int:
    return abs((a - b).days)


def similarity(
    subject: SubjectFeatures,
    comp: CompCandidate,
    params: CompSelectionParams,
    *,
    radius_m: Decimal,
    now: date,
) -> Decimal:
    """A 0–1 similarity weight (03 §26.1). Weighted blend of size, distance, recency, rooms,
    and vintage closeness; a sub-score whose inputs are missing drops out and its weight is
    redistributed, so a comp is never punished for a field the *subject* lacks. Result is
    quantized to 5 decimals to match the `comp_members.similarity` column (Numeric(6,5)).
    """
    recency_days = Decimal(params.recency_months) * Decimal("30.4")
    parts: list[tuple[Decimal, Decimal]] = []  # (weight, sub_score)

    if subject.sqft and comp.sqft:
        # Size closeness relative to the subject: a 25%-off comp scores ~0 at the default band.
        rel = Decimal(abs(subject.sqft - comp.sqft)) / Decimal(subject.sqft)
        parts.append((_W_SQFT, _clamp01(_ONE - rel / params.sqft_tolerance)))
    if comp.distance_m is not None and radius_m > _ZERO:
        parts.append((_W_DISTANCE, _clamp01(_ONE - comp.distance_m / radius_m)))
    if comp.observed_date is not None:
        age = Decimal(_days_between(now, comp.observed_date))
        parts.append((_W_RECENCY, _clamp01(_ONE - age / recency_days) if recency_days else _ONE))
    if subject.beds is not None and comp.beds is not None:
        parts.append((_W_BEDS, _linear_closeness(Decimal(subject.beds - comp.beds), _BED_SPAN)))
    if subject.baths is not None and comp.baths is not None:
        parts.append((_W_BATHS, _linear_closeness(subject.baths - comp.baths, _BATH_SPAN)))
    if subject.year_built and comp.year_built:
        diff = Decimal(subject.year_built - comp.year_built)
        parts.append((_W_YEAR, _linear_closeness(diff, _YEAR_SPAN)))

    if not parts:
        return _ZERO
    total_w = sum((w for w, _ in parts), _ZERO)
    blended = sum((w * s for w, s in parts), _ZERO) / total_w
    return _clamp01(blended).quantize(Decimal("0.00001"))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def comp_set_median_ppsf(candidates: Sequence[CompCandidate]) -> Decimal | None:
    """Median observed $/sqft across the candidate set — the market-local marginal-value basis
    for the sqft adjustment when `AdjustmentConfig.per_sqft` is left unset (03 §26.1). Comps
    with no sqft or a non-positive figure are skipped, not counted as zero.
    """
    ppsf = [
        c.observed_value / Decimal(c.sqft)
        for c in candidates
        if c.sqft and c.sqft > 0 and c.observed_value > _ZERO
    ]
    return _median(ppsf)


def _bool_to_int(v: bool | None) -> int | None:
    return None if v is None else int(v)


def line_item_adjustments(
    subject: SubjectFeatures,
    comp: CompCandidate,
    config: AdjustmentConfig,
    *,
    per_sqft: Decimal,
    target_condition_grade: int | None,
) -> list[CompAdjustment]:
    """The §26.1 adjustment grid for one comp: a signed $ delta per feature that moves the
    comp *toward the subject* (positive = the subject is worth more on this line). Each line is
    emitted only when both sides of the comparison are known — a missing garage count yields no
    garage line, never a phantom $0 adjustment that reads as "verified equal".

    `target_condition_grade` selects the condition basis: the subject's own grade for an as-is
    value, or the renovated ARV target (`config.arv_target_grade`) for ARV — the one knob that
    makes the same grid produce both estimates (03 §26.1).
    """
    adj: list[CompAdjustment] = []

    def line(feature: str, amount: Decimal, rationale: str) -> None:
        if amount != _ZERO:
            adj.append(CompAdjustment(feature=feature, amount=amount, rationale=rationale))

    if subject.sqft and comp.sqft:
        delta = Decimal(subject.sqft - comp.sqft) * per_sqft * config.sqft_damping
        line("sqft", delta, f"{subject.sqft - comp.sqft:+d} sqft @ {per_sqft:.2f}/sqft (damped)")
    if subject.beds is not None and comp.beds is not None:
        line("beds", Decimal(subject.beds - comp.beds) * config.bed_value,
             f"{subject.beds - comp.beds:+d} bed")
    if subject.baths is not None and comp.baths is not None:
        line("baths", (subject.baths - comp.baths) * config.bath_value,
             f"{subject.baths - comp.baths:+} bath")
    if subject.garage_spaces is not None and comp.garage_spaces is not None:
        gd = subject.garage_spaces - comp.garage_spaces
        line("garage", Decimal(gd) * config.garage_space_value, f"{gd:+d} garage space")
    if subject.lot_sqft is not None and comp.lot_sqft is not None:
        line("lot", Decimal(subject.lot_sqft - comp.lot_sqft) * config.per_lot_sqft,
             f"{subject.lot_sqft - comp.lot_sqft:+d} lot sqft")
    sp, cp = _bool_to_int(subject.pool), _bool_to_int(comp.pool)
    if sp is not None and cp is not None:
        line("pool", Decimal(sp - cp) * config.pool_value, "pool difference")
    if target_condition_grade is not None and comp.condition_grade is not None:
        steps = target_condition_grade - comp.condition_grade
        line("condition", Decimal(steps) * config.condition_grade_value,
             f"{steps:+d} condition grade → target {target_condition_grade}")
    return adj


def adjusted_value(comp: CompCandidate, adjustments: Sequence[CompAdjustment]) -> Decimal:
    """The comp's observed money figure plus its signed adjustment lines = the value it implies
    for the subject (03 §26.1). Never negative: a pathological adjustment stack floors at 0
    rather than emitting a nonsense negative comp value into the aggregate.
    """
    total = comp.observed_value + sum((a.amount for a in adjustments), _ZERO)
    return total if total > _ZERO else _ZERO


def score_and_adjust(
    subject: SubjectFeatures,
    candidates: Sequence[CompCandidate],
    params: CompSelectionParams,
    config: AdjustmentConfig,
    *,
    radius_m: Decimal,
    now: date,
    target_condition_grade: int | None,
    per_sqft: Decimal | None = None,
) -> list[ScoredComp]:
    """Score, adjust, rank, and truncate a candidate pool into the working comp set. `per_sqft`
    defaults to the comp-set median (03 §26.1 — a market-local basis). Returns up to
    `params.max_comps` comps, most-similar first; the caller decides whether the count clears
    `min_comps` or the fallback ladder must widen the pool first.
    """
    if per_sqft is None:
        per_sqft = config.per_sqft or comp_set_median_ppsf(candidates) or _ZERO
    scored: list[ScoredComp] = []
    for c in candidates:
        sim = similarity(subject, c, params, radius_m=radius_m, now=now)
        adjustments = line_item_adjustments(
            subject, c, config, per_sqft=per_sqft, target_condition_grade=target_condition_grade
        )
        scored.append(ScoredComp(c, sim, adjusted_value(c, adjustments), adjustments))
    # Rank by similarity desc; break ties by nearer distance so ordering is deterministic and
    # doesn't depend on the query's row order (cacheable, reproducible sets).
    scored.sort(key=lambda s: (-s.similarity, s.candidate.distance_m or Decimal("1e18")))
    return scored[: params.max_comps]


def next_fallback_stage(
    current: FallbackStage, n_found: int, params: CompSelectionParams
) -> FallbackStage | None:
    """The widening policy (03 §26.1): given how many comps the current stage yielded, return
    the next rung to try, or None to stop (enough comps, or the ladder is exhausted). Kept pure
    so the escalation logic is testable without a database; the service performs the re-query.
    """
    if n_found >= params.min_comps:
        return None
    if current >= FallbackStage.MODEL_PRIOR:
        return None
    return FallbackStage(current + 1)


__all__ = [
    "AdjustmentConfig",
    "CompCandidate",
    "CompSelectionParams",
    "FallbackStage",
    "ScoredComp",
    "SubjectFeatures",
    "adjusted_value",
    "comp_set_median_ppsf",
    "line_item_adjustments",
    "next_fallback_stage",
    "score_and_adjust",
    "similarity",
]
