"""Market metric registry + geo-hierarchy resolution + the fair-housing guardrail — pure, no I/O
(03 §28.2, §28.3, §28.5).

Three jobs:
- **Registry (§28.3).** The controlled vocabulary of market metrics with their domain, direction
  (higher-better vs. lower-better), and — critically — whether a metric is a **protected-class
  composition** variable. `market_stats` stores generic `(metric, value)` rows; this registry is
  what gives a metric meaning without a migration per metric.
- **Geo-hierarchy resolution (§28.2).** A metric is computed at the *finest reliable* level and
  inherited downward *with its level labeled* — a crime figure carried at county level is labeled
  county-level, never faked to ZIP precision. `resolve_finest` picks the reading and keeps the
  label.
- **Fair-housing guardrail (§28.5, NFR-07, binding).** Demographic composition (race, ethnicity,
  religion, national origin, familial status) is **never** an index input or a displayed "quality"
  signal. `assert_fair_housing` raises on any denylisted variable class; a test and (per the ADR)
  scoring-config CI enforce it. Economic demographics (population growth, income, household
  formation) are *not* protected-class composition and remain allowed.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from deallens.modules.markets.models import GeoLevel


class MetricDomain(enum.StrEnum):
    """§28.3 metric domains."""

    PRICES = "prices"
    VELOCITY = "velocity"
    RENTS = "rents"
    DEMOGRAPHICS = "demographics"
    EMPLOYMENT = "employment"
    SCHOOLS = "schools"
    CRIME = "crime"
    HAZARD = "hazard"
    WALK = "walk"
    DEVELOPMENT = "development"
    REGULATORY = "regulatory"


@dataclass(frozen=True, slots=True)
class MetricSpec:
    key: str
    domain: MetricDomain
    higher_is_better: bool
    unit: str
    protected_class: bool = False  # §28.5 — never an index input if True


# Controlled metric vocabulary (§28.3). `*_pct`/ratios are fractions unless noted. Protected-class
# composition variables are registered ONLY so the denylist can name and reject them — they are
# never inputs to an index or a displayed quality signal (§28.5).
_SPECS: tuple[MetricSpec, ...] = (
    # Prices
    MetricSpec("median_sale_ppsf", MetricDomain.PRICES, True, "usd_per_sqft"),
    MetricSpec("appreciation_3yr_annualized", MetricDomain.PRICES, True, "fraction"),
    MetricSpec("appreciation_yoy", MetricDomain.PRICES, True, "fraction"),
    MetricSpec("sale_to_list_ratio", MetricDomain.PRICES, True, "ratio"),
    # Velocity / supply
    MetricSpec("median_dom", MetricDomain.VELOCITY, False, "days"),
    MetricSpec("inventory_months", MetricDomain.VELOCITY, False, "months"),
    MetricSpec("absorption_rate", MetricDomain.VELOCITY, True, "fraction"),
    MetricSpec("price_cut_share", MetricDomain.VELOCITY, False, "fraction"),
    # Rents
    MetricSpec("rent_growth_3yr", MetricDomain.RENTS, True, "fraction"),
    MetricSpec("rent_to_price", MetricDomain.RENTS, True, "fraction"),
    MetricSpec("gross_yield", MetricDomain.RENTS, True, "fraction"),
    # Demographics (economic — allowed; NOT protected-class composition)
    MetricSpec("population_growth", MetricDomain.DEMOGRAPHICS, True, "fraction"),
    MetricSpec("household_formation", MetricDomain.DEMOGRAPHICS, True, "fraction"),
    MetricSpec("median_income", MetricDomain.DEMOGRAPHICS, True, "usd"),
    MetricSpec("owner_occupancy_share", MetricDomain.DEMOGRAPHICS, True, "fraction"),
    # Employment
    MetricSpec("job_growth", MetricDomain.EMPLOYMENT, True, "fraction"),
    MetricSpec("unemployment_rate", MetricDomain.EMPLOYMENT, False, "fraction"),
    # Schools / crime / hazard / walk / development / regulatory
    MetricSpec("school_rating_pct", MetricDomain.SCHOOLS, True, "percentile"),
    MetricSpec("crime_index", MetricDomain.CRIME, False, "index_0_100"),
    MetricSpec("walkability", MetricDomain.WALK, True, "index_0_100"),
    MetricSpec("permit_volume", MetricDomain.DEVELOPMENT, True, "count"),
    MetricSpec("rent_control_flag", MetricDomain.REGULATORY, False, "bool"),
    # Protected-class composition — registered ONLY to be denied (§28.5)
    MetricSpec("race_composition", MetricDomain.DEMOGRAPHICS, True, "share", protected_class=True),
    MetricSpec("ethnicity_composition", MetricDomain.DEMOGRAPHICS, True, "share",
               protected_class=True),
    MetricSpec("religion_composition", MetricDomain.DEMOGRAPHICS, True, "share",
               protected_class=True),
    MetricSpec("national_origin_composition", MetricDomain.DEMOGRAPHICS, True, "share",
               protected_class=True),
    MetricSpec("familial_status_composition", MetricDomain.DEMOGRAPHICS, True, "share",
               protected_class=True),
)

METRIC_REGISTRY: dict[str, MetricSpec] = {s.key: s for s in _SPECS}

# Substrings that mark a protected-class variable class even if a feed invents a new key (§28.5) —
# defense in depth beyond the explicit registry flags.
_PROTECTED_SUBSTRINGS = (
    "race", "ethnic", "religio", "national_origin", "familial", "ancestry", "disability",
)


class FairHousingViolation(ValueError):
    """Raised when a protected-class composition variable is used as an index/quality input
    (§28.5, NFR-07). This is a hard, binding guardrail — the request fails, it does not degrade."""


def is_protected_class(metric_key: str) -> bool:
    spec = METRIC_REGISTRY.get(metric_key)
    if spec is not None and spec.protected_class:
        return True
    low = metric_key.lower()
    return any(sub in low for sub in _PROTECTED_SUBSTRINGS)


def assert_fair_housing(metric_keys: Iterable[str]) -> None:
    """Raise `FairHousingViolation` if any key is a protected-class composition variable (§28.5).
    Called on every index's input set — a denylisted variable can never become a "quality" signal,
    by construction, not by reviewer vigilance."""
    offenders = [k for k in metric_keys if is_protected_class(k)]
    if offenders:
        raise FairHousingViolation(
            f"protected-class variables cannot be scoring/index inputs (§28.5): {offenders}"
        )


# --- Geo-hierarchy resolution (§28.2) ---------------------------------------------------

_LEVEL_RANK: dict[GeoLevel, int] = {
    GeoLevel.TRACT: 0, GeoLevel.ZIP: 1, GeoLevel.CITY: 2, GeoLevel.COUNTY: 3, GeoLevel.METRO: 4,
}


@dataclass(frozen=True, slots=True)
class MetricReading:
    metric: str
    geo_level: GeoLevel
    geo_id: str
    value: Decimal
    period: date
    source: str


@dataclass(frozen=True, slots=True)
class ResolvedMetric:
    """A metric value carried to a property/market with its *honest* geography label (§28.2)."""

    metric: str
    value: Decimal
    geo_level: GeoLevel
    geo_id: str
    source: str
    period: date


def resolve_finest(readings: Sequence[MetricReading]) -> ResolvedMetric | None:
    """The finest-reliable reading for a metric, newest period breaking ties (§28.2). The returned
    `geo_level` is the level the value was actually computed at — labeled, never upsampled to fake
    precision. None when no reading is available (an honest gap)."""
    if not readings:
        return None
    best = min(
        readings,
        key=lambda r: (_LEVEL_RANK.get(r.geo_level, 99), -r.period.toordinal()),
    )
    return ResolvedMetric(
        metric=best.metric, value=best.value, geo_level=best.geo_level, geo_id=best.geo_id,
        source=best.source, period=best.period,
    )


__all__ = [
    "METRIC_REGISTRY",
    "FairHousingViolation",
    "MetricDomain",
    "MetricReading",
    "MetricSpec",
    "ResolvedMetric",
    "assert_fair_housing",
    "is_protected_class",
    "resolve_finest",
]
