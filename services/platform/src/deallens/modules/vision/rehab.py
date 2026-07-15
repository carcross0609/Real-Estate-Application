"""Rehab cost model — pure, deterministic, Decimal (03 §27.4). The second half of the §27.1
stance: vision *perceived* what condition each system is in; this code *prices* what it would
cost to bring each system to a target grade. No LLM, no I/O — the money output is deterministic
arithmetic over the aggregated grades + red flags and a versioned unit-cost table, so it is
exact, auditable, and eval-able.

Method (§27.4):
- For each graded system: **cosmetic** cost to lift a grade-3+ system to target
  (`cost_per_grade_step × steps × scale`); a grade-≤2 system is a **major** line (`gut_cost`,
  then cosmetic to target). A system with no usable photo is *not priced* — we can't cost what we
  didn't see (coverage-honest, §27.1), and its absence lowers confidence upstream.
- **Red-flag majors** (foundation/roof/mold …) enter as **inspection-contingent** ranges, clearly
  labeled — "we estimate exposure, we don't diagnose" (§27.4). Severity scales the exposure.
- Every line is a **low/mid/high** band whose width grows as model confidence falls, and the
  totals add the §26.2 contingency (20% when red flags are present, else 15%).

`unit_cost` tables are versioned admin data seeded from published indices and recalibrated from
user actuals via the feedback flywheel (02 §13.5); the defaults here are the v1 seed, never a
hardcode — a market override merges over them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal

from deallens.modules.vision.schemas import (
    CostBasis,
    RedFlag,
    RedFlagSeverity,
    RehabBreakdown,
    RehabCostConfig,
    RehabLineItem,
    UnitCost,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_CENTS = Decimal("0.01")

# Default rehab target grade (§27.4): rent-ready-plus. Flip runs pass 5 (comp-level) explicitly.
DEFAULT_TARGET_GRADE = 4
# Extra band widening as confidence drops (added to each system's own uncertainty, capped).
_CONFIDENCE_WIDENING = Decimal("0.25")
_MAX_UNCERTAINTY = Decimal("0.60")
# Red-flag exposure multiplier by severity (§27.4 — a *possible* flag books less than a *severe*).
_SEVERITY_EXPOSURE: dict[RedFlagSeverity, Decimal] = {
    RedFlagSeverity.POSSIBLE: Decimal("0.4"),
    RedFlagSeverity.LIKELY: Decimal("0.7"),
    RedFlagSeverity.SEVERE: Decimal("1.0"),
}


def default_cost_config() -> RehabCostConfig:
    """The v1 unit-cost seed table (§27.4). Figures are national-prior placeholders — a launch
    market overrides them via `markets.config["rehab_costs"]` before its listings go live."""
    return RehabCostConfig(
        version="v1",
        systems={
            "kitchen": UnitCost(basis=CostBasis.LUMP, cost_per_grade_step=Decimal("6000"),
                                gut_cost=Decimal("25000"), uncertainty=Decimal("0.30")),
            "bath": UnitCost(basis=CostBasis.PER_BATH, cost_per_grade_step=Decimal("3000"),
                             gut_cost=Decimal("9000"), uncertainty=Decimal("0.30")),
            "flooring": UnitCost(basis=CostBasis.PER_SQFT, cost_per_grade_step=Decimal("3.50"),
                                 gut_cost=Decimal("8.00"), uncertainty=Decimal("0.25")),
            "exterior": UnitCost(basis=CostBasis.PER_SQFT, cost_per_grade_step=Decimal("2.00"),
                                 gut_cost=Decimal("6.00"), uncertainty=Decimal("0.30")),
            "roof": UnitCost(basis=CostBasis.LUMP, cost_per_grade_step=Decimal("3000"),
                             gut_cost=Decimal("12000"), uncertainty=Decimal("0.35")),
            "landscaping": UnitCost(basis=CostBasis.LUMP, cost_per_grade_step=Decimal("1500"),
                                    gut_cost=Decimal("5000"), uncertainty=Decimal("0.40")),
        },
        contingency_pct=Decimal("0.15"),
        contingency_pct_flagged=Decimal("0.20"),
        red_flag_costs={
            "foundation_crack": Decimal("15000"),
            "roof_wear": Decimal("12000"),
            "water_stain": Decimal("3500"),
            "mold_suspect": Decimal("6000"),
            "outdated_panel": Decimal("4000"),
            "sagging_line": Decimal("10000"),
            "pest_damage": Decimal("5000"),
            "hazard_other": Decimal("3000"),
        },
    )


def _round(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _scale(basis: CostBasis, *, sqft: int | None, baths: int | None) -> Decimal:
    if basis is CostBasis.PER_SQFT:
        return Decimal(sqft) if sqft and sqft > 0 else _ZERO
    if basis is CostBasis.PER_BATH:
        return Decimal(baths) if baths and baths > 0 else _ONE
    return _ONE  # LUMP


def _band(mid: Decimal, uncertainty: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    u = min(uncertainty, _MAX_UNCERTAINTY)
    return _round(mid * (_ONE - u)), _round(mid), _round(mid * (_ONE + u))


def _effective_uncertainty(base: Decimal, confidence: Decimal | None) -> Decimal:
    conf = _ZERO if confidence is None else confidence
    return base + (_ONE - conf) * _CONFIDENCE_WIDENING


def _system_line(
    system: str, grade: int, unit: UnitCost, *, target: int, sqft: int | None, baths: int | None,
    confidence: Decimal | None,
) -> RehabLineItem | None:
    """Price one graded system to `target` (§27.4). Returns None when the system already meets or
    exceeds target (nothing to spend)."""
    if grade >= target:
        return None
    scale = _scale(unit.basis, sqft=sqft, baths=baths)
    uncertainty = _effective_uncertainty(unit.uncertainty, confidence)

    if grade <= 2:
        # Major: gut to functional (~grade 3) then cosmetic up to target.
        cosmetic_steps = max(0, target - 3)
        mid = unit.gut_cost * scale + unit.cost_per_grade_step * scale * Decimal(cosmetic_steps)
        category = "major"
        note = f"gut (grade {grade}) → {target}"
    else:
        steps = target - grade
        mid = unit.cost_per_grade_step * scale * Decimal(steps)
        category = "cosmetic"
        note = f"refresh grade {grade} → {target}"

    low, mid_r, high = _band(mid, uncertainty)
    if mid_r <= _ZERO:
        return None
    return RehabLineItem(
        system=system, category=category, from_grade=grade, to_grade=target,
        low=low, mid=mid_r, high=high, note=note,
    )


def _red_flag_line(flag: RedFlag, config: RehabCostConfig) -> RehabLineItem | None:
    """Price a red-flag exposure as an inspection-contingent major line (§27.4). Unknown flag
    types (no table entry) are skipped rather than guessed."""
    base = config.red_flag_costs.get(flag.type.value)
    if base is None or base <= _ZERO:
        return None
    exposure = _SEVERITY_EXPOSURE[flag.severity]
    mid = base * exposure
    # Red-flag ranges are deliberately wide (we estimate exposure, we don't diagnose — §27.4).
    low, mid_r, high = _band(mid, Decimal("0.5"))
    return RehabLineItem(
        system=flag.type.value, category="major", low=low, mid=mid_r, high=high,
        inspection_contingent=True,
        note=f"{flag.severity.value} {flag.type.value} — inspection-contingent",
    )


def estimate_rehab(
    grades: Mapping[str, int | None],
    red_flags: Sequence[RedFlag],
    *,
    sqft: int | None,
    baths: int | None,
    confidence: Decimal | None,
    config: RehabCostConfig | None = None,
    target_grade: int = DEFAULT_TARGET_GRADE,
) -> RehabBreakdown:
    """Price a property's rehab to `target_grade` (§27.4). Cosmetic vs. major split, per-system +
    red-flag line items, low/mid/high totals *including* the §26.2 contingency (20% when red flags
    are present). A property with no graded systems and no flags yields a $0 breakdown — an honest
    "nothing to price from these photos," which the financial engine surfaces as turnkey."""
    cfg = config or default_cost_config()
    lines: list[RehabLineItem] = []

    for system, grade in grades.items():
        unit = cfg.systems.get(system)
        if grade is None or unit is None:
            continue
        line = _system_line(
            system, grade, unit, target=target_grade, sqft=sqft, baths=baths, confidence=confidence
        )
        if line is not None:
            lines.append(line)

    for flag in red_flags:
        line = _red_flag_line(flag, cfg)
        if line is not None:
            lines.append(line)

    cosmetic_low = sum((ln.low for ln in lines if ln.category == "cosmetic"), _ZERO)
    cosmetic_high = sum((ln.high for ln in lines if ln.category == "cosmetic"), _ZERO)
    major_low = sum((ln.low for ln in lines if ln.category == "major"), _ZERO)
    major_high = sum((ln.high for ln in lines if ln.category == "major"), _ZERO)
    mid_total = sum((ln.mid for ln in lines), _ZERO)

    red_flags_present = bool(red_flags)
    contingency = cfg.contingency_pct_flagged if red_flags_present else cfg.contingency_pct
    factor = _ONE + contingency
    return RehabBreakdown(
        cosmetic_low=_round(cosmetic_low),
        cosmetic_high=_round(cosmetic_high),
        major_low=_round(major_low),
        major_high=_round(major_high),
        contingency_pct=contingency,
        total_low=_round((cosmetic_low + major_low) * factor),
        total_mid=_round(mid_total * factor),
        total_high=_round((cosmetic_high + major_high) * factor),
        red_flags_present=red_flags_present,
        line_items=lines,
    )


__all__ = [
    "DEFAULT_TARGET_GRADE",
    "default_cost_config",
    "estimate_rehab",
]
