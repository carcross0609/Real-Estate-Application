"""End-to-end vision pipeline against a live Postgres: seed a listing with photos, run the
deterministic provider over them, and assert the persisted `photo_analyses` + `property_conditions`
and the recomputed rehab + scoring factors. Skips when the DB isn't up/migrated (see conftest).

Run with: make dev && make api-migrate && .venv/bin/pytest tests/integration/test_vision.py -q
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.modules.vision import service

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


async def _session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


async def _seed_listing_with_photos(
    engine: AsyncEngine, *, n_photos: int
) -> tuple[str, str, list[str]]:
    market_id, source_id, prop_id, listing_id = _uid(), _uid(), _uid(), _uid()
    photo_ids: list[str] = []
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO markets (id, cbsa_code, name, state, status) "
                 "VALUES (:id, :cbsa, 'M', 'TX', 'active')"),
            {"id": market_id, "cbsa": _uid()[:12]},
        )
        await conn.execute(
            text("INSERT INTO data_sources (id, source_key, name, tier) "
                 "VALUES (:id, :key, 'F', 'mls')"),
            {"id": source_id, "key": f"s_{_uid()[:8]}"},
        )
        await conn.execute(
            text("INSERT INTO properties (id, market_id, property_type, beds, baths, sqft) "
                 "VALUES (:id, :mkt, 'sfr', 3, 2, 1800)"),
            {"id": prop_id, "mkt": market_id},
        )
        await conn.execute(
            text("INSERT INTO listings "
                 "(id, property_id, source_id, source_listing_key, status, list_price, list_date) "
                 "VALUES (:id, :pid, :sid, :key, 'active', 300000, :ld)"),
            {"id": listing_id, "pid": prop_id, "sid": source_id, "key": _uid()[:12],
             "ld": date.today()},
        )
        for i in range(n_photos):
            photo_id = _uid()
            photo_ids.append(photo_id)
            await conn.execute(
                text("INSERT INTO listing_photos (id, listing_id, position, content_hash) "
                     "VALUES (:id, :lid, :pos, :ch)"),
                {"id": photo_id, "lid": listing_id, "pos": i, "ch": _uid()[:16].encode()},
            )
    return prop_id, listing_id, photo_ids


async def test_analyze_listing_persists_condition_and_rehab(system_engine: AsyncEngine) -> None:
    prop_id, listing_id, photo_ids = await _seed_listing_with_photos(system_engine, n_photos=3)
    # Drive the deterministic provider with per-photo hints: a dated kitchen, a fine bath, a
    # foundation flag in the basement.
    hints = {
        uuid.UUID(photo_ids[0]): {"room_type": "kitchen", "condition_grade": 2},
        uuid.UUID(photo_ids[1]): {"room_type": "bath", "condition_grade": 4},
        uuid.UUID(photo_ids[2]): {
            "room_type": "basement", "condition_grade": 2,
            "red_flags": [{"type": "foundation_crack", "severity": "severe"}],
        },
    }
    async with await _session(system_engine) as db:
        out = await service.analyze_listing(
            db, listing_id=uuid.UUID(listing_id), hints=hints
        )
        await db.commit()

    assert out.kitchen_grade == 2
    assert out.bath_grade == 4
    assert out.renovation_difficulty is not None and out.renovation_difficulty >= 3
    assert any(f.type.value == "foundation_crack" for f in out.red_flags)
    assert out.rehab is not None and out.rehab.total_mid > 0
    assert out.rehab.red_flags_present is True

    # Persisted photo_analyses rows exist for the pipeline version.
    async with system_engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM photo_analyses WHERE photo_id = ANY(:ids)"),
            {"ids": [uuid.UUID(p) for p in photo_ids]},
        )
    assert count == 3


async def test_get_condition_reads_back_and_factors(system_engine: AsyncEngine) -> None:
    prop_id, listing_id, photo_ids = await _seed_listing_with_photos(system_engine, n_photos=2)
    hints = {
        uuid.UUID(photo_ids[0]): {"room_type": "kitchen", "condition_grade": 3},
        uuid.UUID(photo_ids[1]): {"room_type": "exterior_front", "condition_grade": 3},
    }
    async with await _session(system_engine) as db:
        await service.analyze_listing(db, listing_id=uuid.UUID(listing_id), hints=hints)
        await db.commit()

    async with await _session(system_engine) as db:
        read = await service.get_property_condition(db, property_id=uuid.UUID(prop_id))
        assert read is not None and read.kitchen_grade == 3
        assert read.rehab is not None

        factors = await service.condition_scoring_factors(
            db, property_id=uuid.UUID(prop_id), arv=Decimal("400000"),
            list_price=Decimal("300000"),
        )
    assert factors is not None
    assert factors.rehab_to_arv_ratio is not None
    assert factors.condition_arbitrage is not None


async def test_empty_listing_still_produces_degraded_condition(system_engine: AsyncEngine) -> None:
    prop_id, listing_id, _ = await _seed_listing_with_photos(system_engine, n_photos=0)
    async with await _session(system_engine) as db:
        out = await service.analyze_listing(db, listing_id=uuid.UUID(listing_id))
        await db.commit()
    # No photos → all-unknown, zero-confidence, degraded — never a blank or a defaulted average.
    assert out.confidence == Decimal("0")
    assert out.kitchen_grade is None
    assert out.rehab is not None and out.rehab.total_mid == Decimal("0.00")
