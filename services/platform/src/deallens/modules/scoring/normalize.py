"""Factor normalization — pure, no I/O (03 §25.2).

Raw factor values live on wildly different scales (a cap rate is 0.06, a DOM is 45, a rehab/ARV
ratio is 0.2). Normalization maps each to a common **0–100** axis so a strategy score is a
meaningful weighted sum (§25.2). Three shapes, matching the framework:

- **Monotone** (more is better, or less is better): a calibrated piecewise-linear curve through
  anchor points — the "national-prior curve" a cold-start market uses until it has ≥300 local
  observations to fit an empirical percentile (§25.2). Anchors are config, not hardcodes.
- **U-shaped** (DOM: very low = bidding war, very high = stale): a piecewise curve that peaks in
  an ideal band and falls off both sides.
- **Empirical percentile**: when a market×class×window cohort sample is supplied, the value's
  winsorized percentile within it — the production path once a market is calibrated (§25.6 drift
  monitoring watches these distributions).

Everything is Decimal and deterministic, so the same raw value always normalizes identically and
the score is reproducible (§25.1 #5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _clamp(v: Decimal, lo: Decimal = _ZERO, hi: Decimal = _HUNDRED) -> Decimal:
    return max(lo, min(hi, v))


@dataclass(frozen=True, slots=True)
class MonotoneCurve:
    """A calibrated monotone normalization: piecewise-linear through `(raw, score)` anchors,
    sorted by raw. `higher_is_better=False` simply means the anchors encode a downward curve
    (a lower cap-rate-of-expense scores higher). Values below/above the anchor range clamp to the
    end anchor's score — winsorization by construction, no runaway from an outlier (§25.2 p2/p98).
    """

    anchors: tuple[tuple[Decimal, Decimal], ...]  # (raw, score), score in [0,100]

    def score(self, value: Decimal) -> Decimal:
        pts = self.anchors
        if value <= pts[0][0]:
            return _clamp(pts[0][1])
        if value >= pts[-1][0]:
            return _clamp(pts[-1][1])
        for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
            if x0 <= value <= x1:
                if x1 == x0:
                    return _clamp(y1)
                frac = (value - x0) / (x1 - x0)
                return _clamp(y0 + (y1 - y0) * frac)
        return _clamp(pts[-1][1])


@dataclass(frozen=True, slots=True)
class UShapedCurve:
    """A calibrated U-shaped (inverted-U) normalization for metrics with an *ideal band* (DOM):
    peaks at `peak_score` across `[ideal_low, ideal_high]`, falls linearly to `floor_score` at
    `zero_at_low`/`zero_at_high` (§25.2). Below-ideal (a bidding war) and above-ideal (stale
    listing) both score down, for different reasons the ledger can name."""

    ideal_low: Decimal
    ideal_high: Decimal
    zero_at_low: Decimal
    zero_at_high: Decimal
    peak_score: Decimal = Decimal("85")
    floor_score: Decimal = Decimal("20")

    def score(self, value: Decimal) -> Decimal:
        if self.ideal_low <= value <= self.ideal_high:
            return _clamp(self.peak_score)
        if value < self.ideal_low:
            span = self.ideal_low - self.zero_at_low
            if span <= _ZERO:
                return _clamp(self.peak_score)
            frac = (value - self.zero_at_low) / span
            return _clamp(self.floor_score + (self.peak_score - self.floor_score) * frac)
        span = self.zero_at_high - self.ideal_high
        if span <= _ZERO:
            return _clamp(self.peak_score)
        frac = (self.zero_at_high - value) / span
        return _clamp(self.floor_score + (self.peak_score - self.floor_score) * frac)


def percentile_score(
    value: Decimal, cohort: Sequence[Decimal], *, higher_is_better: bool = True,
    winsor_lo: Decimal = Decimal("0.02"), winsor_hi: Decimal = Decimal("0.98"),
) -> Decimal | None:
    """The winsorized empirical percentile of `value` within a market×class×window `cohort`,
    scaled to 0–100 (§25.2 production path). Winsorizes at p2/p98 so a single outlier can't own
    the top of the scale. None for a cohort too small to trust (< 20) — the caller then falls back
    to the calibrated prior curve, per the cold-start ladder (§25.2)."""
    clean = sorted(v for v in cohort)
    n = len(clean)
    if n < 20:
        return None
    lo_idx = int(winsor_lo * (n - 1))
    hi_idx = int(winsor_hi * (n - 1))
    lo, hi = clean[lo_idx], clean[hi_idx]
    v = max(lo, min(hi, value))
    below = sum(1 for x in clean if x < v)
    equal = sum(1 for x in clean if x == v)
    pct = (Decimal(below) + Decimal(equal) / 2) / Decimal(n) * _HUNDRED
    return _clamp(pct if higher_is_better else _HUNDRED - pct)


def boolean_score(flag: bool, *, true_score: Decimal, false_score: Decimal) -> Decimal:
    """A boolean flag → a fixed point value (§25.2)."""
    return _clamp(true_score if flag else false_score)


__all__ = [
    "MonotoneCurve",
    "UShapedCurve",
    "boolean_score",
    "percentile_score",
]
