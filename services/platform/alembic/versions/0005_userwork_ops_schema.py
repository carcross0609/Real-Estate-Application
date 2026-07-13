"""user-work + ops schema: buy_boxes, watchlist_items, notifications (partitioned),
reports, share_links, notes, pipeline_deals, feedback_labels (org-scoped, RLS);
ingestion_runs, dq_flags, model_versions, prompt_versions, ai_calls (partitioned) — shared
ops (§11.2 USER-WORK + OPS).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-13
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

alert_channel = postgresql.ENUM(
    "email", "in_app", "push", "sms", name="alert_channel", create_type=False
)
notification_kind = postgresql.ENUM(
    "new_match", "score_change", "price_change", "status_change", "digest", "report_ready",
    "system", name="notification_kind", create_type=False,
)
notification_status = postgresql.ENUM(
    "queued", "sent", "delivered", "read", "failed", name="notification_status", create_type=False
)
report_status = postgresql.ENUM(
    "pending", "generating", "ready", "failed", name="report_status", create_type=False
)
deal_stage = postgresql.ENUM(
    "lead", "analyzing", "offer", "under_contract", "closed", "dead",
    name="deal_stage", create_type=False,
)
ingestion_run_status = postgresql.ENUM(
    "running", "succeeded", "partial", "failed", name="ingestion_run_status", create_type=False
)
dq_severity = postgresql.ENUM(
    "auto_fix", "serve_with_flag", "suppress", name="dq_severity", create_type=False
)
dq_status = postgresql.ENUM("open", "resolved", "suppressed", name="dq_status", create_type=False)
ai_call_purpose = postgresql.ENUM(
    "triage", "condition", "report", "chat", "embedding", "other",
    name="ai_call_purpose", create_type=False,
)
feedback_subject = postgresql.ENUM(
    "photo", "condition", "rehab", "rent", "arv", name="feedback_subject", create_type=False
)

# strategy (0004) and alert_latency (0001) already exist — reference only.
strategy = postgresql.ENUM(name="strategy", create_type=False)
alert_latency = postgresql.ENUM(name="alert_latency", create_type=False)

_NEW_ENUMS = (
    alert_channel, notification_kind, notification_status, report_status, deal_stage,
    ingestion_run_status, dq_severity, dq_status, ai_call_purpose, feedback_subject,
)

# Org-scoped tables → standard tenant_isolation RLS. `notifications` is partitioned but the
# policy is enabled on the parent (Postgres applies it to every partition).
_ORG_SCOPED = (
    "buy_boxes", "watchlist_items", "notifications", "reports", "share_links", "notes",
    "pipeline_deals", "feedback_labels",
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


def _bootstrap_monthly_partitions(table: str) -> None:
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
        "buy_boxes",
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
        sa.Column("filters", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("strategy", strategy),
        sa.Column("assumption_overrides", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "alert_channels",
            postgresql.ARRAY(alert_channel),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("alert_latency", alert_latency, nullable=False, server_default="daily"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_buy_boxes_org_id", "buy_boxes", ["org_id"])
    op.create_index(
        "ix_buy_boxes_market_active",
        "buy_boxes",
        ["market_id"],
        postgresql_where=sa.text("active AND deleted_at IS NULL"),
    )

    op.create_table(
        "watchlist_items",
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
        sa.Column("notes", sa.Text),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "property_id", name="uq_watchlist_user_property"),
    )
    op.create_index("ix_watchlist_items_org_id", "watchlist_items", ["org_id"])

    # notifications: monthly range partitioned by created_at (§11.6). FKs allowed from a
    # partitioned table to regular tables. RLS enabled on the parent (below).
    op.execute(
        """
        CREATE TABLE notifications (
            id uuid NOT NULL,
            org_id uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind notification_kind NOT NULL,
            channel alert_channel NOT NULL,
            status notification_status NOT NULL DEFAULT 'queued',
            title varchar(255) NOT NULL,
            body text,
            data jsonb NOT NULL DEFAULT '{}',
            buy_box_id uuid REFERENCES buy_boxes(id) ON DELETE SET NULL,
            property_id uuid REFERENCES properties(id) ON DELETE SET NULL,
            sent_at timestamptz,
            read_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        "CREATE INDEX ix_notifications_user_created ON notifications (user_id, created_at)"
    )
    op.execute("CREATE INDEX ix_notifications_org_id ON notifications (org_id)")
    _bootstrap_monthly_partitions("notifications")

    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analyses.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "scenario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="SET NULL"),
        ),
        sa.Column("kind", sa.String(32), nullable=False, server_default="property"),
        sa.Column("status", report_status, nullable=False, server_default="pending"),
        sa.Column("context_pack", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("narrative", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("pdf_s3_key", sa.Text),
        sa.Column("model_id", sa.String(64)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_reports_org_property", "reports", ["org_id", "property_id"])

    op.create_table(
        "share_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column(
            "display_policy_market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
        ),
        sa.Column("view_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_share_links_token", "share_links", ["token"], unique=True)
    op.create_index("ix_share_links_org_id", "share_links", ["org_id"])

    op.create_table(
        "notes",
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
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_notes_target", "notes", ["org_id", "target_type", "target_id"])

    op.create_table(
        "pipeline_deals",
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
            "scenario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.String(255)),
        sa.Column("stage", deal_stage, nullable=False, server_default="lead"),
        sa.Column("board_position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("target_price", sa.Numeric(14, 2)),
        sa.Column("offer_price", sa.Numeric(14, 2)),
        sa.Column("notes", sa.Text),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_pipeline_deals_org_stage", "pipeline_deals", ["org_id", "stage"])

    op.create_table(
        "feedback_labels",
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
        sa.Column("subject", feedback_subject, nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_value", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_index("ix_feedback_labels_org", "feedback_labels", ["org_id"])
    op.create_index("ix_feedback_labels_subject", "feedback_labels", ["subject", "subject_id"])

    # --- OPS (shared, no RLS) ---
    op.create_table(
        "ingestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
        ),
        sa.Column("status", ingestion_run_status, nullable=False, server_default="running"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("records_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_upserted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lag_seconds", sa.Integer),
        sa.Column("stats", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text),
        *_timestamps(),
    )
    op.create_index(
        "ix_ingestion_runs_source_started", "ingestion_runs", ["source_id", "started_at"]
    )

    op.create_table(
        "dq_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
        ),
        sa.Column("rule", sa.String(64), nullable=False),
        sa.Column("severity", dq_severity, nullable=False),
        sa.Column("status", dq_status, nullable=False, server_default="open"),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_dq_flags_subject", "dq_flags", ["subject_type", "subject_id"])
    op.create_index("ix_dq_flags_status_severity", "dq_flags", ["status", "severity"])

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="anthropic"),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_index("ix_model_versions_kind_active", "model_versions", ["kind", "active"])

    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("semver", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(64)),
        sa.Column("changelog", sa.Text),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deployed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index(
        "ix_prompt_versions_name_semver", "prompt_versions", ["name", "semver"], unique=True
    )

    # ai_calls: monthly range partitioned by created_at (§11.6) — highest-volume table.
    op.execute(
        """
        CREATE TABLE ai_calls (
            id uuid NOT NULL,
            model varchar(64) NOT NULL,
            prompt_version varchar(32),
            purpose ai_call_purpose NOT NULL,
            subject_type varchar(32),
            subject_id uuid,
            market_id uuid REFERENCES markets(id) ON DELETE SET NULL,
            tokens_in integer,
            tokens_out integer,
            cost_usd numeric(8,5),
            latency_ms integer,
            success boolean NOT NULL DEFAULT true,
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_ai_calls_purpose_created ON ai_calls (purpose, created_at)")
    op.execute("CREATE INDEX ix_ai_calls_subject ON ai_calls (subject_type, subject_id)")
    _bootstrap_monthly_partitions("ai_calls")

    # --- RLS for org-scoped user-work tables (standard tenant_isolation, as in 0001) ---
    for table in _ORG_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            """
        )


def downgrade() -> None:
    for table in (
        "ai_calls",
        "prompt_versions",
        "model_versions",
        "dq_flags",
        "ingestion_runs",
        "feedback_labels",
        "pipeline_deals",
        "notes",
        "share_links",
        "reports",
        "notifications",
        "watchlist_items",
        "buy_boxes",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    bind = op.get_bind()
    for enum_type in _NEW_ENUMS:
        enum_type.drop(bind, checkfirst=True)
