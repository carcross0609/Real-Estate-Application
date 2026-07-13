"""Identity module ORM models — users, orgs, org_members, org_invites, subscriptions,
entitlements, api_keys, audit_log. See docs/02-TECHNICAL-DESIGN.md §11.2/§11.3.

RLS policies for the org-scoped tables here live in
alembic/versions/0001_identity_schema.py — this file is the SQLAlchemy-side mirror of that
DDL, not the source of truth for security policy (Postgres is).
"""

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from deallens.core.db import Base
from deallens.core.ids import uuid7
from deallens.core.sa_types import pg_enum


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# Programmatic-access key prefix (§15 "API keys scoped + metered"). Defined here — the
# lowest module in the identity import graph — so both `service` (mints keys) and
# `core.auth` (authenticates them) reference one constant without importing each other.
API_KEY_PREFIX = "dlk"


class OrgRole(enum.StrEnum):
    """§16.2 role hierarchy, org-scoped. Ordered owner > admin > analyst > viewer."""

    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class ApiScope(enum.StrEnum):
    """The closed vocabulary of capabilities an API key may be granted (§16.2 layer 1, but
    for the programmatic surface: keys authorize via *scopes*, humans via *roles*). Keys are
    validated against this set at creation, so a typo'd scope fails loudly instead of
    silently granting nothing. Add a value here the moment a programmatic endpoint needs it.
    """

    PROPERTIES_READ = "properties:read"
    ANALYSES_READ = "analyses:read"
    ANALYSES_WRITE = "analyses:write"
    ORG_READ = "org:read"


class SubscriptionPlan(enum.StrEnum):
    BASIC = "basic"
    PRO = "pro"
    TEAM = "team"


class SubscriptionStatus(enum.StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class AlertLatency(enum.StrEnum):
    INSTANT = "instant"
    HOURLY = "hourly"
    DAILY = "daily"


class InviteStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class User(TimestampMixin, Base):
    """A person. Identity is owned by Clerk; this is our shadow row (§16.1) — we never
    depend on Clerk being reachable to render data we already have.
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    clerk_user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)

    # FR-053: strategy preferences, default assumption set, notification channels, theme.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # FR-053: account deletion performs data erasure per privacy policy (30-day grace).
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["OrgMember"]] = relationship(back_populates="user")


class Org(TimestampMixin, Base):
    __tablename__ = "orgs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    clerk_org_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # FR-052: owner can lock the assumption set workspace-wide.
    locked_assumption_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    members: Mapped[list["OrgMember"]] = relationship(back_populates="org")


class OrgMember(TimestampMixin, Base):
    __tablename__ = "org_members"
    __table_args__ = (Index("ix_org_members_org_user", "org_id", "user_id", unique=True),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[OrgRole] = mapped_column(pg_enum(OrgRole, name="org_role"), default=OrgRole.VIEWER)

    org: Mapped[Org] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class OrgInvite(TimestampMixin, Base):
    """A pending invitation by email (FR-052). Becomes an `OrgMember` row on accept."""

    __tablename__ = "org_invites"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[OrgRole] = mapped_column(pg_enum(OrgRole, name="org_role"), default=OrgRole.VIEWER)
    invited_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    # >=128-bit random token (§15 "Share & PDF surfaces" convention applied here too).
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[InviteStatus] = mapped_column(
        pg_enum(InviteStatus, name="invite_status"), default=InviteStatus.PENDING
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Subscription(TimestampMixin, Base):
    """Stripe-backed billing state; `Entitlement` is computed/cached from this (§16.2)."""

    __tablename__ = "subscriptions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), unique=True, index=True
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    plan: Mapped[SubscriptionPlan] = mapped_column(
        pg_enum(SubscriptionPlan, name="subscription_plan"), default=SubscriptionPlan.BASIC
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        pg_enum(SubscriptionStatus, name="subscription_status"), default=SubscriptionStatus.TRIALING
    )
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Entitlement(TimestampMixin, Base):
    """Plan-derived limits, cached with the session per §16.2. Recomputed by `billing` on
    every subscription change — never hand-edited outside that flow.
    """

    __tablename__ = "entitlements"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), unique=True, index=True
    )
    markets_limit: Mapped[int] = mapped_column(Integer, default=1)
    seats: Mapped[int] = mapped_column(Integer, default=1)
    alert_latency: Mapped[AlertLatency] = mapped_column(
        pg_enum(AlertLatency, name="alert_latency"), default=AlertLatency.DAILY
    )
    exports_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pipeline_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    api_access: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApiKey(TimestampMixin, Base):
    """Programmatic access (§6.8 "API access" [L], §15 "API keys scoped + metered").

    Only `key_hash` (SHA-256 of the full secret) is ever stored; the plaintext secret is
    shown once at creation and cannot be recovered.
    """

    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(255))
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Auth events, admin actions, impersonation, entitlement changes (§15).

    Partitioned monthly by `created_at` per §11.6 — see the migration for partition DDL and
    the ops runbook note on keeping future partitions provisioned.
    """

    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID | None] = mapped_column(ForeignKey("orgs.id", ondelete="SET NULL"))
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(128), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )
