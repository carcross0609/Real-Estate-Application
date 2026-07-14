"""Adapter normalization + fetch unit tests (no DB, no network — `FixtureTransport`).

These pin the source→canonical mapping that the rest of the pipeline trusts: a RESO record
becomes a `ListingDelta` with the right status/type/price/photos, an ATTOM record fans out to
property + ownership + tax, and unknown fields survive into `attrs` (§14.2 — never dropped).
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from deallens.modules.ingestion.adapters import build_adapter
from deallens.modules.ingestion.adapters.base import (
    AdapterConfig,
    FetchTask,
    FixtureTransport,
    SourceAdapter,
)
from deallens.modules.ingestion.models import ListingStatus, PropertyType
from deallens.modules.ingestion.schemas import (
    ListingDelta,
    OwnershipDelta,
    PropertyDelta,
    TaxDelta,
)

RESO_RECORD = {
    "ListingKey": "MLS-1001",
    "StandardStatus": "Active",
    "ListPrice": 425000,
    "ListingContractDate": "2026-06-01",
    "DaysOnMarket": 12,
    "PublicRemarks": "Charming bungalow, motivated seller.",
    "ModificationTimestamp": "2026-07-10T14:30:00Z",
    "PropertySubType": "Single Family Residence",
    "BedroomsTotal": 3,
    "BathroomsFull": 2,
    "BathroomsHalf": 1,
    "LivingArea": 1850,
    "LotSizeSquareFeet": 7200,
    "YearBuilt": 1998,
    "StoriesTotal": 2,
    "GarageSpaces": 2,
    "PoolPrivateYN": True,
    "AssociationFee": 600,
    "AssociationFeeFrequency": "Annually",
    "StreetNumber": "123",
    "StreetDirPrefix": "North",
    "StreetName": "Main",
    "StreetSuffix": "Street",
    "City": "Austin",
    "StateOrProvince": "TX",
    "PostalCode": "78701",
    "Latitude": 30.2672,
    "Longitude": -97.7431,
    "ParcelNumber": "0203110000",
    "CountyFIPS": "48453",
    "ListAgentFullName": "Jane Broker",
    "ListOfficeName": "Acme Realty",
    "OriginatingSystemName": "ACTRIS",
    "Media": [
        {"MediaURL": "https://cdn/x/1.jpg", "Order": 1},
        {"MediaURL": "https://cdn/x/2.jpg", "Order": 2},
    ],
    "SomeVendorField": "keep-me",
}


def _reso_adapter() -> SourceAdapter:
    cfg = AdapterConfig(source_id=uuid4(), transport=FixtureTransport({}), base_url="https://reso")
    return build_adapter("mls_grid", cfg)


def test_reso_normalize_maps_core_fields() -> None:
    adapter = _reso_adapter()
    from deallens.modules.ingestion.adapters.base import RawItem

    item = RawItem(record_type="listing", source_native_id="MLS-1001", payload=RESO_RECORD)
    deltas = adapter.normalize(item)
    assert len(deltas) == 1
    listing = deltas[0]
    assert isinstance(listing, ListingDelta)
    assert listing.source_listing_key == "MLS-1001"
    assert listing.status == ListingStatus.ACTIVE
    assert listing.list_price == Decimal("425000")
    assert listing.list_date == date(2026, 6, 1)
    assert listing.dom_current == 12

    prop = listing.property
    assert prop.property_type == PropertyType.SFR
    assert prop.beds == 3
    assert prop.baths == Decimal("2.5")  # 2 full + 1 half
    assert prop.sqft == 1850
    assert prop.apn == "0203110000"
    assert prop.fips == "48453"
    assert prop.address.line1 == "123 N MAIN ST"
    assert prop.hoa_monthly == Decimal("50.00")  # 600/yr -> 50/mo
    assert prop.attrs["SomeVendorField"] == "keep-me"  # long-tail preserved (§14.2)
    assert len(listing.photos) == 2


def test_reso_unmapped_status_defaults_safe() -> None:
    adapter = _reso_adapter()
    from deallens.modules.ingestion.adapters.base import RawItem

    rec = {**RESO_RECORD, "StandardStatus": "Weird New Status"}
    item = RawItem(record_type="listing", source_native_id="MLS-1001", payload=rec)
    listing = adapter.normalize(item)[0]
    assert isinstance(listing, ListingDelta)
    # Never silently classified active; raw value preserved for review.
    assert listing.status == ListingStatus.WITHDRAWN
    assert listing.property.attrs["_unmapped_status"] == "Weird New Status"


@pytest.mark.asyncio
async def test_reso_fetch_reads_page() -> None:
    transport = FixtureTransport(
        {"https://reso/Property": {"value": [RESO_RECORD], "@odata.count": 1}}
    )
    cfg = AdapterConfig(source_id=uuid4(), transport=transport, base_url="https://reso")
    adapter = build_adapter("mls_grid", cfg)
    batch = await adapter.fetch(FetchTask(params={"since": None, "page": 0}))
    assert len(batch.items) == 1
    assert batch.items[0].source_native_id == "MLS-1001"
    assert batch.source_reported_total == 1
    assert batch.next_cursor.page == 0  # short page => exhausted


ATTOM_RECORD = {
    "identifier": {"attomId": "A-9", "fips": "48453", "apn": "0203110000"},
    "address": {
        "line1": "123 N Main St", "locality": "Austin", "countrySubd": "TX", "postal1": "78701",
    },
    "location": {"latitude": "30.2672", "longitude": "-97.7431"},
    "summary": {"proptype": "SFR", "propclass": "Single Family Residence", "yearbuilt": 1998},
    "building": {"rooms": {"beds": 3, "bathstotal": 2}, "size": {"livingsize": 1850}},
    "lot": {"lotsize2": 7200, "poolind": "Y"},
    "assessment": {
        "assessed": {"assdttlvalue": 250000, "assdlandvalue": 60000, "assdimprvalue": 190000},
        "tax": {"taxamt": 6250, "taxyear": 2025},
        "owner": {
            "owner1": {"lastname": "DOE", "firstnameandmi": "JOHN"},
            "absenteeownerstatus": "ABSENTEE(MAIL AND SITUS NOT =)",
            "mailingaddressoneline": "PO BOX 5, DALLAS, TX",
        },
    },
    "sale": {"amount": {"saleamt": 180000}, "salesearchdate": "2014-05-01"},
    "vintage": {"lastModified": "2026-01-15"},
}


def test_attom_normalize_fans_out() -> None:
    cfg = AdapterConfig(source_id=uuid4(), transport=FixtureTransport({}), base_url="https://attom")
    adapter = build_adapter("attom", cfg)
    from deallens.modules.ingestion.adapters.base import RawItem

    item = RawItem(record_type="assessor", source_native_id="A-9", payload=ATTOM_RECORD)
    deltas = adapter.normalize(item)
    kinds = {type(d) for d in deltas}
    assert kinds == {PropertyDelta, OwnershipDelta, TaxDelta}

    prop = next(d for d in deltas if isinstance(d, PropertyDelta))
    assert prop.apn == "0203110000" and prop.fips == "48453"
    assert prop.property_type == PropertyType.SFR
    assert prop.address.line1 == "123 N MAIN ST"

    owner = next(d for d in deltas if isinstance(d, OwnershipDelta))
    assert owner.absentee is True
    assert owner.owner_occupied is False
    assert owner.last_sale_price == Decimal("180000")
    assert owner.last_sale_date == date(2014, 5, 1)

    tax = next(d for d in deltas if isinstance(d, TaxDelta))
    assert tax.tax_year == 2025
    assert tax.assessed_value == Decimal("250000")
    assert tax.annual_tax_amount == Decimal("6250")
    assert tax.tax_rate == Decimal("0.025000")  # 6250 / 250000
