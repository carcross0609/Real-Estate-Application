"""End-to-end schema behavior against a live Postgres: extensions present, RLS tenant
isolation (the §15 headline risk), OR-NULL comp visibility, CHECK gates, and partition
routing. Seeds via the owner engine (RLS-exempt); asserts via the app engine (RLS-bound).

Skips wholesale when the DB isn't up/migrated (see conftest). Run with:
    make dev && make api-migrate && .venv/bin/pytest tests/integration -q
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


async def _seed_org(engine: AsyncEngine, *, slug: str) -> tuple[str, str]:
    """Create an org + a member user via the owner role. Returns (org_id, user_id)."""
    org_id, user_id = _uid(), _uid()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO orgs (id, clerk_org_id, name, slug) VALUES (:id, :cid, :name, :slug)"
            ),
            {"id": org_id, "cid": f"clerk_{slug}", "name": slug, "slug": slug},
        )
        await conn.execute(
            text("INSERT INTO users (id, clerk_user_id, email) VALUES (:id, :cid, :email)"),
            {"id": user_id, "cid": f"user_{slug}", "email": f"{slug}@example.com"},
        )
    return org_id, user_id


async def test_extensions_installed(system_engine: AsyncEngine) -> None:
    async with system_engine.connect() as conn:
        exts = set(await conn.scalars(text("SELECT extname FROM pg_extension")))
    assert {"postgis", "vector"} <= exts


async def test_rls_blocks_cross_tenant_read(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_a, user_a = await _seed_org(system_engine, slug=f"a-{_uid()[:8]}")
    org_b, _user_b = await _seed_org(system_engine, slug=f"b-{_uid()[:8]}")

    box_id = _uid()
    async with system_engine.begin() as conn:  # owner bypasses RLS to seed org A's box
        await conn.execute(
            text(
                "INSERT INTO buy_boxes (id, org_id, user_id, name) "
                "VALUES (:id, :org, :user, 'A box')"
            ),
            {"id": box_id, "org": org_a, "user": user_a},
        )

    # As org A: the box is visible.
    async with app_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_a})
        seen_a = await conn.scalar(
            text("SELECT count(*) FROM buy_boxes WHERE id = :id"), {"id": box_id}
        )
    assert seen_a == 1

    # As org B: the same row is invisible — RLS enforced beneath the app layer.
    async with app_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_b})
        seen_b = await conn.scalar(
            text("SELECT count(*) FROM buy_boxes WHERE id = :id"), {"id": box_id}
        )
    assert seen_b == 0


async def test_rls_with_check_blocks_foreign_org_insert(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_a, user_a = await _seed_org(system_engine, slug=f"a-{_uid()[:8]}")
    org_b, _ = await _seed_org(system_engine, slug=f"b-{_uid()[:8]}")

    # Acting as org B, attempt to plant a row owned by org A → WITH CHECK must reject it.
    with pytest.raises(Exception):  # noqa: B017,PT011 — RLS violation surfaces as DBAPIError
        async with app_engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_b})
                await conn.execute(
                    text(
                        "INSERT INTO buy_boxes (id, org_id, user_id, name) "
                        "VALUES (:id, :org, :user, 'smuggled')"
                    ),
                    {"id": _uid(), "org": org_a, "user": user_a},
                )


async def test_comp_sets_or_null_policy(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    """System comp sets (org_id NULL) are visible to every tenant; user comp sets are not."""
    org_a, _ = await _seed_org(system_engine, slug=f"a-{_uid()[:8]}")
    org_b, user_b = await _seed_org(system_engine, slug=f"b-{_uid()[:8]}")

    prop_id, system_cs, user_cs = _uid(), _uid(), _uid()
    async with system_engine.begin() as conn:
        await conn.execute(text("INSERT INTO properties (id) VALUES (:id)"), {"id": prop_id})
        await conn.execute(
            text(
                "INSERT INTO comp_sets (id, subject_property_id, kind, created_by) "
                "VALUES (:id, :p, 'sale', 'system')"
            ),
            {"id": system_cs, "p": prop_id},
        )
        await conn.execute(
            text(
                "INSERT INTO comp_sets "
                "(id, subject_property_id, kind, created_by, org_id, user_id) "
                "VALUES (:id, :p, 'sale', 'user', :org, :user)"
            ),
            {"id": user_cs, "p": prop_id, "org": org_b, "user": user_b},
        )

    async with app_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_a})
        sees_system = await conn.scalar(
            text("SELECT count(*) FROM comp_sets WHERE id = :id"), {"id": system_cs}
        )
        sees_other_user = await conn.scalar(
            text("SELECT count(*) FROM comp_sets WHERE id = :id"), {"id": user_cs}
        )
    assert sees_system == 1  # shared system set visible to org A
    assert sees_other_user == 0  # org B's private set is not


async def test_check_constraint_rejects_overall_analysis(system_engine: AsyncEngine) -> None:
    prop_id = _uid()
    with pytest.raises(Exception):  # noqa: B017 — CHECK violation
        async with system_engine.begin() as conn:
            await conn.execute(text("INSERT INTO properties (id) VALUES (:id)"), {"id": prop_id})
            await conn.execute(
                text(
                    "INSERT INTO analyses (id, property_id, engine_version, strategy) "
                    "VALUES (:id, :p, 'v1', 'overall')"
                ),
                {"id": _uid(), "p": prop_id},
            )


async def test_partitioned_notification_routes_and_reads(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_a, user_a = await _seed_org(system_engine, slug=f"a-{_uid()[:8]}")
    notif_id = _uid()
    async with system_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO notifications (id, org_id, user_id, kind, channel, title) "
                "VALUES (:id, :org, :user, 'new_match', 'in_app', 'hi')"
            ),
            {"id": notif_id, "org": org_a, "user": user_a},
        )
        # It must land in a real monthly partition, not just the DEFAULT catch-all.
        partition = await conn.scalar(
            text("SELECT tableoid::regclass::text FROM notifications WHERE id = :id"),
            {"id": notif_id},
        )
    assert partition is not None and partition.startswith("notifications_")
    assert partition != "notifications_default"

    async with app_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_a})
        seen = await conn.scalar(
            text("SELECT count(*) FROM notifications WHERE id = :id"), {"id": notif_id}
        )
    assert seen == 1
