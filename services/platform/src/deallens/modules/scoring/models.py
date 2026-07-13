"""SCORING ORM models (§11.3): scores, score_factors.

Shared reference data (no RLS). `market_id` is denormalized onto `scores` for the Top-25
composite index `(market_id, strategy, score desc)` (§11.5 query #1). `score_factors` is
the explainability ledger (03 §25.3): one row per named factor with its raw value, market
percentile, weight, and signed contribution — plus `capped_by` when a hard gate (§25.6) is
the binding constraint, because trust requires showing the cap, not hiding it in weights.
"""

import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from deallens.core.db import Base
from deallens.core.enums import Strategy
from deallens.core.ids import uuid7
from deallens.modules.identity.models import TimestampMixin


class Recommendation(enum.StrEnum):
    """§25.5 rule-layer over (score, risk, confidence)."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD_WATCH = "hold_watch"
    PASS = "pass"


class Score(TimestampMixin, Base):
    """A per-(property, strategy) score with risk/confidence/grade/recommendation (§11.3,
    §25.5). `strategy = overall` is the max across the user's enabled strategy scores with
    the winning strategy named (§25.4). Confidence is separate from score — a thin-data
    A-deal is "A, low confidence," never downgraded (§25.1 #4); confidence gates *alerting*.
    """

    __tablename__ = "scores"
    __table_args__ = (
        # The Top-25 query: latest score per (market, strategy) ordered desc (§11.5).
        Index("ix_scores_top25", "market_id", "strategy", "score"),
        Index("ix_scores_property_strategy", "property_id", "strategy", "computed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    listing_id: Mapped[UUID | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    strategy: Mapped[Strategy] = mapped_column(Enum(Strategy, name="strategy"))
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    grade: Mapped[str] = mapped_column(String(2))  # A+ … F within market-window distribution
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # 0–100, higher=riskier
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # 0–100
    recommendation: Mapped[Recommendation | None] = mapped_column(
        Enum(Recommendation, name="recommendation")
    )
    # For `overall`: which strategy won the max (§25.4). NULL for single-strategy scores.
    winning_strategy: Mapped[Strategy | None] = mapped_column(Enum(Strategy, name="strategy"))
    scoring_version: Mapped[str] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    factors: Mapped[list["ScoreFactor"]] = relationship(back_populates="score")


class ScoreFactor(Base):
    """The explanation ledger row (§25.3). `contribution = weight · normalized_value`;
    summing contributions reconstructs the score, which is what "show the math" renders.
    """

    __tablename__ = "score_factors"
    __table_args__ = (Index("ix_score_factors_score", "score_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    score_id: Mapped[UUID] = mapped_column(ForeignKey("scores.id", ondelete="CASCADE"))
    factor_key: Mapped[str] = mapped_column(String(64))  # e.g. flip_net_margin (§25.3)
    factor_group: Mapped[str] = mapped_column(String(1))  # F | D | C | L | M
    raw_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    percentile: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))  # normalized 0–100
    weight: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    contribution: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    rationale: Mapped[str | None] = mapped_column(Text)
    capped_by: Mapped[str | None] = mapped_column(String(64))  # hard-gate id (§25.6), if any

    score: Mapped[Score] = relationship(back_populates="factors")
