# ADR 0005: Comparable-sales & rental analysis engine (`engine` module, no schema change)

## Context

The prompt asks for the comparable sales and rental analysis engine: identify appropriate
comps, estimate ARV, estimate market rent, compute appreciation, evaluate confidence, and keep
the numbers improving as new market data lands. This is the first slice of the product's
intellectual core — 03-ANALYSIS-FRAMEWORKS **§26.1** (value & income estimators) and **§28.2–
§28.4** (market appreciation selection) — and it is the input every downstream engine consumes:
the financial pro-forma (§26.2–§26.8) multiplies against ARV/rent, and scoring's F/M factor
groups (§25.3) read them.

Unlike ADRs 0001–0003, this is **not** ahead of phase order in the schema sense, and unlike
ADR 0004 it adds **no table**: the ANALYSIS group — `comp_sets`, `comp_members`, `valuations`
(plus `analyses`/`scenarios`) and their ORM mirror + RLS — already shipped in
`0004_analysis_schema.py` and `engine/models.py` (commit `eb59653`). What was missing was the
*logic*. So this ADR exists for neither reason in §20's checklist (out-of-phase / schema
extension); it documents a **design decision inside an existing module boundary**: how the
estimator math is factored, and the three product-critical stances (winner's curse, honest
fallback, continuous recompute) that are easy to get quietly wrong.

## Decision

Implement the `engine` module as a **pure-core / impure-seam** split, mirroring `search`
(`query` compiles, `service` executes) so the entire intellectual core is unit-tested with no
database:

- **`comps.py` (pure)** — similarity scoring (a weighted blend of size, distance, recency,
  rooms, vintage; missing sub-scores drop out and their weight redistributes, so a comp is
  never punished for a field the *subject* lacks) and the §26.1 line-item adjustment grid
  (each feature emits a signed $ line only when both sides are known — never a phantom $0 that
  reads as "verified equal"). Also the fallback-ladder *policy* (`next_fallback_stage`).
- **`valuation.py` (pure)** — the estimator: a **similarity-weighted quantile** for the
  P10/P50/P90 band (so a dispersed set widens the band asymmetrically instead of hiding
  disagreement behind a confident midpoint — §21.5), calibrated confidence (count × dispersion
  × fallback-stage penalty × cross-check dent), the model-prior fallback, and the rental
  list-to-effective / RentCast cross-check corrections (§26.1).
- **`appreciation.py` (pure)** — §28.2 honest-geography selection: pick the *finest reliable*
  level that carries an appreciation metric, prefer the 3-yr annualized rate over YoY, newest
  period wins, and **label the level** — a metro rate carried to a parcel is labeled metro,
  never faked to ZIP precision — with a level-appropriate confidence.
- **`recompute.py` (pure)** — the staleness *decision* (`needs_recompute`): fires on no prior
  valuation, an estimator version bump, a comp event newer than the last run (`>`, not `>=`, so
  an idempotent re-trigger doesn't loop), or age past the refresh window — and **accumulates
  every reason** so the §4.3 drift audit can tell "refreshed because a real comp landed" from
  "refreshed because it aged out."
- **`query.py` (pure compiler)** — the PostGIS candidate reads (radius / recency / size-band /
  class, sold-vs-rental listing shape, `DISTINCT ON (property_id)` = one comp per house),
  unit-tested by rendering to SQL text.
- **`service.py` (impure seam)** — orchestration + persistence over the above: ladder-select →
  aggregate → fallback → persist the comp set, members, and a versioned `valuations` row so any
  number renders its own derivation ("show the math", §26.9).

Three stances made explicit because they are the failure modes that quietly lose users money:

1. **Winner's-curse-ready (§25.8).** Estimates are stored as full P10/P50/P90 bands, not
   points; ranking downstream reads the conservative quantile while the property page shows P50
   + band. Two properties with equal midpoints but different band widths must not rank equally.
   The band is produced here; the ranking quantile is scoring's to apply.
2. **Honest fallback, never a blank (§26.1, §27.5).** When comps are thin the ladder widens
   recency → widens radius → drops to a market-$/sqft model prior (wide band, low confidence),
   and only returns "unavailable" when even the prior has no inputs — an honest gap, never a
   fabricated number.
3. **Continuous improvement is event-driven, not a cron guess (§9.5).** `recompute.needs_recompute`
   is the trigger policy; a nearby sale closing (`query.newest_comp_event_select`) or a
   `market_stats` roll is what calls in. Kept pure so the policy is tested without a worker.

**Tenancy.** System valuations are shared (`org_id IS NULL`) and written on the owner-role
`system_session` (RLS forbids the app role writing a NULL-org row — migration 0004). User
pin/exclude (FR-015) instead clones the set into an **org-owned** `comp_set` on the actor's RLS
session and returns a *transient* estimate — one tenant's overrides can never move another's
shared number. This is why `apply_comp_edits` and `value_property` take different session types.

**Conventions inherited unchanged:** Decimal-end-to-end money (§20), Pydantic at the boundary,
pure-core unit tests + skip-guarded integration tests, config-versioned constants
(`CompSelectionParams`/`AdjustmentConfig`, market-table overridable) — no hardcodes.

## Consequences

- ARV, as-is, and market-rent estimates plus market appreciation now exist per property, each
  self-describing, and are read by `GET /v1/properties/{id}/valuation`. Recompute and comp-edit
  endpoints back the property-page analyzer (S12/§21.5).
- **No migration, no metadata-test drift** — the schema was already in place; this change is
  logic + tests + router wiring only.
- **Deliberately deferred (tracked, not done here):**
  - **STR revenue (§26.1 [F])** — needs licensed ADR/occupancy data; `rent_str` valuation kind
    exists but the estimator is LTR-only for now, flagged in the code.
  - The **Celery-beat wiring** that calls `recompute_if_stale` on ingestion/enrichment events —
    the policy and entrypoint exist; the worker trigger is the same infra wire-up deferred since
    ADR 0003 (the ingestion outbox).
  - **RentCast cross-check ingestion** — the divergence math and confidence penalty are in
    `valuation.py`; the actual Tier-C fetch that supplies the external number is an adapter, not
    engine logic.
  - **Appreciation feed** — selection is implemented against `market_stats`; the job that
    computes `appreciation_3yr_annualized` from sold history (§28.6 backfill) lands with the
    market-intelligence engine.
- The financial engine (§26.2–§26.8) and scoring (§25) now have a stable, versioned input
  contract (`PropertyValuationOut`) to build against.
