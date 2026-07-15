"""Factor computation — pure (03 §25.3). Turns a `ScoringInputs` bundle into per-factor
`(raw, normalized 0–100, rationale)` triples, one per named factor in the registry. This is the
first stage of the pipeline in §25.2 (`f_i(raw) → n_i`); `score.py` does the weighting and gates.

A factor whose inputs are absent returns `None` — it drops out of its group and its weight
redistributes, so a thin-data property is scored on what's present, not penalized for a gap
(§25.1 #4). The rationale string is the human-readable "why" that lands in the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from deallens.core.enums import Strategy
from deallens.modules.scoring import weights
from deallens.modules.scoring.normalize import boolean_score
from deallens.modules.scoring.schemas import ScoringInputs, StrategyMetrics

_ZERO = Decimal("0")

# Motivated-seller remark flags (§25.3 `motivated_seller_signals`). A lightweight keyword scan;
# the real NLP classifier is a follow-on, but the signal is load-bearing enough to seed now.
_MOTIVATED_KEYWORDS = (
    "as-is", "as is", "estate", "relocation", "must sell", "motivated", "bring offers",
    "tlc", "handyman", "investor special", "cash only", "fixer", "short sale", "foreclosure",
    "probate", "vacant", "distressed", "priced to sell",
)
# FEMA high-risk flood zones (§25.3 `flood_risk_class`, also a Risk input and a §25.6 gate).
_HIGH_RISK_FLOOD_ZONES = frozenset({"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"})


@dataclass(frozen=True, slots=True)
class FactorValue:
    raw: Decimal | None
    normalized: Decimal
    rationale: str


def _safe_ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den == _ZERO:
        return None
    return num / den


def motivated_signal_count(remarks: str | None) -> int:
    if not remarks:
        return 0
    low = remarks.lower()
    return sum(1 for kw in _MOTIVATED_KEYWORDS if kw in low)


def _f_factor(key: str, m: StrategyMetrics, inputs: ScoringInputs) -> Decimal | None:
    """Raw value for a Group-F factor from the strategy's stored metrics (§26.5 → §25.3)."""
    if key == "flip_net_margin":
        return m.flip_margin
    if key == "flip_spread_pct":
        return _safe_ratio(
            (inputs.arv_value - m.all_in) if (inputs.arv_value and m.all_in) else None,
            inputs.arv_value,
        )
    if key == "coc_return":
        return m.coc
    if key == "dscr":
        return m.dscr
    if key == "cap_rate":
        return m.cap_rate
    if key == "breakeven_occupancy":
        return m.breakeven_occupancy
    if key == "brrrr_capital_left":
        return _safe_ratio(m.capital_left_in, m.cash_invested)
    return None


def compute_factor(  # noqa: PLR0911, PLR0912 — a flat dispatch over the factor registry
    key: str, inputs: ScoringInputs, strategy: Strategy
) -> FactorValue | None:
    """Compute one factor's `(raw, normalized, rationale)` for a strategy, or None if unavailable.
    Normalization uses the calibrated curve for the key, with the two documented special cases:
    DOM (U-shaped on dom/median) and renovation difficulty (inverse-scored for flip)."""
    # --- Group F ---
    if key in weights.group_factors(strategy, "F"):
        raw = _f_factor(key, inputs.strategy_metrics.get(strategy.value, StrategyMetrics()), inputs)
        if raw is None:
            return None
        norm = weights.FACTOR_CURVES[key].score(raw)
        return FactorValue(raw, norm, f"{key}={raw}")

    # --- Group D ---
    if key == "price_vs_avm":
        raw = _safe_ratio(inputs.list_price, inputs.as_is_value)
        if raw is None:
            return None
        return FactorValue(raw, weights.FACTOR_CURVES[key].score(raw),
                           f"list ÷ as-is = {raw:.3f} ({'discount' if raw < 1 else 'premium'})")
    if key == "price_cut_signal":
        if inputs.list_price is None:  # a count factor is meaningless without a live listing
            return None
        raw = Decimal(inputs.price_cut_count)
        return FactorValue(raw, weights.FACTOR_CURVES[key].score(raw),
                           f"{inputs.price_cut_count} price cut(s)")
    if key == "dom_curve":
        if inputs.dom is None or not inputs.market_median_dom:
            return None
        ratio = Decimal(inputs.dom) / Decimal(inputs.market_median_dom)
        return FactorValue(ratio, weights.DOM_CURVE.score(ratio),
                           f"DOM {inputs.dom} vs median {inputs.market_median_dom} (×{ratio:.2f})")
    if key == "motivated_seller_signals":
        if inputs.list_price is None:  # no listing → no remarks signal to read
            return None
        count = motivated_signal_count(inputs.remarks)
        raw = Decimal(count)
        return FactorValue(raw, weights.FACTOR_CURVES[key].score(raw),
                           f"{count} motivated-seller remark signal(s)")

    # --- Group C ---
    if key == "condition_arbitrage":
        if inputs.condition_arbitrage is None:
            return None
        raw = inputs.condition_arbitrage
        return FactorValue(raw, weights.FACTOR_CURVES[key].score(raw),
                           f"condition arbitrage {raw:.3f} of ARV")
    if key == "renovation_difficulty":
        if inputs.renovation_difficulty is None:
            return None
        raw = Decimal(inputs.renovation_difficulty)
        curve = (weights.RENO_DIFFICULTY_FLIP if strategy is Strategy.FLIP
                 else weights.RENO_DIFFICULTY_RENTAL)
        basis = "flip opportunity" if strategy is Strategy.FLIP else "turnkey preference"
        return FactorValue(raw, curve.score(raw), f"reno difficulty {raw}/5 ({basis})")
    if key == "red_flag_severity":
        if inputs.red_flag_severity is None:
            return None
        raw = inputs.red_flag_severity
        return FactorValue(raw, weights.FACTOR_CURVES[key].score(raw),
                           f"red-flag severity {raw:.2f}")
    if key == "rehab_to_arv_ratio":
        if inputs.rehab_to_arv_ratio is None:
            return None
        raw = inputs.rehab_to_arv_ratio
        return FactorValue(raw, weights.FACTOR_CURVES[key].score(raw),
                           f"rehab ÷ ARV = {raw:.3f} (execution risk)")

    # --- Group L ---
    if key == "school_percentile":
        return _simple("school_percentile", inputs.school_percentile, "school percentile")
    if key == "crime_trend":
        return _simple("crime_trend", inputs.crime_index, "crime index (lower better)")
    if key == "walkability":
        return _simple("walkability", inputs.walkability, "walkability")
    if key == "flood_risk":
        if inputs.flood_zone is None:
            return None
        high_risk = inputs.flood_zone.upper() in _HIGH_RISK_FLOOD_ZONES
        norm = boolean_score(not high_risk, true_score=Decimal("82"), false_score=Decimal("20"))
        return FactorValue(None, norm,
                           f"flood zone {inputs.flood_zone}"
                           + (" (high risk)" if high_risk else ""))

    # --- Group M ---
    if key == "appreciation_3yr":
        return _simple("appreciation_3yr", inputs.appreciation_pct, "3yr appreciation")
    if key == "rent_growth_3yr":
        return _simple("rent_growth_3yr", inputs.rent_growth_3yr, "3yr rent growth")
    if key == "inventory_months":
        return _simple("inventory_months", inputs.inventory_months, "months of inventory")
    if key == "market_liquidity":
        return _simple("market_liquidity", inputs.market_liquidity, "market liquidity")
    return None


def _simple(key: str, raw: Decimal | None, label: str) -> FactorValue | None:
    if raw is None:
        return None
    return FactorValue(raw, weights.FACTOR_CURVES[key].score(raw), f"{label} {raw}")


__all__ = ["FactorValue", "compute_factor", "motivated_signal_count"]
