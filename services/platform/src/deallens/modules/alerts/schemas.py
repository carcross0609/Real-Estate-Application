"""Validation model for `buy_boxes.filters` (§11.5 #6) plus the alerts API request/response
shapes. The filter model compiles to matching predicates once per box version, then evaluates
per property on a new score; validating its shape here keeps a malformed box from silently
matching everything (or nothing).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from deallens.core.enums import Strategy
from deallens.modules.alerts.models import (
    AlertChannel,
    NotificationKind,
    NotificationStatus,
)
from deallens.modules.identity.models import AlertLatency
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


# --- Buy-box CRUD (FR-040) --------------------------------------------------------------


class BuyBoxCreate(BaseModel):
    """Create/replace a saved buy box. `market_id` scopes the matcher's fan-out (§11.5 #6);
    a null market matches across every market the org can see."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    market_id: UUID | None = None
    filters: BuyBoxFilters = Field(default_factory=BuyBoxFilters)
    strategy: Strategy | None = None
    alert_channels: list[AlertChannel] = Field(default_factory=lambda: [AlertChannel.IN_APP])
    alert_latency: AlertLatency = AlertLatency.DAILY
    active: bool = True


class BuyBoxUpdate(BaseModel):
    """Partial update — every field optional; only provided ones change."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    market_id: UUID | None = None
    filters: BuyBoxFilters | None = None
    strategy: Strategy | None = None
    alert_channels: list[AlertChannel] | None = None
    alert_latency: AlertLatency | None = None
    active: bool | None = None


class BuyBoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    market_id: UUID | None
    filters: BuyBoxFilters
    strategy: Strategy | None
    alert_channels: list[AlertChannel]
    alert_latency: AlertLatency
    active: bool
    created_at: datetime
    updated_at: datetime


# --- Watchlist (FR-041) -----------------------------------------------------------------


class WatchlistItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    notes: str | None = None


class WatchlistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    notes: str | None
    created_at: datetime


# --- Notifications ----------------------------------------------------------------------


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: NotificationKind
    channel: AlertChannel
    status: NotificationStatus
    title: str
    body: str | None
    data: dict[str, object]
    property_id: UUID | None
    buy_box_id: UUID | None
    created_at: datetime
    read_at: datetime | None
