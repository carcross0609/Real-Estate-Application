"""Raw payload store (§14.2) — the "persist raw *before* transformation" guarantee.

Every fetched record's untransformed bytes are written to object storage and recorded in
`raw_records` *before* normalization runs, so:
- a normalization bug never loses data (fix the mapper, replay from raw — NFR-05), and
- every normalized row can point back to its exact source bytes (NFR-08 auditability).

The blob backend is abstracted (`BlobStore`) so the pipeline is testable without S3
(`InMemoryBlobStore`) and dev runs against MinIO/local disk (`FilesystemBlobStore`). The
production `S3BlobStore` (boto3) is intentionally not added here — boto3 isn't a dependency
yet and the S3/MinIO wiring is worker infra (Phase 1); the protocol is the seam it slots
into. Keys are content-addressed so identical payloads collapse and replay is deterministic.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.modules.ingestion.adapters.base import RawItem
from deallens.modules.ingestion.models import RawRecord


@runtime_checkable
class BlobStore(Protocol):
    """Minimal object-storage surface the raw store needs. Real impl is S3+CloudFront (§10)."""

    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...


class InMemoryBlobStore:
    """Test/dev backend. Not durable — never for production raw storage."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self._data[key] = data

    async def get(self, key: str) -> bytes:
        return self._data[key]

    async def exists(self, key: str) -> bool:
        return key in self._data


class FilesystemBlobStore:
    """Local-disk backend for `make dev` without MinIO. Mirrors the S3 key layout under a
    root directory so a switch to S3 is a config change, not a code change.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        return self._root / key

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()


def _content_key(source_id: str, item: RawItem, payload_hash: bytes) -> str:
    """Content-addressed key: `raw/{source}/{record_type}/{hh}/{hash}.json`. The two-hex-char
    fan-out (`hh`) keeps any one prefix from holding millions of objects (S3 list/latency).
    """
    hex_hash = payload_hash.hex()
    return f"raw/{source_id}/{item.record_type}/{hex_hash[:2]}/{hex_hash}.json"


async def persist_raw(
    db: AsyncSession,
    blob_store: BlobStore,
    *,
    source_id: UUID,
    item: RawItem,
    fetched_at: datetime,
) -> RawRecord:
    """Store one fetched record's bytes + provenance row, returning the `RawRecord` the
    normalized listing/property will reference. Idempotent by content hash: re-fetching an
    identical payload (routine on a re-snapshot, §9.5) reuses the existing blob + row instead
    of piling up duplicates — replay still works and storage stays lean.
    """
    payload_bytes = item.canonical_bytes()
    payload_hash = hashlib.sha256(payload_bytes).digest()
    src_uuid = source_id

    existing = await db.execute(
        select(RawRecord).where(
            RawRecord.source_id == src_uuid, RawRecord.payload_hash == payload_hash
        )
    )
    found = existing.scalars().first()
    if found is not None:
        return found

    key = _content_key(str(source_id), item, payload_hash)
    if not await blob_store.exists(key):
        await blob_store.put(key, payload_bytes)

    record = RawRecord(
        source_id=src_uuid,
        s3_key=key,
        payload_hash=payload_hash,
        record_type=item.record_type,
        fetched_at=fetched_at,
    )
    db.add(record)
    await db.flush()
    return record
