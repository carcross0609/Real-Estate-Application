"""Estimator-math unit tests (no DB): weighted quantiles, honest bands, calibrated confidence,
the model-prior fallback, and the rental corrections (03 §26.1, §25.8). These lock the claims
that make the number trustworthy — a dispersed comp set widens the band instead of hiding
disagreement, and confidence moves separately from the point.
"""

from decimal import Decimal

from deallens.modules.engine import valuation
from deallens.modules.engine.comps import CompCandidate, FallbackStage, ScoredComp

_ZERO = Decimal("0")


def _scored(value: str, sim: str) -> ScoredComp:
    return ScoredComp(
        candidate=CompCandidate(property_id=value),
        similarity=Decimal(sim),
        adjusted_value=Decimal(value),
    )


# --- Weighted aggregation ---------------------------------------------------------------


def test_weighted_mean_pulls_toward_heavier_comp() -> None:
    pairs = [(Decimal("300000"), Decimal("0.9")), (Decimal("400000"), Decimal("0.1"))]
    mean = valuation.weighted_mean(pairs)
    assert mean is not None and mean < Decimal("350000")  # heavier low comp dominates


def test_weighted_mean_none_on_zero_weight() -> None:
    assert valuation.weighted_mean([(Decimal("1"), _ZERO)]) is None


def test_weighted_quantile_single_point() -> None:
    assert valuation.weighted_quantile([(Decimal("300000"), Decimal("1"))], Decimal("0.9")) == (
        Decimal("300000")
    )


def test_quantile_band_captures_outlier_asymmetrically() -> None:
    # Three comps clustered at 300k + one outlier at 360k → P90 pulled up toward the outlier,
    # P10 stays near the cluster. The band is asymmetric, as an honest one should be.
    pairs = [
        (Decimal("300000"), Decimal("1")),
        (Decimal("302000"), Decimal("1")),
        (Decimal("298000"), Decimal("1")),
        (Decimal("360000"), Decimal("1")),
    ]
    p10 = valuation.weighted_quantile(pairs, Decimal("0.10"))
    p50 = valuation.weighted_quantile(pairs, Decimal("0.50"))
    p90 = valuation.weighted_quantile(pairs, Decimal("0.90"))
    assert p10 is not None and p50 is not None and p90 is not None
    assert p90 - p50 > p50 - p10  # right tail longer


def test_quantile_monotone_in_q() -> None:
    pairs = [(Decimal(str(v)), Decimal("1")) for v in (100, 200, 300, 400, 500)]
    qs = [valuation.weighted_quantile(pairs, Decimal(str(q))) for q in ("0.1", "0.5", "0.9")]
    assert qs[0] is not None and qs[1] is not None and qs[2] is not None
    assert qs[0] <= qs[1] <= qs[2]


# --- Confidence -------------------------------------------------------------------------


def test_more_comps_more_confidence() -> None:
    lo = valuation.confidence(comp_count=2, target_comps=8, dispersion=Decimal("0.05"),
                              stage=FallbackStage.BASE)
    hi = valuation.confidence(comp_count=8, target_comps=8, dispersion=Decimal("0.05"),
                              stage=FallbackStage.BASE)
    assert hi > lo


def test_dispersion_lowers_confidence() -> None:
    tight = valuation.confidence(comp_count=8, target_comps=8, dispersion=Decimal("0.02"),
                                 stage=FallbackStage.BASE)
    wide = valuation.confidence(comp_count=8, target_comps=8, dispersion=Decimal("0.4"),
                                stage=FallbackStage.BASE)
    assert tight > wide


def test_fallback_stage_penalizes_confidence() -> None:
    base = valuation.confidence(comp_count=8, target_comps=8, dispersion=Decimal("0.05"),
                                stage=FallbackStage.BASE)
    widened = valuation.confidence(comp_count=8, target_comps=8, dispersion=Decimal("0.05"),
                                   stage=FallbackStage.WIDEN_RADIUS)
    assert widened < base


def test_cross_check_divergence_dents_confidence() -> None:
    clean = valuation.confidence(comp_count=8, target_comps=8, dispersion=Decimal("0.05"),
                                 stage=FallbackStage.BASE, cross_check_divergence=Decimal("0.02"))
    diverged = valuation.confidence(comp_count=8, target_comps=8, dispersion=Decimal("0.05"),
                                    stage=FallbackStage.BASE, cross_check_divergence=Decimal("0.3"))
    assert diverged < clean


def test_confidence_bounded_0_1() -> None:
    c = valuation.confidence(comp_count=99, target_comps=1, dispersion=_ZERO,
                             stage=FallbackStage.BASE)
    assert _ZERO <= c <= Decimal("1")


# --- Estimate assembly ------------------------------------------------------------------


def test_estimate_from_comps_produces_point_and_band() -> None:
    scored = [_scored("300000", "0.9"), _scored("310000", "0.8"), _scored("295000", "0.85")]
    est = valuation.estimate_from_comps(scored, target_comps=8, stage=FallbackStage.BASE)
    assert est.point is not None and est.p10 is not None and est.p90 is not None
    assert est.p10 <= est.point <= est.p90
    assert est.comp_count == 3
    assert est.method == "comp-based"


def test_widened_stage_labels_method() -> None:
    scored = [_scored("300000", "0.9"), _scored("310000", "0.8")]
    est = valuation.estimate_from_comps(scored, target_comps=8, stage=FallbackStage.WIDEN_RECENCY)
    assert est.method == "comp-based (widened)"


def test_empty_comp_set_is_zero_confidence() -> None:
    est = valuation.estimate_from_comps([], target_comps=8, stage=FallbackStage.BASE)
    assert est.point is None and est.confidence == _ZERO and est.comp_count == 0


def test_zero_similarity_comps_excluded_from_estimate() -> None:
    scored = [_scored("300000", "0.9"), _scored("999999", "0")]
    est = valuation.estimate_from_comps(scored, target_comps=8, stage=FallbackStage.BASE)
    assert est.comp_count == 1  # the zero-similarity outlier drops out


# --- Model prior ------------------------------------------------------------------------


def test_model_prior_wraps_wide_band_low_confidence() -> None:
    est = valuation.model_prior_estimate(subject_sqft=1800, market_ppsf=Decimal("175"))
    assert est.point == Decimal("315000.00")
    assert est.p10 is not None and est.p90 is not None and est.p10 < est.point < est.p90
    assert est.method == "model-prior"
    assert est.confidence < Decimal("0.5")


def test_model_prior_unavailable_without_inputs() -> None:
    est = valuation.model_prior_estimate(subject_sqft=None, market_ppsf=Decimal("175"))
    assert est.point is None and est.method == "unavailable"


# --- Rental corrections -----------------------------------------------------------------


def test_list_to_effective_discounts_asking_rent() -> None:
    eff = valuation.apply_list_to_effective(Decimal("2000"), Decimal("-0.03"))
    assert eff == Decimal("1940.00")


def test_list_to_effective_floors_at_zero() -> None:
    assert valuation.apply_list_to_effective(Decimal("1000"), Decimal("-2")) == _ZERO


def test_cross_check_divergence_fraction() -> None:
    div = valuation.cross_check_divergence(Decimal("2100"), Decimal("2000"))
    assert div == Decimal("0.05")


def test_cross_check_none_when_external_missing() -> None:
    assert valuation.cross_check_divergence(Decimal("2100"), None) is None


def test_dispersion_ratio_zero_when_degenerate() -> None:
    assert valuation.dispersion_ratio(None, None, Decimal("300000")) == _ZERO
    assert valuation.dispersion_ratio(Decimal("1"), Decimal("2"), _ZERO) == _ZERO
