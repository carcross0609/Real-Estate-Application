"""End-to-end scoring against a live Postgres: seed a property with sold + rental comps, a tax
record, and photos; run the whole pipeline (comps → valuation → financial analysis → vision →
score) and assert the persisted `scores` + `score_factors`. This is the capstone integration —
every engine feeding the investment score. Skips when the DB isn't up/migrated (see conftest).

Run with: make dev && make api-migrate && .venv/bin/pytest tests/integration/test_scoring.py -q
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.core.enums import Strategy
from deallens.modules.engine import service as engine_service
from deallens.modules.scoring import service as scoring_service
from deallens.modules.vision import service as vision_service

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


async def _session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


async def _seed_full_property(engine: AsyncEngine) -> tuple[str, str]:
    """A subject with sold comps, rental comps, a tax record, and graded photos — enough for the
    whole stack to produce a real score."""
    market_id, sale_src, rent_src = _uid(), _uid(), _uid()
    subject, listing_id = _uid(), _uid()
    lon, lat = -97.20, 30.20
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
                 "year_built, geom) VALUES (:id, :m, 'sfr', 3, 2, 1800, 2000, "
                 "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"),
            {"id": subject, "m": market_id, "lon": lon, "lat": lat})
        await conn.execute(
            text("INSERT INTO listings (id, property_id, source_id, source_listing_key, status, "
                 "list_price, list_date, remarks) VALUES (:id, :p, :s, :k, 'active', 300000, :d, "
                 "'Motivated seller, property sold as-is, needs TLC')"),
            {"id": listing_id, "p": subject, "s": sale_src, "k": _uid()[:12], "d": date.today()})
        await conn.execute(
            text("INSERT INTO tax_records (id, property_id, tax_year, annual_tax_amount) "
                 "VALUES (:id, :p, 2025, 6000)"), {"id": _uid(), "p": subject})
        # Sold comps around the subject.
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
        # Rental comps.
        for i, rent in enumerate(["2500", "2600", "2450", "2550"]):
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
        # A couple of graded photos on the subject listing.
        photo_ids = [_uid(), _uid()]
        for i, pid in enumerate(photo_ids):
            await conn.execute(
                text("INSERT INTO listing_photos (id, listing_id, position, content_hash) "
                     "VALUES (:id, :l, :pos, :ch)"),
                {"id": pid, "l": listing_id, "pos": i, "ch": _uid()[:16].encode()})
    return subject, listing_id


async def _run_full_stack(engine: AsyncEngine, subject: str, listing_id: str) -> None:
    async with await _session(engine) as db:
        await engine_service.value_property(db, property_id=uuid.UUID(subject))
        await vision_service.analyze_listing(
            db, listing_id=uuid.UUID(listing_id),
            hints={},  # no per-photo hints → unknown grades, exercises degraded path
        )
        for strat in (Strategy.LTR, Strategy.FLIP, Strategy.BRRRR):
            await engine_service.analyze_property(
                db, property_id=uuid.UUID(subject), strategy=strat
            )
        await db.commit()


async def test_full_pipeline_scores_property(system_engine: AsyncEngine) -> None:
    subject, listing_id = await _seed_full_property(system_engine)
    await _run_full_stack(system_engine, subject, listing_id)

    async with await _session(system_engine) as db:
        result = await scoring_service.score_property(db, property_id=uuid.UUID(subject))
        await db.commit()

    assert 0 <= result.overall_score <= 100
    assert result.winning_strategy is not None
    assert result.strategy_scores  # per-strategy breakdown
    assert result.category_scores.profitability is not None
    assert 0 <= result.confidence_score <= 100
    # The motivated-seller remark should surface as a D-group signal in the ledger.
    winner = next(s for s in result.strategy_scores if s.strategy == result.winning_strategy)
    keys = {f.factor_key for f in winner.factors}
    assert "price_vs_avm" in keys  # deal-dynamics factor computed from list vs as-is


async def test_score_persisted_and_readable(system_engine: AsyncEngine) -> None:
    subject, listing_id = await _seed_full_property(system_engine)
    await _run_full_stack(system_engine, subject, listing_id)
    async with await _session(system_engine) as db:
        await scoring_service.score_property(db, property_id=uuid.UUID(subject))
        await db.commit()

    async with await _session(system_engine) as db:
        overall = await scoring_service.get_score(db, property_id=uuid.UUID(subject))
        ltr = await scoring_service.get_score(
            db, property_id=uuid.UUID(subject), strategy=Strategy.LTR
        )
    assert overall is not None and overall.strategy is Strategy.OVERALL
    assert overall.winning_strategy is not None
    assert ltr is not None and ltr.strategy is Strategy.LTR

    # score_factors ledger persisted for the overall row.
    async with system_engine.connect() as conn:
        n = await conn.scalar(
            text("SELECT count(*) FROM score_factors WHERE score_id = :sid"), {"sid": overall.id}
        )
    assert n and n > 0
