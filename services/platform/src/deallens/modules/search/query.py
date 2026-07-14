"""The search read path — pure query construction, no execution.

`build_search_select` compiles a `PropertySearchQuery` (+ a resolved geometry) into one
SQLAlchemy `Select` over `properties` ⋈ current-listing ⋈ current-score, ordered by the sort
key with a `properties.id` tie-break and a keyset predicate for the cursor. Nothing here
touches a database, so the whole compiler is unit-tested by rendering to SQL text.

**Why keyset, not OFFSET (NFR-01).** `LIMIT n OFFSET k` re-scans and discards k rows every
page — linear in how deep you've paged, fatal at millions of rows. Keyset carries the last
row's `(sort_value, id)` in an opaque cursor and asks for "the next rows after this point," a
single index seek regardless of depth. The `properties.id` (UUIDv7, time-ordered) tie-break
makes the ordering total, so no row is skipped or repeated across pages.

**Current-listing / current-score projection.** A property has many listings and score rows
over time; search reads the latest of each via `DISTINCT ON (property_id)`. These two
subqueries are the documented seam (ADR 0004) where a denormalized `property_search` read
model would later slot in without touching filter/sort code.

**NULL ordering.** A sort column may be NULL (an unscored property, a listing with no price).
We `COALESCE` it to a direction-aware sentinel so NULLs always sort *last* and the keyset
comparison stays a clean total order — no `NULLS LAST` special-casing in the cursor math.
"""

import base64
import hashlib
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from deallens.core.enums import Strategy
from deallens.core.errors import ValidationError
from deallens.modules.ingestion.models import Listing, ListingStatus, Property
from deallens.modules.scoring.models import Score
from deallens.modules.search.schemas import (
    PropertyFilters,
    PropertySearchQuery,
    SearchScope,
    SortField,
)

SORT_LABEL = "_sort_key"

# Sentinels that pull NULLs to the end of the sort for either direction (§ module docstring).
_NUM_MIN, _NUM_MAX = Decimal("-1e18"), Decimal("1e18")
_INT_MIN, _INT_MAX = -(1 << 62), (1 << 62)
_DATE_MIN, _DATE_MAX = date(1, 1, 1), date(9999, 12, 31)

# Python type each sort field's cursor value round-trips through.
_FIELD_PY_TYPE: dict[SortField, type] = {
    SortField.SCORE: Decimal,
    SortField.PRICE: Decimal,
    SortField.PRICE_PER_SQFT: Decimal,
    SortField.NEWEST: date,
    SortField.DOM: int,
    SortField.SQFT: int,
    SortField.BEDS: int,
    SortField.YEAR_BUILT: int,
    SortField.LOT_SIZE: int,
}


def current_listing_subq() -> Any:
    """Latest listing per property (any status; the status *filter* is applied on the join).
    Ordered by list date then feed modification timestamp so the freshest marketing event wins.
    """
    return (
        select(
            Listing.property_id.label("property_id"),
            Listing.id.label("listing_id"),
            Listing.list_price.label("list_price"),
            Listing.status.label("status"),
            Listing.dom_current.label("dom"),
            Listing.list_date.label("list_date"),
            Listing.remarks.label("remarks"),
            Listing.photo_count.label("photo_count"),
        )
        .distinct(Listing.property_id)
        .order_by(
            Listing.property_id,
            Listing.list_date.desc().nullslast(),
            Listing.source_ts.desc().nullslast(),
            Listing.id.desc(),
        )
        .subquery("cur_listing")
    )


def current_score_subq(strategy: Strategy) -> Any:
    """Latest score per property for the requested strategy (§11.5 — the same rows the Top-25
    index serves). A property with no score for this strategy is absent → left-joined below.
    """
    return (
        select(
            Score.property_id.label("property_id"),
            Score.score.label("score"),
            Score.grade.label("grade"),
            Score.risk_score.label("risk_score"),
            Score.confidence_score.label("confidence_score"),
            Score.recommendation.label("recommendation"),
        )
        .where(Score.strategy == strategy)
        .distinct(Score.property_id)
        .order_by(Score.property_id, Score.computed_at.desc())
        .subquery("cur_score")
    )


def _price_per_sqft(cl: Any) -> Any:
    # NULLIF guards divide-by-zero (land / missing sqft) → NULL, which sorts last.
    return cl.c.list_price / func.nullif(Property.sqft, 0)


def _sort_base_and_sentinel(
    field: SortField, cl: Any, cs: Any, descending: bool
) -> tuple[Any, Any]:
    """The raw (pre-coalesce) sort expression for a field, plus the sentinel that sends its
    NULLs to the tail for this direction.
    """
    py_type = _FIELD_PY_TYPE[field]
    if py_type is Decimal:
        sentinel: Any = _NUM_MIN if descending else _NUM_MAX
    elif py_type is date:
        sentinel = _DATE_MIN if descending else _DATE_MAX
    else:
        sentinel = _INT_MIN if descending else _INT_MAX

    exprs: dict[SortField, Any] = {
        SortField.SCORE: cs.c.score,
        SortField.PRICE: cl.c.list_price,
        SortField.PRICE_PER_SQFT: _price_per_sqft(cl),
        SortField.NEWEST: cl.c.list_date,
        SortField.DOM: cl.c.dom,
        SortField.SQFT: Property.sqft,
        SortField.BEDS: Property.beds,
        SortField.YEAR_BUILT: Property.year_built,
        SortField.LOT_SIZE: Property.lot_sqft,
    }
    return exprs[field], sentinel


def _filter_predicates(f: PropertyFilters, cl: Any, cs: Any) -> list[ColumnElement[bool]]:
    """Every §6.1 filter → a list of SQL predicates (AND-combined by the caller). Absent
    filters contribute nothing; a score filter on an unscored property fails naturally because
    the left-joined `cs` columns are NULL.
    """
    p: list[ColumnElement[bool]] = []

    # Structural (properties)
    if f.beds_min is not None:
        p.append(Property.beds >= f.beds_min)
    if f.beds_max is not None:
        p.append(Property.beds <= f.beds_max)
    if f.baths_min is not None:
        p.append(Property.baths >= f.baths_min)
    if f.baths_max is not None:
        p.append(Property.baths <= f.baths_max)
    if f.sqft_min is not None:
        p.append(Property.sqft >= f.sqft_min)
    if f.sqft_max is not None:
        p.append(Property.sqft <= f.sqft_max)
    if f.lot_sqft_min is not None:
        p.append(Property.lot_sqft >= f.lot_sqft_min)
    if f.lot_sqft_max is not None:
        p.append(Property.lot_sqft <= f.lot_sqft_max)
    if f.year_built_min is not None:
        p.append(Property.year_built >= f.year_built_min)
    if f.year_built_max is not None:
        p.append(Property.year_built <= f.year_built_max)
    if f.stories_min is not None:
        p.append(Property.stories >= f.stories_min)
    if f.stories_max is not None:
        p.append(Property.stories <= f.stories_max)
    if f.garage_min is not None:
        p.append(Property.garage_spaces >= f.garage_min)
    if f.pool is not None:
        p.append(Property.pool.is_(f.pool))
    if f.hoa_max is not None:
        # Unknown/absent HOA is treated as $0 (no HOA) → passes an "HOA at most X" filter.
        p.append(func.coalesce(Property.hoa_monthly, 0) <= f.hoa_max)
    if f.property_types:
        p.append(Property.property_type.in_(f.property_types))

    # Listing (current listing)
    statuses = f.statuses if f.statuses is not None else [ListingStatus.ACTIVE]
    p.append(cl.c.status.in_(statuses))
    if f.price_min is not None:
        p.append(cl.c.list_price >= f.price_min)
    if f.price_max is not None:
        p.append(cl.c.list_price <= f.price_max)
    if f.price_per_sqft_min is not None:
        p.append(_price_per_sqft(cl) >= f.price_per_sqft_min)
    if f.price_per_sqft_max is not None:
        p.append(_price_per_sqft(cl) <= f.price_per_sqft_max)
    if f.dom_min is not None:
        p.append(cl.c.dom >= f.dom_min)
    if f.dom_max is not None:
        p.append(cl.c.dom <= f.dom_max)
    if f.listed_since is not None:
        p.append(cl.c.list_date >= f.listed_since)
    if f.keywords:
        p.append(cl.c.remarks.ilike(f"%{f.keywords}%"))  # value bound as a parameter (safe)

    # Analytics (score of the query strategy)
    if f.score_min is not None:
        p.append(cs.c.score >= f.score_min)
    if f.score_max is not None:
        p.append(cs.c.score <= f.score_max)
    if f.grades:
        p.append(cs.c.grade.in_(f.grades))
    if f.risk_max is not None:
        p.append(cs.c.risk_score <= f.risk_max)
    if f.confidence_min is not None:
        p.append(cs.c.confidence_score >= f.confidence_min)
    if f.recommendations:
        p.append(cs.c.recommendation.in_(f.recommendations))

    return p


def geometry_predicate(geom_wkt: str, radius_m: float | None = None) -> ColumnElement[bool]:
    """Turn a resolved WKT string into a PostGIS predicate on `properties.geom`. A plain
    polygon → `ST_Intersects`; a point + radius → `ST_DWithin` on the geography cast (true
    metres, not degrees). WKT is passed as a bound parameter, never string-interpolated. The
    *service* builds these (it needs the DB to resolve a saved area) and hands the list in, so
    tiles can AND a tile bbox with a drawn polygon.
    """
    geom = func.ST_GeomFromText(geom_wkt, 4326)
    if radius_m is not None:
        # `geography(geometry)` casts to the spheroid so the radius is honest metres, not
        # degrees. ST_DWithin on geography is the standard "within N metres of a point" test.
        return func.ST_DWithin(func.geography(Property.geom), func.geography(geom), radius_m)
    return func.ST_Intersects(Property.geom, geom)


def _scope_predicates(scope: SearchScope) -> list[ColumnElement[bool]]:
    """Coarse, non-geometric scope narrowings resolved straight on `properties` — market, ZIP,
    city, county. The *fine* geometry (area/bbox/polygon/radius) is resolved by the service and
    passed as prebuilt predicates; these compose with it.
    """
    p: list[ColumnElement[bool]] = []
    if scope.market_id is not None:
        p.append(Property.market_id == scope.market_id)
    if scope.county_fips is not None:
        p.append(Property.fips == scope.county_fips)
    if scope.zips:
        p.append(Property.address_norm["zip"].astext.in_(scope.zips))
    if scope.city is not None:
        p.append(Property.address_norm["city"].astext.ilike(scope.city))
    return p


def _base_from(
    query: PropertySearchQuery, geo_predicates: Sequence[ColumnElement[bool]]
) -> Any:
    """Shared FROM/WHERE skeleton for the row search, tile, and count queries: properties
    inner-joined to the current listing (a property with no matching listing isn't a search
    result), left-joined to the current score, filtered by geometry + scope + attribute
    predicates.
    """
    cl = current_listing_subq()
    cs = current_score_subq(query.strategy)
    stmt = (
        select(Property.id)  # replaced by caller via .with_only_columns
        .select_from(Property)
        .join(cl, cl.c.property_id == Property.id)
        .join(cs, cs.c.property_id == Property.id, isouter=True)
    )
    predicates = _filter_predicates(query.filters, cl, cs)
    predicates.extend(_scope_predicates(query.scope))
    predicates.extend(geo_predicates)
    if predicates:
        stmt = stmt.where(and_(*predicates))
    return stmt, cl, cs


def build_search_select(
    query: PropertySearchQuery,
    *,
    geo_predicates: Sequence[ColumnElement[bool]] = (),
) -> Select[Any]:
    """The full result query: projection + keyset + ordering + limit(+1 for has-more)."""
    stmt, cl, cs = _base_from(query, geo_predicates)
    descending = query.sort.descending
    base_expr, sentinel = _sort_base_and_sentinel(query.sort.field, cl, cs, descending)
    sort_expr = func.coalesce(base_expr, sentinel)

    stmt = stmt.with_only_columns(
        Property.id.label("property_id"),
        Property.property_type.label("property_type"),
        Property.beds.label("beds"),
        Property.baths.label("baths"),
        Property.sqft.label("sqft"),
        Property.lot_sqft.label("lot_sqft"),
        Property.year_built.label("year_built"),
        Property.address_norm.label("address_norm"),
        func.ST_Y(Property.geom).label("lat"),
        func.ST_X(Property.geom).label("lon"),
        cl.c.listing_id.label("listing_id"),
        cl.c.list_price.label("list_price"),
        cl.c.status.label("status"),
        cl.c.dom.label("dom"),
        cl.c.list_date.label("list_date"),
        cl.c.photo_count.label("photo_count"),
        _price_per_sqft(cl).label("price_per_sqft"),
        cs.c.score.label("score"),
        cs.c.grade.label("grade"),
        cs.c.risk_score.label("risk_score"),
        cs.c.confidence_score.label("confidence_score"),
        cs.c.recommendation.label("recommendation"),
        sort_expr.label(SORT_LABEL),
        maintain_column_froms=True,
    )

    # Keyset: continue after the cursor's (sort_value, id). Expanded form (not a row-value
    # tuple) for dialect-agnostic clarity and index friendliness.
    cursor = _decode_if_present(query)
    if cursor is not None:
        last_value, last_id = cursor
        if descending:
            stmt = stmt.where(
                or_(sort_expr < last_value, and_(sort_expr == last_value, Property.id < last_id))
            )
        else:
            stmt = stmt.where(
                or_(sort_expr > last_value, and_(sort_expr == last_value, Property.id > last_id))
            )

    order = (
        (sort_expr.desc(), Property.id.desc())
        if descending
        else (sort_expr.asc(), Property.id.asc())
    )
    # Fetch one extra row to detect whether a further page exists without a COUNT.
    return cast("Select[Any]", stmt.order_by(*order).limit(query.limit + 1))


def build_tile_select(
    query: PropertySearchQuery,
    *,
    geo_predicates: Sequence[ColumnElement[bool]] = (),
    limit: int,
) -> Select[Any]:
    """A minimal projection for the map tile endpoint: id + point + the score used for
    bucketing. No price, address, or attributes leave the server (§12.2). Hard `limit` caps a
    single tile's payload; the service flags truncation.
    """
    stmt, cl, cs = _base_from(query, geo_predicates)
    tile_stmt = stmt.with_only_columns(
        Property.id.label("property_id"),
        func.ST_Y(Property.geom).label("lat"),
        func.ST_X(Property.geom).label("lon"),
        cs.c.score.label("score"),
        cl.c.list_price.label("list_price"),
        maintain_column_froms=True,
    ).limit(limit)
    return cast("Select[Any]", tile_stmt)


def build_count_select(
    query: PropertySearchQuery,
    *,
    geo_predicates: Sequence[ColumnElement[bool]] = (),
    cap: int,
) -> Select[Any]:
    """Count matches up to `cap` (a capped count is cheap; an exact count over the full join is
    the query keyset exists to avoid). The service reports `capped=True` when the cap is hit.
    """
    stmt, _cl, _cs = _base_from(query, geo_predicates)
    capped = stmt.with_only_columns(Property.id, maintain_column_froms=True).limit(cap).subquery()
    return cast("Select[Any]", select(func.count()).select_from(capped))


# --- Cursor (opaque, tamper-evident) ----------------------------------------------------


def query_fingerprint(query: PropertySearchQuery) -> str:
    """A short hash of everything that shapes the result set *except* limit/cursor. Embedded in
    the cursor so a cursor minted for one query can't be replayed against a different one
    (which would silently skip/duplicate rows). Sort direction/field are included.
    """
    payload = {
        "scope": query.scope.model_dump(mode="json", exclude_none=True),
        "filters": query.filters.model_dump(mode="json", exclude_none=True),
        "strategy": query.strategy.value,
        "sort": {"field": query.sort.field.value, "descending": query.sort.descending},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def encode_cursor(query: PropertySearchQuery, sort_value: Any, last_id: Any) -> str:
    payload = {
        "v": _serialize_value(sort_value),
        "i": str(last_id),
        "h": query_fingerprint(query),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(query: PropertySearchQuery, cursor: str) -> tuple[Any, str]:
    """Decode + validate a cursor against the current query. Raises `ValidationError` (→ 422)
    on tampering, corruption, or a fingerprint mismatch (the caller changed filters/sort but
    kept a stale cursor).
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        value_str, last_id, fingerprint = payload["v"], payload["i"], payload["h"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValidationError("Malformed pagination cursor") from exc
    if fingerprint != query_fingerprint(query):
        raise ValidationError("Cursor does not match this query; restart pagination")
    return _deserialize_value(query.sort.field, value_str), str(last_id)


def _decode_if_present(query: PropertySearchQuery) -> tuple[Any, str] | None:
    return decode_cursor(query, query.cursor) if query.cursor else None


def _serialize_value(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _deserialize_value(field: SortField, value_str: str) -> Any:
    py_type = _FIELD_PY_TYPE[field]
    try:
        if py_type is Decimal:
            return Decimal(value_str)
        if py_type is date:
            return date.fromisoformat(value_str)
        return int(value_str)
    except (ValueError, ArithmeticError) as exc:
        raise ValidationError("Malformed pagination cursor value") from exc
