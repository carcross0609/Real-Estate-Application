"""ENGINE ORM models (§11.3): comp_sets, comp_members, valuations, analyses, scenarios.

Mixed scoping:
- `valuations`, `analyses` are system-computed shared data (no RLS).
- `comp_sets`/`comp_members` are shared when system-generated (`org_id IS NULL`) but
  org-owned when a user pins/excludes comps (FR-015) — an OR-NULL RLS policy admits both
  (see migration 0004). This faithfully implements §11.3's `created_by (system|user)`.
- `scenarios` are user-saved analyzer states — fully org-scoped with RLS (§11.3).

Every stored engine output embeds full traceability (§26.9): `engine_version`, the complete
`assumption_set` snapshot, and the `comp_set_id`, so any number renders its own derivation.
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from deallens.core.db import Base
from deallens.core.enums import Strategy
from deallens.core.ids import uuid7
from deallens.core.sa_types import pg_enum
from deallens.modules.identity.models import TimestampMixin


class CompSetKind(enum.StrEnum):
    SALE = "sale"  # sold comps → ARV / as-is
    RENTAL = "rental"  # rental comps → market rent


class CompCreatedBy(enum.StrEnum):
    SYSTEM = "system"
    USER = "user"


class ValuationKind(enum.StrEnum):
    """§11.3 valuation kinds."""

    ARV = "arv"  # after-repair value (renovated comps)
    AS_IS = "as_is"  # current-condition value
    RENT_LTR = "rent_ltr"  # long-term monthly rent
    RENT_STR = "rent_str"  # short-term revenue [F]


class CompSet(TimestampMixin, Base):
    """A selected set of comparables for a subject property (§11.3, §26.1). System sets are
    shared; user sets (pin/exclude) carry `org_id`/`user_id`. `params` records the selection
    filters (radius, recency, sqft band) for reproducibility.
    """

    __tablename__ = "comp_sets"
    __table_args__ = (
        CheckConstraint(
            "(created_by = 'user') = (org_id IS NOT NULL)",
            name="ck_comp_sets_user_scoped",
        ),
        Index("ix_comp_sets_subject", "subject_property_id", "kind"),
        Index("ix_comp_sets_org_id", "org_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    subject_property_id: Mapped[UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE")
    )
    kind: Mapped[CompSetKind] = mapped_column(pg_enum(CompSetKind, name="comp_set_kind"))
    created_by: Mapped[CompCreatedBy] = mapped_column(
        pg_enum(CompCreatedBy, name="comp_created_by"), default=CompCreatedBy.SYSTEM
    )
    org_id: Mapped[UUID | None] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    members: Mapped[list["CompMember"]] = relationship(back_populates="comp_set")


class CompMember(TimestampMixin, Base):
    """One comparable within a comp set (§11.3). `adjustments` holds the line-item ±$ grid
    (26.1); `included`/`excluded_reason` capture user pin/exclude. `org_id` mirrors the
    parent set so RLS scopes user sets without a join.
    """

    __tablename__ = "comp_members"
    __table_args__ = (
        Index("ix_comp_members_set", "comp_set_id"),
        Index("ix_comp_members_org_id", "org_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    comp_set_id: Mapped[UUID] = mapped_column(ForeignKey("comp_sets.id", ondelete="CASCADE"))
    comp_property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    comp_listing_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL")
    )
    # Denormalized from the parent set for RLS (NULL = shared/system set).
    org_id: Mapped[UUID | None] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    adjustments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    excluded_reason: Mapped[str | None] = mapped_column(Text)

    comp_set: Mapped[CompSet] = relationship(back_populates="members")


class Valuation(TimestampMixin, Base):
    """A versioned point-plus-interval estimate (§11.3, §26.1). Ranking uses the
    conservative quantile of these (ARV/rent at P30) per the winner's-curse mitigation
    (03 §25.8); the property page shows the full P10/P50/P90 band.
    """

    __tablename__ = "valuations"
    __table_args__ = (Index("ix_valuations_property_kind", "property_id", "kind", "computed_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    kind: Mapped[ValuationKind] = mapped_column(pg_enum(ValuationKind, name="valuation_kind"))
    point: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    low: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    high: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    method: Mapped[str | None] = mapped_column(Text)  # comp-based | model-prior | fallback
    comp_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("comp_sets.id", ondelete="SET NULL")
    )
    model_version: Mapped[str] = mapped_column(String(32), default="v1")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Analysis(TimestampMixin, Base):
    """A deterministic engine run under system assumptions (§11.3, §26). `outputs` is the
    full 03 §26 output block (pro-forma, financing scenarios, return metrics as P10/P50/P90).
    `strategy` cannot be `overall` (that is a scoring construct, not an engine run) —
    enforced by CHECK.
    """

    __tablename__ = "analyses"
    __table_args__ = (
        CheckConstraint("strategy <> 'overall'", name="ck_analyses_no_overall"),
        Index("ix_analyses_property_strategy", "property_id", "strategy", "computed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    listing_id: Mapped[UUID | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))
    engine_version: Mapped[str] = mapped_column(String(32))
    assumption_set: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # full snapshot
    strategy: Mapped[Strategy] = mapped_column(pg_enum(Strategy, name="strategy"))
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # EngineOutputBlock
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Scenario(TimestampMixin, Base):
    """A user-saved analyzer state (§11.3): the same shape as `analyses` plus org/user/name/
    notes and RLS. Users personalize assumptions (FR-013) and keep named what-ifs; org-scoped
    and soft-deleted because it is product-visible (§11.1).
    """

    __tablename__ = "scenarios"
    __table_args__ = (Index("ix_scenarios_org_property", "org_id", "property_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    listing_id: Mapped[UUID | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(String(32))
    assumption_set: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    strategy: Mapped[Strategy] = mapped_column(pg_enum(Strategy, name="strategy"))
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
