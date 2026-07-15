"""Markets module public interface (§9.3/§19). Reads the raw `market_stats` a market has, resolves
each metric to its finest reliable geography (§28.2), computes the four derived indices + the
single market score (§28.4), persists the derived values back as `market_stats` (so scoring and
the market page read them like any other metric), and serves the market report + a compact context
for the scoring/report engines (§28.1).

`market_stats` is shared reference data (no RLS, §11.1); the compute path is an owner-session
worker entrypoint (S43 onboarding + the periodic refresh). The fair-housing guardrail (§28.5) is
enforced inside the pure index layer — a protected-class variable can never reach an index.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.errors import NotFoundError
from deallens.modules.markets import indices as index_math
from deallens.modules.markets import metrics as metric_lib
from deallens.modules.markets.models import GeoLevel, Market, MarketStat
from deallens.modules.markets.schemas import (
    IndexOut,
    MarketContext,
    MarketReportOut,
    MetricValueOut,
)

# Derived index values are written back as market_stats under these keys. `market_liquidity` is
# the key scoring already reads for its M-group liquidity factor — the loop closes here.
_INDEX_METRIC_KEYS = {
    "momentum": "index_momentum",
    "rental_demand": "index_rental_demand",
    "liquidity": "market_liquidity",
    "neighborhood_quality": "index_neighborhood_quality",
}
_MARKET_SCORE_KEY = "market_score"
# The metric set a fully-onboarded market is expected to carry — the denominator for the
# data-completeness honesty signal (§28.6).
_EXPECTED_METRICS = (
    "median_sale_ppsf", "appreciation_3yr_annualized", "median_dom", "inventory_months",
    "absorption_rate", "rent_growth_3yr", "rent_to_price", "household_formation",
    "school_rating_pct", "crime_index", "walkability", "owner_occupancy_share",
)

_GRADE_THRESHOLDS = ((Decimal("85"), "A"), (Decimal("70"), "B"), (Decimal("55"), "C"),
                     (Decimal("40"), "D"))


def _grade(score: Decimal | None) -> str | None:
    if score is None:
        return None
    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


async def _resolved_metrics(
    db: AsyncSession, market_id: UUID
) -> dict[str, metric_lib.ResolvedMetric]:
    """Every metric a market has, resolved to its finest-reliable geography (§28.2). Groups all
    `market_stats` rows by metric, keeps the latest period per (level, geo), and picks the finest
    level — the value carried is labeled with the level it was measured at."""
    rows = await db.execute(
        select(MarketStat).where(MarketStat.market_id == market_id)
    )
    by_metric: dict[str, list[metric_lib.MetricReading]] = defaultdict(list)
    # Keep only the newest period per (metric, level, geo) before resolving.
    newest: dict[tuple[str, str, str], MarketStat] = {}
    for stat in rows.scalars().all():
        if stat.value is None:
            continue
        key = (stat.metric, stat.geo_level.value, stat.geo_id)
        if key not in newest or stat.period > newest[key].period:
            newest[key] = stat
    for stat in newest.values():
        if stat.value is None:
            continue
        by_metric[stat.metric].append(metric_lib.MetricReading(
            metric=stat.metric, geo_level=stat.geo_level, geo_id=stat.geo_id,
            value=stat.value, period=stat.period, source=stat.source,
        ))
    resolved: dict[str, metric_lib.ResolvedMetric] = {}
    for metric, readings in by_metric.items():
        r = metric_lib.resolve_finest(readings)
        if r is not None:
            resolved[metric] = r
    return resolved


async def compute_market_report(
    db: AsyncSession, *, market_id: UUID, persist: bool = True
) -> MarketReportOut:
    """Compute a market's derived indices + score from its metrics and (optionally) persist the
    derived values back as `market_stats` (§28.4/§28.6). Returns the full market report."""
    market = await db.get(Market, market_id)
    if market is None:
        raise NotFoundError("Market not found")

    resolved = await _resolved_metrics(db, market_id)
    raw_values = {k: r.value for k, r in resolved.items()}
    indices = index_math.compute_indices(raw_values)
    score = index_math.market_score(indices)

    if persist:
        await _persist_derived(db, market, indices, score)
        await db.flush()

    present = sum(1 for m in _EXPECTED_METRICS if m in resolved)
    completeness = (Decimal(present) / Decimal(len(_EXPECTED_METRICS))).quantize(Decimal("0.01"))
    return MarketReportOut(
        market_id=str(market_id), name=market.name, cbsa_code=market.cbsa_code,
        market_score=score, grade=_grade(score),
        indices=[
            IndexOut(key=i.key, name=i.name, score=i.score,
                     national_percentile=i.national_percentile,
                     components_used=i.components_used, components_missing=i.components_missing)
            for i in indices
        ],
        metrics=[
            MetricValueOut(metric=r.metric, value=r.value, geo_level=r.geo_level.value,
                           geo_id=r.geo_id, source=r.source, period=r.period)
            for r in resolved.values()
        ],
        data_completeness=completeness,
        generated_at=datetime.now(UTC),
    )


async def _persist_derived(
    db: AsyncSession, market: Market, indices: list[index_math.IndexResult], score: Decimal | None,
) -> None:
    """Write the derived indices + market score back as metro-level `market_stats` rows (§28.6),
    upserting the current period so scoring/reports read them like any ingested metric."""
    period = datetime.now(UTC).date().replace(day=1)
    for idx in indices:
        if idx.score is not None:
            await _upsert_stat(db, market, _INDEX_METRIC_KEYS[idx.key], idx.score, period)
    if score is not None:
        await _upsert_stat(db, market, _MARKET_SCORE_KEY, score, period)


async def _upsert_stat(
    db: AsyncSession, market: Market, metric: str, value: Decimal, period: date,
) -> None:
    existing = await db.execute(
        select(MarketStat).where(
            MarketStat.market_id == market.id, MarketStat.geo_level == GeoLevel.METRO,
            MarketStat.geo_id == market.cbsa_code, MarketStat.metric == metric,
            MarketStat.period == period,
        )
    )
    row = existing.scalars().first()
    if row is None:
        row = MarketStat(
            market_id=market.id, geo_level=GeoLevel.METRO, geo_id=market.cbsa_code,
            metric=metric, period=period, source="derived", method_version="v1",
        )
        db.add(row)
    row.value = value


async def get_market_report(db: AsyncSession, *, market_id: UUID) -> MarketReportOut:
    """The market report without recomputing/persisting — a pure read for the market page."""
    return await compute_market_report(db, market_id=market_id, persist=False)


async def market_context(db: AsyncSession, *, market_id: UUID) -> MarketContext:
    """A compact market snapshot for the scoring/report engines (§28.1 consumer #1): the four index
    scores + the resolved raw metrics, keyed for direct use as factors."""
    report = await compute_market_report(db, market_id=market_id, persist=False)
    idx = {i.key: i.score for i in report.indices}
    return MarketContext(
        market_id=str(market_id), market_score=report.market_score,
        momentum=idx.get("momentum"), rental_demand=idx.get("rental_demand"),
        liquidity=idx.get("liquidity"), neighborhood_quality=idx.get("neighborhood_quality"),
        metrics={m.metric: m.value for m in report.metrics},
    )


__all__ = [
    "compute_market_report",
    "get_market_report",
    "market_context",
]
