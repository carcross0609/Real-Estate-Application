"""property schema: data_sources, raw_records, properties, listings, listing_events
(monthly-partitioned), listing_photos (+ pgvector HNSW index), ownership_records,
tax_records, geo_layers (§11.2 PROPERTY + GEO/geo_layers). All shared reference data — no
`org_id`, no RLS. DML grants to `deallens_app` are inherited from 0001's ALTER DEFAULT
PRIVILEGES; the intended hardening is a dedicated read-only role on shared data (ADR 0002).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

data_source_tier = postgresql.ENUM(
    "mls", "property_data", "rental", "gov_open", "commercial_addon",
    name="data_source_tier", create_type=False,
)
property_type = postgresql.ENUM(
    "sfr", "condo", "townhome", "mf_2_4", "mf_5plus", "land", "commercial", "mixed",
    name="property_type", create_type=False,
)
listing_status = postgresql.ENUM(
    "active", "pending", "contingent", "sold", "withdrawn", "expired", "coming_soon",
    name="listing_status", create_type=False,
)
listing_event_type = postgresql.ENUM(
    "listed", "price_change", "status_change", "photos_change", "remarks_change",
    "back_on_market", "relisted_link", name="listing_event_type", create_type=False,
)
geo_layer_kind = postgresql.ENUM(
    "flood", "school", "crime", "walkability", "hazard", "development", "regulatory", "amenity",
    name="geo_layer_kind", create_type=False,
)
# geo_level already exists (created in 0002) — reference only, never re-create.
geo_level = postgresql.ENUM(name="geo_level", create_type=False)

_NEW_ENUMS = (data_source_tier, property_type, listing_status, listing_event_type, geo_layer_kind)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def _bootstrap_monthly_partitions(table: str) -> None:
    """Provision this month, one back, and three forward — mirrors audit_log (0001). A
    monthly maintenance job (pg_partman / Celery beat) keeps future partitions provisioned;
    the DEFAULT partition means a write never fails outright if that job lapses.
    """
    op.execute(f"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT")
    now = datetime.now(UTC)
    start_month = (now.year * 12 + (now.month - 1)) - 1
    for offset in range(5):
        month_index = start_month + offset
        year, month = divmod(month_index, 12)
        month += 1
        next_year, next_month = (year, month + 1) if month < 12 else (year + 1, 1)
        op.execute(
            f"CREATE TABLE {table}_{year:04d}_{month:02d} PARTITION OF {table} "
            f"FOR VALUES FROM ('{year:04d}-{month:02d}-01') "
            f"TO ('{next_year:04d}-{next_month:02d}-01')"
        )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _NEW_ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "data_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tier", data_source_tier, nullable=False),
        sa.Column("license_policy", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_timestamps(),
    )
    op.create_index("ix_data_sources_source_key", "data_sources", ["source_key"], unique=True)

    op.create_table(
        "raw_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("s3_key", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.LargeBinary, nullable=False),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_raw_records_fetched_at", "raw_records", ["fetched_at"])
    op.create_index("ix_raw_records_source_fetched", "raw_records", ["source_id", "fetched_at"])

    op.create_table(
        "properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("apn", sa.String(64)),
        sa.Column("fips", sa.String(5)),
        sa.Column("address_norm", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("geom", Geometry("POINT", srid=4326, spatial_index=False)),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
        ),
        sa.Column("property_type", property_type),
        sa.Column("beds", sa.SmallInteger),
        sa.Column("baths", sa.Numeric(3, 1)),
        sa.Column("sqft", sa.Integer),
        sa.Column("lot_sqft", sa.Integer),
        sa.Column("year_built", sa.SmallInteger),
        sa.Column("stories", sa.SmallInteger),
        sa.Column("garage_spaces", sa.SmallInteger),
        sa.Column("pool", sa.Boolean),
        sa.Column("hoa_monthly", sa.Numeric(14, 2)),
        sa.Column("zoning", sa.Text),
        sa.Column("attrs", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("resolution_confidence", sa.Numeric(4, 3)),
        *_timestamps(),
    )
    op.create_index("ix_properties_fips", "properties", ["fips"])
    op.create_index(
        "uq_properties_fips_apn",
        "properties",
        ["fips", "apn"],
        unique=True,
        postgresql_where=sa.text("apn IS NOT NULL"),
    )
    op.create_index("ix_properties_geom", "properties", ["geom"], postgresql_using="gist")
    op.create_index("ix_properties_market_type", "properties", ["market_id", "property_type"])

    op.create_table(
        "listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_listing_key", sa.String(128), nullable=False),
        sa.Column("status", listing_status, nullable=False),
        sa.Column("list_price", sa.Numeric(14, 2)),
        sa.Column("close_price", sa.Numeric(14, 2)),
        sa.Column("list_date", sa.Date),
        sa.Column("close_date", sa.Date),
        sa.Column("dom_current", sa.Integer),
        sa.Column("remarks", sa.Text),
        sa.Column("attribution", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("photo_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "raw_record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_records.id", ondelete="SET NULL"),
        ),
        sa.Column("source_ts", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("source_id", "source_listing_key", name="uq_listings_source_key"),
    )
    op.create_index("ix_listings_property_id", "listings", ["property_id"])
    op.create_index("ix_listings_status_list_date", "listings", ["status", "list_date"])

    # listing_events: monthly range partitioned by observed_at (§11.6). Raw DDL because
    # Alembic has no first-class PARTITION BY. "old"/"new" quoted (non-reserved but noisy).
    op.execute(
        """
        CREATE TABLE listing_events (
            id uuid NOT NULL,
            listing_id uuid NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
            event_type listing_event_type NOT NULL,
            "old" jsonb,
            "new" jsonb,
            source_ts timestamptz,
            observed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, observed_at)
        ) PARTITION BY RANGE (observed_at)
        """
    )
    op.execute(
        "CREATE INDEX ix_listing_events_listing ON listing_events (listing_id, observed_at)"
    )
    _bootstrap_monthly_partitions("listing_events")

    op.create_table(
        "listing_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source_url", sa.Text),
        sa.Column("s3_key", sa.Text),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("phash", sa.BigInteger),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("embedding", Vector(1024)),
        *_timestamps(),
        sa.UniqueConstraint("listing_id", "content_hash", name="uq_listing_photos_content_hash"),
    )
    op.create_index("ix_listing_photos_listing", "listing_photos", ["listing_id"])
    op.create_index("ix_listing_photos_phash", "listing_photos", ["phash"])
    # Approximate NN for photo near-dup + comp re-rank (§11.5). Cosine distance ops; index
    # build is instant on the empty table. Revisit ivfflat/dedicated store at >50M (§11.6).
    op.execute(
        "CREATE INDEX ix_listing_photos_embedding ON listing_photos "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "ownership_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_name", sa.Text),
        sa.Column("owner_mailing_address", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("owner_occupied", sa.Boolean),
        sa.Column("absentee", sa.Boolean),
        sa.Column("ownership_length_months", sa.Integer),
        sa.Column("last_sale_price", sa.Numeric(14, 2)),
        sa.Column("last_sale_date", sa.Date),
        sa.Column("deed_date", sa.Date),
        sa.Column("deed_type", sa.String(64)),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "raw_record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_records.id", ondelete="SET NULL"),
        ),
        *_timestamps(),
    )
    op.create_index("ix_ownership_records_property", "ownership_records", ["property_id"])

    op.create_table(
        "tax_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tax_year", sa.SmallInteger, nullable=False),
        sa.Column("assessed_value", sa.Numeric(14, 2)),
        sa.Column("land_value", sa.Numeric(14, 2)),
        sa.Column("improvement_value", sa.Numeric(14, 2)),
        sa.Column("annual_tax_amount", sa.Numeric(14, 2)),
        sa.Column("tax_rate", sa.Numeric(9, 6)),
        sa.Column("exemptions", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("reassessed_on_sale", sa.Boolean),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="SET NULL"),
        ),
        *_timestamps(),
    )
    op.create_index(
        "ix_tax_records_property_year", "tax_records", ["property_id", "tax_year"], unique=True
    )

    op.create_table(
        "geo_layers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", geo_layer_kind, nullable=False),
        sa.Column("geo_level", geo_level),
        sa.Column("geo_id", sa.String(32)),
        sa.Column("geom", Geometry("GEOMETRY", srid=4326, spatial_index=False)),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="SET NULL"),
        ),
        sa.Column("effective_date", sa.Date),
        sa.Column("method_version", sa.String(32), nullable=False, server_default="v1"),
        *_timestamps(),
    )
    op.create_index("ix_geo_layers_kind_geo", "geo_layers", ["kind", "geo_level", "geo_id"])
    op.create_index("ix_geo_layers_geom", "geo_layers", ["geom"], postgresql_using="gist")


def downgrade() -> None:
    for table in (
        "geo_layers",
        "tax_records",
        "ownership_records",
        "listing_photos",
        "listing_events",
        "listings",
        "properties",
        "raw_records",
        "data_sources",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    bind = op.get_bind()
    for enum_type in _NEW_ENUMS:
        enum_type.drop(bind, checkfirst=True)
