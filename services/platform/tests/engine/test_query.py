"""Comp-candidate query-compiler tests (no DB): render to SQL text and assert the hard filters
of 03 §26.1 are present and shaped right — sold-vs-rental listing predicates, radius/recency,
size band, and the one-comp-per-property dedup. Mirrors the search `test_query` approach: the
compiler is pure, so we verify it by inspecting the compiled statement, no database needed.
"""

from datetime import date

from sqlalchemy.dialects import postgresql

from deallens.modules.engine import query
from deallens.modules.engine.models import CompSetKind
from deallens.modules.ingestion.models import PropertyType


def _sql(stmt: object) -> str:
    return str(
        stmt.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}
        )
    )


def _sale_select() -> object:
    return query.comp_candidates_select(
        subject_id="00000000-0000-0000-0000-000000000000",
        lon=-97.7, lat=30.2, property_type=PropertyType.SFR,
        subject_sqft=1800, subject_beds=3, kind=CompSetKind.SALE,
        same_property_type=True, sqft_tolerance=0.25, beds_tolerance=1,
        radius_m=1207.0, recency_cutoff=date(2026, 1, 1),
    )


def test_sale_candidates_filter_on_sold_status_and_close_price() -> None:
    sql = _sql(_sale_select()).lower()
    assert "status" in sql
    assert "close_price" in sql and "close_date" in sql
    assert "st_dwithin" in sql  # radius predicate
    assert "sqft" in sql  # size band


def test_sale_candidates_dedup_one_per_property() -> None:
    sql = _sql(_sale_select()).lower()
    assert "distinct on" in sql  # DISTINCT ON (property_id) → one comp per house


def test_rental_candidates_use_list_price_and_rental_tier() -> None:
    stmt = query.comp_candidates_select(
        subject_id="00000000-0000-0000-0000-000000000000",
        lon=-97.7, lat=30.2, property_type=PropertyType.SFR,
        subject_sqft=1800, subject_beds=3, kind=CompSetKind.RENTAL,
        same_property_type=True, sqft_tolerance=0.25, beds_tolerance=1,
        radius_m=1207.0, recency_cutoff=date(2026, 1, 1),
    )
    sql = _sql(stmt).lower()
    assert "list_price" in sql and "list_date" in sql
    assert "close_price" not in sql  # rentals never read a sale close price


def test_pin_query_ignores_radius_but_keeps_listing_shape() -> None:
    stmt = query.comps_by_ids_select(
        lon=-97.7, lat=30.2,
        property_ids=["11111111-1111-1111-1111-111111111111"],
        kind=CompSetKind.SALE,
    )
    sql = _sql(stmt).lower()
    assert "st_dwithin" not in sql  # a pin overrides the automatic radius net
    assert "close_price" in sql  # but still needs a real sold transaction to anchor a value
    assert "in (" in sql or "= any" in sql  # id membership predicate


def test_newest_comp_event_is_a_max_over_source_ts() -> None:
    stmt = query.newest_comp_event_select(
        lon=-97.7, lat=30.2, radius_m=1207.0, kind=CompSetKind.SALE
    )
    sql = _sql(stmt).lower()
    assert "max(" in sql and "source_ts" in sql
    assert "st_dwithin" in sql
