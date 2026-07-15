"""Scoring configuration — the factor registry, calibrated normalization curves, strategy weight
profiles (§25.4), and hard gates (§25.6). Every constant here is a **versioned config default**,
not a hardcode (03 preamble): weight changes ship like code, eval-gated and changelogged (§25.1
#5). Kept as data (dicts/curves) so the pure scorer in `score.py` is a small, testable engine
over this table, and a future admin-tunable weight set (FR-034, bounded ±50%/group) merges over
it without touching the algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from deallens.core.enums import Strategy
from deallens.modules.scoring.normalize import MonotoneCurve, UShapedCurve

SCORING_VERSION = "v1"

_D = Decimal


def _mono(*anchors: tuple[str, str]) -> MonotoneCurve:
    return MonotoneCurve(anchors=tuple((_D(r), _D(s)) for r, s in anchors))


# --- Factor groups (§25.3) --------------------------------------------------------------

GROUPS = ("F", "D", "C", "L", "M")

# Group F is strategy-specific (flip cares about margin/spread; LTR about CoC/DSCR); D/C/L/M are
# shared. `_F_FACTORS_BY_STRATEGY` selects the F factors per strategy (§25.3/§25.4).
_F_FACTORS_BY_STRATEGY: dict[Strategy, tuple[str, ...]] = {
    Strategy.FLIP: ("flip_net_margin", "flip_spread_pct"),
    Strategy.LTR: ("coc_return", "dscr", "cap_rate", "breakeven_occupancy"),
    Strategy.BRRRR: ("coc_return", "dscr", "brrrr_capital_left", "cap_rate"),
    Strategy.WHOLESALE: ("flip_spread_pct",),
}

_SHARED_GROUP_FACTORS: dict[str, tuple[str, ...]] = {
    "D": ("price_vs_avm", "price_cut_signal", "dom_curve", "motivated_seller_signals"),
    "C": ("condition_arbitrage", "renovation_difficulty", "red_flag_severity",
          "rehab_to_arv_ratio"),
    "L": ("school_percentile", "crime_trend", "walkability", "flood_risk"),
    "M": ("appreciation_3yr", "rent_growth_3yr", "inventory_months", "market_liquidity"),
}


def group_factors(strategy: Strategy, group: str) -> tuple[str, ...]:
    if group == "F":
        return _F_FACTORS_BY_STRATEGY.get(strategy, ())
    return _SHARED_GROUP_FACTORS.get(group, ())


# --- Within-group relative factor weights (renormalized over available factors) ---------

WITHIN_GROUP_WEIGHT: dict[str, Decimal] = {
    # F
    "flip_net_margin": _D("0.6"), "flip_spread_pct": _D("0.4"),
    "coc_return": _D("0.4"), "dscr": _D("0.3"), "cap_rate": _D("0.2"),
    "breakeven_occupancy": _D("0.1"), "brrrr_capital_left": _D("0.35"),
    # D
    "price_vs_avm": _D("0.4"), "price_cut_signal": _D("0.25"), "dom_curve": _D("0.2"),
    "motivated_seller_signals": _D("0.15"),
    # C
    "condition_arbitrage": _D("0.4"), "renovation_difficulty": _D("0.2"),
    "red_flag_severity": _D("0.25"), "rehab_to_arv_ratio": _D("0.15"),
    # L
    "school_percentile": _D("0.35"), "crime_trend": _D("0.3"), "walkability": _D("0.2"),
    "flood_risk": _D("0.15"),
    # M
    "appreciation_3yr": _D("0.4"), "rent_growth_3yr": _D("0.25"),
    "inventory_months": _D("0.2"), "market_liquidity": _D("0.15"),
}


# --- Calibrated normalization curves (national-prior v1, §25.2) --------------------------

FACTOR_CURVES: dict[str, MonotoneCurve] = {
    # Group F
    "flip_net_margin": _mono(("0", "8"), ("0.10", "40"), ("0.20", "65"), ("0.35", "85"),
                             ("0.55", "97")),
    "flip_spread_pct": _mono(("0", "8"), ("0.10", "45"), ("0.20", "75"), ("0.30", "92")),
    "coc_return": _mono(("-0.05", "8"), ("0", "28"), ("0.05", "55"), ("0.08", "72"),
                        ("0.12", "86"), ("0.20", "98")),
    "dscr": _mono(("0.8", "8"), ("1.0", "35"), ("1.2", "60"), ("1.5", "82"), ("2.0", "96")),
    "cap_rate": _mono(("0.03", "18"), ("0.05", "45"), ("0.06", "60"), ("0.08", "80"),
                      ("0.10", "92")),
    # breakeven_occupancy: lower is better (downward curve)
    "breakeven_occupancy": _mono(("0.55", "92"), ("0.70", "72"), ("0.85", "45"), ("0.95", "20"),
                                 ("1.0", "8")),
    # brrrr_capital_left ratio (capital left / initial cash): lower better
    "brrrr_capital_left": _mono(("0", "97"), ("0.25", "72"), ("0.5", "48"), ("1.0", "18")),
    # Group D
    "price_vs_avm": _mono(("0.85", "95"), ("0.95", "76"), ("1.0", "55"), ("1.05", "35"),
                          ("1.15", "12")),
    "price_cut_signal": _mono(("0", "45"), ("1", "62"), ("2", "75"), ("3", "85"), ("5", "93")),
    "motivated_seller_signals": _mono(("0", "45"), ("1", "65"), ("2", "80"), ("3", "90")),
    # Group C
    "condition_arbitrage": _mono(("-0.10", "8"), ("0", "35"), ("0.10", "60"), ("0.20", "80"),
                                 ("0.35", "95")),
    "red_flag_severity": _mono(("0", "90"), ("0.33", "65"), ("0.66", "35"), ("1.0", "8")),
    "rehab_to_arv_ratio": _mono(("0", "90"), ("0.10", "75"), ("0.20", "55"), ("0.35", "30"),
                                ("0.50", "12")),
    # Group L
    "school_percentile": _mono(("0", "10"), ("50", "50"), ("100", "95")),
    "crime_trend": _mono(("0", "95"), ("25", "75"), ("50", "50"), ("75", "25"), ("100", "8")),
    "walkability": _mono(("0", "30"), ("50", "60"), ("100", "90")),
    # Group M
    "appreciation_3yr": _mono(("-0.02", "10"), ("0", "30"), ("0.03", "55"), ("0.05", "72"),
                              ("0.08", "88"), ("0.12", "97")),
    "rent_growth_3yr": _mono(("0", "30"), ("0.02", "50"), ("0.04", "70"), ("0.06", "86")),
    "inventory_months": _mono(("2", "85"), ("4", "65"), ("6", "50"), ("9", "30"), ("12", "15")),
    "market_liquidity": _mono(("0", "15"), ("50", "55"), ("100", "92")),
}

# DOM is U-shaped on the ratio dom/median (§25.2): a little time on market = negotiating leverage;
# very fresh = competition, very stale = a problem. Handled specially by the scorer.
DOM_CURVE = UShapedCurve(
    ideal_low=_D("1.0"), ideal_high=_D("2.5"), zero_at_low=_D("0.2"), zero_at_high=_D("5.0"),
    peak_score=_D("85"), floor_score=_D("25"),
)

# renovation_difficulty is inverse-scored for flip (a fixer is the opportunity) vs. LTR/BRRRR
# (turnkey preferred) — §25.3. Two curves on the 1–5 grade, selected by strategy in the scorer.
RENO_DIFFICULTY_FLIP = _mono(("1", "45"), ("2", "70"), ("3", "85"), ("4", "72"), ("5", "45"))
RENO_DIFFICULTY_RENTAL = _mono(("1", "92"), ("2", "78"), ("3", "58"), ("4", "35"), ("5", "15"))


# --- Strategy group-weight profiles (§25.4) ---------------------------------------------

STRATEGY_GROUP_WEIGHTS: dict[Strategy, dict[str, Decimal]] = {
    Strategy.FLIP: {"F": _D("0.40"), "D": _D("0.20"), "C": _D("0.25"), "L": _D("0.10"),
                    "M": _D("0.05")},
    Strategy.LTR: {"F": _D("0.35"), "D": _D("0.10"), "C": _D("0.15"), "L": _D("0.25"),
                   "M": _D("0.15")},
    Strategy.BRRRR: {"F": _D("0.40"), "D": _D("0.15"), "C": _D("0.20"), "L": _D("0.15"),
                     "M": _D("0.10")},
    Strategy.WHOLESALE: {"F": _D("0.30"), "D": _D("0.35"), "C": _D("0.25"), "L": _D("0.05"),
                         "M": _D("0.05")},
}


def scorable_strategies() -> tuple[Strategy, ...]:
    """Strategies with a weight profile *and* an underwriting model (§25.4/§26). The [F]
    strategies (STR/house-hack/multifamily/…) get profiles when their engines land."""
    return tuple(STRATEGY_GROUP_WEIGHTS.keys())


# --- Hard gates (§25.6) -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gate:
    """A hard cap that binds the score regardless of the weighted arithmetic (§25.6). Appears in
    the ledger as an explicit `capped_by` entry — trust requires showing the cap, not hiding it in
    weights."""

    key: str
    cap: Decimal
    applies_to: frozenset[Strategy] | None  # None = all strategies


GATE_FOUNDATION = Gate("severe_foundation_flag", _D("60"), None)
GATE_FLOOD_UNINSURABLE = Gate("flood_ae_no_insurance", _D("70"), None)
GATE_DSCR_SUB_1 = Gate("dscr_below_1", _D("65"), frozenset({Strategy.LTR, Strategy.BRRRR}))
GATE_WHOLESALE_SPREAD = Gate("wholesale_spread_floor", _D("50"), frozenset({Strategy.WHOLESALE}))

# Grade thresholds on the calibrated 0–100 score (§25.5). The score is calibrated so 70+ ≈ "worth
# a serious look"; grades communicate relative rank within that calibration.
GRADE_THRESHOLDS: tuple[tuple[Decimal, str], ...] = (
    (_D("95"), "A+"), (_D("90"), "A"), (_D("85"), "A-"), (_D("75"), "B+"), (_D("65"), "B"),
    (_D("55"), "B-"), (_D("45"), "C+"), (_D("35"), "C"), (_D("25"), "C-"), (_D("10"), "D"),
)


def grade_for(score: Decimal) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


__all__ = [
    "DOM_CURVE",
    "FACTOR_CURVES",
    "GATE_DSCR_SUB_1",
    "GATE_FLOOD_UNINSURABLE",
    "GATE_FOUNDATION",
    "GATE_WHOLESALE_SPREAD",
    "GROUPS",
    "RENO_DIFFICULTY_FLIP",
    "RENO_DIFFICULTY_RENTAL",
    "SCORING_VERSION",
    "STRATEGY_GROUP_WEIGHTS",
    "WITHIN_GROUP_WEIGHT",
    "Gate",
    "grade_for",
    "group_factors",
    "scorable_strategies",
]
