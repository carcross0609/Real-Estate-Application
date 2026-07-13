# ADR 0002: Full platform database schema built ahead of strict phase order

## Context

02-TECHNICAL-DESIGN.md §11 specifies the complete database for the platform across six
entity groups (IDENTITY, GEO, PROPERTY, ANALYSIS, USER-WORK, OPS). Only the IDENTITY group
existed (migration 0001). 04-ROADMAP.md sequences the *features* that use these tables
across Phases 1–5 and states nothing in 01–03 should be built out of phase order without a
written reason; a few tables in particular (`pipeline_deals` → S20/Phase 3, Stripe-internal
billing tables → Phase 3, `scenarios`) belong to later phases.

Carson asked to design and implement the complete §11 schema now — all tables, indexes,
relationships, migrations, validation, and data models — and, when the phase-order mismatch
was flagged, chose the "complete §11 schema now" option over a Phase-1-only subset. This ADR
is the written reason the roadmap requires.

## Decision

- Implemented the entire §11 schema as ORM models (one `models.py` per bounded context, per
  §9.3: `markets`, `ingestion`, `enrichment`, `vision`, `engine`, `scoring`, `alerts`,
  `reports`, `admin`) plus the migration chain **0002–0005**, grouped by dependency layer
  (GEO → PROPERTY → ANALYSIS/SCORING → USER-WORK/OPS). 34 new tables; 42 total with identity.
- **Conventions inherited from 0001 exactly:** UUIDv7 PKs, `created_at`/`updated_at`,
  money as `numeric(14,2)` / rates as `numeric(9,6)` / confidences as `numeric(4,3)`, the
  least-privilege `deallens_app` role (DML on new tables inherited from 0001's `ALTER
  DEFAULT PRIVILEGES`), and RLS policies reading `current_setting('app.org_id')`.
- **Tenant scoping (§11.1):** user-owned tables carry `org_id` + a `tenant_isolation` RLS
  policy (`search_areas`, `scenarios`, `buy_boxes`, `watchlist_items`, `notifications`,
  `reports`, `share_links`, `notes`, `pipeline_deals`, `feedback_labels`). Shared licensed
  data (properties, listings, scores, market stats, …) is deliberately **not** org-scoped —
  every tenant sees the same dataset; anti-exfiltration is an API/rate-limit concern (§15),
  not row scoping. `comp_sets`/`comp_members` use an OR-NULL policy (shared when
  system-generated, org-owned when a user pins/excludes comps) to implement §11.3's
  `created_by (system|user)`.
- **PostGIS + pgvector (§10):** migration 0002 enables both extensions. `geometry(*,4326)`
  columns (properties, markets, search_areas, geo_layers) get GiST indexes; the
  `listing_photos.embedding vector(1024)` column gets an HNSW cosine index. New Python deps
  `geoalchemy2` + `pgvector` added to pyproject.
- **Partitioning (§11.6):** `listing_events`, `notifications`, `ai_calls` are monthly range
  partitions (like `audit_log` in 0001), with a DEFAULT partition and five bootstrap months.
- **Strategy breadth handled by data, not tables:** STR/MF/land/commercial are values of the
  shared `strategy` enum + JSONB output blocks (`analyses.outputs`, `scenarios.outputs`), not
  speculative per-strategy tables — so the "future expansion" surface is a few enum values and
  flexible `attrs`/`params`/`filters` JSONB, validated by Pydantic models (`schemas.py`).
- **One divergence from 0001's migration style, deliberately:** new enum types are declared
  `create_type=False` and created once via an explicit `.create(checkfirst=True)`, so
  `alembic upgrade --sql` emits each `CREATE TYPE` exactly once (0001 double-emits offline;
  harmless online but not directly runnable) and cross-migration enum reuse (`geo_level`
  0002→0003, `strategy` 0004→0005, `alert_latency` from 0001) is safe.

## Consequences

- Phase 1+ feature work builds against a stable schema — no per-phase migrations for tables
  that were always coming; `expand→contract` discipline (§17) still governs future *changes*.
- A few tables (`pipeline_deals`, `scenarios`, `share_links` white-label fields) exist before
  their feature phase. They are inert until wired up; presence costs nothing and avoids a
  later migration. This does **not** pull forward the Stripe-internal billing tables, which
  remain deferred per ADR 0001 (`Subscription`/`Entitlement` already live in identity's
  schema).
- **Deferred hardening (tracked, not done here):** the app role `deallens_app` inherits full
  DML on shared reference tables via 0001's default privileges. The intended end state is a
  dedicated `deallens_worker` role that writes shared data while `deallens_app` is read-only
  on it. Deferred because the worker fleet/role isn't built yet (Phase 1); revisit when it is.
- **Verification gap to close:** the ORM (mapper config), `mypy --strict`, `ruff`, the
  metadata unit guards, and Alembic offline SQL render all pass. A live-DB apply + the RLS
  integration suite (`tests/integration`) require Postgres+PostGIS+pgvector; they run under
  `make dev && make api-migrate && pytest tests/integration` and auto-skip otherwise.
- `scenarios`/`pipeline_deals`/`notes` are schema-colocated in `engine`/`reports` even though
  their write-services will differ — the same colocation pattern ADR 0001 used for
  `Subscription`/`Entitlement` in `identity`.
