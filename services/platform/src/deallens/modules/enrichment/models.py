"""ENRICHMENT ORM models (§11.2): ownership_records, tax_records, geo_layers.

Shared reference data (no `org_id`, no RLS). `ownership_records`/`tax_records` come from
Tier-B public data (ATTOM); `geo_layers` from Tier-D gov/open sources (FEMA/NCES/FBI CDE).
Both feed L/M scoring factors (03 §25.3, §28) and the engine pro-forma (03 §26.4 — actual
assessor taxes, reassessed-at-purchase where the jurisdiction table says so).
"""

import enum
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from deallens.core.db import Base
from deallens.core.ids import uuid7
from deallens.core.sa_types import pg_enum
from deallens.modules.identity.models import TimestampMixin
from deallens.modules.markets.models import GeoLevel


class GeoLayerKind(enum.StrEnum):
    """§28.3 enrichment domains attached spatially or by geo id."""

    FLOOD = "flood"  # FEMA NFHL zone
    SCHOOL = "school"  # NCES/GreatSchools assignment-zone ratings
    CRIME = "crime"  # FBI CDE + city open data, finest legal level
    WALKABILITY = "walkability"  # Walk Score / OSM POIs [F]
    HAZARD = "hazard"  # wildfire/wind/insurance-cost tables
    DEVELOPMENT = "development"  # permits, planned projects
    REGULATORY = "regulatory"  # STR rules, rent control, landlord-tenant class
    AMENITY = "amenity"


class OwnershipRecord(TimestampMixin, Base):
    """Assessor/deed-derived ownership (§14.1 Tier B). Absentee + long-hold flags feed the
    off-market signal pack (Phase 3) and the D-group `motivated_seller_signals` factor.
    """

    __tablename__ = "ownership_records"
    __table_args__ = (Index("ix_ownership_records_property", "property_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    owner_name: Mapped[str | None] = mapped_column(Text)
    owner_mailing_address: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    owner_occupied: Mapped[bool | None] = mapped_column(Boolean)
    absentee: Mapped[bool | None] = mapped_column(Boolean)
    ownership_length_months: Mapped[int | None] = mapped_column(Integer)
    last_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    last_sale_date: Mapped[date | None] = mapped_column(Date)
    deed_date: Mapped[date | None] = mapped_column(Date)
    deed_type: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    raw_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("raw_records.id", ondelete="SET NULL")
    )


class TaxRecord(TimestampMixin, Base):
    """Assessor tax roll (§14.1 Tier B). `reassessed_on_sale` captures the jurisdiction rule
    the engine needs to avoid the classic underwriting trap of using the seller's
    grandfathered tax basis (03 §26.4).
    """

    __tablename__ = "tax_records"
    __table_args__ = (
        Index("ix_tax_records_property_year", "property_id", "tax_year", unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    tax_year: Mapped[int] = mapped_column(SmallInteger)
    assessed_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    land_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    improvement_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    annual_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    exemptions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reassessed_on_sale: Mapped[bool | None] = mapped_column(Boolean)
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )


class GeoLayer(TimestampMixin, Base):
    """A spatial/geo-keyed enrichment layer (§11.2 GEO). Either a polygon (`geom`, e.g. FEMA
    flood zone) or a value keyed to a geography (`geo_level`/`geo_id`, e.g. crime index by
    county). `attributes` holds the layer-specific payload; scoring reads the current row
    for a property's containing geography.
    """

    __tablename__ = "geo_layers"
    __table_args__ = (
        Index("ix_geo_layers_kind_geo", "kind", "geo_level", "geo_id"),
        Index("ix_geo_layers_geom", "geom", postgresql_using="gist"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"))
    kind: Mapped[GeoLayerKind] = mapped_column(pg_enum(GeoLayerKind, name="geo_layer_kind"))
    geo_level: Mapped[GeoLevel | None] = mapped_column(pg_enum(GeoLevel, name="geo_level"))
    geo_id: Mapped[str | None] = mapped_column(String(32))
    geom: Mapped[WKBElement | None] = mapped_column(
        Geometry("GEOMETRY", srid=4326, spatial_index=False)
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    effective_date: Mapped[date | None] = mapped_column(Date)
    method_version: Mapped[str] = mapped_column(String(32), default="v1")
