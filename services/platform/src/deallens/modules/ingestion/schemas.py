"""Validation models for ingestion JSONB payloads (§20: Pydantic at every boundary — dicts
never cross module boundaries). These are the typed shapes stored in
`properties.address_norm`, `listings.attribution`, and normalized into `properties.attrs`.
Adapters emit these; nothing writes the raw dict directly.
"""

from pydantic import BaseModel, ConfigDict, Field


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
