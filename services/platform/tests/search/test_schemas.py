"""Pure validation tests for the search boundary contracts (no database)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from deallens.modules.search.schemas import (
    BBox,
    PropertyFilters,
    SearchScope,
    SortDirection,
    SortField,
    SortSpec,
)


def test_filter_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError):
        PropertyFilters(price_min=Decimal("500000"), price_max=Decimal("100000"))
    with pytest.raises(ValidationError):
        PropertyFilters(beds_min=5, beds_max=2)


def test_filter_accepts_equal_bounds() -> None:
    f = PropertyFilters(beds_min=3, beds_max=3)
    assert f.beds_min == f.beds_max == 3


def test_filter_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        PropertyFilters(bath_min=2)  # typo for baths_min → forbidden, not silently ignored


def test_scope_rejects_two_fine_geometries() -> None:
    with pytest.raises(ValidationError):
        SearchScope(
            bbox=BBox(min_lon=-1, min_lat=-1, max_lon=1, max_lat=1),
            radius={"lat": 0, "lon": 0, "radius_m": 100},  # type: ignore[arg-type]
        )


def test_scope_allows_market_plus_one_fine_geometry() -> None:
    scope = SearchScope(
        market_id=None, bbox=BBox(min_lon=-1, min_lat=-1, max_lon=1, max_lat=1), city="Austin"
    )
    assert scope.bbox is not None
    assert scope.city == "Austin"


def test_bbox_rejects_inverted_corners() -> None:
    with pytest.raises(ValidationError):
        BBox(min_lon=5, min_lat=0, max_lon=-5, max_lat=1)


def test_sortspec_default_directions() -> None:
    assert SortSpec(field=SortField.SCORE).resolved_direction() is SortDirection.DESC
    assert SortSpec(field=SortField.PRICE).resolved_direction() is SortDirection.ASC
    assert SortSpec(field=SortField.DOM).resolved_direction() is SortDirection.ASC
    # An explicit direction overrides the natural default.
    assert SortSpec(field=SortField.PRICE, direction=SortDirection.DESC).descending is True
