"""Alerts HTTP surface (S30 Alerts Center, S31 Watchlist, buy-box management).

Every endpoint is a user-owned surface, so all run on the actor-scoped RLS session
(`get_actor_db`) — tenant isolation is Postgres's job, and the service filters by the actor's
`user_id`/`org_id` on top. The *automation* side of alerts (matching a new score, dispatching)
is not exposed here — it runs only in the worker (`deallens.worker.tasks.alerts`).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_current_actor
from deallens.modules.alerts import schemas, service

router = APIRouter(tags=["alerts"])


# --- Buy boxes (FR-040) -----------------------------------------------------------------


@router.post(
    "/buy-boxes", response_model=schemas.BuyBoxOut, status_code=status.HTTP_201_CREATED
)
async def create_buy_box(
    body: schemas.BuyBoxCreate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.BuyBoxOut:
    box = await service.create_buy_box(
        db, org_id=actor.require_org(), user_id=actor.user_id, data=body
    )
    return schemas.BuyBoxOut.model_validate(box)


@router.get("/buy-boxes", response_model=list[schemas.BuyBoxOut])
async def list_buy_boxes(
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> list[schemas.BuyBoxOut]:
    boxes = await service.list_buy_boxes(db, user_id=actor.user_id)
    return [schemas.BuyBoxOut.model_validate(b) for b in boxes]


@router.patch("/buy-boxes/{box_id}", response_model=schemas.BuyBoxOut)
async def update_buy_box(
    box_id: UUID,
    body: schemas.BuyBoxUpdate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.BuyBoxOut:
    box = await service.update_buy_box(db, box_id=box_id, user_id=actor.user_id, data=body)
    return schemas.BuyBoxOut.model_validate(box)


@router.delete("/buy-boxes/{box_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_buy_box(
    box_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> Response:
    await service.delete_buy_box(db, box_id=box_id, user_id=actor.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Watchlist (FR-041) -----------------------------------------------------------------


@router.post(
    "/watchlist", response_model=schemas.WatchlistItemOut, status_code=status.HTTP_201_CREATED
)
async def add_watchlist_item(
    body: schemas.WatchlistItemCreate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.WatchlistItemOut:
    item = await service.add_watchlist_item(
        db, org_id=actor.require_org(), user_id=actor.user_id, data=body
    )
    return schemas.WatchlistItemOut.model_validate(item)


@router.get("/watchlist", response_model=list[schemas.WatchlistItemOut])
async def list_watchlist(
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> list[schemas.WatchlistItemOut]:
    items = await service.list_watchlist(db, user_id=actor.user_id)
    return [schemas.WatchlistItemOut.model_validate(i) for i in items]


@router.delete("/watchlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watchlist_item(
    item_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> Response:
    await service.remove_watchlist_item(db, item_id=item_id, user_id=actor.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Notifications (S30) ----------------------------------------------------------------


@router.get("/notifications", response_model=list[schemas.NotificationOut])
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> list[schemas.NotificationOut]:
    notes = await service.list_notifications(
        db, user_id=actor.user_id, unread_only=unread_only, limit=limit
    )
    return [schemas.NotificationOut.model_validate(n) for n in notes]


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> Response:
    await service.mark_notification_read(
        db, notification_id=notification_id, user_id=actor.user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
