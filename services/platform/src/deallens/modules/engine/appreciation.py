"""Market appreciation selection — pure (03 §28.2–§28.4).

The DB read (which `market_stats` rows exist for a property's geographies) is the service's
job; this module is the honest-selection policy over those rows: pick the *finest reliable*
geography that carries an appreciation metric, label it at that level (a metro rate carried
to a property is labeled metro, never faked to parcel precision — §28.2), and express the
result as an annualized fraction with a level-appropriate confidence.

Appreciation feeds two consumers (03 §28.1): the scoring M-group factor `appreciation_3yr`,
and the 5-year equity/IRR view of the financial engine (03 §26.5). Both want one number with
a provenance, which is exactly `Appreciation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from deallens.modules.markets.models import GeoLevel

# Controlled appreciation-metric vocabulary (03 §28.3). Values are stored as *annualized*
# fractions (0.05 = 5%/yr) so consumers never have to know a metric's compounding horizon.
METRIC_YOY = "appreciation_yoy"  # trailing 12-month
METRIC_3YR = "appreciation_3yr_annualized"  # annualized over trailing 3 years (smoother)

# Metric preference: the 3-year annualized rate is less noisy than a single YoY print, so it
# leads; YoY is the fallback when a market has under three years of history (03 §28.6 backfill).
_METRIC_PREFERENCE = (METRIC_3YR, METRIC_YOY)
_PERIOD_YEARS = {METRIC_3YR: 3, METRIC_YOY: 1}

# Finest → coarsest, so "most specific geography wins" is a simple min() over this rank.
_LEVEL_RANK: dict[GeoLevel, int] = {
    GeoLevel.TRACT: 0,
    GeoLevel.ZIP: 1,
    GeoLevel.CITY: 2,
    GeoLevel.COUNTY: 3,
    GeoLevel.METRO: 4,
}
# Confidence by geography (03 §28.2): ZIP/city is the sweet spot — specific enough to matter,
# broad enough for a stable sample. A tract reading is thin; a metro reading is coarse for one
# parcel. Both are usable, just less confident, and both are labeled for what they are.
_LEVEL_CONFIDENCE: dict[GeoLevel, Decimal] = {
    GeoLevel.TRACT: Decimal("0.65"),
    GeoLevel.ZIP: Decimal("0.85"),
    GeoLevel.CITY: Decimal("0.80"),
    GeoLevel.COUNTY: Decimal("0.70"),
    GeoLevel.METRO: Decimal("0.60"),
}


@dataclass(frozen=True, slots=True)
class AppreciationReading:
    """One `market_stats` appreciation row, flattened for pure selection."""

    geo_level: GeoLevel
    geo_id: str
    metric: str
    value: Decimal
    period: date
    source: str


@dataclass(frozen=True, slots=True)
class Appreciation:
    """The chosen appreciation rate + full provenance (03 §28.4)."""

    annual_pct: Decimal | None
    period_years: int
    geo_level: GeoLevel | None
    geo_id: str | None
    source: str | None
    confidence: Decimal | None
    as_of: date | None


_EMPTY = Appreciation(None, 1, None, None, None, None, None)


def select_appreciation(readings: list[AppreciationReading]) -> Appreciation:
    """Pick the appreciation reading for a property from all its geographies' rows. Chooses the
    finest geography level present, then the preferred metric at that level, then the most
    recent period — a total, deterministic order. Returns an empty `Appreciation` when no
    appreciation metric is available (an honest gap, not a fabricated 0% — 03 §27.1 stance).
    """
    usable = [r for r in readings if r.metric in _PERIOD_YEARS and r.geo_level in _LEVEL_RANK]
    if not usable:
        return _EMPTY

    def rank(r: AppreciationReading) -> tuple[int, int, date]:
        # Lower is better: finest level, then preferred metric, then newest period (via -ordinal
        # is awkward for dates, so we sort ascending and negate the period by using reverse key).
        return (_LEVEL_RANK[r.geo_level], _METRIC_PREFERENCE.index(r.metric), r.period)

    # Newest period should win within equal (level, metric); sort by (level, metric) asc and
    # period desc. Do it in two keys: primary asc on rank[:2], then pick max period.
    best_level_metric = min(rank(r)[:2] for r in usable)
    candidates = [r for r in usable if rank(r)[:2] == best_level_metric]
    chosen = max(candidates, key=lambda r: r.period)

    return Appreciation(
        annual_pct=chosen.value,
        period_years=_PERIOD_YEARS[chosen.metric],
        geo_level=chosen.geo_level,
        geo_id=chosen.geo_id,
        source=chosen.source,
        confidence=_LEVEL_CONFIDENCE[chosen.geo_level],
        as_of=chosen.period,
    )


__all__ = [
    "METRIC_3YR",
    "METRIC_YOY",
    "Appreciation",
    "AppreciationReading",
    "select_appreciation",
]
