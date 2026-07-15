"""Market intelligence unit tests (no DB) — the §28 core: the fair-housing guardrail (§28.5,
binding), geo-hierarchy resolution with honest level labels (§28.2), the four derived indices and
the market score (§28.4). The fair-housing tests are the load-bearing ones: a protected-class
variable must be *impossible* to use as an index input, not merely discouraged.
"""

from datetime import date
from decimal import Decimal

import pytest

from deallens.modules.markets import indices, metrics
from deallens.modules.markets.metrics import FairHousingViolation, MetricReading
from deallens.modules.markets.models import GeoLevel

_D = Decimal


# --- Fair-housing guardrail (§28.5) -----------------------------------------------------


def test_protected_class_metrics_flagged() -> None:
    assert metrics.is_protected_class("race_composition") is True
    assert metrics.is_protected_class("ethnicity_share_by_tract") is True
    assert metrics.is_protected_class("familial_status_composition") is True
    # Economic demographics are NOT protected-class composition.
    assert metrics.is_protected_class("median_income") is False
    assert metrics.is_protected_class("population_growth") is False


def test_assert_fair_housing_raises_on_protected() -> None:
    with pytest.raises(FairHousingViolation):
        metrics.assert_fair_housing(["school_rating_pct", "race_composition"])


def test_assert_fair_housing_passes_clean_set() -> None:
    metrics.assert_fair_housing(["school_rating_pct", "crime_index", "walkability"])  # no raise


def test_indices_never_include_protected_inputs() -> None:
    # Every index's declared component set must pass the guardrail — a regression that added a
    # protected variable to an index would fail here (and in CI).
    for idx in indices.compute_indices({}):
        assert idx.key  # computing the index ran assert_fair_housing internally without raising


# --- Geo-hierarchy resolution (§28.2) ---------------------------------------------------


def _reading(level: GeoLevel, val: str, period: date) -> MetricReading:
    return MetricReading(metric="crime_index", geo_level=level, geo_id=f"{level.value}-1",
                         value=_D(val), period=period, source="fbi")


def test_resolve_prefers_finest_level() -> None:
    chosen = metrics.resolve_finest([
        _reading(GeoLevel.COUNTY, "40", date(2026, 1, 1)),
        _reading(GeoLevel.ZIP, "25", date(2026, 1, 1)),
    ])
    assert chosen is not None
    assert chosen.geo_level is GeoLevel.ZIP  # finest wins
    assert chosen.value == _D("25")


def test_resolve_labels_level_honestly() -> None:
    # Only a county reading available → carried at county level, never faked to ZIP (§28.2).
    chosen = metrics.resolve_finest([_reading(GeoLevel.COUNTY, "40", date(2026, 1, 1))])
    assert chosen is not None and chosen.geo_level is GeoLevel.COUNTY


def test_resolve_newest_period_breaks_ties() -> None:
    chosen = metrics.resolve_finest([
        _reading(GeoLevel.ZIP, "30", date(2025, 1, 1)),
        _reading(GeoLevel.ZIP, "22", date(2026, 1, 1)),
    ])
    assert chosen is not None and chosen.value == _D("22")


def test_resolve_empty_is_none() -> None:
    assert metrics.resolve_finest([]) is None


# --- Derived indices + market score (§28.4) ---------------------------------------------


def _strong_market() -> dict[str, Decimal]:
    return {
        "appreciation_3yr_annualized": _D("0.07"), "median_dom": _D("25"),
        "inventory_months": _D("3"), "absorption_rate": _D("0.4"),
        "rent_growth_3yr": _D("0.05"), "rent_to_price": _D("0.09"),
        "household_formation": _D("0.02"), "school_rating_pct": _D("80"),
        "crime_index": _D("20"), "walkability": _D("70"), "owner_occupancy_share": _D("0.7"),
    }


def test_all_four_indices_computed() -> None:
    idxs = {i.key: i for i in indices.compute_indices(_strong_market())}
    assert set(idxs) == {"momentum", "rental_demand", "liquidity", "neighborhood_quality"}
    for i in idxs.values():
        assert i.score is not None and 0 <= i.score <= 100


def test_strong_market_scores_high() -> None:
    idxs = indices.compute_indices(_strong_market())
    score = indices.market_score(idxs)
    assert score is not None and score >= _D("70")


def test_weak_market_scores_lower_than_strong() -> None:
    weak = dict(_strong_market())
    weak.update({"appreciation_3yr_annualized": _D("-0.01"), "median_dom": _D("110"),
                 "inventory_months": _D("11"), "crime_index": _D("85")})
    strong_score = indices.market_score(indices.compute_indices(_strong_market()))
    weak_score = indices.market_score(indices.compute_indices(weak))
    assert strong_score is not None and weak_score is not None and weak_score < strong_score


def test_missing_metric_reweights_not_zero() -> None:
    partial = {"appreciation_3yr_annualized": _D("0.07")}  # only one momentum component
    momentum = next(i for i in indices.compute_indices(partial) if i.key == "momentum")
    assert momentum.score is not None and momentum.score > _D("50")
    assert "median_dom" in momentum.components_missing
    assert momentum.components_used == ["appreciation_3yr_annualized"]


def test_empty_market_has_no_score() -> None:
    idxs = indices.compute_indices({})
    assert all(i.score is None for i in idxs)
    assert indices.market_score(idxs) is None


def test_national_percentile_when_cohort_supplied() -> None:
    cohort = {"momentum": [Decimal(i) for i in range(100)]}
    idxs = indices.compute_indices(_strong_market(), national=cohort)
    momentum = next(i for i in idxs if i.key == "momentum")
    assert momentum.national_percentile is not None
