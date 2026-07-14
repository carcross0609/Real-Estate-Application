"""End-to-end search against a live Postgres (PostGIS): keyset ranking + pagination,
geographic bbox filtering, saved-search RLS isolation, and the FR-001 search-area tier cap.
Seeds shared property/listing/score data via the owner engine; drives the search service on an
RLS-bound app session.

Skips wholesale when the DB isn't up/migrated (see conftest). Run with:
    make dev && make api-migrate && .venv/bin/pytest tests/integration/test_search.py -q
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.core.errors import ForbiddenError
from deallens.modules.search import service
from deallens.modules.search.schemas import (
    BBox,
    GeoJSONGeometry,
    PropertySearchQuery,
    SavedSearchCreate,
    SearchAreaCreate,
    SearchScope,
)

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


@asynccontextmanager
async def _app_tx(
    engine: AsyncEngine, *, org_id: str | None = None, user_id: str | None = None
) -> AsyncIterator[AsyncSession]:
    """An app-role session with the RLS GUCs set, mirroring core.db.request_scoped_session."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        if user_id is not None:
            await session.execute(
                text("SELECT set_config('app.user_id', :v, true)"), {"v": user_id}
            )
        if org_id is not None:
            await session.execute(
                text("SELECT set_config('app.org_id', :v, true)"), {"v": org_id}
            )
        yield session


async def _seed_market(engine: AsyncEngine) -> str:
    market_id = _uid()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO markets (id, cbsa_code, name, state, status) "
                "VALUES (:id, :cbsa, 'Test Metro', 'TX', 'active')"
            ),
            {"id": market_id, "cbsa": _uid()[:12]},
        )
    return market_id


async def _seed_source(engine: AsyncEngine) -> str:
    source_id = _uid()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources (id, source_key, name, tier) "
                "VALUES (:id, :key, 'Test MLS', 'mls')"
            ),
            {"id": source_id, "key": f"src_{_uid()[:8]}"},
        )
    return source_id


async def _seed_property(
    engine: AsyncEngine,
    *,
    market_id: str,
    source_id: str,
    lon: float,
    lat: float,
    price: str,
    score: str | None,
    beds: int = 3,
) -> str:
    """Insert one property + an active listing (+ an `overall` score if given). Returns the
    property id."""
    prop_id, listing_id = _uid(), _uid()
    addr = '{"line1": "1 Test St", "city": "Testville", "state": "TX", "zip": "75001"}'
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO properties "
                "(id, market_id, property_type, beds, baths, sqft, address_norm, geom) "
                "VALUES (:id, :m, 'sfr', :beds, 2, 1500, "
                ":addr ::jsonb, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"
            ),
            {
                "id": prop_id,
                "m": market_id,
                "beds": beds,
                "addr": addr,
                "lon": lon,
                "lat": lat,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO listings "
                "(id, property_id, source_id, source_listing_key, status, list_price, "
                "list_date, dom_current) "
                "VALUES (:id, :p, :s, :key, 'active', :price, CURRENT_DATE, 10)"
            ),
            {"id": listing_id, "p": prop_id, "s": source_id, "key": _uid(), "price": price},
        )
        if score is not None:
            await conn.execute(
                text(
                    "INSERT INTO scores "
                    "(id, property_id, listing_id, market_id, strategy, score, grade, "
                    "scoring_version) "
                    "VALUES (:id, :p, :l, :m, 'overall', :score, 'A', 'v1')"
                ),
                {
                    "id": _uid(),
                    "p": prop_id,
                    "l": listing_id,
                    "m": market_id,
                    "score": score,
                },
            )
    return prop_id


async def test_search_ranks_by_score_and_paginates(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    market_id = await _seed_market(system_engine)
    source_id = await _seed_source(system_engine)
    # Three scored properties in the same market.
    await _seed_property(
        system_engine, market_id=market_id, source_id=source_id,
        lon=-96.8, lat=32.8, price="300000", score="90",
    )
    await _seed_property(
        system_engine, market_id=market_id, source_id=source_id,
        lon=-96.81, lat=32.81, price="250000", score="70",
    )
    await _seed_property(
        system_engine, market_id=market_id, source_id=source_id,
        lon=-96.82, lat=32.82, price="200000", score="50",
    )

    q = PropertySearchQuery(scope=SearchScope(market_id=uuid.UUID(market_id)), limit=2)
    async with _app_tx(app_engine) as db:
        page1 = await service.search_properties(db, q)
    scores = [c.score for c in page1.results]
    assert scores == [Decimal("90.00"), Decimal("70.00")]  # score desc
    assert page1.next_cursor is not None
    assert page1.results[0].city == "Testville"

    # Follow the cursor → the third (lowest-scored) property, no overlap.
    q2 = q.model_copy(update={"cursor": page1.next_cursor})
    async with _app_tx(app_engine) as db:
        page2 = await service.search_properties(db, q2)
    assert [c.score for c in page2.results] == [Decimal("50.00")]
    assert page2.next_cursor is None


async def test_search_bbox_filters_geographically(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    market_id = await _seed_market(system_engine)
    source_id = await _seed_source(system_engine)
    inside = await _seed_property(
        system_engine, market_id=market_id, source_id=source_id,
        lon=-96.80, lat=32.80, price="300000", score="80",
    )
    await _seed_property(  # far outside the bbox
        system_engine, market_id=market_id, source_id=source_id,
        lon=-80.0, lat=25.0, price="300000", score="95",
    )

    q = PropertySearchQuery(
        scope=SearchScope(
            market_id=uuid.UUID(market_id),
            bbox=BBox(min_lon=-97.0, min_lat=32.5, max_lon=-96.5, max_lat=33.0),
        )
    )
    async with _app_tx(app_engine) as db:
        page = await service.search_properties(db, q)
        count = await service.count_matches(db, q)
    ids = {str(c.property_id) for c in page.results}
    assert ids == {inside}  # the higher-scored outside property is excluded by geometry
    assert count.count == 1


async def test_count_and_tile_respect_filters(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    market_id = await _seed_market(system_engine)
    source_id = await _seed_source(system_engine)
    await _seed_property(
        system_engine, market_id=market_id, source_id=source_id,
        lon=-96.80, lat=32.80, price="300000", score="80",
    )
    q = PropertySearchQuery(scope=SearchScope(market_id=uuid.UUID(market_id)))
    # A high-zoom tile containing the seeded point returns individual (bucketed) pins.
    from deallens.modules.search.geo import lonlat_to_tile

    tx, ty = lonlat_to_tile(-96.80, 32.80, 15)
    async with _app_tx(app_engine) as db:
        resp, truncated = await service.tile(db, z=15, x=tx, y=ty, q=q)
    assert resp.clustered is False
    assert len(resp.features) == 1
    assert resp.features[0].score_bucket == 8  # 80/100 → decile 8
    assert not truncated


async def test_saved_search_is_rls_isolated(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_a, user_a = _uid(), _uid()
    org_b, user_b = _uid(), _uid()
    async with system_engine.begin() as conn:
        for org, slug in ((org_a, "a"), (org_b, "b")):
            await conn.execute(
                text("INSERT INTO orgs (id, clerk_org_id, name, slug) VALUES (:id,:c,:n,:s)"),
                {"id": org, "c": f"c_{org}", "n": slug, "s": f"{slug}-{org[:8]}"},
            )
        for u, _org in ((user_a, org_a), (user_b, org_b)):
            await conn.execute(
                text("INSERT INTO users (id, clerk_user_id, email) VALUES (:id,:c,:e)"),
                {"id": u, "c": f"u_{u}", "e": f"{u}@example.com"},
            )

    async with _app_tx(app_engine, org_id=org_a, user_id=user_a) as db:
        await service.create_saved_search(
            db,
            org_id=uuid.UUID(org_a),
            user_id=uuid.UUID(user_a),
            body=SavedSearchCreate(name="A's view"),
        )
    # Org B, different user, sees nothing (RLS + user scoping).
    async with _app_tx(app_engine, org_id=org_b, user_id=user_b) as db:
        b_list = await service.list_saved_searches(db, user_id=uuid.UUID(user_b))
    assert b_list == []


async def test_search_area_tier_limit_enforced(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_id, user_id = _uid(), _uid()
    async with system_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO orgs (id, clerk_org_id, name, slug) VALUES (:id,:c,'O',:s)"),
            {"id": org_id, "c": f"c_{org_id}", "s": f"o-{org_id[:8]}"},
        )
        await conn.execute(
            text("INSERT INTO users (id, clerk_user_id, email) VALUES (:id,:c,:e)"),
            {"id": user_id, "c": f"u_{user_id}", "e": f"{user_id}@example.com"},
        )
        # Basic-tier entitlement: exactly one search area allowed.
        await conn.execute(
            text("INSERT INTO entitlements (id, org_id, markets_limit) VALUES (:id,:o,1)"),
            {"id": _uid(), "o": org_id},
        )

    square = GeoJSONGeometry(
        type="Polygon", coordinates=[[[-97, 32], [-97, 33], [-96, 33], [-96, 32], [-97, 32]]]
    )
    async with _app_tx(app_engine, org_id=org_id, user_id=user_id) as db:
        first = await service.create_search_area(
            db,
            org_id=uuid.UUID(org_id),
            user_id=uuid.UUID(user_id),
            body=SearchAreaCreate(name="Area 1", geometry=square),
        )
        assert first.geometry is not None  # ST_AsGeoJSON round-trips the polygon
        with pytest.raises(ForbiddenError):
            await service.create_search_area(
                db,
                org_id=uuid.UUID(org_id),
                user_id=uuid.UUID(user_id),
                body=SearchAreaCreate(name="Area 2", geometry=square),
            )
