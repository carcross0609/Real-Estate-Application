# ADR 0004: Advanced search module + `saved_searches` table (schema extension)

## Context

Prompt 7 asks for the advanced property search engine: every filter, sort, the map view,
geographic search, saved searches, and user preferences from the PRD, fast at millions of
properties. This is squarely **Phase 1** (S11 Market Explorer + FR-001 are in the Phase-1
screen set of 04-ROADMAP §Phase 1; the ingested `properties`/`listings` and `scores` it reads
already exist from commits `51c81e6`/`eb59653`). So — unlike ADRs 0001–0003 — this is *not*
out of phase order. This ADR exists for the second reason the coding standard (02 §20)
requires one: the work **extends** the §11 data model with a table the blueprint did not name.

The blueprint's §11.2 USER-WORK group lists `buy_boxes` ("saved named filter+strategy+
assumption sets that **drive alerts**") but no separate saved-search entity. The PRD, however,
lists two distinct surfaces: **S11** "saved-view management" inside the Market Explorer, and
**S18** "Buy Boxes" (filters **+ alert channel/speed**). Collapsing explorer views into
`buy_boxes` would force every saved view to carry alerting semantics (channels, latency,
`ScoreUpdated` fan-out indexing) it doesn't want, and would pollute the alert matcher's
`ix_buy_boxes_market_active` working set with non-alerting rows. They are different lifecycles.

## Decision

Add a `search` module (`services/platform/src/deallens/modules/search/`) and **one** new table,
`saved_searches`, in migration `0006_search_schema.py`.

- **`saved_searches`** — org-scoped, RLS, soft-deleted (product-visible, §11.1), same tenancy
  pattern as `buy_boxes`/`search_areas`. Columns: `name`, optional `market_id`/`search_area_id`
  binding, `filters jsonb` (the `PropertyFilters` contract), `strategy`, `sort` text,
  `map_view jsonb` (viewport to restore), `is_default bool`. It is a *view*, not an alert:
  no channels, no latency, not indexed for the matcher. A user promotes a saved search to a
  buy box when they want alerts — that is a copy across two intentionally separate tables.
- **The read path is keyset, not offset.** `query.py` compiles `PropertyFilters` + `SortSpec`
  + a resolved geometry into one SQLAlchemy `Select` over `properties` ⋈ current-listing ⋈
  current-score, ordered by the sort key with a `properties.id` (UUIDv7, time-ordered)
  tie-break, paginated by an opaque base64 cursor of `(last_sort_value, last_id)`. Keyset +
  the composite indexes below is what holds NFR-01 (p95 ≤ 400 ms) at millions of rows — an
  `OFFSET` deep-pages linearly and would violate it. The compiler is pure (no I/O): it is
  unit-tested by compiling to SQL text, no database required.
- **Current-listing / current-score projection.** A property has many listings and many score
  rows over time; search reads the *latest* of each via `DISTINCT ON (property_id) … ORDER BY
  computed_at DESC` subqueries. This is written behind `query.current_listing_subq` /
  `current_score_subq` so it can later be swapped for a denormalized `property_search` read
  model (materialized on `ScoreUpdated`, the §11.5 evolution) without touching filter/sort code.
- **Map tiles reuse §12.2's exfiltration hardening verbatim.** `/v1/tiles/{z}/{x}/{y}` returns
  only `(property_id, lon, lat, score_bucket, price_bucket)` — never full attributes — and
  clusters below a zoom threshold by snapping to a per-zoom grid. Tile math (tile→bbox, grid
  snapping) is pure and in `geo.py`, unit-tested with no DB.
- **Geographic search is one resolver.** `geo.resolve_geometry` turns any of {saved area,
  drawn GeoJSON polygon, viewport bbox, radius, ZIP/city/county via `market_stats` geo ids}
  into a single WKT predicate (`ST_Intersects`, or `ST_DWithin` for radius). Filters compose
  with the geometry, never replace it.
- **User search preferences** (default strategy, default sort, default market, last map view)
  live in the existing `users.preferences` JSONB under a namespaced `"search"` key — no new
  column. The search module reads/writes it only through a new
  `identity.service.merge_user_preferences()` so the module-boundary rule (§19: cross-module
  access via `service.py` only) holds; `identity` still owns the `users` row.
- **Tier limits (FR-001).** Search-area create is gated on `entitlements.markets_limit`
  (Basic 1 / Pro 5 / Team 25) counted over non-deleted areas, enforced in `search.service`.

**Conventions inherited unchanged:** `pg_enum`, UUIDv7, Pydantic-at-boundaries, RLS mirror in
the migration (not the ORM), the two-tier test layout.

## Consequences

- The Market Explorer (S11) backend — filters, sort, geographic search, tiles, saved views —
  and the Markets Manager (S33) search-area CRUD are delivered. The `GET /v1/properties`
  and `GET /v1/tiles/...` contracts from 02 §12.2 now exist.
- **One table added** (`saved_searches`); `EXPECTED_TABLES`/`ORG_SCOPED_TABLES` in the schema
  metadata test are updated in the same change (a deliberate, reviewed drift, per §20).
- **Deliberately deferred (tracked, not done here):**
  - The denormalized `property_search` materialized read model + `ScoreUpdated` refresh
    (§11.5 #1/#2). The live projection is correct and index-backed for Phase-1 single-market
    volume; the subquery seams mark exactly where it slots in.
  - Redis tile cache keyed `(z/x/y, filter-hash)` and per-user tile-rate limits (§12.2) — the
    payload is already minimized; caching is an infra wire-up like the ingestion outbox.
  - pgvector photo-similarity "more like this" search (§11.5 #5) — a search *mode* on top of
    this skeleton, not part of the filter/sort surface.
  - Bulk address-list screening (FR-018, Phase 3) — a different entry point (address resolve),
    not the area/filter search this delivers.
- Buy-box `filters` and saved-search `filters` share the **same** `PropertyFilters` Pydantic
  contract, so the alert matcher (`alerts`) can compile the identical predicates when it is
  built — the two tables diverge in lifecycle, not in filter language.
