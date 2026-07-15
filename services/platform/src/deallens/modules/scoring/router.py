"""Scoring HTTP surface — the property-page score panel + "show the math" (S12/§21, §25).

`GET .../score` serves the latest persisted score bundle (overall + per-strategy + factor ledger)
on the actor's RLS session. `POST .../score` recomputes it — writing shared `scores`/`score_factors`
(no RLS, §11.1), so like the other engine recomputes it runs on the owner-role `system_session`.
The scheduled path calls `service.score_property` off the engine/vision event stream; this endpoint
is the manual/UI refresh.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_current_actor
from deallens.core.db import system_session
from deallens.core.errors import NotFoundError
from deallens.modules.scoring import service
from deallens.modules.scoring.schemas import ScoreResult

router = APIRouter(tags=["scoring"])


@router.post("/properties/{property_id}/score", response_model=ScoreResult)
async def compute_score(
    property_id: UUID,
    _actor: Actor = Depends(get_current_actor),
) -> ScoreResult:
    """Compute + persist the full investment score for a property (03 §25): overall + winning
    strategy, per-strategy and per-category scores, risk, confidence, recommendation, and the
    explanation ledger. Writes shared data, so it runs on the owner-role session."""
    async with system_session() as db:
        result = await service.score_property(db, property_id=property_id)
        await db.commit()
        return result


@router.get("/properties/{property_id}/score", response_model=ScoreResult)
async def get_score(
    property_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> ScoreResult:
    """Recompute the score bundle for display from current engine/vision/market data (03 §25).
    Read-only session; does not persist. 404 only when the property doesn't exist."""
    try:
        return await service.score_property(db, property_id=property_id, persist=False)
    except NotFoundError:
        raise NotFoundError("Property not found") from None
