"""The scoring pipeline — pure, deterministic (03 §25.2). Assembles the normalized factors into
per-strategy scores, applies the hard gates (§25.6), takes the max across enabled strategies as
the overall (§25.4), and derives grade, risk, confidence, recommendation, category scores, and the
explanation ledger — all from the same factor computations, so nothing can drift from the number
(§25.1 #3).

    raw factors → normalize (factors.py) → within-group weight → group weight → strategy score
                → hard gates → max over strategies → grade / risk / confidence / recommendation

Two framework commitments are load-bearing here:
- **Overall = max, not average (§25.4):** a property brilliant for exactly one strategy *is* a
  great deal; the winning strategy is named, never blurred into a mean.
- **Confidence is separate from score (§25.1 #4):** thin data lowers `confidence_score` and can
  block a Strong-Buy, but never silently drags the score itself down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from deallens.core.enums import Strategy
from deallens.modules.scoring import weights
from deallens.modules.scoring.factors import compute_factor
from deallens.modules.scoring.models import Recommendation
from deallens.modules.scoring.schemas import (
    CategoryScores,
    FactorLedger,
    ScoreExplanation,
    ScoreResult,
    ScoringInputs,
    StrategyScore,
)
from deallens.modules.scoring.weights import (
    GATE_DSCR_SUB_1,
    GATE_FLOOD_UNINSURABLE,
    GATE_FOUNDATION,
    GATE_WHOLESALE_SPREAD,
    Gate,
)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_TWO = Decimal("2")
_Q = Decimal("0.01")
_HIGH_RISK_FLOOD = frozenset({"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"})


def _r(v: Decimal) -> Decimal:
    return v.quantize(_Q, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class _StrategyComputation:
    strategy: Strategy
    raw_score: Decimal
    score: Decimal
    capped_by: str | None
    ledger: list[FactorLedger]
    group_scores: dict[str, Decimal] = field(default_factory=dict)


# --- Per-strategy score -----------------------------------------------------------------


def _score_strategy(inputs: ScoringInputs, strategy: Strategy) -> _StrategyComputation | None:
    """Weighted sum over the strategy's available factors (§25.2/§25.4). Group weights renormalize
    over present groups and within-group weights over present factors, so a missing factor
    redistributes rather than zeroing its group. Returns None if no factor at all could be
    computed (nothing to score)."""
    group_weights = weights.STRATEGY_GROUP_WEIGHTS[strategy]
    group_scores: dict[str, Decimal] = {}
    per_group: dict[str, list[tuple[str, Decimal, Decimal | None, str, Decimal]]] = {}

    for group in weights.GROUPS:
        present: list[tuple[str, Decimal, Decimal | None, str, Decimal]] = []
        for key in weights.group_factors(strategy, group):
            fv = compute_factor(key, inputs, strategy)
            if fv is None:
                continue
            present.append(
                (key, fv.normalized, fv.raw, fv.rationale, weights.WITHIN_GROUP_WEIGHT[key])
            )
        if present:
            within_total = sum((w for *_, w in present), _ZERO)
            group_scores[group] = _r(
                sum((norm * w / within_total for _, norm, _, _, w in present), _ZERO)
            )
            per_group[group] = present

    if not group_scores:
        return None

    total_group_w = sum((group_weights[g] for g in group_scores), _ZERO)
    ledger: list[FactorLedger] = []
    raw_score = _ZERO
    for group, present in per_group.items():
        group_w_eff = group_weights[group] / total_group_w
        within_total = sum((w for *_, w in present), _ZERO)
        for key, norm, raw, rationale, w in present:
            final_w = group_w_eff * (w / within_total)
            contribution = final_w * norm
            raw_score += contribution
            ledger.append(FactorLedger(
                factor_key=key, group=group, raw_value=raw, normalized=_r(norm),
                weight=_r(final_w * _HUNDRED) / _HUNDRED, contribution=_r(contribution),
                rationale=rationale,
            ))

    raw_score = _r(raw_score)
    capped, capped_by = _apply_gates(inputs, strategy, raw_score)
    if capped_by is not None:
        for entry in ledger:
            entry.capped_by = capped_by
    return _StrategyComputation(strategy, raw_score, capped, capped_by, ledger, group_scores)


def _applicable_gates(inputs: ScoringInputs, strategy: Strategy) -> list[Gate]:
    """The hard gates that bind for this property × strategy (§25.6)."""
    gates: list[Gate] = []
    if inputs.red_flag_severity is not None and inputs.red_flag_severity >= Decimal("0.9"):
        gates.append(GATE_FOUNDATION)
    if inputs.flood_zone and inputs.flood_zone.upper() in _HIGH_RISK_FLOOD and (
        not inputs.insurance_quotable
    ):
        gates.append(GATE_FLOOD_UNINSURABLE)
    metrics = inputs.strategy_metrics.get(strategy.value)
    if strategy in {Strategy.LTR, Strategy.BRRRR} and metrics and metrics.dscr is not None and (
        metrics.dscr < Decimal("1.0")
    ):
        gates.append(GATE_DSCR_SUB_1)
    if strategy is Strategy.WHOLESALE and metrics and metrics.all_in is not None and (
        inputs.arv_value
    ):
        spread = (inputs.arv_value - metrics.all_in) / inputs.arv_value
        if spread < Decimal("0.08"):
            gates.append(GATE_WHOLESALE_SPREAD)
    return gates


def _apply_gates(
    inputs: ScoringInputs, strategy: Strategy, raw_score: Decimal
) -> tuple[Decimal, str | None]:
    """Cap the raw score by the strictest applicable gate (§25.6). Returns (capped_score, gate_key
    or None). The cap is the binding constraint shown in the ledger — never hidden in weights."""
    binding: Gate | None = None
    for gate in _applicable_gates(inputs, strategy):
        if raw_score > gate.cap and (binding is None or gate.cap < binding.cap):
            binding = gate
    if binding is None:
        return raw_score, None
    return binding.cap, binding.key


# --- Risk (§25.5) -----------------------------------------------------------------------


def _risk_score(inputs: ScoringInputs, computations: list[_StrategyComputation]) -> Decimal:
    """0 (safe) – 100 (risky), a weighted composite of the §25.5 risk inputs over whatever
    signals are present (structural flags, rehab/ARV, flood, comp dispersion, liquidity, leverage,
    single-exit, regulatory). Missing components drop and reweight."""
    parts: list[tuple[Decimal, Decimal]] = []  # (weight, 0-100 risk)

    if inputs.red_flag_severity is not None:
        parts.append((Decimal("0.20"), inputs.red_flag_severity * _HUNDRED))
    if inputs.rehab_to_arv_ratio is not None:
        parts.append((Decimal("0.15"), _clamp(inputs.rehab_to_arv_ratio * Decimal("200"))))
    if inputs.flood_zone:
        high = inputs.flood_zone.upper() in _HIGH_RISK_FLOOD
        parts.append((Decimal("0.10"),
                      _HUNDRED if (high and not inputs.insurance_quotable)
                      else Decimal("70") if high else Decimal("10")))
    disp = _comp_dispersion(inputs)
    if disp is not None:
        parts.append((Decimal("0.15"), _clamp(disp * Decimal("300"))))
    if inputs.market_liquidity is not None:
        parts.append((Decimal("0.15"), _HUNDRED - inputs.market_liquidity))
    dscr = _best_dscr(inputs)
    if dscr is not None:
        # DSCR at +150bps ≈ raw DSCR haircut; below 1.2 stressed → risk rises.
        parts.append((Decimal("0.10"), _clamp((Decimal("1.5") - dscr) * Decimal("80"))))
    if _flip_only(computations):
        parts.append((Decimal("0.10"), Decimal("70")))  # single-exit dependence
    if inputs.str_regulation_flag:
        parts.append((Decimal("0.05"), Decimal("80")))

    if not parts:
        return Decimal("50")  # no signal → neutral, not "safe"
    total_w = sum((w for w, _ in parts), _ZERO)
    return _r(sum((w * r for w, r in parts), _ZERO) / total_w)


def _comp_dispersion(inputs: ScoringInputs) -> Decimal | None:
    if inputs.arv_value and inputs.arv_p10 is not None and inputs.arv_p90 is not None and (
        inputs.arv_value > _ZERO
    ):
        return (inputs.arv_p90 - inputs.arv_p10) / (_TWO * inputs.arv_value)
    return None


def _best_dscr(inputs: ScoringInputs) -> Decimal | None:
    dscrs = [m.dscr for m in inputs.strategy_metrics.values() if m.dscr is not None]
    return max(dscrs) if dscrs else None


def _flip_only(computations: list[_StrategyComputation]) -> bool:
    strategies = {c.strategy for c in computations}
    return strategies == {Strategy.FLIP} or (
        Strategy.FLIP in strategies and not (strategies & {Strategy.LTR, Strategy.BRRRR})
    )


# --- Confidence (§25.5) -----------------------------------------------------------------


def _confidence_score(inputs: ScoringInputs, computations: list[_StrategyComputation]) -> Decimal:
    """0–100 data-and-estimator confidence (§25.5), calibrated to feed the reliability curves
    (PRD §4.3). Blends data completeness (comps, photo coverage, records) with estimator
    self-confidence (ARV/rent/appreciation/condition) and factor coverage."""
    parts: list[tuple[Decimal, Decimal]] = []

    comp_factor = _clamp(Decimal(inputs.comp_count) / Decimal(8) * _HUNDRED)
    parts.append((Decimal("0.25"), comp_factor))
    if inputs.photo_coverage_ratio is not None:
        parts.append((Decimal("0.15"), inputs.photo_coverage_ratio * _HUNDRED))
    parts.append((Decimal("0.05"), _HUNDRED if inputs.records_matched else Decimal("30")))
    for w, conf in (
        (Decimal("0.20"), inputs.arv_confidence),
        (Decimal("0.10"), inputs.rent_confidence),
        (Decimal("0.05"), inputs.appreciation_confidence),
        (Decimal("0.10"), inputs.condition_confidence),
    ):
        if conf is not None:
            parts.append((w, conf * _HUNDRED))
    # Factor coverage: how much of the winning strategy's factor set was actually available.
    if computations:
        best = max(computations, key=lambda c: c.score)
        possible = sum(len(weights.group_factors(best.strategy, g)) for g in weights.GROUPS)
        coverage = Decimal(len(best.ledger)) / Decimal(possible) if possible else _ZERO
        parts.append((Decimal("0.10"), coverage * _HUNDRED))

    total_w = sum((w for w, _ in parts), _ZERO)
    if total_w == _ZERO:
        return _ZERO
    return _r(sum((w * v for w, v in parts), _ZERO) / total_w)


# --- Recommendation (§25.5) -------------------------------------------------------------


def recommendation_for(score: Decimal, risk: Decimal, confidence: Decimal) -> Recommendation:
    """The §25.5 rule layer over (score, risk, confidence). Public so the service can compute a
    per-strategy recommendation for each stored `scores` row, not just the overall."""
    if score >= Decimal("85") and risk <= Decimal("40") and confidence >= Decimal("70"):
        return Recommendation.STRONG_BUY
    if score >= Decimal("70") and risk <= Decimal("60"):
        return Recommendation.BUY
    if score >= Decimal("55") or (score >= Decimal("70") and confidence < Decimal("50")):
        return Recommendation.HOLD_WATCH
    return Recommendation.PASS


# --- Categories & explanation -----------------------------------------------------------


def _category_scores(
    inputs: ScoringInputs, winner: _StrategyComputation, risk: Decimal,
) -> CategoryScores:
    """The ten user-facing category scores (§25 decomposition), derived from the winning
    strategy's group scores plus a few direct factor reads so they reconcile with the overall."""
    gs = winner.group_scores
    reno = (Decimal(inputs.renovation_difficulty - 1) / Decimal(4) * _HUNDRED
            if inputs.renovation_difficulty is not None else None)
    fin_diff = None
    dscr = _best_dscr(inputs)
    if dscr is not None:
        fin_diff = _clamp((Decimal("1.5") - dscr) * Decimal("80"))
    return CategoryScores(
        profitability=gs.get("F"),
        risk=risk,
        location=gs.get("L"),
        condition=gs.get("C"),
        appreciation=_direct(inputs.appreciation_pct, "appreciation_3yr"),
        rental_strength=_rental_strength(inputs),
        liquidity=_direct(inputs.market_liquidity, "market_liquidity"),
        renovation_complexity=_r(reno) if reno is not None else None,
        financing_difficulty=_r(fin_diff) if fin_diff is not None else None,
        market_conditions=gs.get("M"),
    )


def _direct(raw: Decimal | None, key: str) -> Decimal | None:
    if raw is None:
        return None
    return _r(weights.FACTOR_CURVES[key].score(raw))


def _rental_strength(inputs: ScoringInputs) -> Decimal | None:
    """Rental strength from the best rental strategy's CoC/DSCR/cap factors (§25.3)."""
    best: Decimal | None = None
    for strat in (Strategy.LTR, Strategy.BRRRR):
        m = inputs.strategy_metrics.get(strat.value)
        if m is None:
            continue
        vals = [
            weights.FACTOR_CURVES["coc_return"].score(m.coc) if m.coc is not None else None,
            weights.FACTOR_CURVES["dscr"].score(m.dscr) if m.dscr is not None else None,
            weights.FACTOR_CURVES["cap_rate"].score(m.cap_rate) if m.cap_rate is not None else None,
        ]
        present = [v for v in vals if v is not None]
        if present:
            avg = sum(present, _ZERO) / Decimal(len(present))
            best = avg if best is None else max(best, avg)
    return _r(best) if best is not None else None


def _explanation(winner: _StrategyComputation) -> ScoreExplanation:
    """Top-3 positive and negative ledger factors as the stated rationale (§25.5)."""
    ranked = sorted(winner.ledger, key=lambda e: e.contribution, reverse=True)
    positives = [f"{e.rationale}" for e in ranked[:3] if e.normalized >= Decimal("55")]
    negatives = [f"{e.rationale}" for e in reversed(ranked)
                 if e.normalized < Decimal("45")][:3]
    caps = [winner.capped_by] if winner.capped_by else []
    return ScoreExplanation(positives=positives, negatives=negatives, caps=caps)


def _clamp(v: Decimal) -> Decimal:
    return max(_ZERO, min(_HUNDRED, v))


# --- Public entry -----------------------------------------------------------------------


def score_property(inputs: ScoringInputs) -> ScoreResult:
    """Score one property across its enabled strategies (§25). Returns the full result: overall +
    winning strategy, per-strategy and per-category scores, risk/confidence, recommendation, and
    the explanation. A property with no scorable strategy yields a floor result (score 0, Pass) —
    honest, never a crash."""
    enabled = [s for s in inputs.enabled_strategies if s in weights.scorable_strategies()]
    if not enabled:
        enabled = list(weights.scorable_strategies())

    computations = [c for c in (_score_strategy(inputs, s) for s in enabled) if c is not None]
    strategy_scores = [
        StrategyScore(strategy=c.strategy, score=c.score, raw_score=c.raw_score,
                      capped_by=c.capped_by, factors=c.ledger)
        for c in computations
    ]

    risk = _risk_score(inputs, computations)
    confidence = _confidence_score(inputs, computations)

    if not computations:
        return ScoreResult(
            property_id=inputs.property_id, scoring_version=weights.SCORING_VERSION,
            overall_score=_ZERO, grade="F", winning_strategy=None, risk_score=risk,
            confidence_score=confidence, recommendation=Recommendation.PASS,
            category_scores=CategoryScores(risk=risk), strategy_scores=[],
        )

    winner = max(computations, key=lambda c: c.score)
    overall = winner.score
    return ScoreResult(
        property_id=inputs.property_id,
        scoring_version=weights.SCORING_VERSION,
        overall_score=overall,
        grade=weights.grade_for(overall),
        winning_strategy=winner.strategy,
        risk_score=risk,
        confidence_score=confidence,
        recommendation=recommendation_for(overall, risk, confidence),
        category_scores=_category_scores(inputs, winner, risk),
        strategy_scores=strategy_scores,
        explanation=_explanation(winner),
    )


__all__ = ["score_property"]
