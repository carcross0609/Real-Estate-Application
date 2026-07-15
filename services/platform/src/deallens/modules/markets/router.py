"""Markets HTTP surface — the market intelligence page (S33/§28). `GET .../report` serves the
derived indices + market score on the actor's RLS session (shared reference data). `POST
.../report/recompute` recomputes from current `market_stats` and persists the derived values —
writing shared data, so it runs on the owner-role `system_session` (the S43 onboarding /
periodic-refresh path).
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_current_actor
from deallens.core.db import system_session
from deallens.modules.markets import service
from deallens.modules.markets.schemas import MarketReportOut

router = APIRouter(tags=["markets"])


@router.get("/markets/{market_id}/report", response_model=MarketReportOut)
async def get_market_report(
    market_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> MarketReportOut:
    """The market's derived indices (momentum / rental demand / liquidity / neighborhood quality),
    market score, and the metric set behind them — each labeled with the geography it was measured
    at (03 §28.2/§28.4)."""
    return await service.get_market_report(db, market_id=market_id)


@router.post("/markets/{market_id}/report/recompute", response_model=MarketReportOut)
async def recompute_market_report(
    market_id: UUID,
    _actor: Actor = Depends(get_current_actor),
) -> MarketReportOut:
    """Recompute a market's indices + score from current `market_stats` and persist the derived
    values (03 §28.6). Writes shared data, so it runs on the owner-role session."""
    async with system_session() as db:
        result = await service.compute_market_report(db, market_id=market_id)
        await db.commit()
        return result
