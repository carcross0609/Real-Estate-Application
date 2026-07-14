"""Pure query-compiler + cursor tests — compile the Select to SQL text and inspect it; no
database. Locks in the read-path invariants of ADR 0004: keyset (not OFFSET), a total order
with an id tie-break, current-listing/current-score projection, and tamper-evident cursors.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from deallens.core.enums import Strategy
from deallens.core.errors import ValidationError
from deallens.modules.ingestion.models import PropertyType
from deallens.modules.search import query
from deallens.modules.search.schemas import (
    PropertyFilters,
    PropertySearchQuery,
    SearchScope,
    SortDirection,
    SortField,
    SortSpec,
)


def _sql(stmt: object) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def test_default_search_projects_and_orders_with_id_tiebreak() -> None:
    q = PropertySearchQuery()
    sql = _sql(query.build_search_select(q)).lower()
    # Current-listing / current-score projection subqueries are present.
    assert "cur_listing" in sql
    assert "cur_score" in sql
    # Ordered by the coalesced sort key AND properties.id (total order → stable keyset).
    assert "order by" in sql
    assert "properties.id" in sql
    # Keyset fetches limit+1 with no OFFSET.
    assert "limit" in sql
    assert "offset" not in sql


def test_default_sort_is_score_desc() -> None:
    q = PropertySearchQuery()
    assert q.sort.field is SortField.SCORE
    assert q.sort.descending is True


def test_status_defaults_to_active_when_unset() -> None:
    sql = _sql(query.build_search_select(PropertySearchQuery())).lower()
    # No explicit status filter → the builder injects "active".
    assert "status" in sql


def test_filters_emit_predicates() -> None:
    q = PropertySearchQuery(
        filters=PropertyFilters(
            beds_min=3,
            price_max=Decimal("500000"),
            property_types=[PropertyType.SFR, PropertyType.CONDO],
            score_min=Decimal("70"),
            pool=True,
        )
    )
    sql = _sql(query.build_search_select(q)).lower()
    assert "beds" in sql
    assert "list_price" in sql
    assert "property_type in" in sql
    assert "pool" in sql
    assert "score" in sql


def test_scope_market_and_zip_predicates() -> None:
    q = PropertySearchQuery(
        scope=SearchScope(market_id=UUID(int=7), zips=["94110", "94112"], city="San Francisco")
    )
    sql = _sql(query.build_search_select(q)).lower()
    assert "market_id" in sql
    assert "address_norm" in sql  # ->> 'zip' / 'city' extraction


def test_geometry_predicate_intersects() -> None:
    q = PropertySearchQuery()
    preds = [query.geometry_predicate("POLYGON((0 0,0 1,1 1,1 0,0 0))")]
    sql = _sql(query.build_search_select(q, geo_predicates=preds)).lower()
    assert "st_intersects" in sql
    assert "st_geomfromtext" in sql


def test_radius_predicate_uses_dwithin_geography() -> None:
    preds = [query.geometry_predicate("POINT(-122 37)", radius_m=1000)]
    sql = _sql(query.build_search_select(PropertySearchQuery(), geo_predicates=preds)).lower()
    assert "st_dwithin" in sql
    assert "geography" in sql


def test_cursor_adds_keyset_predicate_descending() -> None:
    q = PropertySearchQuery(
        sort=SortSpec(field=SortField.SCORE, direction=SortDirection.DESC),
        cursor=query.encode_cursor(PropertySearchQuery(), Decimal("80"), UUID(int=1)),
    )
    sql = _sql(query.build_search_select(q)).lower()
    # Descending keyset → "... < cursor" plus the id tie-break comparison.
    assert "properties.id <" in sql


def test_cursor_adds_keyset_predicate_ascending() -> None:
    base = PropertySearchQuery(sort=SortSpec(field=SortField.PRICE))  # price default = asc
    cur = query.encode_cursor(base, Decimal("100000"), UUID(int=1))
    q = base.model_copy(update={"cursor": cur})
    sql = _sql(query.build_search_select(q)).lower()
    assert "properties.id >" in sql


def test_tile_select_is_minimal() -> None:
    sql = _sql(query.build_tile_select(PropertySearchQuery(), limit=100)).lower()
    # Only id + point + score/price for bucketing — no address, beds, etc.
    assert "st_x" in sql and "st_y" in sql
    assert "address_norm" not in sql
    assert "beds" not in sql


def test_count_select_is_capped_count() -> None:
    sql = _sql(query.build_count_select(PropertySearchQuery(), cap=1000)).lower()
    assert "count(" in sql
    assert "limit" in sql  # cap applied inside the subquery


# --- Cursor round-trip & tamper-evidence ------------------------------------------------


def test_cursor_roundtrips_decimal() -> None:
    q = PropertySearchQuery(sort=SortSpec(field=SortField.SCORE))
    cur = query.encode_cursor(q, Decimal("73.50"), UUID(int=42))
    value, last_id = query.decode_cursor(q, cur)
    assert value == Decimal("73.50")
    assert last_id == str(UUID(int=42))


def test_cursor_roundtrips_date() -> None:
    q = PropertySearchQuery(sort=SortSpec(field=SortField.NEWEST))
    cur = query.encode_cursor(q, date(2026, 7, 1), UUID(int=5))
    value, _ = query.decode_cursor(q, cur)
    assert value == date(2026, 7, 1)


def test_cursor_roundtrips_int() -> None:
    q = PropertySearchQuery(sort=SortSpec(field=SortField.DOM))
    cur = query.encode_cursor(q, 45, UUID(int=9))
    value, _ = query.decode_cursor(q, cur)
    assert value == 45


def test_cursor_rejected_when_query_changes() -> None:
    q1 = PropertySearchQuery(filters=PropertyFilters(beds_min=2))
    cur = query.encode_cursor(q1, Decimal("50"), UUID(int=1))
    q2 = PropertySearchQuery(filters=PropertyFilters(beds_min=3), cursor=cur)
    with pytest.raises(ValidationError):
        query.decode_cursor(q2, cur)


def test_cursor_rejected_when_sort_changes() -> None:
    q1 = PropertySearchQuery(sort=SortSpec(field=SortField.SCORE))
    cur = query.encode_cursor(q1, Decimal("50"), UUID(int=1))
    q2 = PropertySearchQuery(sort=SortSpec(field=SortField.PRICE))
    with pytest.raises(ValidationError):
        query.decode_cursor(q2, cur)


def test_malformed_cursor_raises() -> None:
    with pytest.raises(ValidationError):
        query.decode_cursor(PropertySearchQuery(), "not-base64!!!")


def test_fingerprint_ignores_limit_and_cursor() -> None:
    a = PropertySearchQuery(limit=10)
    b = PropertySearchQuery(limit=100, cursor="whatever")
    assert query.query_fingerprint(a) == query.query_fingerprint(b)


def test_fingerprint_sensitive_to_strategy() -> None:
    a = PropertySearchQuery(strategy=Strategy.FLIP)
    b = PropertySearchQuery(strategy=Strategy.LTR)
    assert query.query_fingerprint(a) != query.query_fingerprint(b)
