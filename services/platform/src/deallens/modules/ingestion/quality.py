"""Data-quality rules + coverage watchdog (§14.4).

Two jobs:
1. **Normalize-time DQ rules** — cheap range/sanity checks on each delta *before* it becomes
   product-visible. A violation is not a rejection: its `severity` decides whether the value
   auto-fixes, serves with a visible flag, or is suppressed pending review (`DqSeverity`,
   §14.4). This is deliberately separate from Pydantic (which enforces *shape*) — a $10 house
   is a valid `ListingDelta` shape but an implausible price, and that's a DQ concern, so the
   record still lands (never silently dropped) but carries a flag.
2. **Coverage watchdog** — per-market, compares what we stored against the source-reported
   total; a >2% gap breaches the PRD §4.4 coverage SLO and pages ops (S40/S42).

Rule functions are pure (`check_*` → list of violations) so they're exhaustively unit-tested
without a DB; `record_dq_flags` is the thin DB-writing layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.modules.admin.models import DqFlag, DqSeverity, DqStatus
from deallens.modules.ingestion.schemas import ListingDelta, PropertyDelta

# Plausibility bounds (§14.4). Wide on purpose — these catch feed corruption (a price in
# cents, a sqft that's actually lot size), not merely-unusual real properties.
_PRICE_MIN, _PRICE_MAX = 1_000, 500_000_000
_SQFT_MIN, _SQFT_MAX = 100, 1_000_000
_LOT_SQFT_MAX = 500_000_000  # ~11k acres
_YEAR_MIN = 1700
_BEDS_MAX = 60
_BATHS_MAX = 60


@dataclass(frozen=True, slots=True)
class DqViolation:
    rule: str
    severity: DqSeverity
    detail: dict[str, object]


def check_property(prop: PropertyDelta, *, reference_year: int | None = None) -> list[DqViolation]:
    year_cap = (reference_year or datetime.now(UTC).year) + 2
    out: list[DqViolation] = []

    if prop.year_built is not None and not (_YEAR_MIN <= prop.year_built <= year_cap):
        out.append(DqViolation("year_built_out_of_range", DqSeverity.SERVE_WITH_FLAG,
                               {"year_built": prop.year_built}))
    if prop.sqft is not None and not (_SQFT_MIN <= prop.sqft <= _SQFT_MAX):
        out.append(DqViolation("sqft_out_of_range", DqSeverity.SERVE_WITH_FLAG,
                               {"sqft": prop.sqft}))
    if prop.lot_sqft is not None and prop.lot_sqft > _LOT_SQFT_MAX:
        out.append(DqViolation("lot_sqft_out_of_range", DqSeverity.SERVE_WITH_FLAG,
                               {"lot_sqft": prop.lot_sqft}))
    # Living area larger than the lot it sits on is almost always a units/field-swap bug.
    # A genuinely multi-story building can exceed its footprint, so only flag single-story
    # (or unknown-story) records where a lot size is actually present.
    if (
        prop.sqft is not None
        and prop.lot_sqft is not None
        and prop.lot_sqft > 0
        and prop.sqft > prop.lot_sqft
        and prop.stories in (None, 1)
    ):
        out.append(DqViolation("living_area_exceeds_lot", DqSeverity.SERVE_WITH_FLAG,
                               {"sqft": prop.sqft, "lot_sqft": prop.lot_sqft}))
    if prop.beds is not None and not (0 <= prop.beds <= _BEDS_MAX):
        out.append(DqViolation("beds_out_of_range", DqSeverity.SERVE_WITH_FLAG,
                               {"beds": prop.beds}))
    if prop.baths is not None and not (0 <= prop.baths <= _BATHS_MAX):
        out.append(DqViolation("baths_out_of_range", DqSeverity.SERVE_WITH_FLAG,
                               {"baths": str(prop.baths)}))
    # A missing geocode blocks market assignment + geo enrichment — flag, don't drop.
    if prop.latitude is None or prop.longitude is None:
        out.append(DqViolation("missing_geocode", DqSeverity.SERVE_WITH_FLAG, {}))
    return out


def check_listing(listing: ListingDelta) -> list[DqViolation]:
    out: list[DqViolation] = []
    if listing.list_price is not None and not (_PRICE_MIN <= listing.list_price <= _PRICE_MAX):
        out.append(DqViolation("list_price_out_of_range", DqSeverity.SERVE_WITH_FLAG,
                               {"list_price": str(listing.list_price)}))
    if listing.close_price is not None and not (_PRICE_MIN <= listing.close_price <= _PRICE_MAX):
        out.append(DqViolation("close_price_out_of_range", DqSeverity.SERVE_WITH_FLAG,
                               {"close_price": str(listing.close_price)}))
    if listing.dom_current is not None and listing.dom_current < 0:
        out.append(DqViolation("negative_dom", DqSeverity.AUTO_FIX,
                               {"dom_current": listing.dom_current}))
    # A closed listing with no close price can't validate an ARV comp — suppress it from
    # comp selection until reviewed rather than let it skew a valuation.
    if listing.status.value == "sold" and listing.close_price is None:
        out.append(DqViolation("sold_without_close_price", DqSeverity.SUPPRESS, {}))
    return out


def run_dq_checks(delta: object, *, reference_year: int | None = None) -> list[DqViolation]:
    """Dispatch DQ rules by delta type, checking the embedded property too. Unknown delta
    types return no violations (a new delta kind opts into DQ by adding a `check_*`)."""
    violations: list[DqViolation] = []
    if isinstance(delta, ListingDelta):
        violations.extend(check_listing(delta))
        violations.extend(check_property(delta.property, reference_year=reference_year))
    elif isinstance(delta, PropertyDelta):
        violations.extend(check_property(delta, reference_year=reference_year))
    return violations


async def record_dq_flags(
    db: AsyncSession,
    *,
    subject_type: str,
    subject_id: UUID,
    violations: list[DqViolation],
    market_id: UUID | None = None,
) -> list[DqFlag]:
    """Persist violations as `dq_flags`, de-duplicating against still-open flags so a
    re-ingest of the same bad row doesn't fan out identical flags every cycle. Returns the
    rows created this call (empty when clean or all already open)."""
    if not violations:
        return []
    open_rules = await db.execute(
        select(DqFlag.rule).where(
            DqFlag.subject_type == subject_type,
            DqFlag.subject_id == subject_id,
            DqFlag.status == DqStatus.OPEN,
        )
    )
    already_open = set(open_rules.scalars().all())

    created: list[DqFlag] = []
    for v in violations:
        if v.rule in already_open:
            continue
        flag = DqFlag(
            subject_type=subject_type,
            subject_id=subject_id,
            market_id=market_id,
            rule=v.rule,
            severity=v.severity,
            status=DqStatus.OPEN,
            details=v.detail,
        )
        db.add(flag)
        created.append(flag)
    if created:
        await db.flush()
    return created


@dataclass(frozen=True, slots=True)
class CoverageReport:
    stored: int
    source_reported: int | None
    gap_pct: float | None
    breached: bool


def coverage_report(
    *, stored: int, source_reported: int | None, threshold: float = 0.02
) -> CoverageReport:
    """Compare stored count vs. the source's reported total for a market. A gap over
    `threshold` (2% per §14.4) is a coverage-SLO breach → the watchdog pages ops. Returns
    `breached=False` when the source doesn't expose a total (can't judge)."""
    if not source_reported or source_reported <= 0:
        return CoverageReport(stored=stored, source_reported=source_reported, gap_pct=None,
                              breached=False)
    gap = max(0.0, (source_reported - stored) / source_reported)
    return CoverageReport(
        stored=stored,
        source_reported=source_reported,
        gap_pct=round(gap, 4),
        breached=gap > threshold,
    )
