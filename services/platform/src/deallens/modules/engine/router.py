"""Engine HTTP surface — the property-page valuation panel + the comps analyzer (S12/§21.5).

Read (`GET .../valuation`) serves the latest persisted ARV / as-is / rent bundle plus
appreciation on the actor's RLS session — shared derived data, readable by any authenticated
actor. The write paths split by trust:

- **Recompute** refreshes the *shared* valuation and therefore runs on the owner-role
  `system_session` (RLS forbids the app role writing a system, `org_id IS NULL` row — migration
  0004). It is the continuous-improvement trigger (03 §9.5) exposed for a manual/UI refresh; the
  scheduled path calls `service.recompute_if_stale` directly.
- **Comp edit** (pin/exclude, FR-015) writes an *org-owned* comp set and returns a transient
  recomputed estimate, so it stays on the actor session and never mutates the shared number.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_current_actor
from deallens.core.db import system_session
from deallens.core.enums import Strategy
from deallens.core.errors import NotFoundError, ValidationError
from deallens.modules.engine import service
from deallens.modules.engine.models import ValuationKind
from deallens.modules.engine.schemas import (
    AssumptionSet,
    CompEditRequest,
    EngineOutputBlock,
    PropertyValuationOut,
    ValuationOut,
)
from deallens.modules.engine.strategies import UnsupportedStrategyError

router = APIRouter(tags=["engine"])

# Kinds a user may pin/exclude comps for — STR revenue isn't comp-based (03 §26.1), so it's out.
_EDITABLE_KINDS = {ValuationKind.ARV, ValuationKind.AS_IS, ValuationKind.RENT_LTR}


@router.get("/properties/{property_id}/valuation", response_model=PropertyValuationOut)
async def get_valuation(
    property_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> PropertyValuationOut:
    """The latest ARV / as-is / market-rent estimates + appreciation for a property (03 §26.1,
    §28), each with its P10/P50/P90 band, confidence, and comp set. An unvalued property returns
    nulls, not a 404 (the property exists; it just hasn't been valued yet)."""
    return await service.get_property_valuation(db, property_id=property_id)


@router.post("/properties/{property_id}/valuation/recompute", response_model=PropertyValuationOut)
async def recompute_valuation(
    property_id: UUID,
    _actor: Actor = Depends(get_current_actor),
) -> PropertyValuationOut:
    """Force a fresh valuation against the newest market data (03 §9.5 continuous improvement).
    Recomputes ARV/as-is/rent from current comps and appends versioned `valuations` rows. Writes
    shared data, so it runs on the owner-role session."""
    async with system_session() as db:
        result = await service.value_property(db, property_id=property_id)
        await db.commit()
        return result


@router.post(
    "/properties/{property_id}/valuation/{kind}/comps/edit", response_model=ValuationOut
)
async def edit_comps(
    property_id: UUID,
    kind: ValuationKind,
    body: CompEditRequest,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> ValuationOut:
    """Recompute one estimate under user pin/exclude overrides (FR-015). Persists an org-owned
    comp set and returns the recomputed band; the shared valuation is untouched."""
    if kind not in _EDITABLE_KINDS:
        raise ValidationError(f"comps cannot be edited for valuation kind '{kind.value}'")
    return await service.apply_comp_edits(
        db,
        property_id=property_id,
        valuation_kind=kind,
        edits=body,
        org_id=actor.require_org(),
        user_id=actor.user_id,
    )


# --- Financial analysis (§26.2–§26.8) ---------------------------------------------------


@router.get("/properties/{property_id}/analysis/{strategy}", response_model=EngineOutputBlock)
async def get_analysis(
    property_id: UUID,
    strategy: Strategy,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> EngineOutputBlock:
    """The latest persisted deterministic analysis for a property × strategy (03 §26.9): the full
    pro-forma, financing scenarios, and return metrics with their derivation ledgers. 404 when the
    property has never been analyzed for this strategy."""
    result = await service.get_analysis(db, property_id=property_id, strategy=strategy)
    if result is None:
        raise NotFoundError(f"No analysis for strategy '{strategy.value}'")
    return result


@router.post("/properties/{property_id}/analysis/{strategy}", response_model=EngineOutputBlock)
async def run_analysis(
    property_id: UUID,
    strategy: Strategy,
    assumptions: AssumptionSet | None = None,
    _actor: Actor = Depends(get_current_actor),
) -> EngineOutputBlock:
    """Run + persist a fresh analysis for one strategy (03 §26), optionally under a custom
    assumption set (a what-if; FR-013). Writes shared `analyses` data, so it runs on the
    owner-role session. `overall` and the not-yet-built [F] strategies return 422."""
    try:
        async with system_session() as db:
            result = await service.analyze_property(
                db, property_id=property_id, strategy=strategy,
                assumptions_override=assumptions,
            )
            await db.commit()
            return result
    except UnsupportedStrategyError as exc:
        raise ValidationError(str(exc)) from exc


@router.post(
    "/properties/{property_id}/analysis", response_model=dict[str, EngineOutputBlock]
)
async def run_all_analyses(
    property_id: UUID,
    _actor: Actor = Depends(get_current_actor),
) -> dict[str, EngineOutputBlock]:
    """Run + persist the default strategy set (flip / LTR / BRRRR) for the property-page analyzer
    (03 §26). Strategies whose inputs are missing (e.g. no ARV → no flip) are skipped, not fatal —
    the response contains whatever could be underwritten."""
    async with system_session() as db:
        result = await service.analyze_strategies(db, property_id=property_id)
        await db.commit()
        return result
