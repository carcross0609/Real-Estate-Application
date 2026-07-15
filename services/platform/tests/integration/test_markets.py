"""End-to-end market intelligence against a live Postgres: seed a market's `market_stats`, compute
the report, and assert the derived indices + market score are returned and persisted back as
metro-level stats (§28.4/§28.6). Skips when the DB isn't up/migrated (see conftest).

Run with: make dev && make api-migrate && .venv/bin/pytest tests/integration/test_markets.py -q
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.modules.markets import service

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


async def _session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


async def _seed_market_with_stats(engine: AsyncEngine) -> tuple[str, str]:
    market_id, cbsa = _uid(), _uid()[:12]
    stats = {
        "appreciation_3yr_annualized": "0.07", "median_dom": "25", "inventory_months": "3",
        "absorption_rate": "0.4", "rent_growth_3yr": "0.05", "rent_to_price": "0.09",
        "household_formation": "0.02", "school_rating_pct": "80", "crime_index": "20",
        "walkability": "70", "owner_occupancy_share": "0.7",
    }
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO markets (id, cbsa_code, name, state, status) "
                 "VALUES (:id, :c, 'Test Metro', 'TX', 'active')"),
            {"id": market_id, "c": cbsa})
        for metric, value in stats.items():
            await conn.execute(
                text("INSERT INTO market_stats (id, market_id, geo_level, geo_id, metric, period, "
                     "value, source) VALUES (:id, :m, 'metro', :g, :metric, :p, :v, 'tierA')"),
                {"id": _uid(), "m": market_id, "g": cbsa, "metric": metric,
                 "p": date(2026, 6, 1), "v": value})
    return market_id, cbsa


async def test_compute_market_report(system_engine: AsyncEngine) -> None:
    market_id, cbsa = await _seed_market_with_stats(system_engine)
    async with await _session(system_engine) as db:
        report = await service.compute_market_report(db, market_id=uuid.UUID(market_id))
        await db.commit()

    assert report.market_score is not None and report.market_score >= 70
    assert report.grade in {"A", "B"}
    assert len(report.indices) == 4
    assert all(i.score is not None for i in report.indices)
    # Metrics carry their measured geography label (§28.2).
    assert all(m.geo_level == "metro" for m in report.metrics)
    assert report.data_completeness > 0.9  # all expected metrics present


async def test_indices_persisted_back_as_stats(system_engine: AsyncEngine) -> None:
    market_id, cbsa = await _seed_market_with_stats(system_engine)
    async with await _session(system_engine) as db:
        await service.compute_market_report(db, market_id=uuid.UUID(market_id))
        await db.commit()

    # The derived market_liquidity (the scoring M-factor) + market_score are written back.
    async with system_engine.connect() as conn:
        liq = await conn.scalar(
            text("SELECT value FROM market_stats WHERE market_id = :m "
                 "AND metric = 'market_liquidity'"), {"m": market_id})
        score = await conn.scalar(
            text("SELECT value FROM market_stats WHERE market_id = :m AND metric = 'market_score'"),
            {"m": market_id})
    assert liq is not None and 0 <= liq <= 100
    assert score is not None


async def test_market_context_snapshot(system_engine: AsyncEngine) -> None:
    market_id, _ = await _seed_market_with_stats(system_engine)
    async with await _session(system_engine) as db:
        ctx = await service.market_context(db, market_id=uuid.UUID(market_id))
    assert ctx.market_score is not None
    assert ctx.momentum is not None and ctx.liquidity is not None
    assert "crime_index" in ctx.metrics
