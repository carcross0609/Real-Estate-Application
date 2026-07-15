# ADR 0007: AI property image analysis system (`vision` module, no schema change)

## Context

The prompt asks for the AI property image analysis system: analyze listing photos to estimate
renovation difficulty, identify visible defects, estimate repair costs, detect renovation
opportunities, generate confidence scores, feed the investment score, and **allow future upgrades
to newer vision models**. This is 03-ANALYSIS-FRAMEWORKS **§27** (AI property analysis) plus its
§25.3 Group-C hand-off to scoring.

Like ADRs 0005/0006 this adds **no table**: `photo_analyses` / `property_conditions` and the
per-photo perception schemas (`RedFlag`, `PhotoQuality`, `PhotoFindings`, `ConditionCoverage`)
already shipped (migration 0004, commit `eb59653`). The design decision this ADR records is the
one the prompt emphasizes — the **model-upgrade seam** — plus how the deterministic half is kept
separate from the perception half (§27.1).

## Decision

Add the `vision` module logic as a pure-core / thin-provider / impure-seam split:

- **`provider.py` — the model-upgrade seam (§27.2/§13.4).** A `VisionProvider` Protocol
  (`analyze(PhotoInput) -> PhotoPerception`) plus a version registry. `PhotoPerception` is
  **model-agnostic by construction**: whether it came from Claude, a future model, or the shipped
  deterministic `HeuristicVisionProvider`, it's the same schema, and everything downstream
  (aggregation, rehab pricing, scoring factors, storage) is blind to which model produced it.
  Providers are keyed by `pipeline_version`, which `photo_analyses` stores per row — so a
  candidate model is diffed against the incumbent on the eval set before promotion (§27.6), and a
  swap is a config change, never a rewrite. **This is the "allow future upgrades" requirement,
  encoded as an interface rather than promised in prose.**
- **`aggregate.py` (pure, §27.3)** — median (not mean) grade per system, max-pooled red-flag
  severity, virtual-staging exclusion, explicit coverage gaps (`None`, never a faked average),
  renovation difficulty 1–5, and a confidence that moves with coverage/quality, **separate from
  the grade** (§27.5).
- **`rehab.py` (pure, §27.4)** — the deterministic money output: prices each graded system to a
  target grade off a versioned unit-cost table (cosmetic vs. major split), red flags as
  **inspection-contingent** ranges ("we estimate exposure, we don't diagnose"), low/mid/high bands
  that widen as confidence falls, plus the §26.2 contingency.
- **`factors.py` (pure, §25.3)** — the bridge into scoring: `condition_arbitrage` (the flip-
  goldmine factor), `renovation_difficulty`, `red_flag_severity`, `rehab_to_arv_ratio`. Kept
  separate so `vision` never imports `scoring` (acyclic module graph, §9.3).
- **`service.py` (impure seam)** — run the provider over a listing's photos, persist
  `photo_analyses`, aggregate → persist `property_conditions` + rehab, serve the condition bundle,
  and expose the scoring factors. Writes shared reference data, so it's an owner-session worker
  entrypoint. **Router**: `GET /v1/properties/{id}/condition`, `POST /v1/listings/{id}/vision/analyze`.

**§27.1 stance, enforced structurally.** Perception (the model) and pricing (deterministic code)
are in different files with a typed schema between them — the framework's "vision perceives, code
prices" isn't a comment, it's the module boundary. The rehab breakdown stores *pre-contingency*
component sums in `property_conditions`; the financial engine applies its own §26.2 contingency,
so contingency is never double-counted across the two engines.

**Conventions inherited unchanged:** Pydantic-at-boundary for all model output (§20: LLM output is
Pydantic-validated before persistence), pure-core unit tests + skip-guarded integration tests,
config-versioned unit-cost table (market-overridable, not a hardcode), single-service boundary.

## Consequences

- The property-page condition/rehab panel (S12/§21.9) has a backend; the pipeline runs end-to-end
  **today** on the deterministic provider (no model or API key required to demo or test), and the
  investment score can read the Group-C factors.
- **No migration, no metadata-test drift** — schema was already in place.
- **Deliberately deferred (tracked, not done here):**
  - **The concrete multimodal LLM provider (Claude et al.).** It registers behind the same
    `VisionProvider` interface but needs the versioned prompt rubric + visual exemplars in
    `vision/prompts/` (§27.2, eval-gated per §13.4) and live API wiring — the same way the
    ingestion feed adapters are deferred behind their base class. The `HeuristicVisionProvider`
    is the honest bootstrap until then.
  - **Photo embeddings / near-dup skip before LLM spend** (`listing_photos.embedding` exists,
    §10) and the per-property AI-budget ledger (§13.6) — cost controls that ride on top of the
    provider call, wired when the LLM provider lands.
  - **Remarks-NLP contradiction check** ("says renovated, photos say grade 2" → confidence ↓,
    §27.3) — a cross-check that needs the listing-remarks NLP, a separate model call.
  - **The eval harness + ship-gates** (§27.6: ≥80% within-one-grade agreement; rehab within ±25%)
    — the versioning seam (`pipeline_version` per `photo_analyses` row) is in place to support it.
- The financial engine's `RehabEstimate` (ADR 0006) is now populated from real perceived
  condition rather than a placeholder, closing the comps → vision → finance loop.
