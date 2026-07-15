"""Vision HTTP surface — the property-page condition/rehab panel (S12/§21.9, §27).

`GET .../condition` serves the persisted property condition + a recomputed rehab breakdown on the
actor's RLS session (shared derived data, any authenticated actor may read). `POST .../analyze`
re-runs the vision pipeline for a listing — it writes shared `photo_analyses`/`property_conditions`
(no RLS, §11.1), so like the comps recompute it runs on the owner-role `system_session`. The
scheduled path calls `service.analyze_listing` directly off the ingestion event stream; this
endpoint is the manual/UI refresh.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_current_actor
from deallens.core.db import system_session
from deallens.core.errors import NotFoundError
from deallens.modules.vision import service
from deallens.modules.vision.schemas import PropertyConditionOut

router = APIRouter(tags=["vision"])


@router.get("/properties/{property_id}/condition", response_model=PropertyConditionOut)
async def get_condition(
    property_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> PropertyConditionOut:
    """The property's condition profile: per-system grades, red flags, coverage, renovation
    difficulty, confidence, and the priced rehab breakdown (03 §27.3/§27.4). 404 when the property
    has no photos analyzed yet."""
    result = await service.get_property_condition(db, property_id=property_id)
    if result is None:
        raise NotFoundError("No condition analysis for this property")
    return result


@router.post("/listings/{listing_id}/vision/analyze", response_model=PropertyConditionOut)
async def analyze_listing(
    listing_id: UUID,
    pipeline_version: str | None = None,
    _actor: Actor = Depends(get_current_actor),
) -> PropertyConditionOut:
    """Run the vision pipeline over a listing's photos and (re)compute its property condition +
    rehab (03 §27). Optionally pin a `pipeline_version` (model) — defaults to the current one.
    Writes shared data, so it runs on the owner-role session."""
    async with system_session() as db:
        result = await service.analyze_listing(db, listing_id=listing_id,
                                               pipeline_version=pipeline_version)
        await db.commit()
        return result
