"""End-to-end comps engine against a live Postgres (PostGIS): seed a subject property and a
ring of sold comparables, run `value_property` on the owner session, and assert a comp-based
ARV/as-is/rent bundle comes back with persisted comp sets, members, and versioned valuations.
Also exercises the user pin/exclude recompute (FR-015) on an RLS-bound app session.

Skips when the DB isn't up/migrated (see conftest). Run with:
    make dev && make api-migrate && .venv/bin/pytest tests/integration/test_engine.py -q
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.core.enums import Strategy
from deallens.modules.engine import service
from deallens.modules.engine.models import ValuationKind
from deallens.modules.engine.schemas import CompEditRequest

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


@asynccontextmanager
async def _app_tx(
    engine: AsyncEngine, *, org_id: str, user_id: str
) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": user_id})
        await session.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_id})
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


async def _seed_source(engine: AsyncEngine, *, tier: str = "mls") -> str:
    source_id = _uid()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources (id, source_key, name, tier) "
                "VALUES (:id, :key, 'Test Feed', :tier)"
            ),
            {"id": source_id, "key": f"src_{_uid()[:8]}", "tier": tier},
        )
    return source_id


async def _seed_property(
    engine: AsyncEngine,
    *,
    market_id: str,
    lon: float,
    lat: float,
    sqft: int = 1800,
    beds: int = 3,
) -> str:
    prop_id = _uid()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO properties "
                "(id, market_id, property_type, beds, baths, sqft, lot_sqft, year_built, geom) "
                "VALUES (:id, :mkt, 'sfr', :beds, 2, :sqft, 6000, 2000, "
                "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"
            ),
            {"id": prop_id, "mkt": market_id, "beds": beds, "sqft": sqft, "lon": lon, "lat": lat},
        )
    return prop_id


async def _seed_sold_listing(
    engine: AsyncEngine, *, property_id: str, source_id: str, close_price: str, days_ago: int
) -> None:
    close = date.today() - timedelta(days=days_ago)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO listings "
                "(id, property_id, source_id, source_listing_key, status, close_price, "
                " close_date, list_date) "
                "VALUES (:id, :pid, :sid, :key, 'sold', :price, :close, :list)"
            ),
            {
                "id": _uid(), "pid": property_id, "sid": source_id, "key": _uid()[:12],
                "price": close_price, "close": close, "list": close - timedelta(days=30),
            },
        )


async def _seed_rental_listing(
    engine: AsyncEngine, *, property_id: str, source_id: str, rent: str, days_ago: int
) -> None:
    listed = date.today() - timedelta(days=days_ago)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO listings "
                "(id, property_id, source_id, source_listing_key, status, list_price, list_date) "
                "VALUES (:id, :pid, :sid, :key, 'active', :rent, :list)"
            ),
            {"id": _uid(), "pid": property_id, "sid": source_id, "key": _uid()[:12],
             "rent": rent, "list": listed},
        )


def _ring(lon: float, lat: float, n: int) -> list[tuple[float, float]]:
    # A few points ~0.1–0.3 mi from the subject (0.001° ≈ 111 m).
    return [(lon + 0.001 * (i + 1), lat + 0.001 * (i + 1)) for i in range(n)]


async def _system_session(system_engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(system_engine, expire_on_commit=False)()


async def test_value_property_produces_comp_based_arv(
    system_engine: AsyncEngine,
) -> None:
    market_id = await _seed_market(system_engine)
    sale_src = await _seed_source(system_engine, tier="mls")
    subject = await _seed_property(system_engine, market_id=market_id, lon=-97.7400, lat=30.2500)

    prices = ["300000", "310000", "295000", "305000"]
    for (lon, lat), price in zip(_ring(-97.7400, 30.2500, 4), prices, strict=True):
        comp = await _seed_property(system_engine, market_id=market_id, lon=lon, lat=lat)
        await _seed_sold_listing(
            system_engine, property_id=comp, source_id=sale_src, close_price=price, days_ago=45
        )

    async with await _system_session(system_engine) as db:
        result = await service.value_property(db, property_id=uuid.UUID(subject))
        await db.commit()

    assert result.arv is not None
    assert result.arv.point is not None and result.arv.point > 0
    assert result.arv.low is not None and result.arv.high is not None
    assert result.arv.low <= result.arv.point <= result.arv.high
    assert result.arv.comp_count >= 3
    assert result.arv.method == "comp-based"
    assert result.arv.confidence is not None and result.arv.confidence > 0
    # As-is uses the same sold comps against the subject's own (unknown) condition basis.
    assert result.as_is is not None and result.as_is.point is not None


async def test_thin_comps_fall_back_to_model_prior(system_engine: AsyncEngine) -> None:
    # A subject with a market $/sqft stat but no nearby sold comps must not return a blank —
    # it drops to the model-prior with low confidence (03 §26.1 fallback ladder).
    market_id = await _seed_market(system_engine)
    subject = await _seed_property(system_engine, market_id=market_id, lon=-96.0, lat=32.0)
    async with system_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO market_stats "
                "(id, market_id, geo_level, geo_id, metric, period, value, source) "
                "VALUES (:id, :mkt, 'metro', :geo, 'median_sale_ppsf', :period, 170, 'tierA')"
            ),
            {"id": _uid(), "mkt": market_id, "geo": _uid()[:12], "period": date(2026, 6, 1)},
        )

    async with await _system_session(system_engine) as db:
        result = await service.value_property(db, property_id=uuid.UUID(subject))
        await db.commit()

    assert result.arv is not None and result.arv.method == "model-prior"
    assert result.arv.confidence is not None and result.arv.confidence < 1


async def test_get_valuation_reads_latest_persisted(system_engine: AsyncEngine) -> None:
    market_id = await _seed_market(system_engine)
    sale_src = await _seed_source(system_engine, tier="mls")
    subject = await _seed_property(system_engine, market_id=market_id, lon=-97.5, lat=30.5)
    for (lon, lat), price in zip(
        _ring(-97.5, 30.5, 4), ["280000", "285000", "290000", "275000"], strict=True
    ):
        comp = await _seed_property(system_engine, market_id=market_id, lon=lon, lat=lat)
        await _seed_sold_listing(
            system_engine, property_id=comp, source_id=sale_src, close_price=price, days_ago=30
        )

    async with await _system_session(system_engine) as db:
        await service.value_property(db, property_id=uuid.UUID(subject))
        await db.commit()
    async with await _system_session(system_engine) as db:
        read = await service.get_property_valuation(db, property_id=uuid.UUID(subject))

    assert read.arv is not None and read.arv.point is not None
    assert read.arv.comp_set_id is not None  # persisted comp set is linked


async def test_user_pin_exclude_recomputes_on_org_session(
    system_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    org_id, user_id = _uid(), _uid()
    async with system_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO orgs (id, clerk_org_id, name, slug) "
                "VALUES (:id, :clerk, 'Acme', :slug)"
            ),
            {"id": org_id, "clerk": _uid(), "slug": f"acme-{_uid()[:8]}"},
        )
        await conn.execute(
            text("INSERT INTO users (id, clerk_user_id, email) VALUES (:id, :clerk, :email)"),
            {"id": user_id, "clerk": _uid(), "email": f"u{_uid()[:8]}@x.com"},
        )

    market_id = await _seed_market(system_engine)
    sale_src = await _seed_source(system_engine, tier="mls")
    subject = await _seed_property(system_engine, market_id=market_id, lon=-97.1, lat=30.1)
    comp_ids = []
    for (lon, lat), price in zip(
        _ring(-97.1, 30.1, 4), ["320000", "330000", "315000", "500000"], strict=True
    ):
        comp = await _seed_property(system_engine, market_id=market_id, lon=lon, lat=lat)
        comp_ids.append(comp)
        await _seed_sold_listing(
            system_engine, property_id=comp, source_id=sale_src, close_price=price, days_ago=40
        )

    # Exclude the $500k outlier on the org session; the shared valuation is never mutated.
    async with _app_tx(app_engine, org_id=org_id, user_id=user_id) as db:
        out = await service.apply_comp_edits(
            db,
            property_id=uuid.UUID(subject),
            valuation_kind=ValuationKind.ARV,
            edits=CompEditRequest(exclude_property_ids=[uuid.UUID(comp_ids[3])],
                                  exclude_reason="outlier sale"),
            org_id=uuid.UUID(org_id),
            user_id=uuid.UUID(user_id),
        )

    assert out.kind == ValuationKind.ARV.value
    assert out.point is not None
    assert "user-edited" in (out.method or "")
    # The excluded outlier should not be among the returned comps.
    included = [c.property_id for c in out.comps]
    assert uuid.UUID(comp_ids[3]) not in included


# --- Financial analysis (§26.2–§26.8) ---------------------------------------------------


async def _seed_tax(engine: AsyncEngine, *, property_id: str, amount: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tax_records (id, property_id, tax_year, annual_tax_amount) "
                "VALUES (:id, :pid, 2025, :amt)"
            ),
            {"id": _uid(), "pid": property_id, "amt": amount},
        )


async def test_analyze_property_ltr_end_to_end(system_engine: AsyncEngine) -> None:
    """Value a property from sold + rental comps, then underwrite it as an LTR: the analysis
    should read the persisted ARV/rent, assemble the pro-forma, and produce cap rate / DSCR /
    cash flow with their ledgers — the full §26 seam over real data."""
    market_id = await _seed_market(system_engine)
    sale_src = await _seed_source(system_engine, tier="mls")
    rent_src = await _seed_source(system_engine, tier="rental")
    subject = await _seed_property(system_engine, market_id=market_id, lon=-97.30, lat=30.30)
    await _seed_tax(system_engine, property_id=subject, amount="6000")

    for (lon, lat), price in zip(
        _ring(-97.30, 30.30, 4), ["300000", "310000", "295000", "305000"], strict=True
    ):
        comp = await _seed_property(system_engine, market_id=market_id, lon=lon, lat=lat)
        await _seed_sold_listing(
            system_engine, property_id=comp, source_id=sale_src, close_price=price, days_ago=40
        )
    for (lon, lat), rent in zip(
        _ring(-97.30, 30.30, 4), ["2500", "2600", "2450", "2550"], strict=True
    ):
        comp = await _seed_property(system_engine, market_id=market_id, lon=lon, lat=lat)
        await _seed_rental_listing(
            system_engine, property_id=comp, source_id=rent_src, rent=rent, days_ago=20
        )

    async with await _system_session(system_engine) as db:
        await service.value_property(db, property_id=uuid.UUID(subject))
        await db.commit()
    async with await _system_session(system_engine) as db:
        out = await service.analyze_property(
            db, property_id=uuid.UUID(subject), strategy=Strategy.LTR
        )
        await db.commit()

    assert out.strategy == "ltr"
    assert out.proforma is not None and out.proforma.noi != 0
    assert out.return_metrics.cap_rate is not None and out.return_metrics.cap_rate > 0
    assert out.return_metrics.dscr is not None
    assert out.acquisition is not None and out.acquisition.all_in > 0
    assert len(out.financing) == 4  # conventional / dscr / hard_money / cash
    assert out.proforma.taxes == 6000  # the assessor record flowed into opex

    # Persisted and re-readable.
    async with await _system_session(system_engine) as db:
        read = await service.get_analysis(db, property_id=uuid.UUID(subject), strategy=Strategy.LTR)
    assert read is not None and read.return_metrics.cap_rate == out.return_metrics.cap_rate


async def test_analyze_flip_requires_arv_but_skips_gracefully_in_batch(
    system_engine: AsyncEngine,
) -> None:
    # A property with no comps at all → no ARV/rent → the batch analyzer skips every strategy
    # rather than 500-ing, but still must not raise.
    market_id = await _seed_market(system_engine)
    subject = await _seed_property(system_engine, market_id=market_id, lon=-95.0, lat=31.0)
    async with system_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO listings "
                "(id, property_id, source_id, source_listing_key, status, list_price, list_date) "
                "VALUES (:id, :pid, :sid, :key, 'active', 250000, :list)"
            ),
            {"id": _uid(), "pid": subject, "sid": await _seed_source(system_engine),
             "key": _uid()[:12], "list": date.today()},
        )

    async with await _system_session(system_engine) as db:
        await service.value_property(db, property_id=uuid.UUID(subject))
        await db.commit()
        result = await service.analyze_strategies(db, property_id=uuid.UUID(subject))
        await db.commit()
    # No comps → flip/BRRRR unavailable (no ARV); the call returns whatever could be underwritten.
    assert "flip" not in result
