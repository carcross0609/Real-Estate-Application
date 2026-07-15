# ADR 0006: Financial analysis engine (`engine` finance/strategies, no schema change)

## Context

The prompt asks for the financial analysis engine: every investment calculation in the PRD —
ARV, rehab, holding, financing scenarios, ROI, cap rate, NOI, CoC, DSCR, cash flow, break-even,
and profitability projections — each **transparent and explainable**. This is
03-ANALYSIS-FRAMEWORKS **§26.2–§26.8**, the deterministic layer that turns the comps engine's
ARV/rent (ADR 0005) and the vision rehab band (§27.4) into per-strategy underwriting.

Like ADR 0005 this adds **no table**: `analyses`/`scenarios` and the `EngineOutputBlock` /
`AssumptionSet` / `FinancingScenario` / `ReturnMetrics` payload schemas already shipped (migration
0004, commit `eb59653`). The blueprint even names the file — §26 says "all formulas implemented
in `engine/` (pure, Decimal, property-tested)." So this ADR documents a design decision inside an
existing boundary: how the formulas are factored, how "explainable by construction" is enforced,
and how the strategy scope is bounded honestly.

## Decision

Add four files to the `engine` module, extending the pure-core / impure-seam split from ADR 0005:

- **`finance.py` (pure)** — every §26 primitive (mortgage payment + amortization, closing/rehab/
  holding, the full operating pro-forma, cap/CoC/DSCR/GRM/breakeven, flip profit, the BRRRR refi,
  future value / equity multiple / IRR). Each money primitive returns a `Calc` = **value + the
  ledger that produced it**. Explainability is therefore *by construction* (§25.3 principle
  applied to finance): "show the math" (§26.9, NFR-08) reads the same `CalcLine`s the number was
  summed from — there is no second, drift-prone explanation path.
- **`strategies.py` (pure)** — composes the primitives into a full `EngineOutputBlock` per
  strategy (flip / LTR / BRRRR / wholesale), each a recipe differing in primary financing, whether
  there's a rental pro-forma, and which metric defines success. A `dispatch` maps `Strategy` →
  analyzer.
- **`analysis.py` (impure seam)** — assembles a `FinancialInputs` from the ORM (listing price, the
  comps engine's persisted ARV/rent, the vision `property_conditions` rehab band, assessor
  `tax_records`, market appreciation), runs `strategies.analyze`, and persists an `analyses` row
  with the full assumption snapshot + output (§26.9). Re-exported through `service.py` so
  `engine.service` stays the module's single public interface (§19).
- **Router** — `GET/POST /v1/properties/{id}/analysis[/{strategy}]` on the existing engine router.

Three commitments the framework calls out as failure modes:

1. **Bands, not false points (§26.6/§25.8).** Flip profit propagates the ARV and rehab intervals:
   P10 profit = conservative ARV (P10) × expensive rehab (high); P90 = optimistic ARV × cheap
   rehab. A wide, uncertain estimate yields a wide profit band and cannot masquerade as a
   confident number; ranking reads the conservative end (the winner's-curse discipline extended
   from the comps engine into profit).
2. **Standard definitions, labeled disputes (§26.4).** NOI excludes the capex reserve and debt
   (textbook); NOI-after-reserves is surfaced *alongside* it, labeled — we refuse to silently
   pick the side of an argument investors have. Management is on EGI, vacancy/maintenance/capex on
   GPR, exactly per §26.4 — the percentage-base traps are encoded, not hand-waved.
3. **Honest gaps, never a fabricated input (§27.1 stance).** A missing assessor record, insurance
   quote, or ARV becomes an explicit `warning` on the output (and, for a required input, an
   `UnsupportedStrategyError` → 422), never a silent $0 that flatters the return.

**Assumption layering (FR-013).** `resolve_assumptions` merges system v1 defaults → per-market
`markets.config["assumptions"]` → org `locked_assumption_set` → an explicit per-request override
(a scenario what-if). The merged set is snapshotted into `analyses.assumption_set`, so a stored
number always renders under the assumptions it used, even after defaults later change. Financing
rates live in the assumption set as versioned config defaults — the daily FRED/OBMMI ingestion
(§26.3) overwrites them; a stale manual table would be a silent accuracy bug across every
analysis, so the seam for the feed is explicit.

**Conventions inherited unchanged:** Decimal-end-to-end (§20), pure-core unit tests +
skip-guarded integration tests, config-versioned constants (no hardcodes), the single-service
module boundary.

## Consequences

- Every §26 calculation now exists, each carrying its derivation ledger; the property-page
  analyzer (S12) and its "show the math" panel have a backend. Flip, LTR, BRRRR, and wholesale
  underwriting is delivered end-to-end (comps → rehab → pro-forma → returns → persisted analysis).
- **No migration, no metadata-test drift** — schema was already in place; this is logic + tests +
  router wiring only.
- **Deliberately deferred (tracked, not done here):**
  - **STR / house-hack / multifamily / land / commercial / value-add (§26.8 [F]).** These need
    inputs beyond `FinancialInputs` (ADR/occupancy curves, unit-mix rents, acreage, leases). The
    dispatcher raises `UnsupportedStrategyError` for them rather than returning a wrong number —
    they slot in behind the same interface (§26.8 "each is an engine module against the same
    interfaces") when their inputs exist. These are marked [F] (future) in the PRD's own §25.4/§26.8.
  - **Live financing-rate ingestion (§26.3)** — the assumption table holds the defaults; the daily
    FRED/OBMMI adapter + staleness paging is ingestion work, not engine logic.
  - **Reassessment-on-sale tax estimate (§26.4)** — the `reassessed_on_sale` flag is read and
    carried; the jurisdiction table that projects the *new* bill is curated market data (S43).
  - **Scenario save/what-if UI (`scenarios` table)** — the engine takes an `assumptions_override`
    and the table exists; the CRUD surface is a thin follow-on.
- Scoring (§25) can now read the F-group factors (`flip_net_margin`, `coc_return`, `dscr`,
  `cap_rate`, `brrrr_capital_left`, …) straight off the stored `ReturnMetrics`.
