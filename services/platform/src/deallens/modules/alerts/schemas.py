"""Validation model for `buy_boxes.filters` (§11.5 #6). Compiled to SQL predicates once per
box version, then evaluated per market on `ScoreUpdated`. Validating the filter shape here
keeps a malformed box from silently matching everything (or nothing).
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from deallens.modules.ingestion.models import PropertyType


class BuyBoxFilters(BaseModel):
    """Buy criteria. All bounds inclusive; omitted fields are unconstrained. Score/risk/
    confidence gates (§25.5) are applied on top of the physical/price filters."""

    model_config = ConfigDict(extra="forbid")

    price_min: Decimal | None = None
    price_max: Decimal | None = None
    beds_min: int | None = Field(default=None, ge=0)
    baths_min: Decimal | None = Field(default=None, ge=0)
    sqft_min: int | None = Field(default=None, ge=0)
    sqft_max: int | None = Field(default=None, ge=0)
    year_built_min: int | None = None
    property_types: list[PropertyType] = Field(default_factory=list)
    search_area_ids: list[str] = Field(default_factory=list)
    min_score: Decimal | None = Field(default=None, ge=0, le=100)
    max_risk: Decimal | None = Field(default=None, ge=0, le=100)
    min_confidence: Decimal | None = Field(default=None, ge=0, le=100)
    exclude_flood_zones: bool = False
    keywords: list[str] = Field(default_factory=list)  # remarks NLP match
