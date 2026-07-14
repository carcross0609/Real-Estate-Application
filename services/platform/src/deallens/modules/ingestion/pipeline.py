"""Ingestion pipeline orchestration (§9.4) — the loop that turns an adapter + a cursor into
canonical rows, change events, and an `ingestion_runs` record.

This is the "→" between the boxes in the §9.4 data flow, kept adapter-agnostic: it drives
plan → fetch → **persist raw (before transform)** → normalize → route each delta to the
owning module's service → record the run. It never knows what a RESO or ATTOM record looks
like — only `CanonicalDelta`s and the four adapter methods.

Two guarantees the design turns on:
- **Replayability (NFR-05):** `replay_raw_record` re-runs normalize→write from stored raw
  bytes, so a normalization fix is re-applied to history without re-hitting the feed.
- **Idempotency (§9.4):** every downstream write is versioned/upsert, so re-running a cycle
  (a retry, a replay, an overlapping poll) converges to the same state instead of duplicating.

Concurrency (per-property Redis lock, debounced fan-out — §9.5) is the worker's concern; the
orchestration here is single-transaction and deterministic so it's testable end-to-end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.logging import get_logger
from deallens.modules.admin.models import DqSeverity, IngestionRun, IngestionRunStatus
from deallens.modules.enrichment import service as enrichment_service
from deallens.modules.ingestion import quality
from deallens.modules.ingestion import service as ingestion_service
from deallens.modules.ingestion.adapters.base import Cursor, RawItem, SourceAdapter
from deallens.modules.ingestion.events import EventEmitter
from deallens.modules.ingestion.models import DataSource, RawRecord
from deallens.modules.ingestion.raw_store import BlobStore, persist_raw
from deallens.modules.ingestion.schemas import (
    ListingDelta,
    OwnershipDelta,
    PropertyDelta,
    TaxDelta,
)

_log = get_logger("ingestion.pipeline")


@dataclass(slots=True)
class IngestionCycleResult:
    run_id: UUID
    records_fetched: int = 0
    records_upserted: int = 0
    listings_new: int = 0
    stale_skipped: int = 0
    ownership_attached: int = 0
    tax_attached: int = 0
    high_watermark: datetime | None = None
    coverage_breached: bool = False
    errors: list[str] = field(default_factory=list)


async def _route_delta(
    db: AsyncSession,
    delta: object,
    *,
    source: DataSource,
    raw_record: RawRecord,
    emitter: EventEmitter | None,
    result: IngestionCycleResult,
    reference_year: int | None,
) -> None:
    """Send one normalized delta to the module that owns its table. Adding a new delta kind
    is a new branch here + its service — the pipeline is the only place routing lives."""
    if isinstance(delta, ListingDelta):
        upsert = await ingestion_service.ingest_listing(
            db, delta, source=source, raw_record=raw_record, emitter=emitter,
            reference_year=reference_year,
        )
        if upsert.stale:
            result.stale_skipped += 1
        else:
            result.records_upserted += 1
            if upsert.is_new:
                result.listings_new += 1
    elif isinstance(delta, OwnershipDelta):
        await enrichment_service.attach_ownership(
            db, delta, source_id=source.id, raw_record_id=raw_record.id
        )
        result.ownership_attached += 1
    elif isinstance(delta, TaxDelta):
        await enrichment_service.attach_tax(db, delta, source_id=source.id)
        result.tax_attached += 1
    elif isinstance(delta, PropertyDelta):
        await ingestion_service.ingest_property(db, delta, emitter=emitter)
        result.records_upserted += 1


async def run_ingestion_cycle(
    db: AsyncSession,
    adapter: SourceAdapter,
    *,
    source: DataSource,
    blob_store: BlobStore,
    emitter: EventEmitter | None = None,
    start_cursor: Cursor | None = None,
    market_id: UUID | None = None,
    max_pages: int = 1000,
    reference_year: int | None = None,
) -> IngestionCycleResult:
    """Run one poll of `adapter`: page until a pass returns nothing, persisting raw and
    routing every delta, then finalize the `ingestion_runs` row. Pagination is driven by the
    pipeline's own page counter (re-planning each pass) so the loop is correct for both a
    single paged resource (RESO) and a multi-shard plan (ATTOM per-ZIP)."""
    run = IngestionRun(
        source_id=source.id,
        market_id=market_id,
        status=IngestionRunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()

    result = IngestionCycleResult(run_id=run.id)
    since = (start_cursor or Cursor()).since
    page = (start_cursor or Cursor()).page
    source_reported: int | None = None

    while page < max_pages:
        cursor = Cursor(since=since, page=page)
        pass_items = 0
        more_pages = False  # any shard in this pass reported it has further pages
        for task in adapter.plan(cursor):
            batch = await adapter.fetch(task)
            if batch.next_cursor.page != 0:
                more_pages = True
            if batch.source_reported_total is not None:
                source_reported = (source_reported or 0) + batch.source_reported_total
            for item in batch.items:
                pass_items += 1
                result.records_fetched += 1
                if item.source_ts is not None and (
                    result.high_watermark is None or item.source_ts > result.high_watermark
                ):
                    result.high_watermark = item.source_ts
                try:
                    raw_record = await persist_raw(
                        db, blob_store, source_id=source.id, item=item,
                        fetched_at=batch.fetched_at,
                    )
                    for delta in adapter.normalize(item):
                        await _route_delta(
                            db, delta, source=source, raw_record=raw_record,
                            emitter=emitter, result=result, reference_year=reference_year,
                        )
                except Exception as exc:  # noqa: BLE001 — one poison record ≠ dead run (§9.5)
                    result.errors.append(f"{item.source_native_id}: {exc}")
                    _log.exception(
                        "ingest_item_failed", source=source.source_key,
                        native_id=item.source_native_id,
                    )
        # Stop when the pass produced nothing, or the adapter signalled every shard is
        # exhausted (a short/empty final page sets next_cursor.page back to 0).
        if pass_items == 0 or not more_pages:
            break
        page += 1

    await _finalize_run(
        db, run, result, source=source, source_reported=source_reported, market_id=market_id
    )
    return result


async def _finalize_run(
    db: AsyncSession,
    run: IngestionRun,
    result: IngestionCycleResult,
    *,
    source: DataSource,
    source_reported: int | None,
    market_id: UUID | None,
) -> None:
    now = datetime.now(UTC)
    coverage = quality.coverage_report(
        stored=result.records_fetched, source_reported=source_reported
    )
    result.coverage_breached = coverage.breached

    lag = None
    if result.high_watermark is not None:
        lag = int((now - result.high_watermark).total_seconds())

    run.finished_at = now
    run.records_fetched = result.records_fetched
    run.records_upserted = result.records_upserted
    run.lag_seconds = lag
    run.status = IngestionRunStatus.PARTIAL if result.errors else IngestionRunStatus.SUCCEEDED
    run.stats = {
        "listings_new": result.listings_new,
        "stale_skipped": result.stale_skipped,
        "ownership_attached": result.ownership_attached,
        "tax_attached": result.tax_attached,
        "high_watermark": result.high_watermark.isoformat() if result.high_watermark else None,
        "coverage": {
            "stored": coverage.stored,
            "source_reported": coverage.source_reported,
            "gap_pct": coverage.gap_pct,
            "breached": coverage.breached,
        },
        "error_count": len(result.errors),
    }
    if result.errors:
        run.error = "; ".join(result.errors[:20])
    if coverage.breached:
        _log.warning(
            "coverage_watchdog_breach", source=source.source_key,
            gap_pct=coverage.gap_pct, stored=coverage.stored, reported=coverage.source_reported,
        )
        await quality.record_dq_flags(
            db,
            subject_type="ingestion_run",
            subject_id=run.id,
            market_id=market_id,
            violations=[
                quality.DqViolation(
                    "coverage_gap",
                    DqSeverity.SERVE_WITH_FLAG,
                    {"gap_pct": coverage.gap_pct, "stored": coverage.stored},
                )
            ],
        )
    await db.flush()


async def replay_raw_record(
    db: AsyncSession,
    adapter: SourceAdapter,
    raw_record: RawRecord,
    *,
    blob_store: BlobStore,
    source: DataSource,
    emitter: EventEmitter | None = None,
) -> None:
    """Re-run normalize→write from stored raw bytes (NFR-05 replay). The path a normalization
    fix takes to re-process history: load the exact bytes we saved, rebuild the `RawItem`, and
    push it back through routing. Idempotent — versioned upserts make a replay of unchanged
    data a no-op, and a corrected mapper produces the corrected rows."""
    blob = await blob_store.get(raw_record.s3_key)
    item = RawItem.from_dict(json.loads(blob))
    result = IngestionCycleResult(run_id=raw_record.id)
    for delta in adapter.normalize(item):
        await _route_delta(
            db, delta, source=source, raw_record=raw_record, emitter=emitter,
            result=result, reference_year=None,
        )
