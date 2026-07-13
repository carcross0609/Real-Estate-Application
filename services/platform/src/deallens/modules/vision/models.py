"""VISION ORM models (§11.3): photo_analyses, property_conditions.

Shared reference data (no `org_id`, no RLS). Output schemas are specified in 03 §27.2
(per-photo) and §27.3 (property aggregate). Grades are 1–5; coverage gaps are explicit —
no roof photo ⇒ roof grade is *unknown* (NULL), never silently averaged to "average"
(§27.1). `findings`/`red_flags` are validated against the Pydantic models in `schemas.py`
before persistence (§20: LLM outputs are Pydantic at the boundary).
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from deallens.core.db import Base
from deallens.core.ids import uuid7
from deallens.core.sa_types import pg_enum
from deallens.modules.identity.models import TimestampMixin


class RoomType(enum.StrEnum):
    """13 room classes (03 §27.2). `UTILITY` reconciles §27.2's "13 classes" with the 12
    named in §11.3 and matches the stage-2 selection set in §13.1 (kitchen/bath/exterior/
    roof/utility prioritized; bedrooms sampled).
    """

    KITCHEN = "kitchen"
    BATH = "bath"
    BEDROOM = "bedroom"
    LIVING = "living"
    EXTERIOR_FRONT = "exterior_front"
    EXTERIOR_REAR = "exterior_rear"
    ROOF = "roof"
    GARAGE = "garage"
    BASEMENT = "basement"
    YARD = "yard"
    UTILITY = "utility"  # mechanicals: HVAC / water heater / panel
    FLOORPLAN = "floorplan"
    OTHER = "other"


class PhotoAnalysis(TimestampMixin, Base):
    """One row per photo × pipeline version (§11.3). Re-running a newer pipeline appends a
    row rather than mutating history, so eval/audit can diff versions. `cost_usd` +
    `tokens_*` feed the per-property AI budget and the cost dashboard (§13.6, mirrors the
    coarser `ai_calls` ledger).
    """

    __tablename__ = "photo_analyses"
    __table_args__ = (
        UniqueConstraint("photo_id", "pipeline_version", name="uq_photo_analyses_version"),
        CheckConstraint(
            "condition_grade IS NULL OR condition_grade BETWEEN 1 AND 5",
            name="ck_photo_analyses_grade_range",
        ),
        Index("ix_photo_analyses_photo", "photo_id"),
        Index("ix_photo_analyses_room_type", "room_type"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    photo_id: Mapped[UUID] = mapped_column(ForeignKey("listing_photos.id", ondelete="CASCADE"))
    pipeline_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(64))
    room_type: Mapped[RoomType | None] = mapped_column(pg_enum(RoomType, name="room_type"))
    condition_grade: Mapped[int | None] = mapped_column(SmallInteger)  # 1–5, see CHECK
    findings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # PhotoFindings
    red_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # [RedFlag]
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # PhotoQuality
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))


class PropertyCondition(TimestampMixin, Base):
    """Property-level aggregate, current (§11.3, §27.3). One row per property (PK = FK).
    Deterministic roll-up of `photo_analyses`: median grade per system, max-pooled red-flag
    severity, coverage map. `coverage` drives `confidence` and the degraded-state banners
    (§27.5) — below 0.4 the analyzer shows an "estimate from limited photos" notice.
    """

    __tablename__ = "property_conditions"
    __table_args__ = (
        CheckConstraint(
            "renovation_difficulty IS NULL OR renovation_difficulty BETWEEN 1 AND 5",
            name="ck_property_conditions_reno_range",
        ),
    )

    property_id: Mapped[UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), primary_key=True
    )
    as_of_listing_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL")
    )
    pipeline_version: Mapped[str] = mapped_column(String(32))
    kitchen_grade: Mapped[int | None] = mapped_column(SmallInteger)
    bath_grade: Mapped[int | None] = mapped_column(SmallInteger)
    flooring_grade: Mapped[int | None] = mapped_column(SmallInteger)
    exterior_grade: Mapped[int | None] = mapped_column(SmallInteger)
    roof_grade: Mapped[int | None] = mapped_column(SmallInteger)
    landscaping_grade: Mapped[int | None] = mapped_column(SmallInteger)
    renovation_difficulty: Mapped[int | None] = mapped_column(SmallInteger)  # 1–5, see CHECK
    red_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    cosmetic_repair_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cosmetic_repair_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    major_repair_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    major_repair_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    # Which room classes had usable photos — drives confidence (§27.3, PRD §21.9).
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
