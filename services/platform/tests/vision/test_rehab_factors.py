"""Rehab cost model (§27.4) + condition→scoring-factor bridge (§25.3) unit tests (no DB). The
rehab model is the money output, so the tests are concrete: known grades → known cost structure,
cosmetic vs. major split, red-flag inspection-contingent lines, contingency, and confidence-driven
band widening.
"""

from decimal import Decimal

from deallens.modules.vision import factors, rehab
from deallens.modules.vision.schemas import RedFlag, RedFlagSeverity, RedFlagType

_D = Decimal


def _flag(t: RedFlagType, s: RedFlagSeverity) -> RedFlag:
    return RedFlag(type=t, severity=s)


# --- Rehab cost model -------------------------------------------------------------------


def test_grade4_system_costs_nothing_at_target4() -> None:
    bd = rehab.estimate_rehab({"kitchen": 4}, [], sqft=1800, baths=2, confidence=_D("0.8"))
    assert bd.total_mid == _D("0.00")
    assert not bd.line_items


def test_cosmetic_line_for_grade3_kitchen() -> None:
    bd = rehab.estimate_rehab({"kitchen": 3}, [], sqft=1800, baths=2, confidence=_D("0.8"))
    kitchen = next(li for li in bd.line_items if li.system == "kitchen")
    assert kitchen.category == "cosmetic"
    assert kitchen.mid > 0


def test_gut_grade1_kitchen_is_major() -> None:
    bd = rehab.estimate_rehab({"kitchen": 1}, [], sqft=1800, baths=2, confidence=_D("0.8"))
    kitchen = next(li for li in bd.line_items if li.system == "kitchen")
    assert kitchen.category == "major"
    assert kitchen.mid >= 25000  # gut cost floor


def test_flooring_scales_with_sqft() -> None:
    small = rehab.estimate_rehab({"flooring": 2}, [], sqft=1000, baths=2, confidence=_D("0.8"))
    big = rehab.estimate_rehab({"flooring": 2}, [], sqft=3000, baths=2, confidence=_D("0.8"))
    small_floor = next(li for li in small.line_items if li.system == "flooring")
    big_floor = next(li for li in big.line_items if li.system == "flooring")
    assert big_floor.mid > small_floor.mid


def test_red_flag_is_inspection_contingent_major() -> None:
    bd = rehab.estimate_rehab(
        {}, [_flag(RedFlagType.FOUNDATION_CRACK, RedFlagSeverity.SEVERE)],
        sqft=1800, baths=2, confidence=_D("0.8"),
    )
    foundation = next(li for li in bd.line_items if li.system == "foundation_crack")
    assert foundation.category == "major" and foundation.inspection_contingent is True
    assert bd.red_flags_present is True


def test_red_flag_bumps_contingency_to_20pct() -> None:
    no_flag = rehab.estimate_rehab({"kitchen": 3}, [], sqft=1800, baths=2, confidence=_D("0.8"))
    with_flag = rehab.estimate_rehab(
        {"kitchen": 3}, [_flag(RedFlagType.WATER_STAIN, RedFlagSeverity.LIKELY)],
        sqft=1800, baths=2, confidence=_D("0.8"),
    )
    assert no_flag.contingency_pct == _D("0.15")
    assert with_flag.contingency_pct == _D("0.20")


def test_lower_confidence_widens_band() -> None:
    hi = rehab.estimate_rehab({"kitchen": 2}, [], sqft=1800, baths=2, confidence=_D("0.9"))
    lo = rehab.estimate_rehab({"kitchen": 2}, [], sqft=1800, baths=2, confidence=_D("0.1"))
    hi_k = next(li for li in hi.line_items if li.system == "kitchen")
    lo_k = next(li for li in lo.line_items if li.system == "kitchen")
    assert (lo_k.high - lo_k.low) > (hi_k.high - hi_k.low)


def test_unknown_grade_not_priced() -> None:
    bd = rehab.estimate_rehab({"kitchen": None}, [], sqft=1800, baths=2, confidence=_D("0.8"))
    assert bd.total_mid == _D("0.00")


def test_empty_condition_is_zero_rehab() -> None:
    bd = rehab.estimate_rehab({}, [], sqft=1800, baths=2, confidence=None)
    assert bd.total_low == _D("0.00") and bd.total_high == _D("0.00")


# --- Scoring factors --------------------------------------------------------------------


def test_condition_arbitrage_positive_for_discounted_fixer() -> None:
    # ARV 400k, rehab 40k, price 300k → arbitrage = (400-40-300)/400 = 0.15.
    cf = factors.condition_factors(
        rehab_mid=_D("40000"), arv=_D("400000"), list_price=_D("300000"),
        renovation_difficulty=3, red_flags=[], confidence=_D("0.7"),
    )
    assert cf.condition_arbitrage == _D("0.15000")
    assert cf.rehab_to_arv_ratio == _D("0.10000")


def test_factors_none_when_no_arv() -> None:
    cf = factors.condition_factors(
        rehab_mid=_D("40000"), arv=None, list_price=_D("300000"),
        renovation_difficulty=3, red_flags=[], confidence=_D("0.7"),
    )
    assert cf.condition_arbitrage is None and cf.rehab_to_arv_ratio is None


def test_red_flag_severity_score() -> None:
    assert factors.red_flag_severity([]) == _D("0")
    severe = factors.red_flag_severity([_flag(RedFlagType.FOUNDATION_CRACK,
                                              RedFlagSeverity.SEVERE)])
    assert severe == _D("1.0")
