"""identity schema: users, orgs, org_members, org_invites, subscriptions, entitlements,
api_keys, audit_log — plus the least-privilege app role and RLS policies (§11.1, §15).

Revision ID: 0001
Revises:
Create Date: 2026-07-08
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# `create_type=False`: the types are created once by the explicit `.create()` loop in
# upgrade(). Without this, an enum reused across tables (org_role in org_members AND
# org_invites) also emits an inline CREATE TYPE inside CREATE TABLE — which Alembic runs
# without checkfirst, so a fresh `alembic upgrade` fails with "type already exists". Same
# pattern as migrations 0002–0005.
org_role = postgresql.ENUM(
    "owner", "admin", "analyst", "viewer", name="org_role", create_type=False
)
invite_status = postgresql.ENUM(
    "pending", "accepted", "revoked", "expired", name="invite_status", create_type=False
)
subscription_plan = postgresql.ENUM(
    "basic", "pro", "team", name="subscription_plan", create_type=False
)
subscription_status = postgresql.ENUM(
    "trialing", "active", "past_due", "canceled", name="subscription_status", create_type=False
)
alert_latency = postgresql.ENUM(
    "instant", "hourly", "daily", name="alert_latency", create_type=False
)

IDENTITY_TABLES = (
    "users",
    "orgs",
    "org_members",
    "org_invites",
    "subscriptions",
    "entitlements",
    "api_keys",
    "audit_log",
)

# Org-id-scoped tables get a straightforward policy. `org_members` additionally allows a
# row through when it's the caller's own membership (`user_id` match) — a user needs to be
# able to list which orgs they belong to *before* they've picked one to act as, i.e. before
# `app.org_id` is set. See core/db.py for how the two GUCs are populated per request.
SIMPLE_SCOPED_TABLES = ("org_invites", "subscriptions", "entitlements", "api_keys")


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        org_role,
        invite_status,
        subscription_plan,
        subscription_status,
        alert_latency,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("clerk_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("full_name", sa.String(255)),
        sa.Column("avatar_url", sa.Text),
        sa.Column("is_staff", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("preferences", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "orgs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("clerk_org_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("locked_assumption_set", postgresql.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_orgs_clerk_org_id", "orgs", ["clerk_org_id"], unique=True)
    op.create_index("ix_orgs_slug", "orgs", ["slug"], unique=True)

    op.create_table(
        "org_members",
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
        sa.Column("role", org_role, nullable=False, server_default="viewer"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_org_members_org_id", "org_members", ["org_id"])
    op.create_index("ix_org_members_user_id", "org_members", ["user_id"])
    op.create_index("ix_org_members_org_user", "org_members", ["org_id", "user_id"], unique=True)

    op.create_table(
        "org_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", org_role, nullable=False, server_default="viewer"),
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("status", invite_status, nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_org_invites_org_id", "org_invites", ["org_id"])
    op.create_index("ix_org_invites_email", "org_invites", ["email"])
    op.create_index("ix_org_invites_token", "org_invites", ["token"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(255)),
        sa.Column("stripe_subscription_id", sa.String(255)),
        sa.Column("plan", subscription_plan, nullable=False, server_default="basic"),
        sa.Column("status", subscription_status, nullable=False, server_default="trialing"),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_subscriptions_org_id", "subscriptions", ["org_id"], unique=True)

    op.create_table(
        "entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("markets_limit", sa.Integer, nullable=False, server_default="1"),
        sa.Column("seats", sa.Integer, nullable=False, server_default="1"),
        sa.Column("alert_latency", alert_latency, nullable=False, server_default="daily"),
        sa.Column("exports_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("pipeline_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("api_access", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_entitlements_org_id", "entitlements", ["org_id"], unique=True)

    op.create_table(
        "api_keys",
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
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    # audit_log: native monthly range partitioning (§11.6). Alembic/SQLAlchemy have no
    # first-class "CREATE TABLE ... PARTITION BY" API, so this is raw DDL. A DEFAULT
    # partition catches anything outside the bootstrapped range so writes never fail
    # outright; a monthly maintenance job (Celery beat or pg_partman) to keep provisioning
    # future partitions is an ops-runbook follow-up, out of scope for this identity/auth
    # slice.
    op.execute(
        """
        CREATE TABLE audit_log (
            id uuid NOT NULL,
            org_id uuid REFERENCES orgs(id) ON DELETE SET NULL,
            actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            action varchar(128) NOT NULL,
            target_type varchar(64),
            target_id varchar(64),
            event_metadata jsonb NOT NULL DEFAULT '{}',
            ip_address varchar(64),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_audit_log_org_id ON audit_log (org_id)")
    op.execute("CREATE INDEX ix_audit_log_action ON audit_log (action)")
    op.execute("CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT")

    # Bootstrap partitions for the month this migration runs, one month back, and three
    # months forward.
    now = datetime.now(UTC)
    start_month = (now.year * 12 + (now.month - 1)) - 1
    for offset in range(5):
        month_index = start_month + offset
        year, month = divmod(month_index, 12)
        month += 1
        next_year, next_month = (year, month + 1) if month < 12 else (year + 1, 1)
        partition_name = f"audit_log_{year:04d}_{month:02d}"
        op.execute(
            f"""
            CREATE TABLE {partition_name} PARTITION OF audit_log
            FOR VALUES FROM ('{year:04d}-{month:02d}-01') TO ('{next_year:04d}-{next_month:02d}-01')
            """
        )

    # --- Least-privilege app role (§15 "least-privilege IAM") ---
    # The app connects as this role, never as the migration/owner role, so RLS (below) is
    # enforced for it even without FORCE ROW LEVEL SECURITY. Dev-only password below;
    # staging/prod provision this role via Terraform with a generated secret (§15 "secrets
    # in manager, never in code") — see infra/terraform/modules/rds.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'deallens_app') THEN
                CREATE ROLE deallens_app LOGIN PASSWORD 'deallens_app_dev' NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO deallens_app")
    for table in IDENTITY_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO deallens_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO deallens_app"
    )

    # --- Row-level security (§11.1, §15 tenant isolation) ---
    for table in SIMPLE_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            """
        )

    # org_members: also permit a caller's own membership rows so a user can discover which
    # orgs they belong to before `app.org_id` is set (see core/db.py).
    op.execute("ALTER TABLE org_members ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON org_members
        USING (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
        WITH CHECK (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
        """
    )

    # orgs: visible if it's the active org, or the caller is a member of it at all (so "list
    # my orgs" works pre-selection); insertable by any authenticated caller (org creation) —
    # the owner membership row is inserted in the same transaction by identity.service.
    op.execute("ALTER TABLE orgs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON orgs
        USING (
            id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR EXISTS (
                SELECT 1 FROM org_members m
                WHERE m.org_id = orgs.id
                  AND m.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
            )
        )
        WITH CHECK (NULLIF(current_setting('app.user_id', true), '') IS NOT NULL)
        """
    )

    # audit_log: org_id is nullable (staff/system events aren't org-scoped); scope only
    # rows that have one.
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON audit_log
        USING (
            org_id IS NULL
            OR org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        )
        """
    )

    # `users` has no org_id (a user can belong to multiple orgs); scope to self instead.
    # System contexts (Clerk webhooks provisioning a brand-new user) use a session that
    # doesn't set `app.user_id` and therefore can't pass this policy — see
    # core/db.py `system_session()`, which connects via the owner role, not `deallens_app`,
    # for exactly this reason.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY self_only ON users
        USING (id = NULLIF(current_setting('app.user_id', true), '')::uuid)
        WITH CHECK (id = NULLIF(current_setting('app.user_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM deallens_app")
    op.execute("DROP OWNED BY deallens_app")
    op.execute("DROP ROLE IF EXISTS deallens_app")

    for table in ("audit_log",) + IDENTITY_TABLES[:-1][::-1]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    bind = op.get_bind()
    for enum_type in (
        org_role,
        invite_status,
        subscription_plan,
        subscription_status,
        alert_latency,
    ):
        enum_type.drop(bind, checkfirst=True)
