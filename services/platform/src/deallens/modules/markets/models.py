"""GEO / markets ORM models — markets, market_stats, search_areas (§11.2 GEO group).

`markets`/`market_stats` are shared reference data (no `org_id`, no RLS — every tenant
sees the same market context). `search_areas` are user-drawn geometries and therefore
org-scoped with RLS. DDL + policies live in alembic/versions/0002_geo_schema.py; this file
is the ORM mirror, not the security source of truth (Postgres is — §11.1).
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deallens.core.db import Base
from deallens.core.ids import uuid7
from deallens.modules.identity.models import TimestampMixin


class MarketStatus(enum.StrEnum):
    """Lifecycle of a launch market (§14.5 offboarding runbook)."""

    ONBOARDING = "onboarding"  # 24-mo backfill + curve fit in progress, not user-visible
    ACTIVE = "active"
    PAUSED = "paused"  # feed hiccup / budget circuit-breaker; serves last-known-good
    OFFBOARDED = "offboarded"  # license terminated; display fields purged (§14.5)


class GeoLevel(enum.StrEnum):
    """Geographic hierarchy for market metrics (03 §28.2). Metrics are computed at the
    finest reliable level and inherited downward with source-level labeling — a crime
    figure carried at county level is *labeled* county-level, never faked to ZIP precision.
    """

    METRO = "metro"  # CBSA
    COUNTY = "county"
    CITY = "city"
    ZIP = "zip"
    TRACT = "tract"  # census tract


class Market(TimestampMixin, Base):
    """A launch metro (CBSA). Shared reference data. `display_policy` captures the union of
    per-source MLS display rules applied at serialization (§12.3, §14.1) — sold-price
    display, photo caching, attribution strings — so compliance is data, not scattered code.
    """

    __tablename__ = "markets"
    __table_args__ = (Index("ix_markets_boundary", "boundary", postgresql_using="gist"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    cbsa_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(2))
    status: Mapped[MarketStatus] = mapped_column(
        Enum(MarketStatus, name="market_status"), default=MarketStatus.ONBOARDING, index=True
    )
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    boundary: Mapped[WKBElement | None] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=False)
    )
    # Union of per-source license display rules for this market (§14.1 legal guardrails).
    display_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Engine/ingestion knobs versioned per market (closing_pct, vacancy, tax reassessment…).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stats: Mapped[list["MarketStat"]] = relationship(back_populates="market")


class MarketStat(TimestampMixin, Base):
    """A single (geo, metric, period) observation (03 §28.6). Scoring reads the latest
    closed period for L/M factors; the UI charts the series. Kept generic (metric as text,
    value as numeric) so the ~30-metric registry of §28.3 extends without migrations.
    """

    __tablename__ = "market_stats"
    __table_args__ = (
        Index(
            "ix_market_stats_lookup",
            "market_id",
            "geo_level",
            "geo_id",
            "metric",
            "period",
            unique=True,
        ),
        Index("ix_market_stats_metric_period", "metric", "period"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    market_id: Mapped[UUID] = mapped_column(
        ForeignKey("markets.id", ondelete="CASCADE"), index=True
    )
    geo_level: Mapped[GeoLevel] = mapped_column(Enum(GeoLevel, name="geo_level"))
    # Code at the given level: CBSA / FIPS county / city name / ZIP / tract GEOID.
    geo_id: Mapped[str] = mapped_column(String(32))
    metric: Mapped[str] = mapped_column(String(64))  # controlled vocab (03 §28.3)
    period: Mapped[date] = mapped_column(Date)  # closed period start (week/month/year)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source: Mapped[str] = mapped_column(String(64))  # tier/source key (02 §14.1)
    method_version: Mapped[str] = mapped_column(String(32), default="v1")

    market: Mapped[Market] = relationship(back_populates="stats")


class SearchArea(TimestampMixin, Base):
    """A user-drawn geometry defining where they hunt (§11.2 GEO / FR-002). Org-scoped, RLS;
    soft-deleted because it is product-visible (§11.1).
    """

    __tablename__ = "search_areas"
    __table_args__ = (
        Index("ix_search_areas_org_id", "org_id"),
        Index("ix_search_areas_geom", "geom", postgresql_using="gist"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    geom: Mapped[WKBElement] = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
