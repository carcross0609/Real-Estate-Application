"""Search API contracts — the FastAPI boundary for the property search engine (S11 Market
Explorer, S33 Markets Manager). Dicts never cross module boundaries (§20); every router
function accepts/returns one of these models.

Three families live here:
- **`PropertyFilters`** — the source-neutral filter language. This exact model is stored in
  `saved_searches.filters` *and* `buy_boxes.filters` (ADR 0004): the alert matcher compiles
  the same predicates the explorer does. It is permissive on absence (every field optional)
  but strict on shape (`extra="forbid"`) and self-consistent (min ≤ max, enforced here).
- **Geographic scope** — `SearchScope` plus the primitive geometries (`BBox`, `RadiusGeo`,
  `GeoJSONGeometry`) that `search.geo.resolve_geometry` turns into one PostGIS predicate.
- **Results & CRUD** — the keyset `SearchPage`, exfiltration-minimized tile payloads, and the
  saved-search / search-area / preference request+response shapes.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deallens.core.enums import Strategy
from deallens.modules.ingestion.models import ListingStatus, PropertyType
from deallens.modules.scoring.models import Recommendation

# --- Sorting ----------------------------------------------------------------------------


class SortField(StrEnum):
    """The columns a result set may be ordered by. `SCORE` (of the query's strategy) is the
    product default everywhere — "ranked by profit, not price" (PRD §21 #2).
    """

    SCORE = "score"
    PRICE = "price"
    NEWEST = "newest"  # list_date
    DOM = "dom"  # days on market
    PRICE_PER_SQFT = "price_per_sqft"
    SQFT = "sqft"
    BEDS = "beds"
    YEAR_BUILT = "year_built"
    LOT_SIZE = "lot_size"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


# The natural default direction per field (descending for "more is better / newer"; ascending
# for price and days-on-market where lower is the interesting end). `SortSpec.resolved_*`
# applies this when the caller doesn't pin a direction.
_DEFAULT_DIRECTION: dict[SortField, SortDirection] = {
    SortField.SCORE: SortDirection.DESC,
    SortField.PRICE: SortDirection.ASC,
    SortField.NEWEST: SortDirection.DESC,
    SortField.DOM: SortDirection.ASC,
    SortField.PRICE_PER_SQFT: SortDirection.ASC,
    SortField.SQFT: SortDirection.DESC,
    SortField.BEDS: SortDirection.DESC,
    SortField.YEAR_BUILT: SortDirection.DESC,
    SortField.LOT_SIZE: SortDirection.DESC,
}


class SortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: SortField = SortField.SCORE
    direction: SortDirection | None = None  # None → the field's natural default

    def resolved_direction(self) -> SortDirection:
        return self.direction or _DEFAULT_DIRECTION[self.field]

    @property
    def descending(self) -> bool:
        return self.resolved_direction() is SortDirection.DESC


# --- Filters ----------------------------------------------------------------------------


class PropertyFilters(BaseModel):
    """Every §6.1 filter, all optional. Ranges are inclusive on both ends. `extra="forbid"`
    so a typo'd filter name fails loudly at the boundary rather than being silently ignored
    (a silently-dropped filter shows the user more results than they asked for — a trust bug).
    """

    model_config = ConfigDict(extra="forbid")

    # Structural (properties)
    beds_min: int | None = Field(default=None, ge=0)
    beds_max: int | None = Field(default=None, ge=0)
    baths_min: Decimal | None = Field(default=None, ge=0)
    baths_max: Decimal | None = Field(default=None, ge=0)
    sqft_min: int | None = Field(default=None, ge=0)
    sqft_max: int | None = Field(default=None, ge=0)
    lot_sqft_min: int | None = Field(default=None, ge=0)
    lot_sqft_max: int | None = Field(default=None, ge=0)
    year_built_min: int | None = Field(default=None, ge=1600, le=2100)
    year_built_max: int | None = Field(default=None, ge=1600, le=2100)
    stories_min: int | None = Field(default=None, ge=0)
    stories_max: int | None = Field(default=None, ge=0)
    garage_min: int | None = Field(default=None, ge=0)
    pool: bool | None = None
    hoa_max: Decimal | None = Field(default=None, ge=0)
    property_types: list[PropertyType] | None = None

    # Listing (current listing for the property)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    price_per_sqft_min: Decimal | None = Field(default=None, ge=0)
    price_per_sqft_max: Decimal | None = Field(default=None, ge=0)
    dom_min: int | None = Field(default=None, ge=0)
    dom_max: int | None = Field(default=None, ge=0)
    statuses: list[ListingStatus] | None = None  # None → service default (active only)
    listed_since: date | None = None  # list_date >= this
    keywords: str | None = Field(default=None, max_length=200)  # remarks ILIKE

    # Analytics (score of the query strategy)
    score_min: Decimal | None = Field(default=None, ge=0, le=100)
    score_max: Decimal | None = Field(default=None, ge=0, le=100)
    grades: list[str] | None = None
    risk_max: Decimal | None = Field(default=None, ge=0, le=100)
    confidence_min: Decimal | None = Field(default=None, ge=0, le=100)
    recommendations: list[Recommendation] | None = None

    @model_validator(mode="after")
    def _ranges_consistent(self) -> Self:
        pairs: list[tuple[str, str]] = [
            ("beds_min", "beds_max"),
            ("baths_min", "baths_max"),
            ("sqft_min", "sqft_max"),
            ("lot_sqft_min", "lot_sqft_max"),
            ("year_built_min", "year_built_max"),
            ("stories_min", "stories_max"),
            ("price_min", "price_max"),
            ("price_per_sqft_min", "price_per_sqft_max"),
            ("dom_min", "dom_max"),
            ("score_min", "score_max"),
        ]
        for lo_name, hi_name in pairs:
            lo = getattr(self, lo_name)
            hi = getattr(self, hi_name)
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{lo_name} ({lo}) must be ≤ {hi_name} ({hi})")
        return self


# --- Geographic scope -------------------------------------------------------------------


class BBox(BaseModel):
    """A viewport / map-extent rectangle in WGS84 (lon/lat). The map pans, this changes."""

    model_config = ConfigDict(extra="forbid")

    min_lon: float = Field(ge=-180, le=180)
    min_lat: float = Field(ge=-90, le=90)
    max_lon: float = Field(ge=-180, le=180)
    max_lat: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min_lon > self.max_lon or self.min_lat > self.max_lat:
            raise ValueError("bbox min corner must be south-west of max corner")
        return self


class RadiusGeo(BaseModel):
    """"Within N metres of a point" — the "search near this address" affordance."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(gt=0, le=200_000)  # 200 km ceiling — a sane server-side guardrail


class GeoJSONGeometry(BaseModel):
    """A drawn polygon (FR-001 "draw a polygon on the map"). Coordinates are validated for
    depth/shape only; `search.geo` closes rings and hands the WKT to PostGIS for the real
    topological work.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]


class SearchScope(BaseModel):
    """Where to look. A coarse `market_id` may combine with at most one *fine* geometry
    (`area_id` | `bbox` | `polygon` | `radius`); `zips`/`city`/`county_fips` are attribute
    narrowings resolved against the property's market geography. All optional: an empty scope
    means "everything the caller may see" (a `market_id` is still recommended for performance).
    """

    model_config = ConfigDict(extra="forbid")

    market_id: UUID | None = None
    area_id: UUID | None = None  # a saved search_area geometry
    bbox: BBox | None = None
    polygon: GeoJSONGeometry | None = None
    radius: RadiusGeo | None = None
    zips: list[str] | None = None
    city: str | None = None
    county_fips: str | None = Field(default=None, min_length=5, max_length=5)

    @model_validator(mode="after")
    def _one_fine_geometry(self) -> Self:
        fine = [n for n in ("area_id", "bbox", "polygon", "radius") if getattr(self, n) is not None]
        if len(fine) > 1:
            raise ValueError(
                f"provide at most one of area_id/bbox/polygon/radius, got {sorted(fine)}"
            )
        return self


# --- The query --------------------------------------------------------------------------


class PropertySearchQuery(BaseModel):
    """The full search request. `strategy` selects *which* score is filtered and sorted on
    (default `overall`, the per-property best — PRD §25.4); it does not restrict which
    properties appear. Keyset paginated via `cursor` (opaque; see `search.query`).
    """

    model_config = ConfigDict(extra="forbid")

    scope: SearchScope = Field(default_factory=SearchScope)
    filters: PropertyFilters = Field(default_factory=PropertyFilters)
    strategy: Strategy = Strategy.OVERALL
    sort: SortSpec = Field(default_factory=SortSpec)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


# --- Results ----------------------------------------------------------------------------


class PropertyCard(BaseModel):
    """One result row for the explorer table / map hover. Composed in the service from the
    search join — deliberately a flat projection, not the full `PropertyBundle` (that's the
    property-detail read model). No MLS-restricted field appears here; display-policy stripping
    happens before this is built.
    """

    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    listing_id: UUID | None = None
    line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    lat: float | None = None
    lon: float | None = None
    property_type: PropertyType | None = None
    beds: int | None = None
    baths: Decimal | None = None
    sqft: int | None = None
    lot_sqft: int | None = None
    year_built: int | None = None
    list_price: Decimal | None = None
    status: ListingStatus | None = None
    dom: int | None = None
    list_date: date | None = None
    price_per_sqft: Decimal | None = None
    photo_count: int = 0
    # Analytics (for the queried strategy)
    score: Decimal | None = None
    grade: str | None = None
    risk_score: Decimal | None = None
    confidence_score: Decimal | None = None
    recommendation: Recommendation | None = None
    scored_strategy: Strategy | None = None


class SearchPage(BaseModel):
    """A keyset page. `next_cursor` is null on the last page; there is no `total` — an exact
    count over a filtered multi-million-row join is the expensive query keyset exists to avoid
    (NFR-01). Use `/properties/count` for a (cheaper, capped) estimate when the UI needs one.
    """

    model_config = ConfigDict(extra="forbid")

    results: list[PropertyCard]
    next_cursor: str | None = None
    limit: int


class CountEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    capped: bool  # true → the real count is ≥ `count` (we stopped counting at the cap)


# --- Map tiles (§12.2 exfiltration-hardened) --------------------------------------------


class TileFeature(BaseModel):
    """An individual pin. Carries ONLY id + point + coarse buckets — never price, address, or
    attributes. Full data requires a per-property call (§12.2 anti-bulk-export).
    """

    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    lon: float
    lat: float
    score_bucket: int | None = None  # 0..9 decile of the strategy score
    price_bucket: int | None = None  # 0..9 coarse price band


class TileCluster(BaseModel):
    """An aggregated cluster shown at low zoom. No member ids leave the server."""

    model_config = ConfigDict(extra="forbid")

    lon: float
    lat: float
    count: int
    avg_score_bucket: int | None = None


class TileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    z: int
    x: int
    y: int
    clustered: bool
    features: list[TileFeature] = Field(default_factory=list)
    clusters: list[TileCluster] = Field(default_factory=list)


# --- Saved searches (S11 saved-view management) -----------------------------------------


class MapView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    center_lon: float = Field(ge=-180, le=180)
    center_lat: float = Field(ge=-90, le=90)
    zoom: float = Field(ge=0, le=24)


class SavedSearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    market_id: UUID | None = None
    search_area_id: UUID | None = None
    filters: PropertyFilters = Field(default_factory=PropertyFilters)
    strategy: Strategy | None = None
    sort: SortSpec | None = None
    map_view: MapView | None = None
    is_default: bool = False


class SavedSearchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    market_id: UUID | None = None
    search_area_id: UUID | None = None
    filters: PropertyFilters | None = None
    strategy: Strategy | None = None
    sort: SortSpec | None = None
    map_view: MapView | None = None
    is_default: bool | None = None


class SavedSearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    market_id: UUID | None
    search_area_id: UUID | None
    filters: dict[str, Any]
    strategy: Strategy | None
    sort: dict[str, Any] | None
    map_view: dict[str, Any] | None
    is_default: bool
    created_at: datetime
    updated_at: datetime


# --- Search areas (FR-001, S33) ---------------------------------------------------------


class SearchAreaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    market_id: UUID | None = None
    geometry: GeoJSONGeometry


class SearchAreaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    market_id: UUID | None = None
    geometry: GeoJSONGeometry | None = None
    active: bool | None = None


class SearchAreaOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    market_id: UUID | None
    active: bool
    geometry: dict[str, Any] | None  # GeoJSON, materialized via ST_AsGeoJSON on read
    created_at: datetime


# --- User search preferences (stored in users.preferences["search"]) --------------------


class SearchPreferences(BaseModel):
    """Per-user defaults for the explorer (FR-053 "strategy preferences"). Persisted inside
    the existing `users.preferences` JSONB under the `"search"` namespace — no new column —
    written through `identity.service.merge_user_preferences` so `identity` keeps owning the
    `users` row (§19 boundary rule).
    """

    model_config = ConfigDict(extra="forbid")

    default_strategy: Strategy | None = None
    default_sort: SortSpec | None = None
    default_market_id: UUID | None = None
    last_map_view: MapView | None = None
