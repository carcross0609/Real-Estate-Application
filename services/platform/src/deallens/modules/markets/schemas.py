"""Market intelligence API contracts (03 §28). The market report is the user-facing output: the
resolved metric set (each labeled with the geography it was actually measured at — §28.2), the
four derived indices (§28.4), and a single market score. Dicts never cross the module boundary
(§20); the router returns these models.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class MetricValueOut(BaseModel):
    """One resolved market metric with its honest geography label (§28.2). `geo_level` is the
    level the value was *measured* at — a county-level crime figure says "county", never faked to
    the property's ZIP."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    value: Decimal
    geo_level: str
    geo_id: str
    source: str
    period: date


class IndexOut(BaseModel):
    """A derived index (§28.4): calibrated 0–100 score, optional national percentile (shown when a
    reference cohort exists), and the component metrics used vs. missing — so a thin index is
    honest about what it's missing rather than silently confident."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    score: Decimal | None = None
    national_percentile: Decimal | None = None
    components_used: list[str] = Field(default_factory=list)
    components_missing: list[str] = Field(default_factory=list)


class MarketReportOut(BaseModel):
    """The full market intelligence bundle for a market (§28.1 consumer #2 — the user-facing market
    page). `market_score` is the single headline; `grade` a letter for quick scanning; `indices`
    and `metrics` the decomposition. `data_completeness` is the fraction of expected metrics
    present — the honesty signal for an onboarding market (§28.6)."""

    model_config = ConfigDict(extra="forbid")

    market_id: str
    name: str | None = None
    cbsa_code: str | None = None
    market_score: Decimal | None = None
    grade: str | None = None
    indices: list[IndexOut] = Field(default_factory=list)
    metrics: list[MetricValueOut] = Field(default_factory=list)
    data_completeness: Decimal = Decimal("0")
    generated_at: datetime | None = None


class MarketContext(BaseModel):
    """A compact market snapshot for the scoring/report engines (§28.1 consumer #1). The M/L
    signals plus the indices, keyed so a caller can drop them straight into their factor set."""

    model_config = ConfigDict(extra="forbid")

    market_id: str
    market_score: Decimal | None = None
    momentum: Decimal | None = None
    rental_demand: Decimal | None = None
    liquidity: Decimal | None = None
    neighborhood_quality: Decimal | None = None
    metrics: dict[str, Decimal] = Field(default_factory=dict)


__all__ = [
    "IndexOut",
    "MarketContext",
    "MarketReportOut",
    "MetricValueOut",
]
