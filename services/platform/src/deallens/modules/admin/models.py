"""ADMIN / OPS ORM models (§11.3): ingestion_runs, dq_flags, model_versions,
prompt_versions, ai_calls, feedback_labels.

Mostly shared ops data (no RLS) read by the internal console. `ai_calls` is monthly range
partitioned by `created_at` (§11.6) — it is the highest-volume table and the source of the
AI cost dashboard (§13, §18.1). `feedback_labels` is the one org-scoped table here: it is
user-submitted (FR-027) so it carries `org_id`/`user_id` + RLS; the eval flywheel reads it
via the admin/system path (§13.5), never as an end-user request.
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from deallens.core.db import Base
from deallens.core.ids import uuid7
from deallens.modules.identity.models import TimestampMixin


class IngestionRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # some tasks failed / DLQ'd but run completed
    FAILED = "failed"


class DqSeverity(enum.StrEnum):
    """§14.4 DQ rule severities."""

    AUTO_FIX = "auto_fix"
    SERVE_WITH_FLAG = "serve_with_flag"
    SUPPRESS = "suppress"  # suppress-pending-review


class DqStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class AiCallPurpose(enum.StrEnum):
    """Task classes for model routing + cost attribution (§13.4, §13.6)."""

    TRIAGE = "triage"  # haiku photo triage
    CONDITION = "condition"  # sonnet condition analysis
    REPORT = "report"  # sonnet narrative generation
    CHAT = "chat"  # property-scoped Q&A [F]
    EMBEDDING = "embedding"  # voyage-multimodal-3
    OTHER = "other"


class FeedbackSubject(enum.StrEnum):
    """§11.3 feedback_labels.subject — the eval-flywheel correction target (FR-027)."""

    PHOTO = "photo"
    CONDITION = "condition"
    REHAB = "rehab"
    RENT = "rent"
    ARV = "arv"


class IngestionRun(TimestampMixin, Base):
    """One adapter run (§14.2). Counts + lag feed the ops dashboard (S40) and the coverage
    watchdog (§14.4): a >2% gap vs. source-reported totals pages ops.
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingestion_runs_source_started", "source_id", "started_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    status: Mapped[IngestionRunStatus] = mapped_column(
        Enum(IngestionRunStatus, name="ingestion_run_status"), default=IngestionRunStatus.RUNNING
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_upserted: Mapped[int] = mapped_column(Integer, default=0)
    lag_seconds: Mapped[int | None] = mapped_column(Integer)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class DqFlag(TimestampMixin, Base):
    """A data-quality violation (§14.4). Generic (`subject_type`, `subject_id`) so any
    normalized row can be flagged; `severity` decides whether it auto-fixes, serves with a
    flag, or is suppressed pending review. Populates the DQ queue (S42).
    """

    __tablename__ = "dq_flags"
    __table_args__ = (
        Index("ix_dq_flags_subject", "subject_type", "subject_id"),
        Index("ix_dq_flags_status_severity", "status", "severity"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    subject_type: Mapped[str] = mapped_column(String(32))  # property | listing | photo | …
    subject_id: Mapped[UUID] = mapped_column()
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    rule: Mapped[str] = mapped_column(String(64))
    severity: Mapped[DqSeverity] = mapped_column(Enum(DqSeverity, name="dq_severity"))
    status: Mapped[DqStatus] = mapped_column(
        Enum(DqStatus, name="dq_status"), default=DqStatus.OPEN
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelVersion(TimestampMixin, Base):
    """The deployed model routing registry (§13.4): task → model → fallback, swappable
    without deploy. `active` marks the current selection for a `kind`.
    """

    __tablename__ = "model_versions"
    __table_args__ = (Index("ix_model_versions_kind_active", "kind", "active"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[str] = mapped_column(String(32))  # vision_triage | vision_condition | report | …
    model_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32), default="anthropic")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class PromptVersion(TimestampMixin, Base):
    """Maps a deployed prompt asset to its semver (§13.2). Prompts are code: versioned,
    changelogged, eval-gated; every `ai_calls` row references one of these.
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (Index("ix_prompt_versions_name_semver", "name", "semver", unique=True),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(64))
    semver: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str | None] = mapped_column(String(64))
    changelog: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiCall(Base):
    """Every model invocation (§11.3, §13). Monthly range partitioned by `created_at`
    (§11.6); PK carries the partition key. The AI cost dashboard and per-property budget
    reconcile against actuals here (§13.6, §18.1), replacing the planning estimates.
    """

    __tablename__ = "ai_calls"
    __table_args__ = (
        Index("ix_ai_calls_purpose_created", "purpose", "created_at"),
        Index("ix_ai_calls_subject", "subject_type", "subject_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    purpose: Mapped[AiCallPurpose] = mapped_column(Enum(AiCallPurpose, name="ai_call_purpose"))
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[UUID | None] = mapped_column()
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )


class FeedbackLabel(TimestampMixin, Base):
    """A user correction feeding the eval flywheel (§11.3, FR-027, §13.5). Org-scoped with
    RLS (user-submitted); recalibration reads these offline, human-approved before deploy —
    no silent self-training.
    """

    __tablename__ = "feedback_labels"
    __table_args__ = (
        Index("ix_feedback_labels_org", "org_id"),
        Index("ix_feedback_labels_subject", "subject", "subject_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    subject: Mapped[FeedbackSubject] = mapped_column(Enum(FeedbackSubject, name="feedback_subject"))
    subject_id: Mapped[UUID] = mapped_column()
    submitted_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
