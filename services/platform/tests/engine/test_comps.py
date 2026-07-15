"""Comp selection, similarity, and adjustment-grid unit tests (no DB) — the pure core of the
comparable-sales engine (03 §26.1). These assert the intellectual claims: a near-twin outscores
an edge-of-band comp, the adjustment grid moves a comp *toward* the subject, missing fields drop
their line rather than fabricating a $0 "verified equal", and the fallback ladder widens only
while under the comp floor.
"""

from datetime import date
from decimal import Decimal

from deallens.modules.engine import comps
from deallens.modules.engine.comps import (
    CompCandidate,
    FallbackStage,
    SubjectFeatures,
)
from deallens.modules.engine.schemas import AdjustmentConfig, CompSelectionParams

_NOW = date(2026, 7, 1)
_PARAMS = CompSelectionParams()
_CONFIG = AdjustmentConfig()
_RADIUS_M = Decimal("1207")  # ~0.75 mi


def _subject(**over: object) -> SubjectFeatures:
    base: dict[str, object] = dict(
        sqft=1800, beds=3, baths=Decimal("2"), garage_spaces=2, lot_sqft=6000,
        pool=False, year_built=2000, condition_grade=3,
    )
    base.update(over)
    return SubjectFeatures(**base)  # type: ignore[arg-type]


def _comp(pid: str, **over: object) -> CompCandidate:
    # Default comp is a true twin next door, sold today — so an untouched _comp scores ~1 and
    # every degradation (distance, age, size) can be introduced explicitly per test.
    base: dict[str, object] = dict(
        property_id=pid, observed_value=Decimal("300000"), observed_date=_NOW,
        distance_m=Decimal("0"), sqft=1800, beds=3, baths=Decimal("2"),
        garage_spaces=2, lot_sqft=6000, pool=False, year_built=2000, condition_grade=3,
    )
    base.update(over)
    return CompCandidate(**base)  # type: ignore[arg-type]


# --- Similarity -------------------------------------------------------------------------


def test_identical_comp_scores_near_one() -> None:
    sim = comps.similarity(_subject(), _comp("a"), _PARAMS, radius_m=_RADIUS_M, now=_NOW)
    assert sim > Decimal("0.95")


def test_near_twin_beats_edge_of_band_comp() -> None:
    twin = _comp("twin")
    far = _comp("far", sqft=2250, distance_m=Decimal("1100"), observed_date=date(2026, 1, 5),
                beds=2, year_built=1970)
    s_twin = comps.similarity(_subject(), twin, _PARAMS, radius_m=_RADIUS_M, now=_NOW)
    s_far = comps.similarity(_subject(), far, _PARAMS, radius_m=_RADIUS_M, now=_NOW)
    assert s_twin > s_far


def test_similarity_is_zero_when_no_shared_features() -> None:
    subject = SubjectFeatures()  # nothing to compare on
    comp = CompCandidate(property_id="x", observed_value=Decimal("1"))
    assert comps.similarity(subject, comp, _PARAMS, radius_m=_RADIUS_M, now=_NOW) == Decimal("0")


def test_missing_subject_field_does_not_penalize() -> None:
    # A subject with no pool info must not be punished on the pool/other lines; its similarity
    # should rest on the fields both share and stay high for an otherwise-identical comp.
    subject = _subject(pool=None, garage_spaces=None)
    sim = comps.similarity(subject, _comp("a"), _PARAMS, radius_m=_RADIUS_M, now=_NOW)
    assert sim > Decimal("0.95")


def test_similarity_quantized_to_five_decimals() -> None:
    sim = comps.similarity(_subject(), _comp("a", sqft=1750), _PARAMS, radius_m=_RADIUS_M, now=_NOW)
    assert -sim.as_tuple().exponent <= 5


# --- Adjustment grid --------------------------------------------------------------------


def test_bigger_subject_gets_positive_sqft_adjustment() -> None:
    # Subject is larger than the comp → the comp implies the subject is worth *more* (positive).
    comp = _comp("a", sqft=1600, observed_value=Decimal("300000"))
    adj = comps.line_item_adjustments(
        _subject(sqft=1800), comp, _CONFIG, per_sqft=Decimal("150"), target_condition_grade=None
    )
    sqft_line = next(a for a in adj if a.feature == "sqft")
    assert sqft_line.amount > 0


def test_adjustment_moves_comp_toward_subject() -> None:
    # A comp with one fewer bed and bath should be adjusted upward toward the richer subject.
    comp = _comp("a", beds=2, baths=Decimal("1"))
    adj = comps.line_item_adjustments(
        _subject(), comp, _CONFIG, per_sqft=Decimal("150"), target_condition_grade=None
    )
    total = comps.adjusted_value(comp, adj)
    assert total > comp.observed_value


def test_missing_feature_emits_no_line() -> None:
    # Comp has no garage/pool data → no garage or pool adjustment line at all (not a $0 line).
    comp = _comp("a", garage_spaces=None, pool=None)
    adj = comps.line_item_adjustments(
        _subject(), comp, _CONFIG, per_sqft=Decimal("150"), target_condition_grade=None
    )
    features = {a.feature for a in adj}
    assert "garage" not in features and "pool" not in features


def test_condition_line_uses_target_grade_for_arv() -> None:
    # A grade-2 comp normalized to an ARV target of 4 gets a positive condition adjustment.
    comp = _comp("a", condition_grade=2)
    adj = comps.line_item_adjustments(
        _subject(), comp, _CONFIG, per_sqft=Decimal("150"), target_condition_grade=4
    )
    cond = next(a for a in adj if a.feature == "condition")
    assert cond.amount == Decimal("2") * _CONFIG.condition_grade_value


def test_adjusted_value_floors_at_zero() -> None:
    comp = CompCandidate(property_id="x", observed_value=Decimal("1000"))
    huge_negative = [comps.CompAdjustment(feature="sqft", amount=Decimal("-999999"))]
    assert comps.adjusted_value(comp, huge_negative) == Decimal("0")


def test_median_ppsf_skips_zero_and_missing_sqft() -> None:
    cands = [
        _comp("a", sqft=1000, observed_value=Decimal("200000")),  # 200/sqft
        _comp("b", sqft=2000, observed_value=Decimal("400000")),  # 200/sqft
        _comp("c", sqft=None, observed_value=Decimal("500000")),  # skipped
    ]
    assert comps.comp_set_median_ppsf(cands) == Decimal("200")


# --- Ranking & fallback -----------------------------------------------------------------


def test_score_and_adjust_ranks_by_similarity_and_caps() -> None:
    params = CompSelectionParams(min_comps=1, target_comps=2, max_comps=2)
    cands = [
        _comp("twin"),
        _comp("mid", sqft=1950),
        _comp("worst", sqft=2300, distance_m=Decimal("1100"), beds=1),
    ]
    scored = comps.score_and_adjust(
        _subject(), cands, params, _CONFIG, radius_m=_RADIUS_M, now=_NOW,
        target_condition_grade=None,
    )
    assert len(scored) == 2
    assert scored[0].similarity >= scored[1].similarity
    assert scored[0].candidate.property_id == "twin"


def test_fallback_stops_when_enough_comps() -> None:
    params = CompSelectionParams(min_comps=3)
    assert comps.next_fallback_stage(FallbackStage.BASE, 5, params) is None


def test_fallback_advances_one_rung_when_short() -> None:
    params = CompSelectionParams(min_comps=3)
    assert comps.next_fallback_stage(FallbackStage.BASE, 1, params) is FallbackStage.WIDEN_RECENCY
    assert (
        comps.next_fallback_stage(FallbackStage.WIDEN_RECENCY, 1, params)
        is FallbackStage.WIDEN_RADIUS
    )
    assert (
        comps.next_fallback_stage(FallbackStage.WIDEN_RADIUS, 1, params)
        is FallbackStage.MODEL_PRIOR
    )


def test_fallback_exhausts_at_model_prior() -> None:
    params = CompSelectionParams(min_comps=3)
    assert comps.next_fallback_stage(FallbackStage.MODEL_PRIOR, 0, params) is None
