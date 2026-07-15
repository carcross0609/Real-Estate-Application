"""Normalization-curve unit tests (no DB) — the §25.2 mapping of raw factor values onto the 0–100
axis. Monotone interpolation + clamping (winsorization by construction), the U-shaped DOM curve,
empirical percentile with its small-cohort guard, and boolean point values.
"""

from decimal import Decimal

from deallens.modules.scoring import normalize

_D = Decimal


def test_monotone_interpolates_between_anchors() -> None:
    curve = normalize.MonotoneCurve(anchors=((_D("0"), _D("0")), (_D("10"), _D("100"))))
    assert curve.score(_D("5")) == _D("50")
    assert curve.score(_D("2.5")) == _D("25")


def test_monotone_clamps_outside_range() -> None:
    curve = normalize.MonotoneCurve(anchors=((_D("0"), _D("20")), (_D("10"), _D("80"))))
    assert curve.score(_D("-5")) == _D("20")  # below → first anchor (winsorized)
    assert curve.score(_D("999")) == _D("80")  # above → last anchor


def test_monotone_downward_curve() -> None:
    # A "lower is better" factor: raw increases, score decreases.
    curve = normalize.MonotoneCurve(anchors=((_D("0"), _D("90")), (_D("1"), _D("10"))))
    assert curve.score(_D("0")) == _D("90")
    assert curve.score(_D("1")) == _D("10")
    assert curve.score(_D("0.5")) == _D("50")


def test_ushaped_peaks_in_ideal_band() -> None:
    curve = normalize.UShapedCurve(
        ideal_low=_D("1.0"), ideal_high=_D("2.5"), zero_at_low=_D("0.2"), zero_at_high=_D("5.0"),
        peak_score=_D("85"), floor_score=_D("25"),
    )
    assert curve.score(_D("1.5")) == _D("85")  # in the ideal band → peak
    assert curve.score(_D("0.3")) < _D("40")   # too fresh (competition) → low
    assert curve.score(_D("4.5")) < _D("40")   # too stale → low


def test_percentile_needs_min_cohort() -> None:
    assert normalize.percentile_score(_D("5"), [_D("1"), _D("2")]) is None  # < 20 → None


def test_percentile_ranks_within_cohort() -> None:
    cohort = [Decimal(i) for i in range(100)]
    high = normalize.percentile_score(_D("90"), cohort, higher_is_better=True)
    low = normalize.percentile_score(_D("10"), cohort, higher_is_better=True)
    assert high is not None and low is not None and high > low


def test_percentile_lower_is_better_inverts() -> None:
    cohort = [Decimal(i) for i in range(100)]
    hib = normalize.percentile_score(_D("90"), cohort, higher_is_better=True)
    lib = normalize.percentile_score(_D("90"), cohort, higher_is_better=False)
    assert hib is not None and lib is not None
    assert hib + lib == Decimal("100")  # symmetric


def test_boolean_score() -> None:
    assert normalize.boolean_score(True, true_score=_D("80"), false_score=_D("20")) == _D("80")
    assert normalize.boolean_score(False, true_score=_D("80"), false_score=_D("20")) == _D("20")
