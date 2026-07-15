"""Condition → scoring-factor bridge — pure (03 §25.3 Group C, §27 → §25 integration).

Vision perceives and prices; scoring ranks. This module is the seam between them: it turns the
condition aggregate + rehab breakdown + the comps engine's ARV into the four Group-C factors the
investment score consumes (§25.3), so a perceived grade-2 kitchen becomes a real input to "how
good a deal is this." Kept pure and separate from scoring (not yet built) so the contract is
stable and testable, and so vision never imports scoring (module-graph acyclicity, §9.3).

`condition_arbitrage` is the one §25.3 singles out as "the flip goldmine factor": the equity a
buyer unlocks by curing condition — `(ARV − rehab − price) / ARV`. A deep-discount fixer with a
tight rehab scores high; a turnkey at full price scores ~0.
"""

from __future__ import annotations

from decimal import Decimal

from deallens.modules.vision.schemas import ConditionFactors, RedFlag, RedFlagSeverity

_ZERO = Decimal("0")
_Q = Decimal("0.00001")

_SEVERITY_SCORE: dict[RedFlagSeverity, Decimal] = {
    RedFlagSeverity.POSSIBLE: Decimal("0.33"),
    RedFlagSeverity.LIKELY: Decimal("0.66"),
    RedFlagSeverity.SEVERE: Decimal("1.0"),
}


def red_flag_severity(red_flags: list[RedFlag]) -> Decimal:
    """0 (no flags) … 1 (a severe structural flag). Max-pooled — one severe finding dominates,
    consistent with the aggregation stance (§27.3). Also feeds the Risk score (§25.5)."""
    if not red_flags:
        return _ZERO
    return max(_SEVERITY_SCORE[f.severity] for f in red_flags)


def condition_factors(
    *,
    rehab_mid: Decimal | None,
    arv: Decimal | None,
    list_price: Decimal | None,
    renovation_difficulty: int | None,
    red_flags: list[RedFlag],
    confidence: Decimal | None,
) -> ConditionFactors:
    """Compute the Group-C factors (§25.3). `condition_arbitrage` and `rehab_to_arv_ratio` need an
    ARV to normalize against; when it's missing they're None (an honest gap the score treats as a
    thin-data factor, not a zero). Everything is a fraction/ratio so scoring can percentile-
    normalize it within the market window (§25.2)."""
    arbitrage: Decimal | None = None
    rehab_ratio: Decimal | None = None
    if arv is not None and arv > _ZERO:
        rehab = rehab_mid or _ZERO
        if list_price is not None:
            arbitrage = ((arv - rehab - list_price) / arv).quantize(_Q)
        rehab_ratio = (rehab / arv).quantize(_Q)

    return ConditionFactors(
        condition_arbitrage=arbitrage,
        renovation_difficulty=renovation_difficulty,
        red_flag_severity=red_flag_severity(red_flags),
        rehab_to_arv_ratio=rehab_ratio,
        confidence=confidence,
    )


__all__ = ["condition_factors", "red_flag_severity"]
