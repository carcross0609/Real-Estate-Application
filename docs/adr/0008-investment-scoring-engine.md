# ADR 0008: AI investment scoring engine (`scoring` module, no schema change)

## Context

The prompt asks for the investment scoring engine: a weighted framework evaluating profitability,
risk, location, condition, appreciation, rental strength, liquidity, renovation complexity,
financing difficulty, and market conditions — producing overall scores, category scores,
confidence, recommendations, and a detailed explanation for **every** score. This is
03-ANALYSIS-FRAMEWORKS **§25** in full, and the capstone that consumes every engine built this
session (comps/valuation ADR 0005, financial ADR 0006, vision ADR 0007).

Like ADRs 0005–0007 it adds **no table**: `scores` / `score_factors` shipped in migration 0004
(commit `eb59653`). What this ADR records is the algorithm design and how §25's hard principles
are enforced structurally.

## Decision

Add the `scoring` module logic as a pure-core / impure-seam split:

- **`normalize.py` (pure, §25.2)** — the calibrated curves that map raw factor values onto a common
  0–100 axis: monotone piecewise-linear (with clamp = winsorization), U-shaped (DOM), and
  empirical percentile (the production path once a market×class×window cohort exists; falls back to
  the national-prior curve below a min cohort — the §25.2 cold-start ladder).
- **`weights.py` (config, §25.3/§25.4/§25.6)** — the factor registry, per-factor curves, within-
  group weights, the strategy group-weight profiles (the §25.4 table verbatim), the hard gates, and
  the grade thresholds. **All versioned config, not hardcodes** (§25.1 #5) — a future admin-tunable
  set (FR-034) merges over it without touching the algorithm.
- **`factors.py` (pure, §25.3)** — computes each named factor's `(raw, normalized, rationale)` from
  a `ScoringInputs` bundle. A factor with absent inputs returns `None` and drops out.
- **`score.py` (pure, §25.2/§25.4/§25.5)** — the pipeline: within-group weight → group weight →
  strategy score → hard gates → **max across strategies** → grade, risk, confidence, recommendation,
  category scores, and the explanation ledger.
- **`service.py` (impure seam)** — assembles `ScoringInputs` from the other engines' public
  interfaces (comps ARV/rent/appreciation, financial `ReturnMetrics`, vision condition factors,
  enrichment geo layers, market stats), runs the pipeline, and persists one `scores` row per
  strategy + an `overall` row, each with its `score_factors` ledger. **Router**:
  `GET/POST /v1/properties/{id}/score`.

Four §25 principles, enforced by construction:

1. **Explainable by construction (§25.1 #3).** The ledger is produced by the *same* loop that sums
   the score — every `FactorLedger` is `weight × normalized = contribution`, and the contributions
   reconstruct the raw score (a test asserts this). There is no second "explanation" code path to
   drift.
2. **Overall = max, not average (§25.4).** A property brilliant for exactly one strategy *is* a
   great deal; the winning strategy is named, never blurred into a mean.
3. **Confidence is separate from the score (§25.1 #4).** Thin data (few comps, no photos, wide
   estimator bands) lowers `confidence_score` and can block a Strong-Buy, but the score itself is
   never silently dragged down — a thin-data A-deal stays "A, low confidence."
4. **Hard gates show the cap (§25.6).** A severe foundation flag, uninsurable flood, sub-1.0 DSCR,
   or thin wholesale spread caps the score and writes an explicit `capped_by` into the ledger —
   the cap is shown, not hidden in the weights.

**Graceful degradation over §28 gaps.** The market-intelligence engine (§28) isn't built yet, so
L/M factors (school/crime/walkability/flood, rent growth, inventory, liquidity) are read
best-effort from `geo_layers`/`market_stats` and simply omitted where absent — the group reweights
and confidence drops, exactly the §25.1 #4 behavior. When §28 lands, those factors populate with no
change to the scoring algorithm.

## Consequences

- The full deal funnel is closed: **comps → financial + vision → score**. A property now yields an
  overall grade, per-strategy and per-category scores, risk, confidence, a recommendation, and a
  factor-by-factor explanation — the S12 property-page score panel has its backend. The `scores`
  rows also feed the existing search Top-25 / ranking path (ADR 0004).
- **No migration, no metadata-test drift** — schema was already in place.
- **Winner's-curse-aware (§25.8):** the risk score already penalizes comp dispersion (wide ARV
  band → higher risk → capped recommendation), and the conservative-quantile *ranking* input rides
  on the P10/P90 bands the comps engine stores. The rank-position outcome panel (§25.8 #3) is a
  measurement follow-on, not scoring logic.
- **Deliberately deferred (tracked, not done here):**
  - **Empirical percentile normalization at scale (§25.2)** — the curve + cohort seam exists;
    wiring the market×class×90-day cohort query is a follow-on once a market has ≥300 observations.
    v1 ships the calibrated national-prior curves.
  - **Drift monitoring / population-stability index (§25.7)** — the factor distributions are
    computable from `score_factors`; the weekly job is ops wiring.
  - **`ScoreUpdated` event fan-out** to alerts/search-index (§11.5) — the same outbox deferred
    since ADR 0003.
  - **The [F] strategy profiles** (STR/house-hack/multifamily/…) — added when their underwriting
    engines land (§26.8), behind the same weight-profile interface.
  - **Real motivated-seller NLP** — a keyword scan seeds `motivated_seller_signals`; the classifier
    is a follow-on.
