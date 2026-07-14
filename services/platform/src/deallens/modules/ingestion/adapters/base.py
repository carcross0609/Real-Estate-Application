"""The adapter contract every data source implements (§14.2).

    plan(cursor)   -> incremental fetch tasks   (what to pull, given where we left off)
    fetch(task)    -> raw, untransformed batch   (persisted to the raw store *before* any
                                                   transformation — replayability, NFR-05)
    normalize(raw) -> list[CanonicalDelta]        (map one raw record to the source-neutral
                                                   canonical model; unknown fields -> attrs)
    health()       -> SourceHealth                (lag, quota, error budget -> ops S40)

The design goal is that the pipeline (`pipeline.py`) never knows what a "RESO listing" or an
"ATTOM assessor row" looks like — it only ever sees `CanonicalDelta`s. That is what makes a
new source a self-contained module (NFR-12): implement these four methods, register the
adapter, done.

HTTP is injected via `RawTransport` so an adapter's `fetch` is unit-testable against canned
payloads (`FixtureTransport`) with zero network — the real transport (`HttpxTransport`) is
swapped in only in the worker.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from deallens.modules.ingestion.models import DataSourceTier
from deallens.modules.ingestion.schemas import (
    ListingDelta,
    OwnershipDelta,
    PropertyDelta,
    TaxDelta,
)

# The union the whole pipeline speaks. A single raw record may fan out to several
# (an ATTOM record yields a PropertyDelta + OwnershipDelta + TaxDelta); the writer routes
# each variant to its table by type, so adding a new delta kind is additive.
CanonicalDelta = ListingDelta | PropertyDelta | OwnershipDelta | TaxDelta


# --- Fetch primitives ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cursor:
    """Where an incremental pull left off. `since` is the source `ModificationTimestamp`
    high-watermark (the RESO delta cursor, §14.2); `token`/`page` carry source-specific
    pagination. A fresh source starts at `Cursor()` — the adapter's `plan` interprets an
    empty cursor as "full backfill window".
    """

    since: datetime | None = None
    token: str | None = None
    page: int = 0


@dataclass(frozen=True, slots=True)
class FetchTask:
    """One unit of work the poller hands to `fetch` — a page/time-window. Kept opaque
    (`params`) so each adapter defines its own fetch granularity without leaking it upward.
    """

    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawItem:
    """One untransformed source record. Persisted verbatim to the raw store *before*
    normalization, so a normalization bug never loses data and the pipeline replays from here
    (NFR-05/NFR-08). `source_ts` is lifted out of the payload up front because it is the
    version key the whole pipeline orders on (§9.5).

    Serialization is over the *whole item* (not just `payload`) so replay is adapter-agnostic:
    the stored blob round-trips back to an identical `RawItem` that any adapter's `normalize`
    can re-consume, without the adapter needing to re-derive `source_native_id`/`source_ts`.
    """

    record_type: str  # listing | photo | assessor | deed | tax | …
    source_native_id: str
    payload: Mapping[str, Any]
    source_ts: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "source_native_id": self.source_native_id,
            "source_ts": self.source_ts.isoformat() if self.source_ts else None,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RawItem:
        ts = data.get("source_ts")
        parsed = datetime.fromisoformat(ts) if isinstance(ts, str) else None
        return cls(
            record_type=str(data["record_type"]),
            source_native_id=str(data["source_native_id"]),
            payload=dict(data.get("payload") or {}),
            source_ts=parsed,
        )

    def canonical_bytes(self) -> bytes:
        """Stable serialization for content hashing + raw storage — sorted keys so a
        semantically identical record always hashes identically regardless of key order."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), default=str
        ).encode()


@dataclass(frozen=True, slots=True)
class RawBatch:
    """The result of one `fetch`. `next_cursor` advances the high-watermark;
    `source_reported_total` (when the source exposes a count) feeds the coverage watchdog
    (§14.4) — a >2% gap between what we stored and what the source says exists pages ops.
    """

    items: Sequence[RawItem]
    next_cursor: Cursor
    fetched_at: datetime
    source_reported_total: int | None = None


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """Adapter self-report for the ops dashboard (S40). `ok=False` with a `detail` is how an
    adapter signals "my quota is exhausted / the feed is 40 min stale" without the scheduler
    needing to understand the source's internals.
    """

    ok: bool = True
    lag_seconds: int | None = None
    quota_remaining: int | None = None
    error_budget_remaining: float | None = None
    detail: str | None = None


# --- Transport (injected HTTP) ---------------------------------------------------------


@runtime_checkable
class RawTransport(Protocol):
    """The only way an adapter reaches the network. Injected so `fetch` is testable with
    canned payloads and so ret/backoff/rate-limit policy lives in one place, not smeared
    across every adapter.
    """

    async def fetch_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...


class HttpxTransport:
    """Production transport over a shared `httpx.AsyncClient`. Instantiated by the worker
    with per-source auth headers already bound; `httpx` (already a dependency) handles
    connection pooling and timeouts.
    """

    def __init__(self, client: Any, *, default_headers: Mapping[str, str] | None = None) -> None:
        self._client = client
        self._default_headers = dict(default_headers or {})

    async def fetch_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        merged = {**self._default_headers, **(headers or {})}
        response = await self._client.get(url, params=dict(params or {}), headers=merged)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


class FixtureTransport:
    """Test/dev transport: returns canned JSON keyed by `url`, ignoring params, or by
    `(url, frozenset(params))` when a test needs to distinguish pages. Raises on an
    unregistered URL so a test can't accidentally pass by hitting a silent empty response.
    """

    def __init__(self, responses: Mapping[str, dict[str, Any]]) -> None:
        self._responses = dict(responses)

    async def fetch_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if params:
            keyed = f"{url}?{'&'.join(f'{k}={v}' for k, v in sorted(params.items()))}"
            if keyed in self._responses:
                return self._responses[keyed]
        if url not in self._responses:
            raise KeyError(f"FixtureTransport has no canned response for {url!r}")
        return self._responses[url]


# --- Adapter contract + config ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    """What the registry hands a factory to build a concrete adapter instance. `source_id`
    is the `data_sources` row PK (stamped onto every raw record and run); `transport` is the
    injected HTTP; `options` carries source-specific knobs (RESO resource name, page size,
    ATTOM property class filter, …) without widening this dataclass per source.
    """

    source_id: UUID
    transport: RawTransport
    base_url: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    """The four-method contract of §14.2. Concrete adapters are plain classes exposing
    `source_key`/`tier` attributes and these methods; `@register_adapter` wires them into the
    registry so the scheduler can build one by key.
    """

    source_key: str
    tier: DataSourceTier

    def plan(self, cursor: Cursor) -> Iterable[FetchTask]: ...

    async def fetch(self, task: FetchTask) -> RawBatch: ...

    def normalize(self, item: RawItem) -> list[CanonicalDelta]: ...

    def health(self) -> SourceHealth: ...
