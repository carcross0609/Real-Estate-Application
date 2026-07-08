# DealLens — Analysis Frameworks

Version 2.0 · 2026-07-08 · Sections 25–28 of the master outline.

> **v2.0 changelog:** added §25.8 winner's-curse mitigation (PRD §35 R3); cross-strategy
> calibration requirement in §25.4; list-to-effective rent adjustment in §26.1; automated
> daily rate ingestion in §26.3.
These frameworks are the product's intellectual core. They are specified here to be
implemented in `engine/` and `scoring/` ([02 §9.3](02-TECHNICAL-DESIGN.md)) with golden
fixtures; every constant below is a **versioned config default**, not a hardcode.

---

## 25. Investment Scoring Framework

### 25.1 Design principles
1. **Profit-first:** scores estimate risk-adjusted profitability for a specific strategy —
   never "niceness" of a house.
2. **Market-relative:** raw metrics are normalized to percentiles within (market ×
   property-class × trailing-90-day window). An 8% cap rate scores differently in Austin
   vs. Cleveland; the score answers "how good is this *here, now, vs. alternatives*."
3. **Explainable by construction:** the score is a transparent weighted sum over named
   factors; the explanation ledger (`score_factors`) is produced by the same code path
   that produces the number — it cannot drift from the truth.
4. **Confidence is separate from quality:** a thin-data A-deal is shown as "A, low
   confidence," never silently downgraded to a C. Confidence gates *alerting* (no instant
   alerts below confidence 0.5), not the score itself.
5. **Versioned & auditable:** `scoring_version` on every score; weight changes ship like
   code (eval-gated, changelogged).

### 25.2 Pipeline

```
raw inputs (engine outputs, condition profile, market stats, listing signals)
   → factor computation  f_i (raw value)
   → normalization       n_i = percentile / calibrated curve → [0,100]
   → strategy weighting  S = Σ w_i · n_i   (Σ w_i = 1 per strategy profile)
   → penalty gates       hard-risk caps (see 25.6)
   → outputs             strategy scores, overall, grade, risk, confidence,
                         recommendation + ledger
```

Normalization details: monotone metrics (CoC, spread) use market-window percentiles with
winsorization at p2/p98; U-shaped metrics (DOM: very low = competition, very high = stale)
use calibrated piecewise curves; boolean flags map to fixed point values. Cold-start
markets use national-prior curves until n ≥ 300 local observations.

### 25.3 Factor registry (core set, extensible per NFR-12)

**Group F — Financial (per-strategy engine outputs)**
| Factor | Notes |
|---|---|
| `flip_net_margin` | net profit ÷ total cash invested (26.6) |
| `flip_spread_pct` | (ARV − all-in cost) ÷ ARV |
| `coc_return` | year-1 cash-on-cash (26.5) |
| `dscr` | NOI ÷ annual debt service |
| `cap_rate` | vs. market-class percentile |
| `brrrr_capital_left` | cash left in after refi ÷ initial cash (lower better) |
| `rent_to_price` | gross monthly rent ÷ price |
| `breakeven_occupancy` | expenses+debt ÷ gross potential rent |

**Group D — Deal/listing dynamics**
| Factor | Notes |
|---|---|
| `price_vs_avm` | list price ÷ as-is value estimate (discount = points) |
| `price_cut_signal` | count/magnitude/recency of reductions |
| `dom_curve` | calibrated U-curve vs. market median DOM |
| `motivated_seller_signals` | NLP flags from remarks (as-is, estate, relocation…) |
| `relist_history` | failed prior sales (opportunity + risk info) |

**Group C — Condition & rehab (vision outputs, 03 §27)**
| Factor | Notes |
|---|---|
| `condition_arbitrage` | gap between condition-implied value and list price — the flip goldmine factor |
| `renovation_difficulty` | 1–5 inverse-scored for flip; near-turnkey scores high for LTR |
| `red_flag_severity` | foundation/water/roof indicators (also feeds Risk) |
| `rehab_to_arv_ratio` | rehab midpoint ÷ ARV (execution risk) |

**Group L — Location micro (03 §28)**
`school_percentile`, `crime_trend`, `walkability`, `flood_risk_class` (also Risk),
`employer_proximity`, `planned_development_signal`.

**Group M — Market macro (03 §28)**
`appreciation_3yr`, `population_growth`, `inventory_months_trend`, `rent_growth_3yr`,
`market_liquidity` (sale velocity for this property class).

### 25.4 Strategy weight profiles (initial defaults, config-versioned)

| Group → | F | D | C | L | M |
|---|---|---|---|---|---|
| **Flip** | .40 (margin/spread dominant) | .20 | .25 | .10 | .05 |
| **LTR** | .35 (CoC/DSCR dominant) | .10 | .15 | .25 | .15 |
| **BRRRR** | .40 (capital-left + CoC) | .15 | .20 | .15 | .10 |
| **STR** [F] | .35 (RevPAR-based) | .10 | .20 | .25 (incl. regulation) | .10 |
| **House-hack** [F] | .30 | .10 | .20 | .30 | .10 |
| **Wholesale** [F] | .30 (spread) | .35 (motivation) | .25 | .05 | .05 |
| **Multifamily/Commercial/Land/Value-add** [F] | dedicated profiles at build time, same framework |

Within-group factor weights defined in the scoring config (e.g., Flip F-group: margin .5,
spread .3, holding-sensitivity .2). **Overall Score** = max of user's enabled strategy
scores (a property brilliant for exactly one strategy *is* a great deal), with the winning
strategy named — never a blurry average.

Taking a max makes cross-strategy comparability a **calibration requirement, not an
assumption**: strategy scores must be aligned so equal scores ≈ equal risk-adjusted
annualized return expectation, otherwise the max silently favors whichever strategy's
curve is most generous. Checked in evals whenever a weight profile changes (equal-score
cohorts across strategies compared on the §4.3 outcome panel).

### 25.5 Grades, risk, confidence, recommendation

- **Letter grade** (within market-window score distribution): A+ ≥ 95th pct, A ≥ 90, A− ≥ 85,
  B+ ≥ 75, B ≥ 65, B− ≥ 55, C+ ≥ 45, C ≥ 35, C− ≥ 25, D ≥ 10, F < 10. Grades communicate
  *relative* rank; the score itself is calibrated so 70+ ≈ "worth a serious look" across
  markets.
- **Risk Score 0–100** (higher = riskier), weighted composite of: structural red flags,
  rehab-to-ARV ratio, flood/insurance class, comp dispersion (valuation uncertainty),
  market liquidity, leverage sensitivity (DSCR at rate +150 bps), single-exit dependence
  (flip-only viability), regulatory flags (STR). Decomposed in UI like the main score.
- **Confidence Score 0–100:** data completeness (photo coverage map, comp count/quality,
  records matched), estimator interval widths, model self-reported confidence, market
  sample size. Calibrated per PRD §4.3 (reliability curves).
- **Recommendation:** rule-layer over (score, risk, confidence):
  `Strong Buy` score ≥ 85 ∧ risk ≤ 40 ∧ conf ≥ 70 · `Buy` ≥ 70 ∧ risk ≤ 60 ·
  `Hold/Watch` 55–70 or high-score-low-confidence ("verify X") · `Pass` otherwise —
  always with the top-3 positive and negative ledger factors as the stated rationale.

### 25.6 Hard gates (penalty caps, not weights)
Certain findings cap the score regardless of arithmetic: confirmed-severity foundation
flag → strategy scores ≤ 60 pending inspection note; flood zone AE without quotable
insurance → ≤ 70; DSCR < 1.0 at underwriting rate → rental strategies ≤ 65; wholesale
spread < assignment-fee floor → wholesale ≤ 50. Gates appear in the ledger as explicit
capped-by entries (trust requires showing the cap, not hiding it in weights).

### 25.7 Anti-gaming & drift
Weekly job monitors factor distributions per market (population-stability index); drift
pages ops (a feed change or market shock can silently skew scores). User-tunable weights
(FR-034) are bounded ±50% per group and renormalized — users personalize, they can't
break the math.

### 25.8 Winner's-curse mitigation (ranking under estimation error)

Ranking by point estimates systematically surfaces the properties whose ARV/rent we
*over*-estimated — selection on estimation error, the classic AVM-ranking failure and the
single most likely way this product quietly loses users' money (PRD §35 R3). Mitigations,
all in v1:

1. **Rank conservatively, display honestly.** Ranking and alerting inputs use the
   conservative quantile of each estimate (ARV/rent at P30, rehab at P70); the property
   page still shows the full interval and the P50. Two properties with equal midpoints
   but different interval widths must not rank equally — the wide one ranks lower.
2. **Confidence already gates alerts** (25.1 #4) — thin-data properties can top a list
   but cannot page a user.
3. **Detection, not just prevention:** the §4.3 score→outcome panel is segmented by rank
   position. If top-decile properties underperform their predicted spread, that *is*
   winner's curse manifesting — the fix is recalibrating the ranking quantile, not
   fiddling with factor weights.

---

## 26. Financial Calculation Framework

All formulas implemented in `engine/` (pure, Decimal, property-tested). Defaults below are
**system assumption set v1** — user-overridable at global/buy-box/property levels (FR-013),
market-specific tables where noted. Full output block stored per (property × strategy).

### 26.1 Value & income estimators
- **ARV:** comp-based — select sold comps (renovated-condition, ≤ 0.75 mi urban / 3 mi
  rural, ≤ 6 mo, ±25% sqft, same class), adjust per line-item table ($/sqft delta, bed/bath,
  garage, lot, condition, pool), weight by similarity → point + P10/P90 interval. Fallback
  ladder when comps thin: widen recency → widen radius (flagged, confidence ↓) → model-based
  $/sqft prior (low confidence). User pin/exclude → recompute (FR-015).
- **As-is value:** same method against unrenovated-condition comps + condition adjustment
  from vision profile.
- **Market rent (LTR):** rental comps (same filters vs. rental listings) → $/mo point +
  interval; RentCast as cross-check (divergence > 15% → confidence ↓, flag). Listed rents
  are *asking*, not achieved: a market-calibrated list-to-effective adjustment (initially
  −2 to −4%) is applied, tracked against closed-lease data where feeds provide it, and
  PRD §4.3 rent accuracy is measured against effective rents.
- **STR revenue [F]:** ADR × occupancy × (1 − platform fees), seasonal curve, from licensed
  data; LTR fallback with explicit "STR data unavailable."

### 26.2 Acquisition & rehab
```
closing_costs_buy   = price × closing_pct (default 2.0%, market table) + fixed_fees
rehab               = Σ line items (from AI estimate 27.4, user-editable)
                      + contingency (default 15% of rehab; 20% if red flags)
all_in              = price + closing_buy + rehab + holding_costs(hold months)
```

### 26.3 Financing scenarios (≥ 3 computed per property)
Standard set: **(a)** conventional 20% down 30-yr; **(b)** investor DSCR loan 25% down;
**(c)** hard money (flip: 10% down, 2 pts, 11.5%, interest-only, 12-mo) → refi (BRRRR);
**(d)** cash. Rates auto-ingested daily (FRED/OBMMI conventional series + an
admin-adjustable investor-loan spread table for DSCR/hard-money); live per-user quotes
[L]. A stale manual rate table is a silent accuracy bug across every analysis — automated
from Phase 1, with staleness (> 3 business days) paging ops.

```
payment M = P · r(1+r)^n / ((1+r)^n − 1)      r = annual/12
full amortization schedule computed (analyzer chart + payoff at exit)
loan costs = points + origination + fixed
```

### 26.4 Operating pro-forma (rental strategies)
```
GPR   = market_rent × 12
vacancy         (default 8% GPR; market table)
EGI   = GPR − vacancy + other_income
opex: taxes (actual assessor, reassessed-at-purchase where jurisdiction table says so —
      a classic underwriting trap), insurance (market $/sqft table; flood addl if zone),
      management (default 9% EGI), maintenance (default 8% GPR, age-adjusted),
      capex reserve (default 7% GPR, roof/HVAC age-adjusted from vision),
      HOA (actual), utilities (owner-paid config), landscaping/snow (market table)
NOI   = EGI − opex            (excl. debt & capex-reserve per standard defn; we surface
                               both "NOI" and "NOI after reserves" — labeled, because
                               investors argue about this)
```

### 26.5 Return metrics
```
cap_rate            = NOI / price                     (and ARV-basis variant, labeled)
cash_flow_monthly   = (EGI − opex − debt_service)/12  (after reserves — conservative)
CoC                 = annual_cash_flow / cash_invested
cash_invested       = down + closing_buy + rehab + holding_to_stabilization − financed_rehab
DSCR                = NOI / annual_debt_service
GRM                 = price / GPR
breakeven_occupancy = (opex + debt_service) / GPR
5-yr equity/IRR view = appreciation (market rate, user-cappable) + amortization
                       + cash flows − exit costs → simple IRR & equity multiple [Phase 2]
```

### 26.6 Flip model
```
holding_months (default 6; rehab-difficulty-adjusted 4–12)
holding = (taxes + insurance(builder's-risk table) + utilities + HOA)/12 × months
          + loan interest (from scenario c)
selling = agent_commission (default 5.5%) + closing_sell (1%) + concessions (default 1%)
net_profit = ARV − all_in − selling − loan_costs
flip_margin = net_profit / cash_invested         ROI_annualized = margin × 12/months
rule_70_check = price + rehab ≤ 0.70 × ARV       (shown as sanity flag, not gospel)
```
Outputs as P10/P50/P90 by propagating ARV and rehab intervals (no false precision — UI
shows the band, PRD §21.5).

### 26.7 BRRRR model
```
phase 1 = flip model through rehab (hard money or cash)
refi: seasoning (default 6 mo), refi_LTV (default 75% of ARV), DSCR-constrained loan size
      = min(LTV·ARV, loan where DSCR ≥ 1.20)
cash_out = new_loan − payoff − refi_costs
capital_left_in = cash_invested − cash_out       (the BRRRR metric)
post-refi rental pro-forma (26.4–26.5) at new debt service
"infinite return" flagged when capital_left_in ≤ 0
```

### 26.8 Other strategies [F]
House-hack (owner unit imputed rent, FHA 3.5% scenario, net housing cost vs. renting);
Multifamily (per-unit mix rents, economic vacancy, payroll opex line, value = NOI/cap);
Wholesale (MAO = ARV×0.70 − rehab − assignment_fee; spread analysis); Land (comp $/acre,
carry cost, entitlement-upside flags); Commercial (NOI/cap + simple DCF, tenant/lease
inputs). Each is an engine module against the same interfaces (NFR-12).

### 26.9 Traceability
Every stored output embeds: engine_version, full assumption snapshot, estimator versions,
comp_set ids. UI renders any number's full derivation on demand ("show the math") — the
same JSON the auditor sees (NFR-08).

---

## 27. AI Property Analysis Framework

### 27.1 Scope & stance
Vision models **perceive** (what condition is this kitchen?); deterministic code
**prices** what was perceived (what does a grade-2 kitchen cost to bring to grade-4/5?).
Every perception carries confidence; coverage gaps are explicit (no photos of the roof ⇒
roof grade = "unknown," not "average").

### 27.2 Per-photo analysis (stage-2 output schema)
```
room_type: enum (13 classes, 02 §11.3)
condition_grade: 1–5        1=gut/damaged 2=dated-worn 3=functional-dated
                            4=updated 5=recently-renovated/luxury
observations:               structured booleans+notes per room type, e.g. kitchen:
  cabinets{grade,style_era}, counters{material,grade}, appliances{present,age_class},
  layout_constraint_flag …  bath: fixtures, tile, vanity, signs_of_leak …
  exterior: siding, windows_era, roof{visible?,material,wear_class}, grading_drainage,
  foundation_visible{cracks?,type}, landscaping_grade
red_flags: [ {type: foundation_crack|water_stain|mold_suspect|roof_wear|outdated_panel|
              sagging_line|…, severity: possible|likely|severe, photo_evidence: bbox/desc} ]
quality: {photo_usable, staged_or_virtual (virtual staging detection — critical:
          virtually staged photos systematically hide condition), wide_angle_distortion}
confidence: 0–1 per field group
```
Rubric with visual exemplars lives in `vision/prompts/` (versioned, eval-gated per 02 §13.4).

### 27.3 Property-level aggregation (deterministic)
Max-pool severity for red flags; median grade per room class across its photos; coverage
map = which classes have usable photos (drives confidence, PRD §21.9 degraded states);
**renovation difficulty 1–5** = f(worst structural flag, count of grade ≤ 2 systems, age,
sqft, layout flags); listing-remarks NLP cross-check (says "renovated," photos say grade
2 → contradiction flag, confidence ↓, DQ-style surfacing).

### 27.4 Rehab cost model (deterministic, the money output)
```
for each system s ∈ {kitchen, baths(×count), flooring, interior_paint, exterior_paint,
                     roof, windows, HVAC, electrical, plumbing, foundation, landscaping,
                     structural_other}:
  target_grade = strategy-dependent (flip→4/5 to comp level; BRRRR/LTR→3/4 rent-ready)
  cost_s = unit_cost[market][s][from_grade→to_grade] × scale(sqft | count | lump)
  range  = cost_s × uncertainty(confidence_s, severity_if_flag)
cosmetic = Σ grades 3→target;  major = Σ grades ≤2 and structural flags
totals as low/mid/high + contingency (26.2)
```
`unit_cost` tables are market-versioned admin data (initially seeded from published cost
indices + local contractor validation in launch markets; recalibrated from user actuals
via feedback flywheel 02 §13.5). Red-flag items (foundation *possible*) enter as
inspection-contingent ranges, clearly labeled — we estimate exposure, we don't diagnose.

### 27.5 Confidence & failure honesty
Property AI confidence = f(coverage completeness, photo quality, model self-confidence
calibration, remarks agreement). Below 0.4 → analyzer shows "estimate from limited
photos" banner and score confidence inherits it. Pipeline failure → property still scored
via non-vision factors with widened intervals (FR-020) — never a blank.

### 27.6 Evaluation (binding, from Phase 0)
Labeled sets per 02 §13.4; ship-gates per PRD §4.3 (≥80% within-one-grade agreement;
rehab within ±25% of actuals ≥60% → 75%). Quarterly drift audit. Prompt/model changes are
diffed on the eval set before deploy — vision quality is a measured claim, not vibes.

---

## 28. Market Analysis Framework

### 28.1 Purpose
Two consumers: (1) scoring factors L/M (25.3) for every property; (2) user-facing market
context (dashboard pulse strip, market pages, future screener 01 §29.3).

### 28.2 Geographic hierarchy
`metro (CBSA) → county → city → ZIP → census tract → property`. Metrics computed at the
finest reliable level and inherited downward with source-level labeling (crime at county
level is *labeled* county-level — no fake precision).

### 28.3 Metric set & sources
| Domain | Metrics | Source (02 §14.1) | Cadence |
|---|---|---|---|
| Prices | median sale $/sqft, YoY/3yr appreciation, sale-to-list ratio | Tier A sold data | weekly |
| Velocity/supply | median DOM, months of inventory, absorption, price-cut share | Tier A | weekly |
| Rents | median rent by beds, rent growth, rent-to-price | Tier A/C | monthly |
| Demographics | population growth, household formation, income, age mix | Census/ACS | annual |
| Employment | job growth, unemployment, top employers & proximity, announced expansions | BLS + curated per-market table | monthly/curated |
| Schools | ratings percentile by assignment zone | NCES/GreatSchools | annual |
| Crime | index + 3-yr trend at finest legal level | FBI CDE + city open data | annual/quarterly |
| Hazard/insurance | FEMA zone, insurance cost class, (wildfire/wind tables by state) | FEMA + market tables | on-change |
| Walk/amenity | walkability, transit, grocery/amenity proximity | Walk Score + OSM POIs | annual |
| Development | permits volume, notable planned projects | city open data + curated | monthly/curated |
| Regulatory | STR rules, rent control flags, landlord-tenant climate class | curated legal table per market | on-change |

"Curated" = admin-maintained per launch market (S43) — honest about what can't be fully
automated yet; automation debt tracked per market.

### 28.4 Derived indices
- **Market Momentum** (appreciation, DOM trend, inventory trend, absorption) — feeds M.
- **Rental Demand** (rent growth, vacancy proxy, rent-to-price, household formation).
- **Liquidity** (sale velocity for the property class — exit-risk input).
- **Neighborhood Quality** (schools, crime trend, amenity, owner-occupancy) — feeds L.
- Each index: 0–100 within-metro percentile + national percentile, both shown.

### 28.5 Fair-housing guardrail (binding, NFR-07)
Demographic composition (race/ethnicity/religion/national origin, familial status) is
**never** a factor, an index input, or a displayed "quality" signal. Crime and school
metrics are shown as sourced facts with level-of-geography labels, not blended into any
"people quality" proxy. Factor registry additions require a documented fair-housing review
in the ADR. This is checked in scoring-config CI (denylisted variable classes).

### 28.6 Storage & serving
`market_stats` rows keyed (geo_id, metric, period) with source + method version; scoring
reads the latest closed period; UI charts read the series. Baseline calibration for a new
market (S43 onboarding) = 24-month backfill + percentile-curve fit before listings go live
to users (scores need a distribution to be relative *to*).

---

*End of frameworks. Roadmap → [04-ROADMAP.md](04-ROADMAP.md).*
