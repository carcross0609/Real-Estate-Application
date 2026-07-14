"""ATTOM adapter — Tier B national property data (§14.1): assessor rolls, deeds/ownership,
and tax. Fills the public-records enrichment that FR-005 requires and that no MLS feed
carries. One ATTOM record fans out to three canonical deltas — the physical `PropertyDelta`
(so ATTOM can resolve/enrich a property that has no active listing, enabling Analyze-Any-
Address, FR-017), plus an `OwnershipDelta` and a `TaxDelta`.

ATTOM's response shape (`{"status": {...}, "property": [ ... ]}`) and its nested
`identifier/address/summary/building/lot/assessment/sale` blocks are mapped here; the
adapter is deliberately defensive (every field optional) because coverage varies by county
(PRD §31 A6 — degraded confidence where public records are thin, never a hard failure).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from deallens.modules.ingestion.adapters.base import (
    AdapterConfig,
    CanonicalDelta,
    Cursor,
    FetchTask,
    RawBatch,
    RawItem,
    SourceHealth,
)
from deallens.modules.ingestion.adapters.registry import register_adapter
from deallens.modules.ingestion.models import DataSourceTier, PropertyType
from deallens.modules.ingestion.normalize import normalize_address
from deallens.modules.ingestion.schemas import OwnershipDelta, PropertyDelta, TaxDelta

# ATTOM proptype/propclass tokens → our PropertyType, matched by substring on the uppercased
# class string (ATTOM's vocabulary is inconsistent across counties).
_TYPE_TOKENS: tuple[tuple[str, PropertyType], ...] = (
    ("CONDO", PropertyType.CONDO),
    ("TOWNHOUSE", PropertyType.TOWNHOME),
    ("ROWHOUSE", PropertyType.TOWNHOME),
    ("DUPLEX", PropertyType.MF_2_4),
    ("TRIPLEX", PropertyType.MF_2_4),
    ("QUADRUPLEX", PropertyType.MF_2_4),
    ("APARTMENT", PropertyType.MF_5PLUS),
    ("MULTI", PropertyType.MF_5PLUS),
    ("COMMERCIAL", PropertyType.COMMERCIAL),
    ("VACANT", PropertyType.LAND),
    ("LAND", PropertyType.LAND),
    ("MIXED", PropertyType.MIXED),
    ("SFR", PropertyType.SFR),
    ("SINGLE FAMILY", PropertyType.SFR),
)

_ACRE_TO_SQFT = Decimal(43560)
_DEFAULT_PAGE_SIZE = 100


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    dec = _as_decimal(value)
    return int(dec) if dec is not None else None


def _as_float(value: Any) -> float | None:
    dec = _as_decimal(value)
    return float(dec) if dec is not None else None


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _dig(obj: Mapping[str, Any], *path: str) -> Any:
    """Nested-get that tolerates missing intermediate objects (ubiquitous with ATTOM)."""
    cur: Any = obj
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _property_type(rec: Mapping[str, Any]) -> PropertyType | None:
    blob = " ".join(
        str(v or "")
        for v in (_dig(rec, "summary", "proptype"), _dig(rec, "summary", "propclass"))
    ).upper()
    for token, ptype in _TYPE_TOKENS:
        if token in blob:
            return ptype
    return None


def _lot_sqft(rec: Mapping[str, Any]) -> int | None:
    sqft = _as_int(_dig(rec, "lot", "lotsize2"))
    if sqft:
        return sqft
    acres = _as_decimal(_dig(rec, "lot", "lotsize1"))
    return int(acres * _ACRE_TO_SQFT) if acres else None


def _property_delta(rec: Mapping[str, Any]) -> PropertyDelta:
    address = normalize_address(
        line1=str(_dig(rec, "address", "line1") or ""),
        city=str(_dig(rec, "address", "locality") or ""),
        state=str(_dig(rec, "address", "countrySubd") or ""),
        zip_code=str(_dig(rec, "address", "postal1") or ""),
        plus4=str(_dig(rec, "address", "postal2") or "") or None,
    )
    mapped_top = {"identifier", "address", "location", "summary", "building", "lot", "vintage"}
    attrs: dict[str, object] = {k: v for k, v in rec.items() if k not in mapped_top}
    return PropertyDelta(
        apn=str(_dig(rec, "identifier", "apn") or "").strip() or None,
        fips=(str(_dig(rec, "identifier", "fips") or "").strip() or None),
        address=address,
        latitude=_as_float(_dig(rec, "location", "latitude")),
        longitude=_as_float(_dig(rec, "location", "longitude")),
        property_type=_property_type(rec),
        beds=_as_int(_dig(rec, "building", "rooms", "beds")),
        baths=_as_decimal(_dig(rec, "building", "rooms", "bathstotal")),
        sqft=_as_int(_dig(rec, "building", "size", "livingsize"))
        or _as_int(_dig(rec, "building", "size", "universalsize")),
        lot_sqft=_lot_sqft(rec),
        year_built=_as_int(_dig(rec, "summary", "yearbuilt")),
        stories=_as_int(_dig(rec, "building", "summary", "levels")),
        garage_spaces=_as_int(_dig(rec, "building", "parking", "prkgSize")),
        pool=(_dig(rec, "lot", "poolind") == "Y") if _dig(rec, "lot", "poolind") else None,
        attrs=attrs,
    )


def _ownership_delta(rec: Mapping[str, Any], prop: PropertyDelta) -> OwnershipDelta | None:
    owner = _dig(rec, "assessment", "owner")
    sale = _dig(rec, "sale")
    if not isinstance(owner, Mapping) and not isinstance(sale, Mapping):
        return None
    name_parts = [
        str(_dig(owner or {}, "owner1", "firstnameandmi") or ""),
        str(_dig(owner or {}, "owner1", "lastname") or ""),
    ]
    owner_name = " ".join(p for p in name_parts if p).strip() or None

    status = str(_dig(owner or {}, "absenteeownerstatus") or "").upper()
    owner_occupied: bool | None = None
    absentee: bool | None = None
    if "OWNER OCCUPIED" in status:
        owner_occupied, absentee = True, False
    elif "ABSENTEE" in status:
        owner_occupied, absentee = False, True

    mailing_raw = str(_dig(owner or {}, "mailingaddressoneline") or "")
    return OwnershipDelta(
        property=prop,
        owner_name=owner_name,
        owner_mailing_address=None,  # one-line mailing string isn't reliably parseable; kept
        # in attrs by the caller if needed — absentee flag is the load-bearing signal.
        owner_occupied=owner_occupied,
        absentee=absentee,
        ownership_length_months=None,  # derived from last_sale_date at attach time (DB clock)
        last_sale_price=_as_decimal(_dig(sale or {}, "amount", "saleamt")),
        last_sale_date=_parse_date(_dig(sale or {}, "salesearchdate")),
        deed_date=_parse_date(_dig(sale or {}, "saleTransDate")),
        deed_type=str(_dig(sale or {}, "amount", "saledoctype") or "") or None,
    ) if (owner_name or mailing_raw or sale) else None


def _tax_delta(rec: Mapping[str, Any]) -> TaxDelta | None:
    tax = _dig(rec, "assessment", "tax")
    assessed = _dig(rec, "assessment", "assessed")
    tax_year = _as_int(_dig(tax or {}, "taxyear"))
    if tax_year is None:
        return None
    assessed_total = _as_decimal(_dig(assessed or {}, "assdttlvalue"))
    tax_amt = _as_decimal(_dig(tax or {}, "taxamt"))
    # Effective rate as a decimal fraction, when both sides are present and sane.
    tax_rate = (
        (tax_amt / assessed_total).quantize(Decimal("0.000001"))
        if tax_amt and assessed_total and assessed_total > 0
        else None
    )
    prop = _dig(rec, "property")  # unused; TaxDelta carries its own locator below
    _ = prop
    return TaxDelta(
        property=_property_delta(rec),
        tax_year=tax_year,
        assessed_value=assessed_total,
        land_value=_as_decimal(_dig(assessed or {}, "assdlandvalue")),
        improvement_value=_as_decimal(_dig(assessed or {}, "assdimprvalue")),
        annual_tax_amount=tax_amt,
        tax_rate=tax_rate,
        reassessed_on_sale=None,  # jurisdiction rule; set from market config at engine time
    )


class AttomAdapter:
    """ATTOM property-data source. `options` sets the pull window: `postalcodes` (list) or
    `geoid` for batch backfill, `page_size`, and the `endpoint` (defaults to the property
    snapshot). Auth (`apikey` header) is bound to the transport by the worker.
    """

    source_key = "attom"
    tier = DataSourceTier.PROPERTY_DATA

    def __init__(self, config: AdapterConfig) -> None:
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._endpoint = str(config.options.get("endpoint", "property/snapshot"))
        self._page_size = int(config.options.get("page_size", _DEFAULT_PAGE_SIZE))
        self._postalcodes: list[str] = [
            str(z) for z in config.options.get("postalcodes", [])
        ]

    def plan(self, cursor: Cursor) -> Iterable[FetchTask]:
        """One task per (postalcode, page). ATTOM has no delta cursor like RESO — enrichment
        is refreshed on a slow cadence (§14.2: Tier B daily/weekly), so a run re-snapshots the
        configured postal windows; the versioned upsert makes re-ingesting unchanged rows a
        no-op (§9.5), so a full re-snapshot is cheap and safe.
        """
        page = cursor.page
        return [
            FetchTask(params={"postalcode": zip_code, "page": page})
            for zip_code in (self._postalcodes or [""])
        ]

    async def fetch(self, task: FetchTask) -> RawBatch:
        params: dict[str, str] = {
            "page": str(int(task.params.get("page", 0)) + 1),  # ATTOM pages are 1-based
            "pagesize": str(self._page_size),
        }
        if task.params.get("postalcode"):
            params["postalcode"] = str(task.params["postalcode"])
        url = f"{self._base_url}/{self._endpoint}"
        body = await self._config.transport.fetch_json(url, params=params)

        records = body.get("property", []) or []
        items = [
            RawItem(
                record_type="assessor",
                source_native_id=str(
                    _dig(rec, "identifier", "attomId")
                    or _dig(rec, "identifier", "obPropId")
                    or ""
                ),
                payload=rec,
                source_ts=_vintage_ts(rec),
            )
            for rec in records
        ]
        exhausted = len(records) < self._page_size
        next_cursor = Cursor(page=0 if exhausted else task.params.get("page", 0) + 1)
        total = _dig(body, "status", "total")
        return RawBatch(
            items=items,
            next_cursor=next_cursor,
            fetched_at=datetime.now(UTC),
            source_reported_total=_as_int(total),
        )

    def normalize(self, item: RawItem) -> list[CanonicalDelta]:
        rec = item.payload
        prop = _property_delta(rec)
        deltas: list[CanonicalDelta] = [prop]
        ownership = _ownership_delta(rec, prop)
        if ownership is not None:
            deltas.append(ownership)
        tax = _tax_delta(rec)
        if tax is not None:
            deltas.append(tax)
        return deltas

    def health(self) -> SourceHealth:
        return SourceHealth(ok=True)


def _vintage_ts(rec: Mapping[str, Any]) -> datetime | None:
    last_modified = _dig(rec, "vintage", "lastModified")
    if not last_modified or not isinstance(last_modified, str):
        return None
    parsed = _parse_date(last_modified)
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC) if parsed else None


@register_adapter("attom")
def _build_attom(config: AdapterConfig) -> AttomAdapter:
    return AttomAdapter(config)
