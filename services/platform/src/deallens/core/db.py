"""Async SQLAlchemy engine/session plumbing.

Two engines, two trust levels:
- `engine` connects as `deallens_app`, a least-privilege role with no BYPASSRLS — every
  policy in migration 0001 applies to it. This is what serves user requests.
- `system_engine` connects as the schema-owner role, which RLS does not restrict (no
  FORCE ROW LEVEL SECURITY is set). Reserved for Alembic-adjacent bootstrap paths that must
  write before any actor-scoped GUC can exist — currently just the Clerk webhook handler
  creating a brand-new user/org shadow row. Never used to serve a user request.

Tenant isolation pooling gotcha (§11.1): RLS policies read `current_setting('app.*')` GUCs.
Because connections are pooled, those GUCs must be set transaction-locally (via
`set_config(key, value, true)`) inside every request's transaction — never assumed to
persist across checkouts. `request_scoped_session()` is the only sanctioned way to get a
session for a user request.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from deallens.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10)
_session_maker = async_sessionmaker(engine, expire_on_commit=False)

system_engine = create_async_engine(settings.database_url_system, pool_pre_ping=True, pool_size=2)
_system_session_maker = async_sessionmaker(system_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Unscoped app-role session — no GUCs set, so RLS admits nothing on tables that don't
    have a bootstrap escape hatch. Only useful for pre-actor-resolution reads that are
    themselves RLS-permitted (there are none today); prefer `request_scoped_session()`.
    """
    async with _session_maker() as session:
        yield session


@asynccontextmanager
async def request_scoped_session(
    *, user_id: UUID | None = None, org_id: UUID | None = None
) -> AsyncGenerator[AsyncSession]:
    """The session for serving an authenticated request. Sets `app.user_id`/`app.org_id`
    for the lifetime of one transaction so RLS policies can evaluate them.

    `user_id` should be set as soon as an actor is resolved (permits self-scoped reads,
    e.g. "which orgs am I a member of"); `org_id` once the actor has an active org
    selected (permits org-scoped reads across every other identity table).
    """
    async with _session_maker() as session, session.begin():
        # `set_config(key, value, is_local=true)` is the transaction-local equivalent of
        # `SET LOCAL key = value`, but — unlike `SET`, a utility statement that rejects bind
        # parameters under the extended query protocol (asyncpg/psycopg both use it) — it is
        # an ordinary function call that accepts a bound value. `SET LOCAL app.org_id = :v`
        # raises `syntax error at or near "$1"` on asyncpg; this form is what the RLS
        # policies in the migrations read via `current_setting('app.org_id')`.
        if user_id is not None:
            await session.execute(
                text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id)}
            )
        if org_id is not None:
            await session.execute(
                text("SELECT set_config('app.org_id', :v, true)"), {"v": str(org_id)}
            )
        yield session


@asynccontextmanager
async def system_session() -> AsyncGenerator[AsyncSession]:
    """Owner-role session — RLS does not apply. Reserved for Clerk webhook handlers
    provisioning a brand-new user/org and other trusted-internal bootstrap paths. Anything
    reachable from an HTTP request on behalf of an end user must use
    `request_scoped_session()` instead.
    """
    async with _system_session_maker() as session, session.begin():
        yield session
