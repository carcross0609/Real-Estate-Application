"""Scheduling for ingestion polls (§14.2). Sources are polled at their license-permitted
cadence: RESO deltas every few minutes, Tier-B public records daily, Tier-D gov/open data
weekly/quarterly. This module is the cadence policy + the poll entrypoint; the *trigger* is
Celery beat (already a dependency), which is worker infra not built yet (Phase 1 M1.1) — so,
like the billing stub (ADR 0001), the orchestration function lives behind the boundary now
and the beat schedule wires to it later without touching this code.

`next_run_due` and `cadence_for` are pure so scheduling logic is testable; `poll_source` is
the thin entrypoint a beat task (or a manual `make ingest-once`) calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.modules.admin.models import IngestionRun, IngestionRunStatus
from deallens.modules.ingestion import service as ingestion_service
from deallens.modules.ingestion.adapters.base import AdapterConfig, Cursor, RawTransport
from deallens.modules.ingestion.adapters.registry import build_adapter
from deallens.modules.ingestion.events import EventEmitter
from deallens.modules.ingestion.models import DataSourceTier
from deallens.modules.ingestion.pipeline import IngestionCycleResult, run_ingestion_cycle
from deallens.modules.ingestion.raw_store import BlobStore

# Default poll cadence per source tier, in seconds (§14.2). Overridable per source via
# `data_sources.license_policy["cadence_seconds"]` — some MLS licenses cap delta frequency.
DEFAULT_CADENCES: dict[DataSourceTier, int] = {
    DataSourceTier.MLS: 300,  # 5 min RESO deltas
    DataSourceTier.PROPERTY_DATA: 86_400,  # daily assessor/deed refresh
    DataSourceTier.RENTAL: 3_600,  # hourly rental estimates
    DataSourceTier.GOV_OPEN: 604_800,  # weekly gov/open data
    DataSourceTier.COMMERCIAL_ADDON: 604_800,
}


@dataclass(frozen=True, slots=True)
class IngestionSchedule:
    source_key: str
    tier: DataSourceTier
    interval_seconds: int
    market_id: UUID | None = None


def cadence_for(tier: DataSourceTier, *, override_seconds: int | None = None) -> int:
    """Poll interval for a tier, honoring a per-source override (a stricter license cap)."""
    if override_seconds is not None and override_seconds > 0:
        return override_seconds
    return DEFAULT_CADENCES[tier]


def next_run_due(interval_seconds: int, last_run: datetime | None, now: datetime) -> bool:
    """Whether a source is due to poll. Never-run sources are always due; otherwise due once
    `interval_seconds` has elapsed since the last run started."""
    if last_run is None:
        return True
    return now >= last_run + timedelta(seconds=interval_seconds)


async def resolve_start_cursor(db: AsyncSession, source_id: UUID) -> Cursor:
    """The cursor a poll resumes from: the `high_watermark` (max source ModificationTimestamp)
    of the most recent successful run. This is what makes polls incremental (§14.2) — each run
    pulls only records changed since the last one saw the newest. A first run resumes from an
    empty cursor (full backfill)."""
    result = await db.execute(
        select(IngestionRun)
        .where(
            IngestionRun.source_id == source_id,
            IngestionRun.status.in_(
                (IngestionRunStatus.SUCCEEDED, IngestionRunStatus.PARTIAL)
            ),
        )
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    )
    last = result.scalars().first()
    if last is None:
        return Cursor()
    watermark = (last.stats or {}).get("high_watermark")
    since = datetime.fromisoformat(watermark) if isinstance(watermark, str) else None
    return Cursor(since=since)


async def poll_source(
    db: AsyncSession,
    *,
    source_key: str,
    transport: RawTransport,
    blob_store: BlobStore,
    base_url: str = "",
    options: dict[str, object] | None = None,
    emitter: EventEmitter | None = None,
    market_id: UUID | None = None,
) -> IngestionCycleResult:
    """One scheduled poll: look up the source, build its adapter, resume from the last
    watermark, and run a cycle. The Celery-beat task is a one-liner over this — it supplies a
    bound `HttpxTransport` (per-source auth) and the S3 `blob_store`; a test supplies a
    `FixtureTransport` + `InMemoryBlobStore`, so the entrypoint itself is exercised without a
    network."""
    source = await ingestion_service.get_data_source(db, source_key)
    config = AdapterConfig(
        source_id=source.id,
        transport=transport,
        base_url=base_url,
        options=options or {},
    )
    adapter = build_adapter(source_key, config)
    start_cursor = await resolve_start_cursor(db, source.id)
    return await run_ingestion_cycle(
        db,
        adapter,
        source=source,
        blob_store=blob_store,
        emitter=emitter,
        start_cursor=start_cursor,
        market_id=market_id,
    )
