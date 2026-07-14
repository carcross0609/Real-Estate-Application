# ADR 0003: Property ingestion pipeline built ahead of strict phase order

## Context

02-TECHNICAL-DESIGN.md §9.4/§9.5/§14 specifies the ingestion pipeline: source adapters, a
raw payload store, normalization, entity resolution, versioned upserts, a listing-event
change log, DQ rules, a coverage watchdog, scheduling, and public-records enrichment. The
§11 database for all of this already exists (ADR 0002) but had **no business logic** — the
memory/handoff explicitly listed "MLS/data licensing + source adapters, … enrichment" as
schema-only.

04-ROADMAP.md sequences this work: FR-001–005/007 (ingestion + enrichment) are **Phase 1**
(milestone M1.1, "live feed → normalized DB, coverage watchdog green"), and FR-006 (entity
resolution + relist chains) is **Phase 2**. We are nominally still in Phase 0. The roadmap
states nothing in 01–03 should be built out of phase order without a written reason, and the
project rule (confirmed by Carson 2026-07-13) is to *flag phase mismatches rather than
silently pull work forward*.

Carson directly and specifically requested this build: "Build the property ingestion
system. Implement integrations with approved property data sources and public records,
normalize incoming data, remove duplicates, maintain historical changes, schedule updates,
and automatically enrich listings … Design the ingestion pipeline to be modular and easily
expandable." "Remove duplicates" and "maintain historical changes" are the FR-006 (Phase 2)
surface; the rest is Phase 1 (M1.1). This ADR is the written reason the roadmap requires —
the same disposition ADR 0002 used when the full schema was pulled forward on request.

This did **not** pull forward anything gated on external dependencies that don't exist yet:
no live MLS/ATTOM credentials, no S3/MinIO bucket, no Celery app, no boto3. Those are worker
infra + Phase-0 licensing deliverables. The pipeline is built to slot into them behind
seams, not to assume them.

## Decision

Implemented the full ingestion pipeline as business logic in
`services/platform/src/deallens/modules/ingestion/` (+ `enrichment/service.py`), against the
existing §11 schema — **no new migrations, no schema changes**.

- **Adapter framework (§14.2), pluggable by design (NFR-12).** One `SourceAdapter` protocol
  (`plan/fetch/normalize/health`) + a `registry` keyed by `source_key`. Concrete adapters:
  `ResoAdapter` (Tier A MLS — registered for `mls_grid`/`trestle`/`bridge`, the three RESO
  Web API aggregators) and `AttomAdapter` (Tier B public records → property + ownership +
  tax). A new source is a new module + one `@register_adapter` decorator; nothing downstream
  changes. HTTP is injected via a `RawTransport` seam (`HttpxTransport` in prod,
  `FixtureTransport` in tests), so adapters are unit-tested with zero network.
- **Canonical delta contract.** Every adapter emits source-neutral Pydantic deltas
  (`ListingDelta`/`PropertyDelta`/`PhotoDelta`/`OwnershipDelta`/`TaxDelta`); the pipeline
  speaks only these. Unknown source fields land in `attrs`, never dropped (§14.2).
- **Normalization (§14.3).** Deterministic USPS Pub-28 address normalizer + an `address_key`
  dedupe key, pure-Python and dependency-free (libpostal is named as the production swap
  behind the same `normalize_address` signature; it can't run in CI and is overkill for the
  structured feed components we get).
- **Raw store first (§14.2, NFR-05).** `persist_raw` writes untransformed bytes to a
  content-addressed blob (`BlobStore`: `InMemory`/`Filesystem` now, S3 the documented prod
  impl) + a `raw_records` row *before* normalization; `pipeline.replay_raw_record` re-runs
  normalize→write from stored bytes, so a mapper fix replays history without re-hitting a
  feed.
- **Entity resolution / dedup (FR-006).** APN+FIPS → normalized-address-key → geospatial+
  attribute-similarity ladder → create; low-confidence creates are DQ-flagged but still
  served (resolution never blocks ingestion). This is the "remove duplicates" guarantee: one
  canonical property across relistings and across MLS+ATTOM.
- **Versioned upsert + change log (§9.5, §14.4, FR-003).** Every write is conditional on the
  source `ModificationTimestamp`, so out-of-order/duplicate feed events no-op by
  construction; field-level diffs produce immutable `listing_events` (the price/status
  history + re-score triggers); photos diff by content hash; a new listing on an existing
  property emits `RELISTED_LINK` so DOM/price-history span the true time-on-market.
- **Quality + coverage watchdog (§14.4).** Pure DQ rules (plausibility bounds, unit sanity,
  missing geocode) → severity-typed `dq_flags`; the >2% coverage-gap watchdog writes the run
  stats + a flag.
- **Enrichment (§9.4 step 2, FR-005).** `enrichment.service` attaches assessor tax + deed
  ownership (with derived absentee/hold-time signals) and geo layers to the resolved parcel.
  Dependency points **into** ingestion (`resolve_or_create_property`), keeping the module
  graph acyclic (§9.3).
- **Events + scheduling.** Ingestion emits `ListingUpserted`/`ListingChanged`/
  `PropertyResolved`/`PhotosChanged` behind an `EventEmitter` seam (the §9.4 outbox — a
  Redis/outbox transport swaps in without touching the writer). `scheduler.py` holds per-tier
  cadence policy + a `poll_source` entrypoint; the Celery-beat trigger wires to it later,
  exactly as the billing stub (ADR 0001) deferred the Stripe trigger.

**Conventions inherited unchanged:** `pg_enum` everywhere, UUIDv7, Pydantic-at-boundaries
(no loose dicts cross a module boundary), the §19 rule that modules import each other only
via `service.py`, and the two-tier test layout (pure unit + auto-skipping real-PG
integration).

## Consequences

- **Phase 1 M1.1 is effectively delivered** (feed → normalized DB → change log → coverage
  watchdog), and the Phase-2 FR-006 entity-resolution/relist surface is delivered early.
  Verified: `ruff check .` clean, `mypy --strict src` clean, **135 tests pass** (38 new: unit
  coverage of normalize/adapters/diff/quality/registry/events/scheduler + a real-Postgres
  integration suite proving versioned upserts, cross-source APN dedup, relist chaining, the
  full adapter→raw→write cycle, and replay idempotency).
- **Deliberately deferred (tracked, not done here):**
  - Live source credentials + the actual S3/MinIO bucket + a Celery app/beat schedule —
    Phase-0 licensing + Phase-1 worker infra. The pipeline runs today only against injected
    transports/blob stores (which is how the tests drive it end-to-end).
  - libpostal (production address normalizer) behind the existing `normalize_address` seam.
  - A functional index on `(address_norm->>'zip')` / `attrs->>'address_key'` for the
    deterministic address match at scale (§11.6 follow-up); fine at Phase-1 single-market
    volume, noted in `resolution.py`.
  - The probabilistic-resolution DQ review queue UI (S42) and the `deallens_worker` write
    role (ADR 0002's deferred hardening) — unchanged.
- **No schema drift:** because §11 was already complete (ADR 0002), this added zero tables and
  zero migrations; `expand→contract` discipline still governs any future change.
- The vision/engine/scoring/alerts consumers of `ListingUpserted`/`ListingChanged` remain
  unbuilt; the events are emitted and logged today, ready for those subscribers.
