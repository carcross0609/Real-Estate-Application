"""End-to-end ingestion against a live Postgres (PostGIS): resolution/dedup, the versioned
upsert + change log, relist chaining, cross-source dedup by APN, the full adapter→raw→write
cycle, replay idempotency, and enrichment attach. Complements the pure unit tests (which pin
the logic) by exercising the real SQL — the partial unique index, JSONB address match, the
partitioned `listing_events`, and the geometry column.

Isolation: each test runs in one owner-role session that is rolled back at teardown (the
pipeline only flushes, never commits), so tests neither see nor leave each other's rows.
Skips wholesale when the DB isn't up/migrated (see tests/integration/conftest.py). Run:
    make dev && make api-migrate && .venv/bin/pytest tests/integration -q
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.modules.enrichment import service as enrichment_service
from deallens.modules.ingestion import service as ingestion_service
from deallens.modules.ingestion.adapters import build_adapter
from deallens.modules.ingestion.adapters.base import AdapterConfig, FixtureTransport
from deallens.modules.ingestion.events import CollectingEmitter, ListingUpserted, PropertyResolved
from deallens.modules.ingestion.models import (
    DataSourceTier,
    Listing,
    ListingEvent,
    ListingEventType,
    ListingPhoto,
    Property,
)
from deallens.modules.ingestion.pipeline import replay_raw_record, run_ingestion_cycle
from deallens.modules.ingestion.raw_store import InMemoryBlobStore
from deallens.modules.ingestion.schemas import (
    AddressNorm,
    ListingAttribution,
    ListingDelta,
    OwnershipDelta,
    PhotoDelta,
    PropertyDelta,
    TaxDelta,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db(system_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(system_engine, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()


def _address(line1: str = "123 N Main St", zip_code: str = "78701") -> AddressNorm:
    from deallens.modules.ingestion.normalize import normalize_address

    return normalize_address(line1=line1, city="Austin", state="TX", zip_code=zip_code)


def _property_delta(**over: object) -> PropertyDelta:
    base: dict[str, object] = dict(
        apn="0203110000",
        fips="48453",
        address=_address(),
        latitude=30.2672,
        longitude=-97.7431,
        beds=3,
        baths=Decimal("2.5"),
        sqft=1850,
        year_built=1998,
    )
    base.update(over)
    return PropertyDelta(**base)  # type: ignore[arg-type]


def _listing_delta(
    key: str = "MLS-1",
    *,
    price: str = "425000",
    source_ts: datetime | None = None,
    photos: list[PhotoDelta] | None = None,
    prop: PropertyDelta | None = None,
    list_date: date | None = None,
    status: object = None,
) -> ListingDelta:
    from deallens.modules.ingestion.models import ListingStatus

    return ListingDelta(
        source_listing_key=key,
        property=prop or _property_delta(),
        status=status or ListingStatus.ACTIVE,  # type: ignore[arg-type]
        list_price=Decimal(price),
        list_date=list_date or date(2026, 6, 1),
        attribution=ListingAttribution(mls_name="ACTRIS"),
        photos=photos or [],
        source_ts=source_ts or datetime(2026, 7, 10, tzinfo=UTC),
    )


async def _mls_source(db: AsyncSession, key: str = "mls_grid"):  # type: ignore[no-untyped-def]
    return await ingestion_service.register_data_source(
        db, source_key=key, name=key.upper(), tier=DataSourceTier.MLS
    )


async def _ingest(db: AsyncSession, delta: ListingDelta, source: object, emitter: object = None):  # type: ignore[no-untyped-def]
    """Ingest a listing with a fixed reference year (keeps DQ year-bounds deterministic)."""
    return await ingestion_service.ingest_listing(
        db, delta, source=source, emitter=emitter, reference_year=2026  # type: ignore[arg-type]
    )


async def _count(db: AsyncSession, model: Any, **where: object) -> int:
    stmt = select(func.count()).select_from(model)
    for col, val in where.items():
        stmt = stmt.where(getattr(model, col) == val)
    return int((await db.execute(stmt)).scalar_one())


# --- resolution + write ----------------------------------------------------------------


async def test_listing_ingest_creates_property_listing_and_events(db: AsyncSession) -> None:
    source = await _mls_source(db)
    emitter = CollectingEmitter()

    result = await _ingest(db, _listing_delta(), source, emitter=emitter)

    assert result.is_new and not result.stale
    prop = await db.get(Property, result.listing.property_id)
    assert prop is not None and prop.apn == "0203110000"
    # LISTED change event was recorded.
    listed = await _count(db, ListingEvent, listing_id=result.listing.id,
                          event_type=ListingEventType.LISTED)
    assert listed == 1
    # Events emitted for the downstream pipeline.
    kinds = {type(e) for e in emitter.events}
    assert PropertyResolved in kinds and ListingUpserted in kinds


async def test_versioned_upsert_skips_stale_and_logs_change(db: AsyncSession) -> None:
    source = await _mls_source(db)
    t1 = datetime(2026, 7, 10, tzinfo=UTC)
    t2 = datetime(2026, 7, 11, tzinfo=UTC)

    await _ingest(db, _listing_delta(price="425000", source_ts=t1), source)
    # Newer version with a price cut → applied + PRICE_CHANGE logged.
    r2 = await _ingest(db, _listing_delta(price="399000", source_ts=t2), source)
    assert not r2.stale and r2.listing.list_price == Decimal("399000")
    price_changes = await _count(db, ListingEvent, listing_id=r2.listing.id,
                                event_type=ListingEventType.PRICE_CHANGE)
    assert price_changes == 1

    # Replaying the OLD version (t1) is a no-op — out-of-order/duplicate delivery is safe.
    r_stale = await _ingest(db, _listing_delta(price="425000", source_ts=t1), source)
    assert r_stale.stale is True
    refreshed = await db.get(Listing, r2.listing.id)
    assert refreshed is not None and refreshed.list_price == Decimal("399000")  # unchanged


async def test_relisting_resolves_to_one_property_and_links(db: AsyncSession) -> None:
    source = await _mls_source(db)
    # First listing, then a NEW listing (different key) on the same address a year later.
    await _ingest(db, _listing_delta("MLS-OLD", list_date=date(2025, 1, 1)), source)
    r2 = await _ingest(db, _listing_delta("MLS-NEW", list_date=date(2026, 1, 1)), source)
    prop_id = r2.listing.property_id

    # Dedup: one canonical property, two listings chained.
    assert await _count(db, Property, id=prop_id) == 1
    assert await _count(db, Listing, property_id=prop_id) == 2
    assert r2.relisted_from is not None
    relist_links = await _count(db, ListingEvent, listing_id=r2.listing.id,
                               event_type=ListingEventType.RELISTED_LINK)
    assert relist_links == 1

    history = await ingestion_service.get_listing_history(db, prop_id)
    assert len(history) == 2


async def test_cross_source_dedup_by_apn(db: AsyncSession) -> None:
    mls = await _mls_source(db, "mls_grid")
    attom = await ingestion_service.register_data_source(
        db, source_key="attom", name="ATTOM", tier=DataSourceTier.PROPERTY_DATA
    )
    # MLS listing establishes the property...
    r = await _ingest(db, _listing_delta(), mls)
    prop_id = r.listing.property_id

    # ...ATTOM assessor record for the same APN/FIPS must resolve to the SAME property.
    tax = TaxDelta(property=_property_delta(), tax_year=2025, assessed_value=Decimal("250000"),
                   annual_tax_amount=Decimal("6250"))
    await enrichment_service.attach_tax(db, tax, source_id=attom.id)

    assert await _count(db, Property, apn="0203110000", fips="48453") == 1
    from deallens.modules.enrichment.models import TaxRecord

    assert await _count(db, TaxRecord, property_id=prop_id) == 1


# --- photo diff ------------------------------------------------------------------------


async def test_photo_set_diff_applies(db: AsyncSession) -> None:
    source = await _mls_source(db)
    t1 = datetime(2026, 7, 10, tzinfo=UTC)
    t2 = datetime(2026, 7, 11, tzinfo=UTC)
    r1 = await _ingest(
        db,
        _listing_delta(
            source_ts=t1, photos=[PhotoDelta(content_hash=b"a"), PhotoDelta(content_hash=b"b")]
        ),
        source,
    )
    assert await _count(db, ListingPhoto, listing_id=r1.listing.id) == 2

    # Newer version: drop b, add c → set is {a, c}.
    r2 = await _ingest(
        db,
        _listing_delta(
            source_ts=t2, photos=[PhotoDelta(content_hash=b"a"), PhotoDelta(content_hash=b"c")]
        ),
        source,
    )
    assert r2.photos_added == 1 and r2.photos_removed == 1
    assert await _count(db, ListingPhoto, listing_id=r2.listing.id) == 2


# --- full pipeline + replay ------------------------------------------------------------

_RESO_PAYLOAD = {
    "ListingKey": "MLS-777",
    "StandardStatus": "Active",
    "ListPrice": 350000,
    "ListingContractDate": "2026-06-15",
    "ModificationTimestamp": "2026-07-12T10:00:00Z",
    "PropertySubType": "Single Family Residence",
    "BedroomsTotal": 4,
    "BathroomsFull": 2,
    "LivingArea": 2100,
    "YearBuilt": 2005,
    "StreetNumber": "900",
    "StreetName": "Oak",
    "StreetSuffix": "Ave",
    "City": "Austin",
    "StateOrProvince": "TX",
    "PostalCode": "78704",
    "Latitude": 30.24,
    "Longitude": -97.77,
    "ParcelNumber": "0300550000",
    "CountyFIPS": "48453",
    "Media": [{"MediaURL": "https://cdn/a.jpg", "Order": 1}],
}


async def test_full_cycle_persists_raw_and_replay_is_idempotent(db: AsyncSession) -> None:
    source = await _mls_source(db)
    transport = FixtureTransport(
        {"https://reso/Property": {"value": [_RESO_PAYLOAD], "@odata.count": 1}}
    )
    adapter = build_adapter(
        "mls_grid",
        AdapterConfig(source_id=source.id, transport=transport, base_url="https://reso"),
    )
    blob = InMemoryBlobStore()

    result = await run_ingestion_cycle(
        db, adapter, source=source, blob_store=blob, reference_year=2026
    )
    assert result.records_fetched == 1
    assert result.records_upserted == 1
    assert result.high_watermark == datetime(2026, 7, 12, 10, 0, tzinfo=UTC)

    from deallens.modules.admin.models import IngestionRun, IngestionRunStatus
    from deallens.modules.ingestion.models import RawRecord

    run = await db.get(IngestionRun, result.run_id)
    assert run is not None and run.status == IngestionRunStatus.SUCCEEDED
    assert await _count(db, RawRecord, source_id=source.id) == 1
    assert await _count(db, Listing, source_id=source.id) == 1

    # Replay from stored raw bytes: no duplicate listing, same single row (NFR-05 idempotency).
    raw_rows = await db.execute(select(RawRecord).where(RawRecord.source_id == source.id))
    raw = raw_rows.scalars().one()
    await replay_raw_record(db, adapter, raw, blob_store=blob, source=source)
    assert await _count(db, Listing, source_id=source.id) == 1


# --- enrichment ------------------------------------------------------------------------


async def test_enrichment_attaches_ownership_with_derived_signals(db: AsyncSession) -> None:
    attom = await ingestion_service.register_data_source(
        db, source_key="attom", name="ATTOM", tier=DataSourceTier.PROPERTY_DATA
    )
    delta = OwnershipDelta(
        property=_property_delta(),
        owner_name="JOHN DOE",
        absentee=True,
        last_sale_date=date(2014, 5, 1),
    )
    record = await enrichment_service.attach_ownership(
        db, delta, source_id=attom.id, as_of=date(2026, 7, 1)
    )
    assert record.absentee is True
    assert record.owner_occupied is False  # derived from absentee
    # Hold time derived from last sale to `as_of`: 2014-05 → 2026-07 = 146 months.
    assert record.ownership_length_months == 146
