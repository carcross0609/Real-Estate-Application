"""analysis schema: photo_analyses, property_conditions, comp_sets, comp_members,
valuations, analyses, scenarios, scores, score_factors (§11.2 ANALYSIS + scoring).

Scoping:
- Shared (no RLS): photo_analyses, property_conditions, valuations, analyses, scores,
  score_factors.
- OR-NULL RLS (shared when system-generated, org-owned when user pins/excludes comps):
  comp_sets, comp_members.
- Org-scoped RLS: scenarios.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

room_type = postgresql.ENUM(
    "kitchen", "bath", "bedroom", "living", "exterior_front", "exterior_rear", "roof",
    "garage", "basement", "yard", "utility", "floorplan", "other",
    name="room_type", create_type=False,
)
comp_set_kind = postgresql.ENUM("sale", "rental", name="comp_set_kind", create_type=False)
comp_created_by = postgresql.ENUM("system", "user", name="comp_created_by", create_type=False)
valuation_kind = postgresql.ENUM(
    "arv", "as_is", "rent_ltr", "rent_str", name="valuation_kind", create_type=False
)
# strategy is created here (first use) and reused by buy_boxes in 0005.
strategy = postgresql.ENUM(
    "flip", "ltr", "brrrr", "str", "house_hack", "wholesale", "multifamily", "land",
    "commercial", "value_add", "overall", name="strategy", create_type=False,
)
recommendation = postgresql.ENUM(
    "strong_buy", "buy", "hold_watch", "pass", name="recommendation", create_type=False
)

_NEW_ENUMS = (
    room_type, comp_set_kind, comp_created_by, valuation_kind, strategy, recommendation
)

# Org-scoped tables here (standard tenant_isolation policy, as in 0001).
_ORG_SCOPED = ("scenarios",)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def _computed_at() -> sa.Column:
    return sa.Column(
        "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _NEW_ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "photo_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "photo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listing_photos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pipeline_version", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("room_type", room_type),
        sa.Column("condition_grade", sa.SmallInteger),
        sa.Column("findings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("red_flags", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("quality", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("tokens_in", sa.Integer),
        sa.Column("tokens_out", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(8, 5)),
        *_timestamps(),
        sa.UniqueConstraint("photo_id", "pipeline_version", name="uq_photo_analyses_version"),
        sa.CheckConstraint(
            "condition_grade IS NULL OR condition_grade BETWEEN 1 AND 5",
            name="ck_photo_analyses_grade_range",
        ),
    )
    op.create_index("ix_photo_analyses_photo", "photo_analyses", ["photo_id"])
    op.create_index("ix_photo_analyses_room_type", "photo_analyses", ["room_type"])

    op.create_table(
        "property_conditions",
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "as_of_listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="SET NULL"),
        ),
        sa.Column("pipeline_version", sa.String(32), nullable=False),
        sa.Column("kitchen_grade", sa.SmallInteger),
        sa.Column("bath_grade", sa.SmallInteger),
        sa.Column("flooring_grade", sa.SmallInteger),
        sa.Column("exterior_grade", sa.SmallInteger),
        sa.Column("roof_grade", sa.SmallInteger),
        sa.Column("landscaping_grade", sa.SmallInteger),
        sa.Column("renovation_difficulty", sa.SmallInteger),
        sa.Column("red_flags", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("cosmetic_repair_low", sa.Numeric(14, 2)),
        sa.Column("cosmetic_repair_high", sa.Numeric(14, 2)),
        sa.Column("major_repair_low", sa.Numeric(14, 2)),
        sa.Column("major_repair_high", sa.Numeric(14, 2)),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("coverage", postgresql.JSONB, nullable=False, server_default="{}"),
        _computed_at(),
        *_timestamps(),
        sa.CheckConstraint(
            "renovation_difficulty IS NULL OR renovation_difficulty BETWEEN 1 AND 5",
            name="ck_property_conditions_reno_range",
        ),
    )

    op.create_table(
        "comp_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subject_property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", comp_set_kind, nullable=False),
        sa.Column("created_by", comp_created_by, nullable=False, server_default="system"),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE")
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("params", postgresql.JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
        sa.CheckConstraint(
            "(created_by = 'user') = (org_id IS NOT NULL)", name="ck_comp_sets_user_scoped"
        ),
    )
    op.create_index("ix_comp_sets_subject", "comp_sets", ["subject_property_id", "kind"])
    op.create_index("ix_comp_sets_org_id", "comp_sets", ["org_id"])

    op.create_table(
        "comp_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "comp_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("comp_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "comp_property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "comp_listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE")
        ),
        sa.Column("distance_m", sa.Numeric(10, 2)),
        sa.Column("similarity", sa.Numeric(6, 5)),
        sa.Column("adjustments", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("included", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("excluded_reason", sa.Text),
        *_timestamps(),
    )
    op.create_index("ix_comp_members_set", "comp_members", ["comp_set_id"])
    op.create_index("ix_comp_members_org_id", "comp_members", ["org_id"])

    op.create_table(
        "valuations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", valuation_kind, nullable=False),
        sa.Column("point", sa.Numeric(14, 2)),
        sa.Column("low", sa.Numeric(14, 2)),
        sa.Column("high", sa.Numeric(14, 2)),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("method", sa.Text),
        sa.Column(
            "comp_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("comp_sets.id", ondelete="SET NULL"),
        ),
        sa.Column("model_version", sa.String(32), nullable=False, server_default="v1"),
        _computed_at(),
        *_timestamps(),
    )
    op.create_index(
        "ix_valuations_property_kind", "valuations", ["property_id", "kind", "computed_at"]
    )

    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="SET NULL"),
        ),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("assumption_set", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("strategy", strategy, nullable=False),
        sa.Column("outputs", postgresql.JSONB, nullable=False, server_default="{}"),
        _computed_at(),
        *_timestamps(),
        sa.CheckConstraint("strategy <> 'overall'", name="ck_analyses_no_overall"),
    )
    op.create_index(
        "ix_analyses_property_strategy", "analyses", ["property_id", "strategy", "computed_at"]
    )

    op.create_table(
        "scenarios",
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
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="SET NULL"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("assumption_set", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("strategy", strategy, nullable=False),
        sa.Column("outputs", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        _computed_at(),
        *_timestamps(),
    )
    op.create_index("ix_scenarios_org_property", "scenarios", ["org_id", "property_id"])

    op.create_table(
        "scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
        ),
        sa.Column("strategy", strategy, nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("grade", sa.String(2), nullable=False),
        sa.Column("risk_score", sa.Numeric(5, 2)),
        sa.Column("confidence_score", sa.Numeric(5, 2)),
        sa.Column("recommendation", recommendation),
        sa.Column("winning_strategy", strategy),
        sa.Column("scoring_version", sa.String(32), nullable=False),
        _computed_at(),
        *_timestamps(),
    )
    op.create_index("ix_scores_top25", "scores", ["market_id", "strategy", "score"])
    op.create_index(
        "ix_scores_property_strategy", "scores", ["property_id", "strategy", "computed_at"]
    )
    op.create_index("ix_scores_computed_at", "scores", ["computed_at"])

    op.create_table(
        "score_factors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "score_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("factor_key", sa.String(64), nullable=False),
        sa.Column("factor_group", sa.String(1), nullable=False),
        sa.Column("raw_value", sa.Numeric(18, 6)),
        sa.Column("percentile", sa.Numeric(6, 3)),
        sa.Column("weight", sa.Numeric(6, 5)),
        sa.Column("contribution", sa.Numeric(8, 4)),
        sa.Column("rationale", sa.Text),
        sa.Column("capped_by", sa.String(64)),
    )
    op.create_index("ix_score_factors_score", "score_factors", ["score_id"])

    # --- RLS ---
    # scenarios: standard org-scoped isolation.
    for table in _ORG_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            """
        )

    # comp_sets / comp_members: shared when org_id IS NULL (system-generated), org-owned
    # otherwise. USING admits both; WITH CHECK forbids a user writing a NULL-org (system)
    # row — system rows are inserted by the owner role, which bypasses RLS (core/db.py).
    for table in ("comp_sets", "comp_members"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (
                org_id IS NULL
                OR org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            )
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            """
        )


def downgrade() -> None:
    for table in (
        "score_factors",
        "scores",
        "scenarios",
        "analyses",
        "valuations",
        "comp_members",
        "comp_sets",
        "property_conditions",
        "photo_analyses",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    bind = op.get_bind()
    for enum_type in _NEW_ENUMS:
        enum_type.drop(bind, checkfirst=True)
