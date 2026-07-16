"""ALERTS module public interface (§9.3/§19) — buy-box + watchlist CRUD, buy-box matching,
notification creation/dispatch, and digest roll-ups (FR-040/FR-041).

Two entry surfaces, two trust levels:
- **CRUD** (`create_buy_box`, `add_watchlist_item`, `list_notifications`, …) serves the user's
  own request on the request-scoped session — every row is org/user filtered and RLS enforces
  it underneath. These are what `alerts.router` calls.
- **Automation** (`match_property`, `notify_watchers`, `dispatch_pending`, `build_digests`)
  runs in the worker on the owner session. It reads a freshly-scored property, evaluates it
  against the active boxes for its market (§11.5 #6), and writes system notifications. It never
  runs on behalf of one user — it fans a shared score update out to every user who asked for it.

Confidence gating (03 §25.1 #4) lives in the pure matcher; this file only supplies the floor
from settings and chooses instant-vs-digest delivery from the box's `alert_latency`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.config import get_settings
from deallens.core.enums import Strategy
from deallens.core.errors import NotFoundError
from deallens.core.logging import get_logger
from deallens.modules.alerts import buybox
from deallens.modules.alerts.channels import (
    LoggingDispatcher,
    NotificationDispatcher,
    OutboundMessage,
)
from deallens.modules.alerts.models import (
    AlertChannel,
    BuyBox,
    Notification,
    NotificationKind,
    NotificationStatus,
    WatchlistItem,
)
from deallens.modules.alerts.schemas import (
    BuyBoxCreate,
    BuyBoxFilters,
    BuyBoxUpdate,
    WatchlistItemCreate,
)
from deallens.modules.identity.models import AlertLatency, User
from deallens.modules.ingestion.models import Listing, Property
from deallens.modules.scoring.models import Score

_log = get_logger("alerts.service")

# Change kinds `notify_watchers` maps onto NotificationKind for a watched property.
_WATCH_KIND = {
    "price_change": NotificationKind.PRICE_CHANGE,
    "status_change": NotificationKind.STATUS_CHANGE,
    "score_change": NotificationKind.SCORE_CHANGE,
}


# --- Buy-box CRUD (request-scoped) ------------------------------------------------------


async def create_buy_box(
    db: AsyncSession, *, org_id: UUID, user_id: UUID, data: BuyBoxCreate
) -> BuyBox:
    box = BuyBox(
        org_id=org_id,
        user_id=user_id,
        name=data.name,
        market_id=data.market_id,
        filters=data.filters.model_dump(mode="json"),
        strategy=data.strategy,
        alert_channels=list(data.alert_channels),
        alert_latency=data.alert_latency,
        active=data.active,
    )
    db.add(box)
    await db.flush()
    return box


async def list_buy_boxes(db: AsyncSession, *, user_id: UUID) -> list[BuyBox]:
    rows = await db.execute(
        select(BuyBox)
        .where(BuyBox.user_id == user_id, BuyBox.deleted_at.is_(None))
        .order_by(BuyBox.created_at.desc())
    )
    return list(rows.scalars().all())


async def _get_owned_box(db: AsyncSession, *, box_id: UUID, user_id: UUID) -> BuyBox:
    row = await db.execute(
        select(BuyBox).where(
            BuyBox.id == box_id, BuyBox.user_id == user_id, BuyBox.deleted_at.is_(None)
        )
    )
    box = row.scalars().first()
    if box is None:
        raise NotFoundError("Buy box not found")
    return box


async def update_buy_box(
    db: AsyncSession, *, box_id: UUID, user_id: UUID, data: BuyBoxUpdate
) -> BuyBox:
    box = await _get_owned_box(db, box_id=box_id, user_id=user_id)
    fields = data.model_dump(exclude_unset=True)
    if "filters" in fields and data.filters is not None:
        box.filters = data.filters.model_dump(mode="json")
    for key in ("name", "market_id", "strategy", "alert_latency", "active"):
        if key in fields:
            setattr(box, key, fields[key])
    if "alert_channels" in fields and data.alert_channels is not None:
        box.alert_channels = list(data.alert_channels)
    await db.flush()
    return box


async def delete_buy_box(db: AsyncSession, *, box_id: UUID, user_id: UUID) -> None:
    box = await _get_owned_box(db, box_id=box_id, user_id=user_id)
    box.deleted_at = datetime.now(UTC)
    await db.flush()


# --- Watchlist CRUD (request-scoped) ----------------------------------------------------


async def add_watchlist_item(
    db: AsyncSession, *, org_id: UUID, user_id: UUID, data: WatchlistItemCreate
) -> WatchlistItem:
    """Idempotent add: re-watching a property (even one soft-removed) returns the live row with
    notes refreshed, honoring the (user, property) uniqueness constraint."""
    existing = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id, WatchlistItem.property_id == data.property_id
        )
    )
    item = existing.scalars().first()
    if item is not None:
        item.deleted_at = None
        if data.notes is not None:
            item.notes = data.notes
        await db.flush()
        return item
    item = WatchlistItem(
        org_id=org_id, user_id=user_id, property_id=data.property_id, notes=data.notes
    )
    db.add(item)
    await db.flush()
    return item


async def list_watchlist(db: AsyncSession, *, user_id: UUID) -> list[WatchlistItem]:
    rows = await db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.user_id == user_id, WatchlistItem.deleted_at.is_(None))
        .order_by(WatchlistItem.created_at.desc())
    )
    return list(rows.scalars().all())


async def remove_watchlist_item(db: AsyncSession, *, item_id: UUID, user_id: UUID) -> None:
    row = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.id == item_id,
            WatchlistItem.user_id == user_id,
            WatchlistItem.deleted_at.is_(None),
        )
    )
    item = row.scalars().first()
    if item is None:
        raise NotFoundError("Watchlist item not found")
    item.deleted_at = datetime.now(UTC)
    await db.flush()


# --- Notification reads (request-scoped) ------------------------------------------------


async def list_notifications(
    db: AsyncSession, *, user_id: UUID, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    stmt: Select[tuple[Notification]] = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = await db.execute(stmt)
    return list(rows.scalars().all())


async def mark_notification_read(
    db: AsyncSession, *, notification_id: UUID, user_id: UUID
) -> None:
    row = await db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
    )
    note = row.scalars().first()
    if note is None:
        raise NotFoundError("Notification not found")
    if note.read_at is None:
        note.read_at = datetime.now(UTC)
        note.status = NotificationStatus.READ
    await db.flush()


# --- Automation: buy-box matching (owner session) ---------------------------------------


@dataclass(slots=True)
class MatchSummary:
    property_id: UUID
    boxes_evaluated: int = 0
    boxes_matched: int = 0
    notifications_created: int = 0


async def _build_candidate(
    db: AsyncSession, *, property_id: UUID
) -> tuple[buybox.MatchCandidate, Property] | None:
    """Assemble the matcher's snapshot from the property, its latest listing, and its latest
    overall score. Returns None when the property is unscored — an unscored property can't
    match a quality-gated box and shouldn't fire an alert."""
    prop = await db.get(Property, property_id)
    if prop is None:
        return None

    listing = (
        await db.execute(
            select(Listing)
            .where(Listing.property_id == property_id)
            .order_by(Listing.list_date.desc().nullslast(), Listing.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    score = (
        await db.execute(
            select(Score)
            .where(Score.property_id == property_id, Score.strategy == Strategy.OVERALL)
            .order_by(Score.computed_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if score is None:
        return None

    candidate = buybox.MatchCandidate(
        property_id=str(property_id),
        list_price=listing.list_price if listing else None,
        beds=prop.beds,
        baths=prop.baths,
        sqft=prop.sqft,
        year_built=prop.year_built,
        property_type=prop.property_type,
        # search_area_ids left None: geo membership read not wired (see buybox.MatchCandidate).
        score=score.score,
        risk=score.risk_score,
        confidence=score.confidence_score,
        remarks=listing.remarks if listing else None,
    )
    return candidate, prop


async def match_property(
    db: AsyncSession,
    *,
    property_id: UUID,
    dispatcher: NotificationDispatcher | None = None,
) -> MatchSummary:
    """Evaluate a freshly-scored property against every active buy box for its market and
    create a NEW_MATCH notification per matching box (deduped against notifications already
    sent for that box×property). Instant-latency boxes that clear the confidence gate are
    dispatched now; the rest are left QUEUED for the next digest."""
    summary = MatchSummary(property_id=property_id)
    built = await _build_candidate(db, property_id=property_id)
    if built is None:
        return summary
    candidate, prop = built
    gate = Decimal(str(get_settings().alerts_confidence_gate))

    boxes = (
        await db.execute(
            select(BuyBox).where(
                BuyBox.active.is_(True),
                BuyBox.deleted_at.is_(None),
                # A null-market box matches everywhere; otherwise the property's market must match.
                (BuyBox.market_id == prop.market_id) | (BuyBox.market_id.is_(None)),
            )
        )
    ).scalars().all()

    for box in boxes:
        summary.boxes_evaluated += 1
        filters = BuyBoxFilters.model_validate(box.filters or {})
        outcome = buybox.evaluate(candidate, filters, confidence_gate=gate)
        if not outcome.matched:
            continue
        summary.boxes_matched += 1
        if await _already_notified(db, box_id=box.id, property_id=property_id):
            continue
        created = await _create_match_notifications(
            db, box=box, property_id=property_id, instant=outcome.instant_eligible,
            dispatcher=dispatcher,
        )
        summary.notifications_created += created

    await db.flush()
    return summary


async def _already_notified(db: AsyncSession, *, box_id: UUID, property_id: UUID) -> bool:
    """Has this box already alerted on this property? Keeps a re-score of the same listing from
    re-alerting (§9.5 idempotent fan-out) — a genuinely new opportunity is a new property."""
    row = await db.execute(
        select(Notification.id)
        .where(
            Notification.buy_box_id == box_id,
            Notification.property_id == property_id,
            Notification.kind == NotificationKind.NEW_MATCH,
        )
        .limit(1)
    )
    return row.first() is not None


async def _create_match_notifications(
    db: AsyncSession,
    *,
    box: BuyBox,
    property_id: UUID,
    instant: bool,
    dispatcher: NotificationDispatcher | None,
) -> int:
    """One notification row per configured channel (in-app is always included as the feed
    item). Instant + confidence-cleared boxes dispatch immediately; digest boxes stay QUEUED."""
    channels = box.alert_channels or [AlertChannel.IN_APP]
    title = f"New match for “{box.name}”"
    count = 0
    for channel in channels:
        note = Notification(
            org_id=box.org_id,
            user_id=box.user_id,
            kind=NotificationKind.NEW_MATCH,
            channel=channel,
            title=title,
            body=None,
            data={"property_id": str(property_id), "buy_box": box.name},
            buy_box_id=box.id,
            property_id=property_id,
        )
        db.add(note)
        count += 1
        if instant and box.alert_latency == AlertLatency.INSTANT:
            await _dispatch(db, note, box_user=box.user_id, dispatcher=dispatcher)
    return count


# --- Automation: watched-property change notifications ----------------------------------


async def notify_watchers(
    db: AsyncSession,
    *,
    property_id: UUID,
    change: str,
    detail: dict[str, object] | None = None,
    dispatcher: NotificationDispatcher | None = None,
) -> int:
    """Notify every user watching a property of a price/status/score change (FR-041). Watch
    alerts are always instant in-app — a watched property is one the user already asked to hear
    about, so the confidence gate (which guards *unsolicited* buy-box alerts) doesn't apply."""
    kind = _WATCH_KIND.get(change)
    if kind is None:
        return 0
    watchers = (
        await db.execute(
            select(WatchlistItem).where(
                WatchlistItem.property_id == property_id, WatchlistItem.deleted_at.is_(None)
            )
        )
    ).scalars().all()

    title = {
        NotificationKind.PRICE_CHANGE: "Price change on a watched property",
        NotificationKind.STATUS_CHANGE: "Status change on a watched property",
        NotificationKind.SCORE_CHANGE: "Score change on a watched property",
    }[kind]

    count = 0
    for item in watchers:
        note = Notification(
            org_id=item.org_id,
            user_id=item.user_id,
            kind=kind,
            channel=AlertChannel.IN_APP,
            title=title,
            body=None,
            data={"property_id": str(property_id), **(detail or {})},
            property_id=property_id,
        )
        db.add(note)
        await _dispatch(db, note, box_user=item.user_id, dispatcher=dispatcher)
        count += 1
    await db.flush()
    return count


# --- Dispatch + digests (owner session) -------------------------------------------------


async def _recipient(db: AsyncSession, *, user_id: UUID, channel: AlertChannel) -> str:
    """The delivery address for a channel. Email/SMS resolve from the user row; in-app/push
    target the user id (the SSE feed / device registry keys on it)."""
    if channel in (AlertChannel.IN_APP, AlertChannel.PUSH):
        return str(user_id)
    user = await db.get(User, user_id)
    return user.email if user and channel == AlertChannel.EMAIL else str(user_id)


async def _dispatch(
    db: AsyncSession,
    note: Notification,
    *,
    box_user: UUID,
    dispatcher: NotificationDispatcher | None,
) -> None:
    """Send one notification and stamp its delivery outcome. A transport failure marks the row
    FAILED (surfaced to ops) rather than raising — one undeliverable SMS can't sink a batch."""
    disp = dispatcher or LoggingDispatcher()
    to = await _recipient(db, user_id=box_user, channel=note.channel)
    result = await disp.send(
        OutboundMessage(channel=note.channel, to=to, title=note.title, body=note.body)
    )
    now = datetime.now(UTC)
    if result.delivered:
        note.status = NotificationStatus.SENT
        note.sent_at = now
    else:
        note.status = NotificationStatus.FAILED
        note.data = {**note.data, "dispatch_error": result.error or "unknown"}


async def dispatch_pending(
    db: AsyncSession, *, limit: int = 500, dispatcher: NotificationDispatcher | None = None
) -> int:
    """Send every QUEUED notification (the backstop for anything not dispatched inline — a
    failed instant send, an in-app row). Idempotent: only QUEUED rows are picked up."""
    rows = (
        await db.execute(
            select(Notification)
            .where(Notification.status == NotificationStatus.QUEUED)
            .order_by(Notification.created_at)
            .limit(limit)
        )
    ).scalars().all()
    count = 0
    for note in rows:
        await _dispatch(db, note, box_user=note.user_id, dispatcher=dispatcher)
        count += 1
    await db.flush()
    return count


async def build_digests(
    db: AsyncSession, *, latency: AlertLatency, dispatcher: NotificationDispatcher | None = None
) -> int:
    """Roll every QUEUED NEW_MATCH notification for boxes at this `latency` up into one DIGEST
    per user, dispatch the digest, and mark the rolled-up rows SENT. This is what turns a burst
    of hourly/daily matches into a single "12 new matches" email instead of twelve pings."""
    box_ids = (
        await db.execute(
            select(BuyBox.id).where(
                BuyBox.alert_latency == latency,
                BuyBox.active.is_(True),
                BuyBox.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not box_ids:
        return 0

    pending = (
        await db.execute(
            select(Notification).where(
                Notification.status == NotificationStatus.QUEUED,
                Notification.kind == NotificationKind.NEW_MATCH,
                Notification.buy_box_id.in_(box_ids),
                Notification.channel != AlertChannel.IN_APP,  # in-app is already the feed item
            )
        )
    ).scalars().all()

    by_user: dict[UUID, list[Notification]] = {}
    for note in pending:
        by_user.setdefault(note.user_id, []).append(note)

    digests = 0
    for user_id, notes in by_user.items():
        org_id = notes[0].org_id
        channel = notes[0].channel
        digest = Notification(
            org_id=org_id,
            user_id=user_id,
            kind=NotificationKind.DIGEST,
            channel=channel,
            title=f"{len(notes)} new {latency.value} match{'es' if len(notes) != 1 else ''}",
            body=None,
            data={"match_count": len(notes),
                  "property_ids": [str(n.property_id) for n in notes if n.property_id]},
        )
        db.add(digest)
        await _dispatch(db, digest, box_user=user_id, dispatcher=dispatcher)
        for note in notes:  # fold the individual rows into the digest
            note.status = NotificationStatus.SENT
            note.sent_at = digest.sent_at
        digests += 1

    await db.flush()
    return digests


__all__ = [
    "MatchSummary",
    "add_watchlist_item",
    "build_digests",
    "create_buy_box",
    "delete_buy_box",
    "dispatch_pending",
    "list_buy_boxes",
    "list_notifications",
    "list_watchlist",
    "mark_notification_read",
    "match_property",
    "notify_watchers",
    "remove_watchlist_item",
    "update_buy_box",
]
