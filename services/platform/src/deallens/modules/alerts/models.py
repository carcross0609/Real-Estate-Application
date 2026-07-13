"""ALERTS ORM models (§11.3): buy_boxes, watchlist_items, notifications.

All org-scoped with RLS (USER-WORK group, §11.2). `notifications` is monthly range
partitioned by `created_at` (§11.6) with the RLS policy on the partitioned parent (Postgres
applies it to every partition). Confidence gates alerting: no instant alert fires below
confidence 0.5 (03 §25.1 #4) — enforced in the matcher, not the schema.

`alert_latency` reuses the enum type created in migration 0001 (identity) — its Python
mirror `AlertLatency` is imported rather than redeclared so the two never drift.
"""

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from deallens.core.db import Base
from deallens.core.enums import Strategy
from deallens.core.ids import uuid7
from deallens.modules.identity.models import AlertLatency, TimestampMixin


class AlertChannel(enum.StrEnum):
    """§11.3 buy_boxes.alert_channel[] delivery targets (§10 Resend/Web Push/Twilio)."""

    EMAIL = "email"
    IN_APP = "in_app"
    PUSH = "push"
    SMS = "sms"


class NotificationKind(enum.StrEnum):
    NEW_MATCH = "new_match"  # a buy-box matched a newly-scored property
    SCORE_CHANGE = "score_change"  # watched property's score moved
    PRICE_CHANGE = "price_change"
    STATUS_CHANGE = "status_change"
    DIGEST = "digest"  # hourly/daily roll-up
    REPORT_READY = "report_ready"  # async report render finished (§12.2 202+operation)
    SYSTEM = "system"


class NotificationStatus(enum.StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class BuyBox(TimestampMixin, Base):
    """A user's saved buy criteria (§11.3, FR-040). `filters` compiles to SQL predicates once
    per box version (§11.5 #6); `assumption_overrides` personalizes the engine for matching.
    Soft-deleted because product-visible (§11.1).
    """

    __tablename__ = "buy_boxes"
    __table_args__ = (
        Index("ix_buy_boxes_org_id", "org_id"),
        # The matcher fans out per market on ScoreUpdated — active boxes only (§11.5 #6).
        Index(
            "ix_buy_boxes_market_active",
            "market_id",
            postgresql_where=text("active AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # BuyBoxFilters
    strategy: Mapped[Strategy | None] = mapped_column(Enum(Strategy, name="strategy"))
    assumption_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    alert_channels: Mapped[list[AlertChannel]] = mapped_column(
        ARRAY(Enum(AlertChannel, name="alert_channel")), default=list
    )
    alert_latency: Mapped[AlertLatency] = mapped_column(
        Enum(AlertLatency, name="alert_latency"), default=AlertLatency.DAILY
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchlistItem(TimestampMixin, Base):
    """A property a user is tracking (§11.3, FR-041). One row per (user, property);
    soft-deleted because product-visible (§11.1).
    """

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "property_id", name="uq_watchlist_user_property"),
        Index("ix_watchlist_items_org_id", "org_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    """A dispatched alert (§11.3). Monthly range partitioned by `created_at` (§11.6); PK
    carries the partition key. `data` holds render context (property id, score delta) for the
    in-app SSE feed and email template.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_org_id", "org_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind, name="notification_kind"))
    channel: Mapped[AlertChannel] = mapped_column(Enum(AlertChannel, name="alert_channel"))
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status"),
        default=NotificationStatus.QUEUED,
    )
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    buy_box_id: Mapped[UUID | None] = mapped_column(ForeignKey("buy_boxes.id", ondelete="SET NULL"))
    property_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
