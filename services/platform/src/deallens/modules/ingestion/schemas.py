"""Validation models for ingestion JSONB payloads and the canonical-delta contract (§20:
Pydantic at every boundary — dicts never cross module boundaries).

Two layers live here:
- The persisted JSONB shapes (`AddressNorm`, `ListingAttribution`) — the typed shapes stored
  in `properties.address_norm` / `listings.attribution`.
- The **canonical delta contract** (`PropertyDelta`/`ListingDelta`/`PhotoDelta` and the
  enrichment `OwnershipDelta`/`TaxDelta`) — the source-neutral intermediate every
  `SourceAdapter.normalize()` emits (§14.2). Adapters map their idiosyncratic raw payloads
  to these; the writer/resolution layer consumes *only* these, never a source's raw dict, so
  a new source is a new adapter and nothing downstream changes (NFR-12).
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from deallens.modules.ingestion.models import ListingStatus, PropertyType


class AddressNorm(BaseModel):
    """Normalized US address (libpostal output, §14.3). Stored in
    `properties.address_norm`; the (line1, zip) pair drives deterministic entity resolution.
    """

    model_config = ConfigDict(extra="forbid")

    line1: str
    line2: str | None = None
    city: str
    state: str = Field(min_length=2, max_length=2)
    zip: str = Field(pattern=r"^\d{5}$")
    plus4: str | None = Field(default=None, pattern=r"^\d{4}$")


class ListingAttribution(BaseModel):
    """MLS display-compliance attribution (§11.3, §12.3). Rendered verbatim where the
    source's `license_policy` requires it; stripped where not permitted.
    """

    model_config = ConfigDict(extra="allow")  # sources carry idiosyncratic attribution fields

    listing_agent_name: str | None = None
    listing_agent_license: str | None = None
    listing_office_name: str | None = None
    listing_office_phone: str | None = None
    mls_name: str | None = None
    disclaimer: str | None = None


# --- Canonical delta contract (§14.2) --------------------------------------------------
#
# A "delta" is the normalized form of one source record — the unit of information the
# pipeline resolves, versions, and upserts. It is deliberately *permissive on plausibility*:
# Pydantic here enforces shape and types only; range/sanity checks (a $10 house, a
# 9,000-bath listing) are the data-quality layer's job (`quality.py`, §14.4), so a bad value
# is flagged and served-with-caveat rather than silently rejected at the schema boundary.


class PhotoDelta(BaseModel):
    """One listing image in normalized form (§11.3 listing_photos). `content_hash` is the
    exact-dedupe key computed by the adapter over the fetched bytes; `phash` (perceptual) is
    optional and filled by the vision pre-pass where images are actually downloaded.
    """

    model_config = ConfigDict(extra="forbid")

    position: int = 0
    source_url: str | None = None
    content_hash: bytes
    phash: int | None = None
    width: int | None = None
    height: int | None = None


class PropertyDelta(BaseModel):
    """The physical-asset facet of a source record — everything entity resolution needs to
    find-or-create a canonical `properties` row (§14.3), plus the structural attributes a
    feed reports. Also serves as the *locator* on enrichment deltas (an ATTOM assessor row
    carries no listing, only a property it must resolve to).

    `attrs` is the long-tail escape hatch (§14.2): any source field we don't promote to a
    column lands here and is never dropped.
    """

    model_config = ConfigDict(extra="forbid")

    apn: str | None = None
    fips: str | None = Field(default=None, min_length=5, max_length=5)
    address: AddressNorm
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    property_type: PropertyType | None = None
    beds: int | None = None
    baths: Decimal | None = None
    sqft: int | None = None
    lot_sqft: int | None = None
    year_built: int | None = None
    stories: int | None = None
    garage_spaces: int | None = None
    pool: bool | None = None
    hoa_monthly: Decimal | None = None
    zoning: str | None = None
    attrs: dict[str, object] = Field(default_factory=dict)


class ListingDelta(BaseModel):
    """The marketing-event facet of a source record (§11.3 listings). Embeds its
    `PropertyDelta` snapshot — one fetch of an MLS listing tells us both "this house exists"
    and "it is for sale at $X". `source_ts` is the feed `ModificationTimestamp` and is the
    version key for the conditional upsert (§9.5): a delta whose `source_ts` is not newer
    than what is stored is a no-op, so out-of-order/duplicate feed events are correct by
    construction.
    """

    model_config = ConfigDict(extra="forbid")

    source_listing_key: str = Field(min_length=1, max_length=128)
    property: PropertyDelta
    status: ListingStatus
    list_price: Decimal | None = None
    close_price: Decimal | None = None
    list_date: date | None = None
    close_date: date | None = None
    dom_current: int | None = None
    remarks: str | None = None
    attribution: ListingAttribution = Field(default_factory=ListingAttribution)
    photos: list[PhotoDelta] = Field(default_factory=list)
    source_ts: datetime | None = None


class OwnershipDelta(BaseModel):
    """Assessor/deed ownership facet (§14.1 Tier B) → `ownership_records`. Resolves to a
    canonical property via its `property` locator, then attaches; the absentee / long-hold
    flags feed the D-group motivated-seller factor (03 §25.3).
    """

    model_config = ConfigDict(extra="forbid")

    property: PropertyDelta
    owner_name: str | None = None
    owner_mailing_address: AddressNorm | None = None
    owner_occupied: bool | None = None
    absentee: bool | None = None
    ownership_length_months: int | None = None
    last_sale_price: Decimal | None = None
    last_sale_date: date | None = None
    deed_date: date | None = None
    deed_type: str | None = None


class TaxDelta(BaseModel):
    """Assessor tax-roll facet (§14.1 Tier B) → `tax_records`. `reassessed_on_sale` carries
    the jurisdiction rule the engine needs to avoid underwriting on the seller's
    grandfathered basis (03 §26.4).
    """

    model_config = ConfigDict(extra="forbid")

    property: PropertyDelta
    tax_year: int
    assessed_value: Decimal | None = None
    land_value: Decimal | None = None
    improvement_value: Decimal | None = None
    annual_tax_amount: Decimal | None = None
    tax_rate: Decimal | None = None
    exemptions: dict[str, object] = Field(default_factory=dict)
    reassessed_on_sale: bool | None = None
