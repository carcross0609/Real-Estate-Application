"""Estimator math — pure, Decimal, property-testable (03 §26.1, §25.8).

Turns a scored comp set into a point estimate with an honest P10/P50/P90 band, a calibrated
confidence, and the two rental-specific corrections (list-to-effective, cross-check blend).
Everything here is deterministic arithmetic over the `ScoredComp` list `comps.py` produced —
no database, no I/O — so the intellectual core of the product is exercised by fixtures.

Design commitments:
- **Show the band, not a false point.** The interval is a *weighted* quantile of the adjusted
  comp values, so a dispersed set widens the band instead of hiding disagreement behind a
  confident-looking midpoint (03 §21.5). Ranking downstream reads the conservative quantile
  (P30 for value), the property page shows P50 + band (03 §25.8).
- **Confidence is separate from the estimate** (03 §25.1 #4). A thin or dispersed set lowers
  confidence; it never silently shifts the number. Confidence blends comp count, band width,
  and the fallback penalty, and takes a hit when an external cross-check disagrees.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from deallens.modules.engine.comps import FallbackStage, ScoredComp

_ZERO = Decimal("0")
_ONE = Decimal("1")
_CENTS = Decimal("0.01")
_CONF_Q = Decimal("0.001")

# Confidence weighting: band tightness matters more than raw count once you have a few comps —
# five tight comps beat twelve scattered ones. Both feed a [0,1] score before the penalties.
_W_COUNT = Decimal("0.4")
_W_DISPERSION = Decimal("0.6")
# A band whose half-width reaches this fraction of the point contributes ~0 to confidence.
_DISPERSION_FULL_PENALTY = Decimal("0.5")

# Confidence multiplier per fallback rung (03 §26.1 — each widening step costs confidence).
_STAGE_PENALTY: dict[FallbackStage, Decimal] = {
    FallbackStage.BASE: Decimal("1.0"),
    FallbackStage.WIDEN_RECENCY: Decimal("0.9"),
    FallbackStage.WIDEN_RADIUS: Decimal("0.8"),
    FallbackStage.MODEL_PRIOR: Decimal("0.4"),
}
# Cross-check divergence past this fraction (03 §26.1: RentCast > 15%) flags + dents confidence.
_CROSS_CHECK_THRESHOLD = Decimal("0.15")
_CROSS_CHECK_PENALTY = Decimal("0.85")
# Model-prior band half-width (03 §26.1 fallback — a $/sqft prior is deliberately wide).
_MODEL_PRIOR_HALF_WIDTH = Decimal("0.25")


@dataclass(frozen=True, slots=True)
class Estimate:
    """A computed value estimate: point + P10/P50/P90 band, confidence (0–1), and the method
    trail stored in `valuations.method` (comp-based | comp-based (widened) | model-prior).
    `comp_count` is the number of *included* comps behind it.
    """

    point: Decimal | None
    p10: Decimal | None
    p50: Decimal | None
    p90: Decimal | None
    confidence: Decimal
    method: str
    comp_count: int


def _clamp01(x: Decimal) -> Decimal:
    if x < _ZERO:
        return _ZERO
    return _ONE if x > _ONE else x


def weighted_mean(pairs: Sequence[tuple[Decimal, Decimal]]) -> Decimal | None:
    """Σ(w·v) / Σw over (value, weight) pairs; None if the weights sum to zero (no signal)."""
    total_w = sum((w for _, w in pairs), _ZERO)
    if total_w <= _ZERO:
        return None
    return sum((w * v for v, w in pairs), _ZERO) / total_w


def weighted_quantile(pairs: Sequence[tuple[Decimal, Decimal]], q: Decimal) -> Decimal | None:
    """The weight-interpolated `q`-quantile (0 ≤ q ≤ 1) of (value, weight) pairs. Sorts by
    value, walks the cumulative-weight axis, and linearly interpolates between the two values
    bracketing `q·Σw` — so a comp with twice the similarity has twice the pull on where the
    quantile lands. Returns None for an empty/zero-weight set.

    This is what makes the band honest: three comps clustered at $300k and one outlier at
    $360k give a P90 near $360k, not a symmetric ±σ that pretends the outlier isn't there.
    """
    pts = sorted(((v, w) for v, w in pairs if w > _ZERO), key=lambda p: p[0])
    total_w = sum((w for _, w in pts), _ZERO)
    if not pts or total_w <= _ZERO:
        return None
    if len(pts) == 1:
        return pts[0][0]
    target = q * total_w
    cum = _ZERO
    prev_v, prev_mid = pts[0][0], _ZERO
    for v, w in pts:
        mid = cum + w / Decimal(2)  # plot each mass at its cumulative midpoint (type 7-ish)
        if target <= mid:
            span = mid - prev_mid
            if span <= _ZERO:
                return v
            frac = (target - prev_mid) / span
            return prev_v + (v - prev_v) * _clamp01(frac)
        prev_v, prev_mid = v, mid
        cum += w
    return pts[-1][0]


def dispersion_ratio(p10: Decimal | None, p90: Decimal | None, point: Decimal | None) -> Decimal:
    """Band half-width as a fraction of the point — the valuation-uncertainty signal fed to
    confidence and (downstream) the Risk score's comp-dispersion input (03 §25.5). 0 when the
    band or point is missing/degenerate (handled as "no dispersion signal", not "certain").
    """
    if point is None or point <= _ZERO or p10 is None or p90 is None:
        return _ZERO
    return (p90 - p10) / (Decimal(2) * point)


def confidence(
    *,
    comp_count: int,
    target_comps: int,
    dispersion: Decimal,
    stage: FallbackStage,
    cross_check_divergence: Decimal | None = None,
) -> Decimal:
    """Calibrated 0–1 confidence for an estimate (03 §25.5, §27.5). Blends how many comps we
    had (vs. the ideal set size) with how tight they agreed, then applies the fallback-stage
    penalty and a cross-check dent. Kept monotone and bounded so it reads as a probability the
    reliability curves (PRD §4.3) can calibrate against — never a vibe.
    """
    count_factor = _clamp01(Decimal(comp_count) / Decimal(max(target_comps, 1)))
    dispersion_factor = _clamp01(_ONE - dispersion / _DISPERSION_FULL_PENALTY)
    base = _W_COUNT * count_factor + _W_DISPERSION * dispersion_factor
    conf = base * _STAGE_PENALTY.get(stage, _ONE)
    if cross_check_divergence is not None and cross_check_divergence > _CROSS_CHECK_THRESHOLD:
        conf *= _CROSS_CHECK_PENALTY
    return _clamp01(conf).quantize(_CONF_Q)


def estimate_from_comps(
    comps: Sequence[ScoredComp],
    *,
    target_comps: int,
    stage: FallbackStage,
    cross_check_divergence: Decimal | None = None,
) -> Estimate:
    """Aggregate a scored, *included* comp set into a value estimate (03 §26.1). The point is
    the similarity-weighted mean; the band is the similarity-weighted P10/P50/P90 of the same
    adjusted values; confidence follows from count + dispersion + stage. An empty set yields a
    zero-confidence empty estimate (the caller then drops to the model prior).
    """
    pairs = [(c.adjusted_value, c.similarity) for c in comps if c.similarity > _ZERO]
    if not pairs:
        return Estimate(None, None, None, None, _ZERO, "no-comps", 0)

    point = weighted_mean(pairs)
    p10 = weighted_quantile(pairs, Decimal("0.10"))
    p50 = weighted_quantile(pairs, Decimal("0.50"))
    p90 = weighted_quantile(pairs, Decimal("0.90"))
    disp = dispersion_ratio(p10, p90, point)
    conf = confidence(
        comp_count=len(pairs),
        target_comps=target_comps,
        dispersion=disp,
        stage=stage,
        cross_check_divergence=cross_check_divergence,
    )
    method = "comp-based" if stage == FallbackStage.BASE else "comp-based (widened)"
    return Estimate(
        point=_round(point),
        p10=_round(p10),
        p50=_round(p50),
        p90=_round(p90),
        confidence=conf,
        method=method,
        comp_count=len(pairs),
    )


def model_prior_estimate(subject_sqft: int | None, market_ppsf: Decimal | None) -> Estimate:
    """The last-resort fallback when no comps survive (03 §26.1): the market median $/sqft ×
    subject size, wrapped in a deliberately wide band and stamped low confidence. Returns an
    empty, zero-confidence estimate when even the prior is unavailable (no size, no market
    $/sqft) — an honest "we can't value this," never a fabricated number.
    """
    if not subject_sqft or subject_sqft <= 0 or market_ppsf is None or market_ppsf <= _ZERO:
        return Estimate(None, None, None, None, _ZERO, "unavailable", 0)
    point = market_ppsf * Decimal(subject_sqft)
    low = point * (_ONE - _MODEL_PRIOR_HALF_WIDTH)
    high = point * (_ONE + _MODEL_PRIOR_HALF_WIDTH)
    conf = confidence(
        comp_count=0, target_comps=1, dispersion=_MODEL_PRIOR_HALF_WIDTH,
        stage=FallbackStage.MODEL_PRIOR,
    )
    return Estimate(_round(point), _round(low), _round(point), _round(high), conf,
                    "model-prior", 0)


def apply_list_to_effective(asking_rent: Decimal, adjustment_pct: Decimal) -> Decimal:
    """Convert an asking (listed) rent to an effective rent (03 §26.1). Listed rents are
    aspirational; the market-calibrated adjustment (initially −2 to −4%) discounts to what
    actually gets signed. `adjustment_pct` is signed (−0.03 = −3%); the result floors at 0.
    """
    effective = asking_rent * (_ONE + adjustment_pct)
    return effective if effective > _ZERO else _ZERO


def cross_check_divergence(point: Decimal | None, external: Decimal | None) -> Decimal | None:
    """Fractional gap between our comp point and an external estimate (03 §26.1 RentCast
    cross-check): |ours − theirs| / theirs. None when either side is missing — no cross-check
    signal, as opposed to zero divergence (perfect agreement).
    """
    if point is None or external is None or external <= _ZERO:
        return None
    return abs(point - external) / external


def _round(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(_CENTS)


__all__ = [
    "Estimate",
    "apply_list_to_effective",
    "confidence",
    "cross_check_divergence",
    "dispersion_ratio",
    "estimate_from_comps",
    "model_prior_estimate",
    "weighted_mean",
    "weighted_quantile",
]
