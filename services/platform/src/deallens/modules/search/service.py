"""Search module public interface (§19: modules import each other only via `service.py`).

Orchestrates the pure query compiler (`query`), geometry helpers (`geo`), and the ORM
(`models`) into the operations the router exposes: property search (keyset), map tiles
(exfiltration-minimized), match counts, and CRUD for saved searches, search areas (tier-
limited, FR-001), and per-user search preferences.

The engine reads shared property/listing/score data (no RLS); the user-owned surfaces
(`saved_searches`, `search_areas`) ride the request-scoped RLS session, so tenant isolation is
enforced by Postgres, not by hand-written `WHERE org_id =` clauses.
"""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from geoalchemy2 import WKTElement
from geoalchemy2.functions import ST_AsGeoJSON, ST_AsText
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from deallens.core.errors import ForbiddenError, NotFoundError, ValidationError
from deallens.modules.identity import service as identity_service
from deallens.modules.identity.models import User
from deallens.modules.markets.models import SearchArea
from deallens.modules.search import geo, query
from deallens.modules.search.models import SavedSearch
from deallens.modules.search.schemas import (
    CountEstimate,
    PropertyCard,
    PropertySearchQuery,
    SavedSearchCreate,
    SavedSearchUpdate,
    SearchAreaCreate,
    SearchAreaOut,
    SearchAreaUpdate,
    SearchPage,
    SearchPreferences,
    TileFeature,
    TileResponse,
)

# A single tile never returns more than this many pins; the service flags truncation rather
# than silently dropping the tail (§20 no-silent-caps). Zoom in for the rest.
TILE_MAX_FEATURES = 2000
# Count is capped — an exact count over a filtered multi-million-row join is exactly what
# keyset pagination exists to avoid (NFR-01). `capped=True` tells the UI to render "1000+".
COUNT_CAP = 1000
# Coarse price band ceiling for the tile `price_bucket` decile (a color hint only; the exact
# price never leaves the server per §12.2).
_PRICE_BAND_MAX = 2_000_000.0
SEARCH_PREF_NAMESPACE = "search"


# --- Geometry resolution ----------------------------------------------------------------


async def _fine_geometry_predicates(
    db: AsyncSession, scope: Any
) -> list[ColumnElement[bool]]:
    """Resolve the one optional *fine* geometry (saved area | drawn polygon | viewport | radius)
    into a PostGIS predicate list. A saved `area_id` is fetched through the RLS session, so an
    area belonging to another org resolves to "not found," never a cross-tenant read.
    """
    if scope.area_id is not None:
        row = await db.execute(
            select(ST_AsText(SearchArea.geom)).where(
                SearchArea.id == scope.area_id, SearchArea.deleted_at.is_(None)
            )
        )
        wkt = row.scalar_one_or_none()
        if wkt is None:
            raise NotFoundError("Search area not found")
        return [query.geometry_predicate(wkt)]
    if scope.polygon is not None:
        return [query.geometry_predicate(geo.geojson_to_wkt(scope.polygon))]
    if scope.bbox is not None:
        return [query.geometry_predicate(geo.bbox_to_wkt(scope.bbox))]
    if scope.radius is not None:
        return [query.geometry_predicate(geo.radius_point_wkt(scope.radius), scope.radius.radius_m)]
    return []


# --- Property search --------------------------------------------------------------------


def _row_to_card(row: Any, q: PropertySearchQuery) -> PropertyCard:
    addr = row.address_norm or {}
    return PropertyCard(
        property_id=row.property_id,
        listing_id=row.listing_id,
        line1=addr.get("line1"),
        city=addr.get("city"),
        state=addr.get("state"),
        zip=addr.get("zip"),
        lat=row.lat,
        lon=row.lon,
        property_type=row.property_type,
        beds=row.beds,
        baths=row.baths,
        sqft=row.sqft,
        lot_sqft=row.lot_sqft,
        year_built=row.year_built,
        list_price=row.list_price,
        status=row.status,
        dom=row.dom,
        list_date=row.list_date,
        price_per_sqft=row.price_per_sqft,
        photo_count=row.photo_count or 0,
        score=row.score,
        grade=row.grade,
        risk_score=row.risk_score,
        confidence_score=row.confidence_score,
        recommendation=row.recommendation,
        scored_strategy=q.strategy if row.score is not None else None,
    )


async def search_properties(db: AsyncSession, q: PropertySearchQuery) -> SearchPage:
    """Run one keyset page of the property search. Fetches `limit + 1` rows to know whether a
    further page exists, then mints the next cursor from the last in-page row's (sort key, id).
    """
    geo_preds = await _fine_geometry_predicates(db, q.scope)
    stmt = query.build_search_select(q, geo_predicates=geo_preds)
    rows = (await db.execute(stmt)).all()

    has_more = len(rows) > q.limit
    page = rows[: q.limit]
    cards = [_row_to_card(r, q) for r in page]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = query.encode_cursor(q, getattr(last, query.SORT_LABEL), last.property_id)
    return SearchPage(results=cards, next_cursor=next_cursor, limit=q.limit)


async def count_matches(db: AsyncSession, q: PropertySearchQuery) -> CountEstimate:
    geo_preds = await _fine_geometry_predicates(db, q.scope)
    stmt = query.build_count_select(q, geo_predicates=geo_preds, cap=COUNT_CAP)
    n = int((await db.execute(stmt)).scalar_one())
    return CountEstimate(count=n, capped=n >= COUNT_CAP)


# --- Map tiles (§12.2) ------------------------------------------------------------------


async def tile(
    db: AsyncSession, *, z: int, x: int, y: int, q: PropertySearchQuery
) -> tuple[TileResponse, bool]:
    """Serve one map tile; returns `(response, truncated)`. The tile's bounding box is the
    primary geometry; any drawn scope geometry is AND-ed in so a polygon draw still constrains
    the map. Below the cluster-zoom threshold the pins collapse to grid clusters; above it,
    individual (bucketed) pins. `truncated` is True when a non-clustered tile hit the pin cap
    (the router surfaces it as a response header).
    """
    bbox = geo.tile_to_bbox(z, x, y)  # raises ValueError → 422 on an out-of-range tile
    geo_preds = [query.geometry_predicate(geo.bbox_to_wkt(bbox))]
    geo_preds += await _fine_geometry_predicates(db, q.scope)

    stmt = query.build_tile_select(q, geo_predicates=geo_preds, limit=TILE_MAX_FEATURES + 1)
    rows = (await db.execute(stmt)).all()
    truncated = len(rows) > TILE_MAX_FEATURES

    features: list[TileFeature] = []
    for r in rows[:TILE_MAX_FEATURES]:
        if r.lon is None or r.lat is None:
            continue
        features.append(
            TileFeature(
                property_id=r.property_id,
                lon=r.lon,
                lat=r.lat,
                score_bucket=(
                    geo.decile_bucket(float(r.score), 0, 100) if r.score is not None else None
                ),
                price_bucket=(
                    geo.decile_bucket(float(r.list_price), 0, _PRICE_BAND_MAX)
                    if r.list_price is not None
                    else None
                ),
            )
        )

    if geo.should_cluster(z):
        # A clustered tile can't "truncate" — clustering already bounds the payload.
        clusters = geo.cluster_features(z, features)
        return TileResponse(z=z, x=x, y=y, clustered=True, clusters=clusters), False
    return TileResponse(z=z, x=x, y=y, clustered=False, features=features), truncated


# --- Saved searches (S11) ---------------------------------------------------------------


async def _clear_default(db: AsyncSession, *, user_id: UUID) -> None:
    """Unset the user's current default view. Runs before setting a new default so the partial
    unique index `uq_saved_searches_one_default` is never violated mid-transaction.
    """
    await db.execute(
        update(SavedSearch)
        .where(SavedSearch.user_id == user_id, SavedSearch.is_default.is_(True))
        .values(is_default=False)
    )


async def create_saved_search(
    db: AsyncSession, *, org_id: UUID, user_id: UUID, body: SavedSearchCreate
) -> SavedSearch:
    if body.is_default:
        await _clear_default(db, user_id=user_id)
    saved = SavedSearch(
        org_id=org_id,
        user_id=user_id,
        name=body.name,
        market_id=body.market_id,
        search_area_id=body.search_area_id,
        filters=body.filters.model_dump(mode="json", exclude_none=True),
        strategy=body.strategy,
        sort=body.sort.model_dump(mode="json") if body.sort else None,
        map_view=body.map_view.model_dump(mode="json") if body.map_view else None,
        is_default=body.is_default,
    )
    db.add(saved)
    await db.flush()
    return saved


async def list_saved_searches(db: AsyncSession, *, user_id: UUID) -> Sequence[SavedSearch]:
    result = await db.execute(
        select(SavedSearch)
        .where(SavedSearch.user_id == user_id, SavedSearch.deleted_at.is_(None))
        .order_by(SavedSearch.is_default.desc(), SavedSearch.created_at.desc())
    )
    return list(result.scalars().all())


async def _get_saved_search(db: AsyncSession, *, saved_id: UUID) -> SavedSearch:
    saved = await db.get(SavedSearch, saved_id)
    if saved is None or saved.deleted_at is not None:
        raise NotFoundError("Saved search not found")
    return saved


async def update_saved_search(
    db: AsyncSession, *, saved_id: UUID, user_id: UUID, body: SavedSearchUpdate
) -> SavedSearch:
    saved = await _get_saved_search(db, saved_id=saved_id)
    if body.is_default is True:
        await _clear_default(db, user_id=user_id)
    fields = body.model_dump(exclude_unset=True)
    if "filters" in fields and body.filters is not None:
        saved.filters = body.filters.model_dump(mode="json", exclude_none=True)
    if "sort" in fields:
        saved.sort = body.sort.model_dump(mode="json") if body.sort else None
    if "map_view" in fields:
        saved.map_view = body.map_view.model_dump(mode="json") if body.map_view else None
    for scalar in ("name", "market_id", "search_area_id", "strategy", "is_default"):
        if scalar in fields:
            setattr(saved, scalar, fields[scalar])
    await db.flush()
    return saved


async def delete_saved_search(db: AsyncSession, *, saved_id: UUID) -> None:
    saved = await _get_saved_search(db, saved_id=saved_id)
    saved.is_default = False  # a deleted view can't be the default
    saved.deleted_at = func.now()
    await db.flush()


# --- Search areas (FR-001, S33) ---------------------------------------------------------


def _area_out(
    area_id: UUID,
    name: str,
    market_id: UUID | None,
    active: bool,
    geojson: str | None,
    created_at: Any,
) -> SearchAreaOut:
    return SearchAreaOut(
        id=area_id,
        name=name,
        market_id=market_id,
        active=active,
        geometry=json.loads(geojson) if geojson else None,
        created_at=created_at,
    )


async def create_search_area(
    db: AsyncSession, *, org_id: UUID, user_id: UUID, body: SearchAreaCreate
) -> SearchAreaOut:
    """Create a user-defined search area, enforcing the plan's area cap (FR-001). The count is
    over non-deleted areas for the whole org (areas are a shared team asset), compared to
    `entitlements.markets_limit`.
    """
    limit = await identity_service.market_slot_limit(db, org_id=org_id)
    current = await db.execute(
        select(func.count())
        .select_from(SearchArea)
        .where(SearchArea.org_id == org_id, SearchArea.deleted_at.is_(None))
    )
    if int(current.scalar_one()) >= limit:
        raise ForbiddenError(
            f"Search-area limit reached for this plan ({limit}); upgrade or remove an area"
        )

    wkt = geo.geojson_to_wkt(body.geometry)
    area = SearchArea(
        org_id=org_id,
        user_id=user_id,
        market_id=body.market_id,
        name=body.name,
        geom=WKTElement(wkt, srid=4326),
        active=True,
    )
    db.add(area)
    await db.flush()
    return await get_search_area(db, area_id=area.id)


async def get_search_area(db: AsyncSession, *, area_id: UUID) -> SearchAreaOut:
    row = await db.execute(
        select(
            SearchArea.id,
            SearchArea.name,
            SearchArea.market_id,
            SearchArea.active,
            ST_AsGeoJSON(SearchArea.geom),
            SearchArea.created_at,
        ).where(SearchArea.id == area_id, SearchArea.deleted_at.is_(None))
    )
    result = row.first()
    if result is None:
        raise NotFoundError("Search area not found")
    return _area_out(*result)


async def list_search_areas(db: AsyncSession, *, org_id: UUID) -> list[SearchAreaOut]:
    rows = await db.execute(
        select(
            SearchArea.id,
            SearchArea.name,
            SearchArea.market_id,
            SearchArea.active,
            ST_AsGeoJSON(SearchArea.geom),
            SearchArea.created_at,
        )
        .where(SearchArea.org_id == org_id, SearchArea.deleted_at.is_(None))
        .order_by(SearchArea.created_at.desc())
    )
    return [_area_out(*r) for r in rows.all()]


async def update_search_area(
    db: AsyncSession, *, area_id: UUID, body: SearchAreaUpdate
) -> SearchAreaOut:
    area = await db.get(SearchArea, area_id)
    if area is None or area.deleted_at is not None:
        raise NotFoundError("Search area not found")
    if body.name is not None:
        area.name = body.name
    if body.market_id is not None:
        area.market_id = body.market_id
    if body.active is not None:
        area.active = body.active
    if body.geometry is not None:
        area.geom = WKTElement(geo.geojson_to_wkt(body.geometry), srid=4326)  # type: ignore[assignment]
    await db.flush()
    return await get_search_area(db, area_id=area_id)


async def delete_search_area(db: AsyncSession, *, area_id: UUID) -> None:
    area = await db.get(SearchArea, area_id)
    if area is None or area.deleted_at is not None:
        raise NotFoundError("Search area not found")
    area.deleted_at = func.now()
    area.active = False
    await db.flush()


# --- User search preferences (users.preferences["search"]) ------------------------------


async def get_search_preferences(db: AsyncSession, *, user_id: UUID) -> SearchPreferences:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    raw = user.preferences.get(SEARCH_PREF_NAMESPACE, {})
    try:
        return SearchPreferences.model_validate(raw)
    except ValueError as exc:  # a hand-corrupted preferences blob shouldn't 500 the app
        raise ValidationError("Stored search preferences are malformed") from exc


async def set_search_preferences(
    db: AsyncSession, *, user_id: UUID, prefs: SearchPreferences
) -> SearchPreferences:
    value = prefs.model_dump(mode="json")  # None entries clear their key (see merge helper)
    merged = await identity_service.merge_user_preferences(
        db, user_id=user_id, namespace=SEARCH_PREF_NAMESPACE, value=value
    )
    return SearchPreferences.model_validate(merged)


__all__ = [
    "count_matches",
    "create_saved_search",
    "create_search_area",
    "delete_saved_search",
    "delete_search_area",
    "get_search_area",
    "get_search_preferences",
    "list_saved_searches",
    "list_search_areas",
    "search_properties",
    "set_search_preferences",
    "tile",
    "update_saved_search",
    "update_search_area",
]
