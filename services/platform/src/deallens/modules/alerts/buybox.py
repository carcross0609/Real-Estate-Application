"""Pure buy-box matching (§11.5 #6, FR-040) — does one scored property clear a saved box?

Kept pure and side-effect free so the matching rules are unit-testable without a database:
the service (`alerts.service`) assembles a `MatchCandidate` from the property/listing/score,
and this decides. Two distinct verdicts come back:

- `matched` — the property satisfies every constraint the box actually set (omitted fields are
  unconstrained; all bounds inclusive, mirroring `BuyBoxFilters`). Physical/price filters and
  the box's own score/risk/confidence gates all bind here.
- `instant_eligible` — the score's confidence clears the global instant-alert floor (03 §25.1
  #4: never interrupt a user for a low-confidence call). A matched-but-not-instant property
  still alerts — it just waits for the next digest instead of firing immediately.

`reasons` records which constraint failed, so a "why didn't my box match this" answer is a
read, not a re-derivation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from deallens.modules.alerts.schemas import BuyBoxFilters
from deallens.modules.ingestion.models import PropertyType

# FEMA Special Flood Hazard Areas — zones beginning A or V (the 1%-annual-chance floodplain).
# `exclude_flood_zones` drops a property whose flood layer puts it in one of these.
_SFHA_PREFIXES = ("A", "V")


def is_special_flood_hazard(zone: str | None) -> bool:
    if not zone:
        return False
    return zone.strip().upper().startswith(_SFHA_PREFIXES)


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    """The snapshot of a scored property the matcher reads. Every field is optional — a
    property mid-enrichment (no beds yet, no flood layer) is matched on what is known and
    fails only a filter it actually contradicts, never one it merely lacks data for … except
    where a filter is set and the datum is missing, which is a conservative non-match (we do
    not alert on an unverified guess)."""

    property_id: str
    list_price: Decimal | None = None
    beds: int | None = None
    baths: Decimal | None = None
    sqft: int | None = None
    year_built: int | None = None
    property_type: PropertyType | None = None
    # None = membership not determined (the geo read isn't wired yet) → the area filter is not
    # applied, rather than failing every area-scoped box. An empty set means "known to be in no
    # area" and *does* fail an area filter.
    search_area_ids: frozenset[str] | None = None
    score: Decimal | None = None
    risk: Decimal | None = None
    confidence: Decimal | None = None  # 0–100, as persisted on `scores.confidence_score`
    flood_zone: str | None = None
    remarks: str | None = None


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    matched: bool
    instant_eligible: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.matched


def _fails_min(value: Decimal | int | None, bound: Decimal | int | None) -> bool:
    """A min bound fails when it is set and the value is missing or below it."""
    if bound is None:
        return False
    return value is None or value < bound


def _fails_max(value: Decimal | int | None, bound: Decimal | int | None) -> bool:
    if bound is None:
        return False
    return value is None or value > bound


def evaluate(
    candidate: MatchCandidate,
    filters: BuyBoxFilters,
    *,
    confidence_gate: Decimal = Decimal("0.5"),
) -> MatchOutcome:
    """Match `candidate` against `filters`. `confidence_gate` is on the 0–1 scale (from
    settings); it is compared to the score's 0–100 confidence after scaling. It gates only
    *instant* delivery, never whether the box matched."""
    reasons: list[str] = []

    if _fails_min(candidate.list_price, filters.price_min):
        reasons.append("price_below_min")
    if _fails_max(candidate.list_price, filters.price_max):
        reasons.append("price_above_max")
    if _fails_min(candidate.beds, filters.beds_min):
        reasons.append("beds_below_min")
    if _fails_min(candidate.baths, filters.baths_min):
        reasons.append("baths_below_min")
    if _fails_min(candidate.sqft, filters.sqft_min):
        reasons.append("sqft_below_min")
    if _fails_max(candidate.sqft, filters.sqft_max):
        reasons.append("sqft_above_max")
    if _fails_min(candidate.year_built, filters.year_built_min):
        reasons.append("year_built_below_min")

    if filters.property_types and candidate.property_type not in filters.property_types:
        reasons.append("property_type_excluded")

    if (
        filters.search_area_ids
        and candidate.search_area_ids is not None
        and candidate.search_area_ids.isdisjoint(filters.search_area_ids)
    ):
        reasons.append("outside_search_areas")

    # Quality gates (§25.5) — the box's own thresholds on the calibrated score/risk/confidence.
    if _fails_min(candidate.score, filters.min_score):
        reasons.append("score_below_min")
    if _fails_max(candidate.risk, filters.max_risk):
        reasons.append("risk_above_max")
    if _fails_min(candidate.confidence, filters.min_confidence):
        reasons.append("confidence_below_min")

    if filters.exclude_flood_zones and is_special_flood_hazard(candidate.flood_zone):
        reasons.append("in_flood_zone")

    if filters.keywords:
        remarks = (candidate.remarks or "").lower()
        if not any(kw.lower() in remarks for kw in filters.keywords):
            reasons.append("no_keyword_match")

    matched = not reasons
    # Scale the 0–1 gate to the 0–100 confidence scale; a missing confidence never fires an
    # instant alert (unverified → digest, not interrupt).
    gate_100 = confidence_gate * Decimal(100)
    instant_eligible = (
        matched and candidate.confidence is not None and candidate.confidence >= gate_100
    )
    return MatchOutcome(matched=matched, instant_eligible=instant_eligible, reasons=tuple(reasons))


__all__ = [
    "MatchCandidate",
    "MatchOutcome",
    "evaluate",
    "is_special_flood_hazard",
]
