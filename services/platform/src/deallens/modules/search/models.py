"""SEARCH ORM model — `saved_searches` (ADR 0004).

A saved *view* for the Market Explorer (S11): a named filter+sort+scope the user returns to.
Distinct from `buy_boxes` (alerts.models), which carry alert channels/latency and are indexed
for the `ScoreUpdated` matcher — a saved search never alerts. Both share the `PropertyFilters`
contract (search.schemas), so promoting a saved search to a buy box is a field copy.

Org-scoped with RLS, soft-deleted because product-visible (§11.1). DDL + the tenant_isolation
policy live in alembic/versions/0006_search_schema.py; this file is the ORM mirror.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from deallens.core.db import Base
from deallens.core.enums import Strategy
from deallens.core.ids import uuid7
from deallens.core.sa_types import pg_enum
from deallens.modules.identity.models import TimestampMixin


class SavedSearch(TimestampMixin, Base):
    """A named explorer view (FR-001 saved-view management). `filters` stores a
    `PropertyFilters`; `sort` a `SortSpec`; `map_view` the viewport to restore. At most one
    row per user may be `is_default` (partial unique index below).
    """

    __tablename__ = "saved_searches"
    __table_args__ = (
        Index("ix_saved_searches_org_id", "org_id"),
        Index("ix_saved_searches_market_id", "market_id"),
        Index("ix_saved_searches_search_area_id", "search_area_id"),
        # One default view per user (ignoring soft-deleted rows). Also serves as the required
        # index on the user_id FK.
        Index(
            "uq_saved_searches_one_default",
            "user_id",
            unique=True,
            postgresql_where=text("is_default AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    search_area_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("search_areas.id", ondelete="SET NULL")
    )
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # PropertyFilters
    strategy: Mapped[Strategy | None] = mapped_column(pg_enum(Strategy, name="strategy"))
    sort: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # SortSpec
    map_view: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # MapView
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
