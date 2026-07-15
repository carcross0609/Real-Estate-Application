"""End-to-end report generation against a live Postgres: run the full engine stack, then generate
a report on an RLS-bound org session and assert it persists, re-reads, and exports. Skips when the
DB isn't up/migrated (see conftest).

Run with: make dev && make api-migrate && .venv/bin/pytest tests/integration/test_reports.py -q
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.core.enums import Strategy
from deallens.modules.engine import service as engine_service
from deallens.modules.reports import service as reports_service
from deallens.modules.vision import service as vision_service

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


async def _sys(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


@asynccontextmanager
async def _app_tx(engine: AsyncEngine, *, org_id: str, user_id: str) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": user_id})
        await session.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_id})
        yield session


async def _seed_org(engine: AsyncEngine) -> tuple[str, str]:
    org_id, user_id = _uid(), _uid()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO orgs (id, clerk_org_id, name, slug) VALUES (:id, :c, 'Acme', :s)"),
            {"id": org_id, "c": _uid(), "s": f"acme-{_uid()[:8]}"})
        await conn.execute(
            text("INSERT INTO users (id, clerk_user_id, email) VALUES (:id, :c, :e)"),
            {"id": user_id, "c": _uid(), "e": f"u{_uid()[:8]}@x.com"})
    return org_id, user_id


async def _seed_property(engine: AsyncEngine) -> tuple[str, str]:
    market_id, sale_src, rent_src = _uid(), _uid(), _uid()
    subject, listing_id = _uid(), _uid()
    lon, lat = -97.4, 30.4
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO markets (id, cbsa_code, name, state, status) "
                 "VALUES (:id, :c, 'M', 'TX', 'active')"), {"id": market_id, "c": _uid()[:12]})
        for sid, tier in ((sale_src, "mls"), (rent_src, "rental")):
            await conn.execute(
                text("INSERT INTO data_sources (id, source_key, name, tier) "
                     "VALUES (:id, :k, 'F', :t)"), {"id": sid, "k": f"s_{_uid()[:8]}", "t": tier})
        await conn.execute(
            text("INSERT INTO properties (id, market_id, property_type, beds, baths, sqft, "
                 "year_built, address_norm, geom) VALUES (:id, :m, 'sfr', 3, 2, 1800, 2000, "
                 ":addr, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"),
            {"id": subject, "m": market_id, "lon": lon, "lat": lat,
             "addr": '{"line1": "123 Main St", "city": "Austin", "state": "TX", "zip": "78701"}'})
        await conn.execute(
            text("INSERT INTO listings (id, property_id, source_id, source_listing_key, status, "
                 "list_price, list_date) VALUES (:id, :p, :s, :k, 'active', 300000, :d)"),
            {"id": listing_id, "p": subject, "s": sale_src, "k": _uid()[:12], "d": date.today()})
        await conn.execute(
            text("INSERT INTO tax_records (id, property_id, tax_year, annual_tax_amount) "
                 "VALUES (:id, :p, 2025, 6000)"), {"id": _uid(), "p": subject})
        for i, price in enumerate(["330000", "340000", "325000", "335000"]):
            cp = _uid()
            await conn.execute(
                text("INSERT INTO properties (id, market_id, property_type, beds, baths, sqft, "
                     "geom) VALUES (:id, :m, 'sfr', 3, 2, 1800, "
                     "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"),
                {"id": cp, "m": market_id, "lon": lon + 0.001 * (i + 1),
                 "lat": lat + 0.001 * (i + 1)})
            await conn.execute(
                text("INSERT INTO listings (id, property_id, source_id, source_listing_key, "
                     "status, close_price, close_date, list_date) VALUES (:id, :p, :s, :k, 'sold', "
                     ":pr, :cd, :ld)"),
                {"id": _uid(), "p": cp, "s": sale_src, "k": _uid()[:12], "pr": price,
                 "cd": date.today() - timedelta(days=40), "ld": date.today() - timedelta(days=70)})
        for i, rent in enumerate(["2500", "2600", "2450"]):
            cp = _uid()
            await conn.execute(
                text("INSERT INTO properties (id, market_id, property_type, beds, baths, sqft, "
                     "geom) VALUES (:id, :m, 'sfr', 3, 2, 1800, "
                     "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"),
                {"id": cp, "m": market_id, "lon": lon - 0.001 * (i + 1),
                 "lat": lat - 0.001 * (i + 1)})
            await conn.execute(
                text("INSERT INTO listings (id, property_id, source_id, source_listing_key, "
                     "status, list_price, list_date) VALUES (:id, :p, :s, :k, 'active', :r, :ld)"),
                {"id": _uid(), "p": cp, "s": rent_src, "k": _uid()[:12], "r": rent,
                 "ld": date.today() - timedelta(days=15)})
        await conn.execute(
            text("INSERT INTO listing_photos (id, listing_id, position, content_hash) "
                 "VALUES (:id, :l, 0, :ch)"),
            {"id": _uid(), "l": listing_id, "ch": _uid()[:16].encode()})
    return subject, listing_id


async def test_generate_report_end_to_end(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_id, user_id = await _seed_org(system_engine)
    subject, listing_id = await _seed_property(system_engine)

    # Run the full engine stack (shared writes on the owner session).
    async with await _sys(system_engine) as db:
        await engine_service.value_property(db, property_id=uuid.UUID(subject))
        await vision_service.analyze_listing(db, listing_id=uuid.UUID(listing_id))
        for strat in (Strategy.LTR, Strategy.FLIP, Strategy.BRRRR):
            await engine_service.analyze_property(
                db, property_id=uuid.UUID(subject), strategy=strat)
        await db.commit()

    # Generate the report on the org's RLS session.
    async with _app_tx(app_engine, org_id=org_id, user_id=user_id) as db:
        report = await reports_service.generate_report(
            db, property_id=uuid.UUID(subject), org_id=uuid.UUID(org_id),
            user_id=uuid.UUID(user_id),
        )
        report_id = report.report_id

    assert report_id is not None
    assert report.status == "ready"
    section_ids = {s.id for s in report.sections}
    # A fully-analyzed property gets the comprehensive set of sections.
    assert {"opportunity", "calculations", "comparables", "projections", "exits"} <= section_ids
    assert report.html and report.html.startswith("<!doctype html>")
    assert report.markdown and "# Investment Analysis" in report.markdown
    assert "123 Main St" in report.html  # grounded in the real property facts

    # Persisted + re-readable (identical re-render from the stored context pack).
    async with _app_tx(app_engine, org_id=org_id, user_id=user_id) as db:
        reread = await reports_service.get_report(db, report_id=uuid.UUID(report_id))
    assert reread.property_id == subject
    assert {s.id for s in reread.sections} == section_ids


async def test_report_is_org_scoped(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_a, user_a = await _seed_org(system_engine)
    org_b, user_b = await _seed_org(system_engine)
    subject, listing_id = await _seed_property(system_engine)
    async with await _sys(system_engine) as db:
        await engine_service.value_property(db, property_id=uuid.UUID(subject))
        await db.commit()

    async with _app_tx(app_engine, org_id=org_a, user_id=user_a) as db:
        report = await reports_service.generate_report(
            db, property_id=uuid.UUID(subject), org_id=uuid.UUID(org_a), user_id=uuid.UUID(user_a),
        )
        rid = report.report_id
    assert rid is not None

    # Org B cannot read org A's report — RLS returns not-found.
    from deallens.core.errors import NotFoundError
    async with _app_tx(app_engine, org_id=org_b, user_id=user_b) as db:
        with pytest.raises(NotFoundError):
            await reports_service.get_report(db, report_id=uuid.UUID(rid))
