"""REPORTS + workspace ORM models (§11.3): reports, share_links, notes, pipeline_deals.

All org-scoped with RLS (USER-WORK group, §11.2). Reports are generated lazily on first
view and cached (§9.4); `context_pack` stores the grounded structured inputs the generator
saw (§13.3) so a report is reproducible and auditable. Public share pages are served by a
system/by-token path (like the Clerk webhook uses `system_session`), not the RLS app role —
these RLS policies govern *owner* management of the links, not anonymous read.
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deallens.core.db import Base
from deallens.core.ids import uuid7
from deallens.core.sa_types import pg_enum
from deallens.modules.identity.models import TimestampMixin


class ReportStatus(enum.StrEnum):
    """Lazy async generation lifecycle (§9.4, §12.2 202+operation)."""

    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class DealStage(enum.StrEnum):
    """Pipeline board columns (S20, FR pipeline board — Phase 3)."""

    LEAD = "lead"
    ANALYZING = "analyzing"
    OFFER = "offer"
    UNDER_CONTRACT = "under_contract"
    CLOSED = "closed"
    DEAD = "dead"


class Report(TimestampMixin, Base):
    """A generated property report (§11.3, FR-060). Narrative is grounded in `context_pack`
    and post-validated so no unsourced number appears (§13.3). `analysis_id`/`scenario_id`
    pin which engine output the prose describes.
    """

    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_org_property", "org_id", "property_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    analysis_id: Mapped[UUID | None] = mapped_column(ForeignKey("analyses.id", ondelete="SET NULL"))
    scenario_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), default="property")
    status: Mapped[ReportStatus] = mapped_column(
        pg_enum(ReportStatus, name="report_status"), default=ReportStatus.PENDING
    )
    context_pack: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # §13.3 grounding
    narrative: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # section → prose
    pdf_s3_key: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    share_links: Mapped[list["ShareLink"]] = relationship(back_populates="report")


class ShareLink(TimestampMixin, Base):
    """A public, revocable link to a report (§11.3, §15 Share & PDF surfaces). `token` is
    ≥128-bit random; default 90-day expiry, owner-extendable; `noindex` + per-token rate
    limits applied at render. `display_policy_market_id` selects the per-market MLS display
    rules stripped from the rendered page (§12.3 compliance-in-code).
    """

    __tablename__ = "share_links"
    __table_args__ = (
        Index("ix_share_links_token", "token", unique=True),
        Index("ix_share_links_org_id", "org_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    report_id: Mapped[UUID] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(64))
    display_policy_market_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("markets.id", ondelete="SET NULL")
    )
    view_count: Mapped[int] = mapped_column(BigInteger, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    report: Mapped[Report] = relationship(back_populates="share_links")


class Note(TimestampMixin, Base):
    """A free-text user annotation on a property / analysis / deal (§11.2 USER-WORK). Kept
    generic via (`target_type`, `target_id`) so it attaches anywhere without a wide FK set.
    Note bodies are user-authored input embedded in reports → an SSRF/PDF vector, so the
    renderer runs network-isolated (§15).
    """

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_target", "org_id", "target_type", "target_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    target_type: Mapped[str] = mapped_column(String(32))  # property | analysis | scenario | deal
    target_id: Mapped[UUID] = mapped_column()
    body: Mapped[str] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipelineDeal(TimestampMixin, Base):
    """A tracked deal on the user's pipeline board (§11.3, S20 — Phase 3). Kanban stage +
    ordering; links to the property and the analysis/scenario that justified pursuing it.
    """

    __tablename__ = "pipeline_deals"
    __table_args__ = (Index("ix_pipeline_deals_org_stage", "org_id", "stage"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    scenario_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(String(255))
    stage: Mapped[DealStage] = mapped_column(
        pg_enum(DealStage, name="deal_stage"), default=DealStage.LEAD
    )
    board_position: Mapped[int] = mapped_column(Integer, default=0)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    offer_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
