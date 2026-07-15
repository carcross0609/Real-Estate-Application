"""Financial-analysis orchestration (03 §26.2–§26.8) — the impure seam that assembles a
`FinancialInputs` from the ORM (listing price, the comps engine's ARV/rent, the vision rehab
model, assessor taxes, market appreciation), runs the pure `strategies` underwriting, and
persists an `analyses` row with full traceability (§26.9).

Kept in its own file (not `service.py`) so the comps/valuation seam and the finance seam don't
grow into one 1,500-line module; both are re-exported through `engine.service`, which stays the
module's single public interface (§19). All the arithmetic is in `finance`/`strategies` and is
DB-free; this file only *gathers inputs* and *stores outputs*.

Assumption resolution is layered (FR-013): system v1 defaults → per-market overrides
(`markets.config["assumptions"]`) → org-locked set (`orgs.locked_assumption_set`). The merged
result is snapshotted into `analyses.assumption_set`, so an analysis renders under exactly the
assumptions it was computed with even after the defaults later change.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.enums import Strategy
from deallens.core.errors import NotFoundError, ValidationError
from deallens.modules.engine import service as comps_service
from deallens.modules.engine import strategies
from deallens.modules.engine.models import Analysis
from deallens.modules.engine.schemas import (
    AssumptionSet,
    EngineOutputBlock,
    FinancialInputs,
    Interval,
    PropertyValuationOut,
    RehabEstimate,
    ValuationOut,
)
from deallens.modules.enrichment.models import TaxRecord
from deallens.modules.ingestion.models import Listing, Property
from deallens.modules.markets.models import Market
from deallens.modules.vision.models import PropertyCondition

_ZERO = Decimal("0")
# Insurance fallback when no quote/market table exists: a coarse $/sqft/yr so the pro-forma isn't
# silently missing an opex line (§26.4 uses a market $/sqft table; this is the cold-start prior).
_DEFAULT_INSURANCE_PER_SQFT = Decimal("0.55")


# --- Input assembly ---------------------------------------------------------------------


def _interval_from_valuation(v: ValuationOut | None) -> Interval | None:
    """Persisted `ValuationOut` (point/low/high/p50) → engine `Interval` (point + P10/P50/P90).
    `low` is P10 and `high` is P90 by construction (03 §26.1)."""
    if v is None or v.point is None:
        return None
    return Interval(point=v.point, p10=v.low, p50=v.p50 or v.point, p90=v.high)


def _rehab_from_condition(condition: PropertyCondition | None) -> RehabEstimate:
    """Vision rehab model (03 §27.4) → the finance `RehabEstimate` band. Cosmetic + major low/high
    sum to the band ends, the midpoint is their average, and the presence of any red flag drives
    the higher (20%) contingency (§26.2). No condition data → a $0 band (turnkey assumption),
    surfaced as a warning by the strategy layer, never a hidden guess."""
    if condition is None:
        return RehabEstimate()
    low = (condition.cosmetic_repair_low or _ZERO) + (condition.major_repair_low or _ZERO)
    high = (condition.cosmetic_repair_high or _ZERO) + (condition.major_repair_high or _ZERO)
    if high < low:
        high = low
    mid = (low + high) / Decimal(2)
    return RehabEstimate(
        low=low, mid=mid, high=high, red_flags_present=bool(condition.red_flags)
    )


async def _latest_listing(db: AsyncSession, property_id: UUID) -> Listing | None:
    """The most recent listing for a property — its list price is the contract price the analysis
    underwrites against (03 §26). Sold/withdrawn listings still carry the last known price, so we
    don't filter by status here; the caller may pass a `price_override` for a what-if."""
    row = await db.execute(
        select(Listing)
        .where(Listing.property_id == property_id)
        .order_by(Listing.list_date.desc().nullslast(), Listing.source_ts.desc().nullslast())
        .limit(1)
    )
    return row.scalars().first()


async def _annual_taxes(db: AsyncSession, property_id: UUID) -> tuple[Decimal | None, bool]:
    """Latest assessor tax amount + whether the jurisdiction reassesses on sale (03 §26.4). When
    it reassesses, underwriting on the seller's grandfathered bill understates the true cost — the
    flag is carried so the pro-forma/UI can note it (the reassessment estimate itself is a market
    table, deferred)."""
    row = await db.execute(
        select(TaxRecord)
        .where(TaxRecord.property_id == property_id)
        .order_by(TaxRecord.tax_year.desc())
        .limit(1)
    )
    rec = row.scalars().first()
    if rec is None:
        return None, False
    return rec.annual_tax_amount, bool(rec.reassessed_on_sale)


def _annual_insurance(market: Market | None, sqft: int | None) -> Decimal | None:
    """Insurance estimate from the market $/sqft table (03 §26.4), or the cold-start prior. None
    when there's no sqft to scale against (an honest gap the strategy layer warns on)."""
    if not sqft:
        return None
    per_sqft = _DEFAULT_INSURANCE_PER_SQFT
    if market is not None:
        raw = (market.config.get("assumptions", {}) or {}).get("insurance_per_sqft")
        if raw is not None:
            per_sqft = Decimal(str(raw))
    return (per_sqft * Decimal(sqft)).quantize(Decimal("0.01"))


def resolve_assumptions(
    market: Market | None, org_locked: dict[str, Any] | None, override: AssumptionSet | None
) -> AssumptionSet:
    """Layer the assumption set (FR-013): v1 defaults → market overrides → org-locked → an
    explicit per-request override (a scenario what-if). The merged set is what gets snapshotted
    into the stored analysis, so the number always renders under the assumptions it used."""
    if override is not None:
        return override
    data = AssumptionSet().model_dump(mode="json")
    if market is not None:
        data.update(market.config.get("assumptions", {}) or {})
    if org_locked:
        data.update(org_locked)
    return AssumptionSet.model_validate(data)


async def build_financial_inputs(
    db: AsyncSession, *, property_id: UUID, price_override: Decimal | None = None
) -> tuple[FinancialInputs, PropertyValuationOut, Listing | None]:
    """Gather everything a deterministic run needs for a property (03 §26). Reads the comps
    engine's persisted valuations (ARV / as-is / rent), the vision rehab band, assessor taxes,
    an insurance estimate, and market appreciation. Returns the inputs plus the valuation bundle
    and listing so the caller can persist provenance."""
    prop = await db.get(Property, property_id)
    if prop is None:
        raise NotFoundError("Property not found")

    valuation = await comps_service.get_property_valuation(db, property_id=property_id)
    listing = await _latest_listing(db, property_id)

    price = price_override or (listing.list_price if listing else None)
    if price is None:
        price = valuation.as_is.point if valuation.as_is else None
    if price is None:
        raise ValidationError("no list price or as-is estimate to underwrite against")

    market = await db.get(Market, prop.market_id) if prop.market_id else None
    condition = await db.get(PropertyCondition, property_id)
    taxes, reassessed = await _annual_taxes(db, property_id)

    rent = valuation.rent_ltr
    confidences = [
        v.confidence for v in (valuation.arv, rent) if v is not None and v.confidence is not None
    ]
    inputs = FinancialInputs(
        price=price,
        arv=_interval_from_valuation(valuation.arv),
        as_is=_interval_from_valuation(valuation.as_is),
        market_rent_monthly=rent.point if rent else None,
        rehab=_rehab_from_condition(condition),
        annual_taxes=taxes,
        reassessed_on_sale=reassessed,
        annual_insurance=_annual_insurance(market, prop.sqft),
        hoa_monthly=prop.hoa_monthly or _ZERO,
        sqft=prop.sqft,
        appreciation_pct=(
            valuation.appreciation.annual_pct if valuation.appreciation else None
        ),
        estimator_confidence=min(confidences) if confidences else None,
    )
    return inputs, valuation, listing


# --- Run + persist ----------------------------------------------------------------------


async def analyze_property(
    db: AsyncSession,
    *,
    property_id: UUID,
    strategy: Strategy,
    assumptions_override: AssumptionSet | None = None,
    price_override: Decimal | None = None,
    persist: bool = True,
) -> EngineOutputBlock:
    """Underwrite one property × strategy (03 §26) and (optionally) persist an `analyses` row with
    the full assumption snapshot + output block (§26.9). Runs the pure `strategies.analyze` over
    inputs gathered from the comps engine, vision, and enrichment. Raises
    `UnsupportedStrategyError` for `overall` / the [F] strategies (surfaced as a 4xx by the
    router)."""
    inputs, valuation, listing = await build_financial_inputs(
        db, property_id=property_id, price_override=price_override
    )
    market = None
    prop = await db.get(Property, property_id)
    org_locked = None
    if prop is not None and prop.market_id is not None:
        market = await db.get(Market, prop.market_id)
    assumptions = resolve_assumptions(market, org_locked, assumptions_override)

    output = strategies.analyze(strategy, inputs, assumptions)
    output.comp_set_ids = [
        str(v.comp_set_id)
        for v in (valuation.arv, valuation.as_is, valuation.rent_ltr)
        if v is not None and v.comp_set_id is not None
    ]

    if persist:
        db.add(
            Analysis(
                property_id=property_id,
                listing_id=listing.id if listing else None,
                engine_version=strategies.ENGINE_VERSION,
                assumption_set=assumptions.model_dump(mode="json"),
                strategy=strategy,
                outputs=output.model_dump(mode="json"),
            )
        )
        await db.flush()
    return output


async def analyze_strategies(
    db: AsyncSession,
    *,
    property_id: UUID,
    strategy_list: list[Strategy] | None = None,
    persist: bool = True,
) -> dict[str, EngineOutputBlock]:
    """Run several strategies for a property in one pass (the property-page analyzer shows them
    side by side). Unsupported/[F] strategies are skipped, not fatal — a property that can't be a
    flip (no ARV) still returns its LTR analysis."""
    wanted = strategy_list or [Strategy.FLIP, Strategy.LTR, Strategy.BRRRR]
    out: dict[str, EngineOutputBlock] = {}
    for strat in wanted:
        try:
            out[strat.value] = await analyze_property(
                db, property_id=property_id, strategy=strat, persist=persist
            )
        except strategies.UnsupportedStrategyError:
            continue
    return out


async def get_analysis(
    db: AsyncSession, *, property_id: UUID, strategy: Strategy
) -> EngineOutputBlock | None:
    """The latest persisted analysis for a property × strategy (the property-page read; no
    recompute). None when never analyzed for this strategy."""
    row = await db.execute(
        select(Analysis)
        .where(Analysis.property_id == property_id, Analysis.strategy == strategy)
        .order_by(Analysis.computed_at.desc())
        .limit(1)
    )
    analysis = row.scalars().first()
    if analysis is None:
        return None
    return EngineOutputBlock.model_validate(analysis.outputs)


__all__ = [
    "analyze_property",
    "analyze_strategies",
    "build_financial_inputs",
    "get_analysis",
    "resolve_assumptions",
]
