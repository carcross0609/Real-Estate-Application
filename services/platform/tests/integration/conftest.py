"""Integration-tier fixtures. These require a live Postgres (PostGIS + pgvector) reachable
at the configured URLs and migrated to head (`make dev && make api-migrate`). When the DB
is unreachable they SKIP rather than fail, so unit-only environments (this sandbox, a CI
box that hasn't started compose) stay green.

Two engines mirror the two trust levels of core/db.py:
- `system_engine` connects as the owner role (bypasses RLS) — used to seed fixtures.
- `app_engine` connects as `deallens_app` (subject to RLS) — the surface under test.

Engines are function-scoped: pytest-asyncio (auto mode) runs each test in its own event
loop, and an asyncpg connection is bound to the loop that created it — a session-scoped
engine would raise "another operation is in progress" when reused in a later test's loop.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SYSTEM_URL = os.environ["DATABASE_URL_SYSTEM"]
APP_URL = os.environ["DATABASE_URL"]


async def _skip_reason(engine: AsyncEngine) -> str | None:
    """Return a skip reason, or None if the DB is reachable and migrated to head."""
    try:
        async with engine.connect() as conn:
            has_table = await conn.scalar(
                text("SELECT to_regclass('public.buy_boxes') IS NOT NULL")
            )
    except Exception as exc:  # noqa: BLE001 — any connection failure = skip, not fail
        return f"Postgres unreachable ({exc.__class__.__name__}); run `make dev`"
    if not has_table:
        return "schema not migrated; run `make api-migrate`"
    return None


@pytest_asyncio.fixture
async def system_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(SYSTEM_URL)
    reason = await _skip_reason(engine)
    if reason:
        await engine.dispose()
        pytest.skip(reason)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(APP_URL)
    yield engine
    await engine.dispose()
