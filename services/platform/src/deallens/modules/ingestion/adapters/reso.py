"""RESO Web API adapter — Tier A MLS feeds (§14.1): MLS Grid, Trestle (CoreLogic), Bridge
Interactive all speak the RESO Web API (OData) over the RESO Data Dictionary, so one adapter
covers them, differing only by `base_url` + auth (bound to the transport) + `options`. This
is exactly the "new market = configuration" payoff (NFR-12): a second MLS Grid market is a
new `data_sources` row, not new code.

Incremental pulls follow the RESO delta pattern (§14.2): order by `ModificationTimestamp`
ascending, filter `gt` the last high-watermark, page with `$top`/`$skip`. `ModificationTimestamp`
becomes the delta's `source_ts` — the version key the whole pipeline orders on (§9.5).

Only the RESO Data Dictionary standard fields are mapped to columns; everything else the
feed sends is preserved in `PropertyDelta.attrs` (§14.2 — unknown fields never dropped).
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
from deallens.modules.ingestion.models import DataSourceTier, ListingStatus, PropertyType
from deallens.modules.ingestion.normalize import normalize_address
from deallens.modules.ingestion.schemas import (
    ListingAttribution,
    ListingDelta,
    PhotoDelta,
    PropertyDelta,
)

# RESO StandardStatus → our ListingStatus (§11.3). RESO's vocabulary is broader; several
# values collapse to one of ours. Unmapped statuses fall through to WITHDRAWN with the raw
# value preserved in attrs so nothing is silently misclassified as active.
_STATUS_MAP: dict[str, ListingStatus] = {
    "Active": ListingStatus.ACTIVE,
    "Active Under Contract": ListingStatus.CONTINGENT,
    "Contingent": ListingStatus.CONTINGENT,
    "Pending": ListingStatus.PENDING,
    "Closed": ListingStatus.SOLD,
    "Sold": ListingStatus.SOLD,
    "Canceled": ListingStatus.WITHDRAWN,
    "Cancelled": ListingStatus.WITHDRAWN,
    "Withdrawn": ListingStatus.WITHDRAWN,
    "Expired": ListingStatus.EXPIRED,
    "Coming Soon": ListingStatus.COMING_SOON,
    "Hold": ListingStatus.WITHDRAWN,
    "Delete": ListingStatus.WITHDRAWN,
}

# RESO PropertySubType / PropertyType → our PropertyType. Checked subtype-first (more
# specific), then the coarse PropertyType.
_TYPE_MAP: dict[str, PropertyType] = {
    "Single Family Residence": PropertyType.SFR,
    "Single Family Detached": PropertyType.SFR,
    "Condominium": PropertyType.CONDO,
    "Townhouse": PropertyType.TOWNHOME,
    "Duplex": PropertyType.MF_2_4,
    "Triplex": PropertyType.MF_2_4,
    "Quadruplex": PropertyType.MF_2_4,
    "Multi Family": PropertyType.MF_5PLUS,
    "Residential Income": PropertyType.MF_5PLUS,
    "Land": PropertyType.LAND,
    "Unimproved Land": PropertyType.LAND,
    "Commercial": PropertyType.COMMERCIAL,
    "Business": PropertyType.COMMERCIAL,
    "Mixed Use": PropertyType.MIXED,
}

_DEFAULT_PAGE_SIZE = 200


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


def _parse_dt(value: Any) -> datetime | None:
    """Parse a RESO ISO-8601 timestamp. RESO emits `...Z`; normalize to aware UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_date(value: Any) -> date | None:
    dt = _parse_dt(value) if isinstance(value, str) and "T" in value else None
    if dt is not None:
        return dt.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


_MAPPED_KEYS = frozenset(
    {
        "ListingKey", "ListingId", "ListPrice", "ClosePrice", "StandardStatus", "MlsStatus",
        "ListingContractDate", "OnMarketDate", "CloseDate", "DaysOnMarket", "PublicRemarks",
        "ModificationTimestamp", "PropertyType", "PropertySubType", "BedroomsTotal",
        "BathroomsTotalInteger", "BathroomsFull", "BathroomsHalf", "LivingArea",
        "LotSizeSquareFeet", "YearBuilt", "StoriesTotal", "GarageSpaces", "PoolPrivateYN",
        "AssociationFee", "AssociationFeeFrequency", "Zoning", "StreetNumber",
        "StreetDirPrefix", "StreetName", "StreetSuffix", "StreetDirSuffix", "UnitNumber",
        "City", "StateOrProvince", "PostalCode", "PostalCodePlus4", "Latitude", "Longitude",
        "ParcelNumber", "CountyOrParish", "Media", "ListAgentFullName", "ListAgentMlsId",
        "ListOfficeName", "ListOfficePhone", "SourceSystemName", "OriginatingSystemName",
    }
)


class ResoAdapter:
    """A RESO Web API source. Built by the registry with a transport whose auth headers are
    already bound. `options` may override `resource` (default `Property`), `page_size`, and
    `backfill_months` (how far back the initial pull reaches — §14.1 "sold ≥ 24 mo back").
    """

    tier = DataSourceTier.MLS

    def __init__(self, config: AdapterConfig, *, source_key: str) -> None:
        self.source_key = source_key
        self._config = config
        self._resource = str(config.options.get("resource", "Property"))
        self._page_size = int(config.options.get("page_size", _DEFAULT_PAGE_SIZE))
        self._base_url = config.base_url.rstrip("/")

    # --- plan / fetch ------------------------------------------------------------------

    def plan(self, cursor: Cursor) -> Iterable[FetchTask]:
        """One page-task per pass. The pipeline re-plans from each returned `next_cursor`
        until a pass comes back empty, so a full incremental sync is a sequence of these.
        A `cursor.since` of None means "initial backfill" — `fetch` widens the filter window.
        """
        return [FetchTask(params={"since": cursor.since, "page": cursor.page})]

    def _build_filter(self, since: datetime | None) -> str:
        if since is not None:
            return f"ModificationTimestamp gt {since.astimezone(UTC).isoformat()}"
        # Initial backfill: everything the license permits. Cadence/window tuning lives in
        # options; the coarse filter here keeps the adapter honest about "since=None = all".
        return "ModificationTimestamp gt 1970-01-01T00:00:00Z"

    async def fetch(self, task: FetchTask) -> RawBatch:
        since = task.params.get("since")
        page = int(task.params.get("page", 0))
        params: dict[str, str] = {
            "$filter": self._build_filter(since),
            "$orderby": "ModificationTimestamp asc",
            "$top": str(self._page_size),
            "$skip": str(page * self._page_size),
            "$count": "true",
        }
        url = f"{self._base_url}/{self._resource}"
        body = await self._config.transport.fetch_json(url, params=params)

        records = body.get("value", []) or []
        items = [
            RawItem(
                record_type="listing",
                source_native_id=str(rec.get("ListingKey") or rec.get("ListingId") or ""),
                payload=rec,
                source_ts=_parse_dt(rec.get("ModificationTimestamp")),
            )
            for rec in records
        ]
        # The pipeline stops when a pass yields nothing; a short page also means we've caught
        # up, so we don't advance `page` past the tail.
        exhausted = len(records) < self._page_size
        next_cursor = Cursor(since=since, page=0 if exhausted else page + 1)
        count = body.get("@odata.count")
        return RawBatch(
            items=items,
            next_cursor=next_cursor,
            fetched_at=datetime.now(UTC),
            source_reported_total=int(count) if isinstance(count, int) else None,
        )

    # --- normalize ---------------------------------------------------------------------

    def _property_delta(self, rec: Mapping[str, Any]) -> PropertyDelta:
        street_fields = (
            "StreetNumber", "StreetDirPrefix", "StreetName", "StreetSuffix", "StreetDirSuffix"
        )
        street_parts = [str(rec.get(k, "")).strip() for k in street_fields]
        line1 = " ".join(p for p in street_parts if p)
        address = normalize_address(
            line1=line1,
            line2=str(rec.get("UnitNumber", "") or "") or None,
            city=str(rec.get("City", "") or ""),
            state=str(rec.get("StateOrProvince", "") or ""),
            zip_code=str(rec.get("PostalCode", "") or ""),
            plus4=str(rec.get("PostalCodePlus4", "") or "") or None,
        )
        subtype = str(rec.get("PropertySubType", "") or "")
        ptype_raw = str(rec.get("PropertyType", "") or "")
        prop_type = _TYPE_MAP.get(subtype) or _TYPE_MAP.get(ptype_raw)

        baths = _as_decimal(rec.get("BathroomsTotalInteger"))
        if baths is None:
            full = _as_decimal(rec.get("BathroomsFull")) or Decimal(0)
            half = _as_decimal(rec.get("BathroomsHalf")) or Decimal(0)
            baths = full + half / 2 if (full or half) else None

        # Long-tail: every non-standard field the feed sent, preserved verbatim (§14.2).
        attrs: dict[str, object] = {k: v for k, v in rec.items() if k not in _MAPPED_KEYS}
        fips = _county_fips(rec)
        return PropertyDelta(
            apn=str(rec.get("ParcelNumber") or "").strip() or None,
            fips=fips,
            address=address,
            latitude=_as_float(rec.get("Latitude")),
            longitude=_as_float(rec.get("Longitude")),
            property_type=prop_type,
            beds=_as_int(rec.get("BedroomsTotal")),
            baths=baths,
            sqft=_as_int(rec.get("LivingArea")),
            lot_sqft=_as_int(rec.get("LotSizeSquareFeet")),
            year_built=_as_int(rec.get("YearBuilt")),
            stories=_as_int(rec.get("StoriesTotal")),
            garage_spaces=_as_int(rec.get("GarageSpaces")),
            pool=_as_bool(rec.get("PoolPrivateYN")),
            hoa_monthly=_monthly_hoa(rec),
            zoning=str(rec.get("Zoning") or "").strip() or None,
            attrs=attrs,
        )

    def normalize(self, item: RawItem) -> list[CanonicalDelta]:
        rec = item.payload
        raw_status = str(rec.get("StandardStatus") or rec.get("MlsStatus") or "")
        status = _STATUS_MAP.get(raw_status, ListingStatus.WITHDRAWN)
        prop = self._property_delta(rec)
        if raw_status and raw_status not in _STATUS_MAP:
            prop.attrs["_unmapped_status"] = raw_status

        photos = [
            PhotoDelta(
                position=_as_int(m.get("Order")) or i,
                source_url=str(m.get("MediaURL") or "") or None,
                # Exact-dedupe hash over the stable media identity available pre-download
                # (URL + order). The perceptual `phash` is filled later by the vision
                # pre-pass once bytes are actually fetched (§13, §11.3).
                content_hash=_media_hash(m, i),
            )
            for i, m in enumerate(rec.get("Media", []) or [])
            if isinstance(m, dict)
        ]

        listing = ListingDelta(
            source_listing_key=item.source_native_id,
            property=prop,
            status=status,
            list_price=_as_decimal(rec.get("ListPrice")),
            close_price=_as_decimal(rec.get("ClosePrice")),
            list_date=_parse_date(rec.get("ListingContractDate") or rec.get("OnMarketDate")),
            close_date=_parse_date(rec.get("CloseDate")),
            dom_current=_as_int(rec.get("DaysOnMarket")),
            remarks=str(rec.get("PublicRemarks") or "") or None,
            attribution=ListingAttribution(
                listing_agent_name=str(rec.get("ListAgentFullName") or "") or None,
                listing_agent_license=str(rec.get("ListAgentMlsId") or "") or None,
                listing_office_name=str(rec.get("ListOfficeName") or "") or None,
                listing_office_phone=str(rec.get("ListOfficePhone") or "") or None,
                mls_name=str(
                    rec.get("OriginatingSystemName") or rec.get("SourceSystemName") or ""
                )
                or None,
            ),
            photos=photos,
            source_ts=item.source_ts,
        )
        return [listing]

    def health(self) -> SourceHealth:
        # A real health probe hits the source's `$metadata`/quota endpoint; the adapter
        # surfaces lag/quota it observed on the last fetch. Placeholder until the worker
        # threads live counters through (Phase 1 M1.1 ops dashboard, S40).
        return SourceHealth(ok=True)


def _county_fips(rec: Mapping[str, Any]) -> str | None:
    """RESO carries county name, not FIPS, in the standard dictionary. A real deployment
    joins CountyOrParish + StateOrProvince to a FIPS lookup table; feeds that include a
    non-standard `CountyFIPS`/`FIPS` field are honored directly here.
    """
    for key in ("CountyFIPS", "FIPS", "CountyOrParishFIPS"):
        value = rec.get(key)
        if value:
            digits = "".join(ch for ch in str(value) if ch.isdigit())[:5]
            if len(digits) == 5:
                return digits
    return None


def _monthly_hoa(rec: Mapping[str, Any]) -> Decimal | None:
    fee = _as_decimal(rec.get("AssociationFee"))
    if fee is None:
        return None
    freq = str(rec.get("AssociationFeeFrequency") or "Monthly").lower()
    divisor = {"annually": 12, "yearly": 12, "quarterly": 3, "semiannually": 6}.get(freq, 1)
    return (fee / divisor).quantize(Decimal("0.01"))


def _media_hash(media: Mapping[str, Any], index: int) -> bytes:
    import hashlib

    identity = f"{media.get('MediaURL', '')}|{media.get('Order', index)}"
    return hashlib.sha256(identity.encode()).digest()


def _as_float(value: Any) -> float | None:
    dec = _as_decimal(value)
    return float(dec) if dec is not None else None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    return str(value).strip().lower() in ("true", "yes", "y", "1")


# --- registration ----------------------------------------------------------------------
# The three launch aggregators are one class, three registered keys (§14.1). A market's
# `data_sources.source_key` selects which base_url/auth the worker binds.


@register_adapter("mls_grid")
def _build_mls_grid(config: AdapterConfig) -> ResoAdapter:
    return ResoAdapter(config, source_key="mls_grid")


@register_adapter("trestle")
def _build_trestle(config: AdapterConfig) -> ResoAdapter:
    return ResoAdapter(config, source_key="trestle")


@register_adapter("bridge")
def _build_bridge(config: AdapterConfig) -> ResoAdapter:
    return ResoAdapter(config, source_key="bridge")
