"""geo schema: postgis/pgvector extensions, markets, market_stats, search_areas
(§11.2 GEO group). `markets`/`market_stats` are shared reference data; `search_areas` are
user-drawn geometries → org-scoped with RLS (§11.1).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# `create_type=False` so a column referencing the enum never re-emits CREATE TYPE inside
# CREATE TABLE — the type is created exactly once by the explicit `.create()` below. This
# keeps `alembic upgrade --sql` output runnable and makes cross-migration reuse (e.g.
# `geo_level` reused by 0003, `strategy` by 0004/0005) safe.
market_status = postgresql.ENUM(
    "onboarding", "active", "paused", "offboarded", name="market_status", create_type=False
)
# geo_level is created here (first use) and reused by geo_layers/enrichment in 0003.
geo_level = postgresql.ENUM(
    "metro", "county", "city", "zip", "tract", name="geo_level", create_type=False
)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()
    # Superpowers of the one database (§10). Extensions are owner-created; `deallens_app`
    # gets USAGE on the resulting types by the PUBLIC default (types are world-usable).
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    market_status.create(bind, checkfirst=True)
    geo_level.create(bind, checkfirst=True)

    op.create_table(
        "markets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cbsa_code", sa.String(12), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("status", market_status, nullable=False, server_default="onboarding"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="America/New_York"),
        sa.Column("boundary", Geometry("MULTIPOLYGON", srid=4326, spatial_index=False)),
        sa.Column("display_policy", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("launched_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_markets_cbsa_code", "markets", ["cbsa_code"], unique=True)
    op.create_index("ix_markets_status", "markets", ["status"])
    op.create_index("ix_markets_boundary", "markets", ["boundary"], postgresql_using="gist")

    op.create_table(
        "market_stats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("geo_level", geo_level, nullable=False),
        sa.Column("geo_id", sa.String(32), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric(18, 6)),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("method_version", sa.String(32), nullable=False, server_default="v1"),
        *_timestamps(),
    )
    op.create_index("ix_market_stats_market_id", "market_stats", ["market_id"])
    op.create_index(
        "ix_market_stats_lookup",
        "market_stats",
        ["market_id", "geo_level", "geo_id", "metric", "period"],
        unique=True,
    )
    op.create_index("ix_market_stats_metric_period", "market_stats", ["metric", "period"])

    op.create_table(
        "search_areas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("geom", Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_search_areas_org_id", "search_areas", ["org_id"])
    op.create_index("ix_search_areas_geom", "search_areas", ["geom"], postgresql_using="gist")

    # search_areas is org-owned → RLS, same pattern as identity's org-scoped tables (0001).
    op.execute("ALTER TABLE search_areas ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON search_areas
        USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    for table in ("search_areas", "market_stats", "markets"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    bind = op.get_bind()
    for enum_type in (geo_level, market_status):
        enum_type.drop(bind, checkfirst=True)
    # Extensions are left installed on downgrade — other schemas may depend on them and
    # dropping postgis is destructive; removal is a manual DBA action if ever needed.
