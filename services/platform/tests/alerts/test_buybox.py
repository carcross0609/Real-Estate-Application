"""Buy-box matcher unit tests (no DB) — the pure predicate that decides whether a scored
property clears a saved box (§11.5 #6, FR-040), including the instant-alert confidence gate
(§25.1 #4) and the flood-zone exclusion.
"""

from decimal import Decimal

from deallens.modules.alerts.buybox import (
    MatchCandidate,
    evaluate,
    is_special_flood_hazard,
)
from deallens.modules.alerts.schemas import BuyBoxFilters
from deallens.modules.ingestion.models import PropertyType


def _candidate(**over: object) -> MatchCandidate:
    base: dict[str, object] = dict(
        property_id="p1",
        list_price=Decimal("300000"),
        beds=3,
        baths=Decimal("2"),
        sqft=1600,
        year_built=2005,
        property_type=PropertyType.SFR,
        score=Decimal("80"),
        risk=Decimal("30"),
        confidence=Decimal("70"),
    )
    base.update(over)
    return MatchCandidate(**base)  # type: ignore[arg-type]


def test_empty_filters_match_everything() -> None:
    out = evaluate(_candidate(), BuyBoxFilters())
    assert out.matched
    assert out.instant_eligible  # confidence 70 ≥ gate 50
    assert out.reasons == ()


def test_price_ceiling_excludes() -> None:
    filters = BuyBoxFilters(price_max=Decimal("400000"))
    out = evaluate(_candidate(list_price=Decimal("450000")), filters)
    assert not out.matched
    assert "price_above_max" in out.reasons


def test_min_bounds_fail_when_value_missing() -> None:
    # A filter set on a datum the property lacks is a conservative non-match, not a pass.
    out = evaluate(_candidate(beds=None), BuyBoxFilters(beds_min=3))
    assert not out.matched
    assert "beds_below_min" in out.reasons


def test_score_and_risk_gates() -> None:
    filters = BuyBoxFilters(min_score=Decimal("75"), max_risk=Decimal("40"))
    assert evaluate(_candidate(score=Decimal("80"), risk=Decimal("30")), filters).matched
    assert not evaluate(_candidate(score=Decimal("70")), filters).matched
    assert not evaluate(_candidate(risk=Decimal("55")), filters).matched


def test_property_type_filter() -> None:
    filters = BuyBoxFilters(property_types=[PropertyType.CONDO])
    assert not evaluate(_candidate(property_type=PropertyType.SFR), filters).matched
    assert evaluate(_candidate(property_type=PropertyType.CONDO), filters).matched


def test_instant_gate_blocks_low_confidence_but_still_matches() -> None:
    # Low confidence still matches the box — it just isn't eligible for an instant interrupt.
    out = evaluate(
        _candidate(confidence=Decimal("40")), BuyBoxFilters(), confidence_gate=Decimal("0.5")
    )
    assert out.matched
    assert not out.instant_eligible


def test_missing_confidence_never_instant() -> None:
    out = evaluate(_candidate(confidence=None), BuyBoxFilters())
    assert out.matched
    assert not out.instant_eligible


def test_flood_exclusion() -> None:
    filters = BuyBoxFilters(exclude_flood_zones=True)
    assert not evaluate(_candidate(flood_zone="AE"), filters).matched
    assert evaluate(_candidate(flood_zone="X"), filters).matched
    assert evaluate(_candidate(flood_zone=None), filters).matched


def test_keywords_or_match() -> None:
    filters = BuyBoxFilters(keywords=["cash only", "handyman"])
    assert evaluate(_candidate(remarks="Great handyman special!"), filters).matched
    assert not evaluate(_candidate(remarks="Move-in ready"), filters).matched


def test_search_area_unknown_is_not_constrained() -> None:
    filters = BuyBoxFilters(search_area_ids=["area-1"])
    # None = membership undetermined → the area filter is skipped (no false suppression).
    assert evaluate(_candidate(search_area_ids=None), filters).matched
    # Empty set = known to be in no area → fails an area filter.
    assert not evaluate(_candidate(search_area_ids=frozenset()), filters).matched
    assert evaluate(_candidate(search_area_ids=frozenset({"area-1"})), filters).matched


def test_is_special_flood_hazard() -> None:
    assert is_special_flood_hazard("A")
    assert is_special_flood_hazard("VE")
    assert not is_special_flood_hazard("X")
    assert not is_special_flood_hazard(None)
