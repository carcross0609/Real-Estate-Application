"""Ingestion polling tasks — the "continuously monitor new listings" loop (§14.2).

`poll_due_sources` is the once-a-minute beat tick: it reads every registered `data_sources`
row, asks the pure cadence policy (`ingestion.scheduler`) whether each is due at its licensed
interval, and enqueues a `poll_source` for the ones that are. One cheap tick drives every feed
at its own rate — no beat entry per source, and the license cap (`license_policy.cadence_seconds`)
is honored per source.

`poll_source` binds the source's transport + a blob store and runs one incremental cycle behind
a `CeleryEmitter`, so each new/changed listing fans out to the analysis→score→match chain. A
source with no configured `base_url` (the dev default — feeds are licensed per market at
onboarding, S43) is skipped with a log, never a failure: the scheduling path is exercised even
before any real feed is wired.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.logging import get_logger
from deallens.modules.admin.models import IngestionRun, IngestionRunStatus
from deallens.modules.ingestion import scheduler
from deallens.modules.ingestion import service as ingestion_service
from deallens.modules.ingestion.adapters.base import HttpxTransport
from deallens.modules.ingestion.events import CompositeEmitter, LoggingEmitter
from deallens.modules.ingestion.models import DataSource
from deallens.modules.ingestion.raw_store import FilesystemBlobStore
from deallens.worker.app import celery_app
from deallens.worker.emitter import CeleryEmitter
from deallens.worker.runtime import run_async, task_session

_log = get_logger("worker.tasks.ingestion")


async def _last_run_started(db: AsyncSession, source_id: UUID) -> datetime | None:
    """The start time of the most recent completed run for a source — the clock the cadence
    policy measures the next poll from."""
    row = await db.execute(
        select(IngestionRun.started_at)
        .where(
            IngestionRun.source_id == source_id,
            IngestionRun.status.in_(
                (IngestionRunStatus.SUCCEEDED, IngestionRunStatus.PARTIAL)
            ),
        )
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    )
    started: datetime | None = row.scalar_one_or_none()
    return started


@celery_app.task(name="deallens.ingestion.poll_due_sources")
def poll_due_sources() -> dict[str, object]:
    """Beat tick: enqueue a poll for every source whose license cadence is due."""
    async def _run() -> dict[str, object]:
        now = datetime.now(UTC)
        due: list[str] = []
        async with task_session() as db:
            sources = (await db.execute(select(DataSource))).scalars().all()
            for source in sources:
                override = (source.license_policy or {}).get("cadence_seconds")
                interval = scheduler.cadence_for(
                    source.tier, override_seconds=override if isinstance(override, int) else None
                )
                last = await _last_run_started(db, source.id)
                if scheduler.next_run_due(interval, last, now):
                    celery_app.send_task(
                        "deallens.ingestion.poll_source",
                        kwargs={"source_key": source.source_key},
                    )
                    due.append(source.source_key)
        return {"enqueued": due, "count": len(due)}

    return run_async(_run())


@celery_app.task(
    name="deallens.ingestion.poll_source",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def poll_source(source_key: str) -> dict[str, object]:
    """Run one incremental poll of a source, fanning changes out via `CeleryEmitter`."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            source = await ingestion_service.get_data_source(db, source_key)
            policy = source.license_policy or {}
            base_url = policy.get("base_url")
            if not base_url:
                _log.info("source_not_configured", source_key=source_key)
                return {"source_key": source_key, "skipped": "no_base_url"}

            import httpx

            raw_headers = policy.get("auth_headers")
            headers = raw_headers if isinstance(raw_headers, dict) else {}
            transport = HttpxTransport(httpx.AsyncClient(timeout=30.0), default_headers=headers)
            blob_store = FilesystemBlobStore(Path(tempfile.gettempdir()) / "deallens-raw")
            emitter = CompositeEmitter(LoggingEmitter(), CeleryEmitter(celery_app))

            result = await scheduler.poll_source(
                db, source_key=source_key, transport=transport, blob_store=blob_store,
                base_url=str(base_url), emitter=emitter,
            )
            return {
                "source_key": source_key,
                "run_id": str(result.run_id),
                "fetched": result.records_fetched,
                "upserted": result.records_upserted,
                "new_listings": result.listings_new,
            }

    return run_async(_run())
