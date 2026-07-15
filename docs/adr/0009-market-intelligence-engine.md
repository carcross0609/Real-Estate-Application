# ADR 0009: Market intelligence engine (`markets` module, no schema change)

## Context

The prompt asks for the market intelligence engine: analyze neighborhoods via demographics,
appreciation, crime, schools, employers, transportation, flood, insurance, zoning, development,
rental demand, and the other §28 indicators, and produce detailed market reports and market
scores. This is 03-ANALYSIS-FRAMEWORKS **§28**, and the last engine feeding the investment score's
L/M factor groups (§25.3).

Like ADRs 0005–0008 it adds **no table**: `markets` / `market_stats` shipped in migration 0002
(GEO schema). `market_stats` is deliberately generic — `(geo_level, geo_id, metric, period, value,
source)` — so the ~30-metric registry of §28.3 extends without migrations. What this ADR records
is the metric registry, the geo-hierarchy honesty rule, the derived-index math, and the binding
fair-housing guardrail.

## Decision

Add the `markets` logic as a pure-core / impure-seam split:

- **`metrics.py` (pure, §28.2/§28.3/§28.5)** — the metric registry (domain, direction,
  protected-class flag), geo-hierarchy resolution (`resolve_finest`: pick the finest reliable
  level, **label it honestly** — a county crime figure is carried as county-level, never upsampled
  to ZIP), and the **fair-housing guardrail** (`assert_fair_housing`).
- **`indices.py` (pure, §28.4)** — the four derived indices (Market Momentum, Rental Demand,
  Liquidity, Neighborhood Quality) as calibrated 0–100 composites, plus the single market score.
  Every index calls `assert_fair_housing` on its component set, so a protected-class variable
  cannot reach an index **by construction**.
- **`service.py` (impure seam)** — read a market's `market_stats`, resolve each metric to its
  finest geography, compute indices + score, persist the derived values back as metro-level
  `market_stats` (so scoring and the market page read them like any ingested metric), and serve the
  market report + a compact `MarketContext`. **Router**: `GET /v1/markets/{id}/report`,
  `POST .../report/recompute`.

**Fair-housing guardrail (§28.5, NFR-07, binding).** Demographic *composition* (race, ethnicity,
religion, national origin, familial status) is never an index input or a displayed quality signal.
This is enforced two ways: an explicit `protected_class` flag in the registry, and a substring
denylist catching a feed that invents a new protected key. `assert_fair_housing` raises a
`FairHousingViolation` (a hard failure, not a degrade) and runs on every index computation; a unit
test asserts no index's declared components are protected, and the ADR records that scoring-config
CI enforces the same denylist. Economic demographics (population growth, income, household
formation) are explicitly *not* protected-class composition and remain valid economic signals.

**Honest geography (§28.2).** A metric carries the level it was *measured* at. The market report's
`MetricValueOut` includes `geo_level`, so the UI shows "crime (county)" rather than faking parcel
precision — the same discipline as the comps engine's appreciation selection (ADR 0005).

**The loop closes.** The engine writes its Liquidity index back as the `market_liquidity` metric —
exactly the key the scoring engine (ADR 0008) already reads for its M-group liquidity factor — so
building §28 populates scoring's L/M factors with **no change to the scoring algorithm**, precisely
the graceful-degradation seam ADR 0008 anticipated.

## Consequences

- Market reports (indices + score + labeled metrics + a data-completeness honesty signal) and the
  `MarketContext` snapshot exist; the S33 market page and the scoring L/M factors have their
  backend. All five analysis engines (comps, financial, vision, scoring, market) are now built.
- **No migration, no metadata-test drift** — schema was already in place.
- **Deliberately deferred (tracked, not done here):**
  - **The metric ingestion adapters (§28.3, §14.1)** — Census/BLS/FBI/FEMA/GreatSchools/Walk Score
    feeds that *populate* `market_stats`. The engine consumes whatever is present and reports
    `data_completeness`; the adapters are ingestion work behind their base class (like the vision
    LLM provider and the financing-rate feed).
  - **Within-metro + national percentile at scale (§28.4)** — the `national` cohort seam exists;
    the calibrated national-prior curve stands in until a cohort of market scores is accumulated
    (the §28.6 baseline-calibration path).
  - **Curated per-market tables (§28.3)** — employers, planned developments, STR/rent-control
    class: admin-maintained per launch market (S43), tracked as automation debt.
  - **24-month backfill + percentile-curve fit before a market goes live (§28.6)** — the
    onboarding runbook that the `MarketStatus.ONBOARDING` lifecycle already models.
