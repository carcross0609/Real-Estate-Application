"""Search HTTP surface — the S11 Market Explorer + S33 Markets Manager backend.

Read endpoints (`/properties`, `/tiles`) serve the shared property dataset and need only an
authenticated actor. The user-owned surfaces (`/search-areas`, `/saved-searches`,
`/me/search-preferences`) run on the actor-scoped RLS session (`get_actor_db`), so tenant
isolation is Postgres's job. Search/tile endpoints accept the query as a POST body: the filter
set is large and nested (polygons, status lists) — awkward and cache-poisoning as a query
string, and these are `private, no-store` reads anyway (§12.2), not CDN-cached GETs.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_current_actor
from deallens.modules.search import schemas, service

router = APIRouter(tags=["search"])


# --- Property search & tiles ------------------------------------------------------------


@router.post("/properties/search", response_model=schemas.SearchPage)
async def search_properties(
    body: schemas.PropertySearchQuery,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SearchPage:
    """Keyset-paginated property search (S11). Default sort is strategy score, descending —
    "ranked by profit, not price" (§21). Follow `next_cursor` for subsequent pages.
    """
    return await service.search_properties(db, body)


@router.post("/properties/count", response_model=schemas.CountEstimate)
async def count_properties(
    body: schemas.PropertySearchQuery,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.CountEstimate:
    """A capped match count for the result header ("1000+"); exact counts are deliberately not
    offered (NFR-01)."""
    return await service.count_matches(db, body)


@router.post("/tiles/{z}/{x}/{y}", response_model=schemas.TileResponse)
async def map_tile(
    z: int,
    x: int,
    y: int,
    body: schemas.PropertySearchQuery,
    response: Response,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.TileResponse:
    """One map tile of pins/clusters for `{z}/{x}/{y}`, filtered by the same query body as
    search. Payload carries only id + point + coarse buckets (§12.2 anti-exfiltration); full
    attributes require a per-property call. `X-Tile-Truncated: 1` signals the pin cap was hit.
    """
    tile, truncated = await service.tile(db, z=z, x=x, y=y, q=body)
    response.headers["Cache-Control"] = "private, no-store"
    if truncated:
        response.headers["X-Tile-Truncated"] = "1"
    return tile


# --- Saved searches (S11 saved views) ---------------------------------------------------


@router.post(
    "/saved-searches",
    response_model=schemas.SavedSearchOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_saved_search(
    body: schemas.SavedSearchCreate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SavedSearchOut:
    saved = await service.create_saved_search(
        db, org_id=actor.require_org(), user_id=actor.user_id, body=body
    )
    return schemas.SavedSearchOut.model_validate(saved)


@router.get("/saved-searches", response_model=list[schemas.SavedSearchOut])
async def list_saved_searches(
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> list[schemas.SavedSearchOut]:
    saved = await service.list_saved_searches(db, user_id=actor.user_id)
    return [schemas.SavedSearchOut.model_validate(s) for s in saved]


@router.patch("/saved-searches/{saved_id}", response_model=schemas.SavedSearchOut)
async def update_saved_search(
    saved_id: UUID,
    body: schemas.SavedSearchUpdate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SavedSearchOut:
    saved = await service.update_saved_search(
        db, saved_id=saved_id, user_id=actor.user_id, body=body
    )
    return schemas.SavedSearchOut.model_validate(saved)


@router.delete("/saved-searches/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_search(
    saved_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> None:
    await service.delete_saved_search(db, saved_id=saved_id)


# --- Search areas (FR-001, S33 Markets Manager) -----------------------------------------


@router.post(
    "/search-areas", response_model=schemas.SearchAreaOut, status_code=status.HTTP_201_CREATED
)
async def create_search_area(
    body: schemas.SearchAreaCreate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SearchAreaOut:
    """Draw/define a search area (FR-001). Enforces the plan's area cap server-side."""
    return await service.create_search_area(
        db, org_id=actor.require_org(), user_id=actor.user_id, body=body
    )


@router.get("/search-areas", response_model=list[schemas.SearchAreaOut])
async def list_search_areas(
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> list[schemas.SearchAreaOut]:
    return await service.list_search_areas(db, org_id=actor.require_org())


@router.get("/search-areas/{area_id}", response_model=schemas.SearchAreaOut)
async def get_search_area(
    area_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SearchAreaOut:
    return await service.get_search_area(db, area_id=area_id)


@router.patch("/search-areas/{area_id}", response_model=schemas.SearchAreaOut)
async def update_search_area(
    area_id: UUID,
    body: schemas.SearchAreaUpdate,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SearchAreaOut:
    return await service.update_search_area(db, area_id=area_id, body=body)


@router.delete("/search-areas/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search_area(
    area_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> None:
    await service.delete_search_area(db, area_id=area_id)


# --- User search preferences ------------------------------------------------------------


@router.get("/me/search-preferences", response_model=schemas.SearchPreferences)
async def get_search_preferences(
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SearchPreferences:
    return await service.get_search_preferences(db, user_id=actor.user_id)


@router.put("/me/search-preferences", response_model=schemas.SearchPreferences)
async def set_search_preferences(
    body: schemas.SearchPreferences,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.SearchPreferences:
    return await service.set_search_preferences(db, user_id=actor.user_id, prefs=body)
