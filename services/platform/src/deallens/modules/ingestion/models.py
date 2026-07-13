"""PROPERTY-group ORM models (§11.2/§11.3): data_sources, raw_records, properties,
listings, listing_events, listing_photos.

All shared reference data (no `org_id`, no RLS): every tenant sees the same canonical
properties and listings — the product *is* this shared dataset. Anti-exfiltration is
enforced at the API/rate-limit layer (§15), not by row scoping. Writes happen only on the
ingestion worker path.

Ordering/concurrency correctness (§9.5) rides on `source_ts` (the feed
`ModificationTimestamp`): normalized upserts are conditional `WHERE stored_source_ts <
:new_ts`, so out-of-order and duplicate feed events no-op by construction.
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from deallens.core.db import Base
from deallens.core.ids import uuid7
from deallens.modules.identity.models import TimestampMixin


class DataSourceTier(enum.StrEnum):
    """§14.1 source tiers. Drives cadence and which fields a source may populate."""

    MLS = "mls"  # Tier A — RESO Web API aggregators
    PROPERTY_DATA = "property_data"  # Tier B — ATTOM/Estated
    RENTAL = "rental"  # Tier C — RentCast
    GOV_OPEN = "gov_open"  # Tier D — FEMA/Census/BLS/schools/crime
    COMMERCIAL_ADDON = "commercial_addon"  # Tier E — Walk Score/AirDNA-class [F]


class PropertyType(enum.StrEnum):
    """§11.3 property_type enum."""

    SFR = "sfr"
    CONDO = "condo"
    TOWNHOME = "townhome"
    MF_2_4 = "mf_2_4"
    MF_5PLUS = "mf_5plus"
    LAND = "land"
    COMMERCIAL = "commercial"
    MIXED = "mixed"


class ListingStatus(enum.StrEnum):
    """§11.3 listing status enum."""

    ACTIVE = "active"
    PENDING = "pending"
    CONTINGENT = "contingent"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    COMING_SOON = "coming_soon"


class ListingEventType(enum.StrEnum):
    """§11.3 immutable change-log event types (price/listing history)."""

    LISTED = "listed"
    PRICE_CHANGE = "price_change"
    STATUS_CHANGE = "status_change"
    PHOTOS_CHANGE = "photos_change"
    REMARKS_CHANGE = "remarks_change"
    BACK_ON_MARKET = "back_on_market"
    RELISTED_LINK = "relisted_link"  # entity-resolution links a new listing to a prior one


class DataSource(TimestampMixin, Base):
    """A licensed feed (§14.1). `license_policy` is the machine-readable contract enforced
    at serialization and at offboarding (§14.5): display rules, retention window, whether
    photo caching / sold-price display / AI photo processing are permitted, and termination
    obligations. A source we may ingest but not run vision on does not satisfy the Phase-0
    AI-processing gate (PRD §31 A4) — captured here, checked at market onboarding (S43).
    """

    __tablename__ = "data_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    source_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    tier: Mapped[DataSourceTier] = mapped_column(Enum(DataSourceTier, name="data_source_tier"))
    license_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RawRecord(TimestampMixin, Base):
    """Provenance: the untransformed payload persisted to S3 *before* normalization (§14.2),
    so a normalization bug never loses data and the pipeline is replayable from raw
    (NFR-05/NFR-08). Every normalized row points back here via `raw_record_id`.
    """

    __tablename__ = "raw_records"
    __table_args__ = (Index("ix_raw_records_source_fetched", "source_id", "fetched_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id", ondelete="RESTRICT"))
    s3_key: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[bytes] = mapped_column(LargeBinary)
    record_type: Mapped[str] = mapped_column(String(32))  # listing|photo|assessor|deed|…
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Property(TimestampMixin, Base):
    """Canonical physical asset — survives relistings (§11.3). One row per real-world
    parcel, resolved from feeds by entity resolution (FR-006); `resolution_confidence`
    records how sure that match is.
    """

    __tablename__ = "properties"
    __table_args__ = (
        # Assessor parcel number is unique within a county (FIPS) — but only where present.
        Index(
            "uq_properties_fips_apn",
            "fips",
            "apn",
            unique=True,
            postgresql_where=text("apn IS NOT NULL"),
        ),
        Index("ix_properties_geom", "geom", postgresql_using="gist"),
        Index("ix_properties_market_type", "market_id", "property_type"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    apn: Mapped[str | None] = mapped_column(String(64))
    fips: Mapped[str | None] = mapped_column(String(5), index=True)  # county FIPS
    address_norm: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # AddressNorm
    geom: Mapped[WKBElement | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False)
    )
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    property_type: Mapped[PropertyType | None] = mapped_column(
        Enum(PropertyType, name="property_type")
    )
    beds: Mapped[int | None] = mapped_column(SmallInteger)
    baths: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    sqft: Mapped[int | None] = mapped_column(Integer)
    lot_sqft: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(SmallInteger)
    stories: Mapped[int | None] = mapped_column(SmallInteger)
    garage_spaces: Mapped[int | None] = mapped_column(SmallInteger)
    pool: Mapped[bool | None] = mapped_column(Boolean)
    hoa_monthly: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    zoning: Mapped[str | None] = mapped_column(Text)
    # Long-tail source fields we don't promote to columns — never dropped (§14.2).
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    resolution_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    listings: Mapped[list["Listing"]] = relationship(back_populates="property")


class Listing(TimestampMixin, Base):
    """A marketing event for a property (§11.3). A property may be listed many times over
    the years; relisting chains are linked via `RELISTED_LINK` events so DOM and
    price-history views span the *true* time-on-market (§14.3).
    """

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source_id", "source_listing_key", name="uq_listings_source_key"),
        Index("ix_listings_property_id", "property_id"),
        Index("ix_listings_status_list_date", "status", "list_date"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    property_id: Mapped[UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id", ondelete="RESTRICT"))
    source_listing_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus, name="listing_status"))
    list_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    list_date: Mapped[date | None] = mapped_column(Date)
    close_date: Mapped[date | None] = mapped_column(Date)
    dom_current: Mapped[int | None] = mapped_column(Integer)
    remarks: Mapped[str | None] = mapped_column(Text)
    # Agent/broker attribution rendered for MLS display compliance (§11.3, §12.3).
    attribution: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    photo_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("raw_records.id", ondelete="SET NULL")
    )
    # Feed ModificationTimestamp — the version key for conditional upserts (§9.5).
    source_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped[Property] = relationship(back_populates="listings")
    photos: Mapped[list["ListingPhoto"]] = relationship(back_populates="listing")


class ListingEvent(Base):
    """Immutable field-level change log = price/listing history (§11.3). Monthly range
    partitioned by `observed_at` (§11.6); the PK carries the partition key. Never updated.
    """

    __tablename__ = "listing_events"
    __table_args__ = (Index("ix_listing_events_listing", "listing_id", "observed_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    event_type: Mapped[ListingEventType] = mapped_column(
        Enum(ListingEventType, name="listing_event_type")
    )
    old: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )


class ListingPhoto(TimestampMixin, Base):
    """A listing image (§11.3). Deduped two ways: `content_hash` (exact) and `phash`
    (perceptual near-dup — feeds recompress/resize on relisting, so exact hashing alone
    misses duplicates). `embedding` (voyage-multimodal-3, §10) is computed at ingest, before
    any LLM spend, so near-dups are skipped ahead of triage and it also serves comp re-rank.
    """

    __tablename__ = "listing_photos"
    __table_args__ = (
        UniqueConstraint("listing_id", "content_hash", name="uq_listing_photos_content_hash"),
        Index("ix_listing_photos_listing", "listing_id"),
        Index("ix_listing_photos_phash", "phash"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[str | None] = mapped_column(Text)
    s3_key: Mapped[str | None] = mapped_column(Text)  # only where cache-licensed
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    phash: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))

    listing: Mapped[Listing] = relationship(back_populates="photos")
