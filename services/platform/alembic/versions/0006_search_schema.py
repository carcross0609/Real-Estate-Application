"""search schema: saved_searches (org-scoped, RLS) + a listings recency index supporting the
current-listing DISTINCT ON in the search read path (ADR 0004).

`saved_searches` are named Market-Explorer views — distinct from `buy_boxes` (which alert).
The `strategy` enum already exists (created in 0004); referenced by name only here.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# strategy (0004) already exists — reference only, never re-create.
strategy = postgresql.ENUM(name="strategy", create_type=False)


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
    op.create_table(
        "saved_searches",
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
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "search_area_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("search_areas.id", ondelete="SET NULL"),
        ),
        sa.Column("filters", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("strategy", strategy),
        sa.Column("sort", postgresql.JSONB),
        sa.Column("map_view", postgresql.JSONB),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_saved_searches_org_id", "saved_searches", ["org_id"])
    op.create_index("ix_saved_searches_market_id", "saved_searches", ["market_id"])
    op.create_index("ix_saved_searches_search_area_id", "saved_searches", ["search_area_id"])
    # At most one default view per user (soft-deleted rows excluded). Doubles as the user_id
    # FK index.
    op.create_index(
        "uq_saved_searches_one_default",
        "saved_searches",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )

    # search_areas is org-owned → standard tenant_isolation RLS (identical to 0002/0005).
    op.execute("ALTER TABLE saved_searches ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON saved_searches
        USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )

    # Search read path (ADR 0004): the current-listing projection is
    # `DISTINCT ON (property_id) … ORDER BY property_id, list_date DESC, source_ts DESC`.
    # This composite lets Postgres satisfy that with an index scan instead of a sort.
    op.create_index(
        "ix_listings_property_recency",
        "listings",
        ["property_id", sa.text("list_date DESC"), sa.text("source_ts DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_listings_property_recency", table_name="listings")
    op.execute("DROP TABLE IF EXISTS saved_searches CASCADE")
