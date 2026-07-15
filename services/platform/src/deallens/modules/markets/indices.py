"""Derived market indices + market score — pure, no I/O (03 §28.4).

Four indices, each a calibrated 0–100 composite of named metrics:
- **Market Momentum** — appreciation, DOM, inventory, absorption.
- **Rental Demand** — rent growth, rent-to-price, household formation.
- **Liquidity** — sale velocity for the class (DOM + inventory + absorption).
- **Neighborhood Quality** — schools, crime, walkability, owner-occupancy. **Never** demographic
  composition (§28.5) — enforced by `assert_fair_housing` on every index's input set, so a
  fair-housing violation is impossible by construction, not by review.

Each index maps its raw metrics onto 0–100 via calibrated national-prior curves (the same
cold-start approach as scoring §25.2), then a weighted mean over the metrics actually present —
a market missing a metric reweights rather than scoring 0. Where a national reference cohort is
supplied, the index is *also* expressed as a national percentile (§28.4 "both shown"); otherwise
the calibrated value stands in and is labeled as such.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from deallens.modules.markets.metrics import assert_fair_housing

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_Q = Decimal("0.1")


def _r(v: Decimal) -> Decimal:
    return v.quantize(_Q, rounding=ROUND_HALF_UP)


def _clamp(v: Decimal) -> Decimal:
    return max(_ZERO, min(_HUNDRED, v))


@dataclass(frozen=True, slots=True)
class _Curve:
    anchors: tuple[tuple[Decimal, Decimal], ...]

    def score(self, value: Decimal) -> Decimal:
        pts = self.anchors
        if value <= pts[0][0]:
            return _clamp(pts[0][1])
        if value >= pts[-1][0]:
            return _clamp(pts[-1][1])
        for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
            if x0 <= value <= x1:
                frac = (value - x0) / (x1 - x0) if x1 != x0 else _ZERO
                return _clamp(y0 + (y1 - y0) * frac)
        return _clamp(pts[-1][1])


def _c(*a: tuple[str, str]) -> _Curve:
    return _Curve(anchors=tuple((Decimal(x), Decimal(y)) for x, y in a))


# Calibrated national-prior curves per metric (§28.4/§28.6 baseline). Lower-is-better metrics
# encode a downward curve.
_METRIC_CURVES: dict[str, _Curve] = {
    "appreciation_3yr_annualized": _c(("-0.02", "10"), ("0", "35"), ("0.04", "62"),
                                      ("0.07", "82"), ("0.12", "97")),
    "median_dom": _c(("15", "90"), ("30", "70"), ("50", "50"), ("80", "28"), ("120", "10")),
    "inventory_months": _c(("2", "88"), ("4", "68"), ("6", "50"), ("9", "28"), ("12", "12")),
    "absorption_rate": _c(("0.1", "15"), ("0.2", "45"), ("0.35", "70"), ("0.5", "90")),
    "rent_growth_3yr": _c(("0", "25"), ("0.02", "48"), ("0.04", "70"), ("0.06", "88")),
    "rent_to_price": _c(("0.04", "15"), ("0.06", "45"), ("0.08", "68"), ("0.10", "88")),
    "household_formation": _c(("0", "30"), ("0.01", "55"), ("0.02", "78"), ("0.03", "92")),
    "school_rating_pct": _c(("0", "10"), ("50", "50"), ("100", "95")),
    "crime_index": _c(("0", "95"), ("25", "75"), ("50", "50"), ("75", "25"), ("100", "8")),
    "walkability": _c(("0", "25"), ("50", "58"), ("100", "90")),
    "owner_occupancy_share": _c(("0.2", "25"), ("0.5", "55"), ("0.7", "78"), ("0.9", "92")),
}

# Index → (metric, weight). Weights renormalize over present metrics. NO protected-class metric
# appears here (§28.5); `_index` asserts it every call regardless.
_INDEX_COMPONENTS: dict[str, tuple[tuple[str, Decimal], ...]] = {
    "momentum": (("appreciation_3yr_annualized", Decimal("0.4")), ("median_dom", Decimal("0.2")),
                 ("inventory_months", Decimal("0.2")), ("absorption_rate", Decimal("0.2"))),
    "rental_demand": (("rent_growth_3yr", Decimal("0.4")), ("rent_to_price", Decimal("0.35")),
                      ("household_formation", Decimal("0.25"))),
    "liquidity": (("median_dom", Decimal("0.45")), ("inventory_months", Decimal("0.3")),
                  ("absorption_rate", Decimal("0.25"))),
    "neighborhood_quality": (("school_rating_pct", Decimal("0.35")),
                             ("crime_index", Decimal("0.3")), ("walkability", Decimal("0.2")),
                             ("owner_occupancy_share", Decimal("0.15"))),
}

INDEX_NAMES = {
    "momentum": "Market Momentum", "rental_demand": "Rental Demand", "liquidity": "Liquidity",
    "neighborhood_quality": "Neighborhood Quality",
}

# Market score = weighted mean of the four indices (§28.4 → a single 0–100 headline).
_MARKET_SCORE_WEIGHTS: dict[str, Decimal] = {
    "momentum": Decimal("0.35"), "rental_demand": Decimal("0.25"), "liquidity": Decimal("0.2"),
    "neighborhood_quality": Decimal("0.2"),
}


@dataclass(frozen=True, slots=True)
class IndexResult:
    key: str
    name: str
    score: Decimal | None  # calibrated 0–100; None if no component metric present
    national_percentile: Decimal | None = None  # when a national cohort is supplied (§28.4)
    components_used: list[str] = field(default_factory=list)
    components_missing: list[str] = field(default_factory=list)


def _index(
    key: str, metrics: Mapping[str, Decimal], national: Mapping[str, Sequence[Decimal]] | None,
) -> IndexResult:
    components = _INDEX_COMPONENTS[key]
    assert_fair_housing([m for m, _ in components])  # §28.5 — binding, every call

    present: list[tuple[str, Decimal, Decimal]] = []  # (metric, normalized, weight)
    missing: list[str] = []
    for metric, weight in components:
        raw = metrics.get(metric)
        curve = _METRIC_CURVES.get(metric)
        if raw is None or curve is None:
            missing.append(metric)
            continue
        present.append((metric, curve.score(raw), weight))

    if not present:
        return IndexResult(key, INDEX_NAMES[key], None, None, [], [m for m, _ in components])

    total_w = sum((w for *_, w in present), _ZERO)
    score = _r(sum((norm * w for _, norm, w in present), _ZERO) / total_w)
    nat_pct = _national_percentile(key, score, national)
    return IndexResult(
        key, INDEX_NAMES[key], score, nat_pct,
        [m for m, *_ in present], missing,
    )


def _national_percentile(
    key: str, score: Decimal, national: Mapping[str, Sequence[Decimal]] | None,
) -> Decimal | None:
    """The index's percentile within a national cohort of the same index's scores (§28.4). None
    when no cohort is supplied — the calibrated score stands alone, labeled as such."""
    if national is None:
        return None
    cohort = sorted(national.get(key, ()))
    if len(cohort) < 20:
        return None
    below = sum(1 for v in cohort if v < score)
    return _r(Decimal(below) / Decimal(len(cohort)) * _HUNDRED)


def compute_indices(
    metrics: Mapping[str, Decimal], *, national: Mapping[str, Sequence[Decimal]] | None = None,
) -> list[IndexResult]:
    """All four derived indices for a market from its resolved metric values (§28.4)."""
    return [_index(k, metrics, national) for k in _INDEX_COMPONENTS]


def market_score(indices: Sequence[IndexResult]) -> Decimal | None:
    """The single 0–100 market score = weighted mean of the available indices (§28.4). None when
    no index could be computed (a market with no metrics yet — onboarding, §28.6)."""
    present = [(i.key, i.score) for i in indices if i.score is not None]
    if not present:
        return None
    total_w = sum((_MARKET_SCORE_WEIGHTS[k] for k, _ in present), _ZERO)
    return _r(sum((s * _MARKET_SCORE_WEIGHTS[k] for k, s in present), _ZERO) / total_w)


__all__ = [
    "INDEX_NAMES",
    "IndexResult",
    "compute_indices",
    "market_score",
]
