# DealLens — Product Requirements Document (PRD)

Version 2.0 · 2026-07-08 · Owners: Carson Crossno (co-founder), Claude (co-founder/architect)

> **v2.0 changelog (principal-architect review pass):** added §31 Assumptions &
> Constraints, §32 Competitive Landscape, §33 Pricing & Packaging, §34 Go-to-Market,
> §35 Risk Register; new features/FRs (Analyze Any Address, bulk screening); alert-
> precision counter-metric; NFR-16 (data lifecycle); rent ground-truth fix. Companion
> docs updated in lockstep (see each header).

Companion documents: [02-TECHNICAL-DESIGN.md](02-TECHNICAL-DESIGN.md) ·
[03-ANALYSIS-FRAMEWORKS.md](03-ANALYSIS-FRAMEWORKS.md) · [04-ROADMAP.md](04-ROADMAP.md)

---

## 1. Executive Summary

Real estate investors lose deals to speed and lose money to bad analysis. The best
opportunities in any market are typically identified and put under contract within 24–72
hours of listing; meanwhile, accurately underwriting a single property (ARV, rehab cost,
rent, cash flow, risk) takes an experienced investor 30–90 minutes and a novice much longer
— and both routinely get rehab costs wrong because they can't see past listing-photo
staging.

**DealLens** is a SaaS platform that does the underwriting continuously and automatically.
It ingests every listing in a user's target markets from licensed data feeds, enriches each
property with public records, market data, and AI vision analysis of listing photos, runs a
deterministic financial model across ten investment strategies, and assigns every property
an explainable 0–100 investment score. Investors open their dashboard to a ranked **Top 25
Deals** list for their market and strategy, drill into a full AI-generated investment report
on any property, and get alerted the moment a new listing scores above their threshold.

The wedge is **speed plus explainability**: competitors either rank by crude heuristics
(price/rent ratios) or hide their math. DealLens shows every assumption, every comp, every
line item, and every factor's contribution to the score — so investors trust it enough to
act fast.

**Business model:** subscription SaaS (individual investor tiers by market count and alert
speed; team tier for small funds/agents). Target: paying self-serve customers in 1–3 launch
metros within two quarters of MVP, expanding market-by-market.

**What this is not:** a brokerage, a lender, a licensed advisory service, or a consumer
home-search portal. It is professional decision-support tooling for investors.

The assumptions this plan depends on are registered in §31; the risks that could kill it
in §35. Both are reviewed monthly (04 rule 5).

---

## 2. Vision Statement

> **Every investor, from first-timer to fund, underwrites every property in their market in
> real time — with institutional-grade analysis they can see, question, and trust.**

Five-year picture: DealLens is the default screen a US real estate investor opens each
morning. It watches every market they care about, knows their strategy and buy box, has
already analyzed everything new overnight — photos included — and presents the handful of
opportunities worth their attention, each with a complete, defensible investment case. Over
time it becomes a full AI investment assistant: modeling offers, tracking the portfolio,
projecting exits, and negotiating alongside the investor.

---

## 3. Product Goals

| # | Goal | Description |
|---|---|---|
| G1 | **Comprehensive coverage** | Analyze 100% of active listings in every supported market within 1 hour of listing appearance in the feed; refresh on every change event. |
| G2 | **Trustworthy analysis** | Every number traceable: assumptions visible, comps inspectable, score decomposable factor-by-factor. Accuracy targets in §4. |
| G3 | **Strategy-native ranking** | Rank by projected profit for the user's strategy (flip, BRRRR, LTR, STR, house-hack, multifamily, commercial, land, value-add, wholesale) — never by price or superficial ratios. |
| G4 | **Speed to opportunity** | New high-scoring listings alerted to matching users within 15 minutes of ingestion (Pro tier). |
| G5 | **Premium experience** | Sub-second perceived interactions, map-first exploration, information-dense but calm UI that feels like a Bloomberg terminal for real estate. |
| G6 | **Scalable foundation** | Architecture supports 50+ markets, 1M+ tracked properties, thousands of concurrent users without redesign (see NFRs and 02-TECHNICAL-DESIGN). |
| G7 | **Compliant by construction** | All data licensed; MLS display rules, Fair Housing, and data-provider terms enforced in code, not policy docs. |

---

## 4. Success Metrics

### 4.1 North Star

**Weekly Qualified Deal Views (WQDV):** count of property-report views where the property
scored ≥ 70 for the viewer's active strategy. Measures whether we surface deals worth
investors' time — combines coverage, accuracy, and engagement in one number.

### 4.2 Product & Engagement

| Metric | Definition | MVP target (90 days post-launch) | Scale target (12 mo) |
|---|---|---|---|
| Activation rate | Signup → first market configured + first report viewed within 48h | ≥ 50% | ≥ 65% |
| Weekly active / monthly active | Stickiness | ≥ 40% | ≥ 55% |
| D30 retention (paid) | Paying users active at day 30 | ≥ 60% | ≥ 75% |
| Alert open rate | High-score alerts opened within 24h | ≥ 35% | ≥ 45% |
| Alert precision (counter-metric) | % of instant alerts acted on within 48h (report viewed, watched, or pipelined) — guards against alert fatigue (§35 R10); an alert volume increase that drops this number is a regression, not growth | ≥ 25% | ≥ 40% |
| Reports generated / WAU / week | Depth of analysis usage | ≥ 3 | ≥ 6 |
| NPS | Quarterly in-app survey | ≥ 30 | ≥ 50 |

### 4.3 Analysis Accuracy (the credibility metrics — tracked from day one)

| Metric | Definition | Target |
|---|---|---|
| ARV accuracy | Median absolute % error of ARV estimate vs. eventual resale price (for properties that later sell renovated) | ≤ 10% MVP → ≤ 7% |
| Rent accuracy | Median abs % error vs. *effective* market rents. Listed rents are asking, not achieved — a list-to-effective adjustment is applied and calibrated against closed-lease data where feeds provide it (03 §26.1) | ≤ 8% |
| Rehab estimate calibration | % of AI rehab estimates within user-reported actuals ±25% (feedback loop) | ≥ 60% → ≥ 75% |
| Photo condition agreement | AI condition grades vs. human expert labels on audit sample (quarterly, n ≥ 200) | ≥ 80% within one grade step |
| Score → outcome correlation | Do 80+ scored properties sell faster / at better spreads than 50-scored ones? (Spearman ρ on outcome panel) | ρ ≥ 0.4 and improving |
| Confidence calibration | When we say 90% confidence, are we right ~90% of the time? (reliability curves per estimator) | Brier-calibrated within ±10 pts |

### 4.4 Data & Platform Health

| Metric | Target |
|---|---|
| Listing ingestion latency (feed event → analyzed & scored) | p50 ≤ 15 min, p95 ≤ 60 min |
| Listing coverage vs. MLS ground truth per market | ≥ 98% |
| Stale-listing rate (status wrong > 24h) | ≤ 1% |
| API p95 latency (read endpoints) | ≤ 400 ms |
| Uptime (app + API) | 99.5% MVP → 99.9% |
| AI cost per fully analyzed property | ≤ $0.15 MVP → ≤ $0.06 (see AI pipeline cost controls) |

### 4.5 Business

| Metric | MVP target | 12-month target |
|---|---|---|
| Paying subscribers | 100 | 1,500 |
| MRR | $5k | $75k |
| Trial → paid conversion | ≥ 8% | ≥ 12% |
| Gross churn (monthly) | ≤ 7% | ≤ 4% |
| CAC payback | — | ≤ 6 months |
| Gross margin (after data + AI + infra COGS) | ≥ 60% | ≥ 75% |

---

## 5. User Personas

### P1 — "First-Deal Fiona" (new investor)
- 28–40, W-2 income, $30–80k to deploy, wants first rental or house-hack. Consumes
  BiggerPockets/YouTube; over-researches, under-acts. Fears overpaying and hidden rehab costs.
- **Needs:** guided analysis, plain-English explanations, conservative defaults, confidence
  signals, education embedded in the report ("what is a cap rate and why yours is 5.8%").
- **Success:** closes first deal within 6 months without a spreadsheet.
- **Willingness to pay:** $29–49/mo. Highest volume segment; highest churn risk if scared off.

### P2 — "Flipper Frank" (active flipper, 3–10 flips/yr)
- 35–55, full-time investor or contractor background. Lives on speed: sees a deal, needs
  ARV, rehab budget, and spread in minutes, offers same day. Currently uses a VA + MLS
  alerts + gut feel.
- **Needs:** instant flip score, photo-derived rehab estimate with line items he can sanity-
  check, tight comps he can defend to his hard-money lender, fastest possible alerts.
- **Success:** finds one incremental deal per quarter he'd have missed; kills bad deals faster.
- **Willingness to pay:** $99–199/mo without blinking if it wins him one deal.

### P3 — "Landlord Lena" (buy-and-hold, 2–20 doors)
- 40–60, builds long-term wealth via LTR/BRRRR. Analyzes monthly cash flow, DSCR, tenant
  quality of area. Patient — but wants to see everything that hits her buy box.
- **Needs:** rent estimates with comp backup, expense modeling she can tune (her insurance
  and management costs), BRRRR capital-recovery math, neighborhood quality signals
  (schools, crime trend), portfolio watchlist.
- **Success:** every acquisition meets her DSCR/cash-flow floor; zero surprise expenses.

### P4 — "STR Sam" (short-term rental operator)
- 30–45, runs 1–5 Airbnbs, optimizing for revenue per property. Regulatory risk is his top
  fear (city STR bans).
- **Needs:** ADR/occupancy-based revenue estimates, seasonality, STR regulation flags per
  jurisdiction, STR-vs-LTR comparison on every property.
- **Success:** confidently underwrites STR revenue within 10% and avoids banned zones.
- **Note:** STR revenue data (AirDNA-class) is a Phase-3 licensed integration; persona is
  served with LTR fallback + regulation flags before then.

### P5 — "Agent/Wholesaler Wes" (deal finder for others)
- 25–45, investor-friendly agent or wholesaler. Product is deal flow itself: sends
  opportunities to a buyer list. Volume user — screens hundreds of properties weekly.
- **Needs:** bulk screening, shareable branded reports, spread/assignment-fee math, filters
  for motivated-seller signals (DOM, price cuts, probate/absentee flags from public records).
- **Success:** sends 5 credible deals/week; reports close his buyers faster.

### P6 — "Fund Manager Farah" (small fund / partnership, team tier)
- 35–55, deploys $2–20M/yr, 2–8 person team. Needs consistency across analysts,
  auditability for LPs, and pipeline collaboration.
- **Needs:** shared workspaces, locked assumption sets ("house view" on rates/expenses),
  deal pipeline stages, export to Excel/PDF for IC memos, API access (later).
- **Success:** team underwrites 3× more deals with uniform assumptions.

**Prioritization:** MVP optimizes for **P2 and P3** (clearest pain, clearest willingness to
pay, strategies with the most tractable data). P1 is served by the same features with better
defaults/education. P4–P6 are fast-followers per the roadmap.

---

## 6. Complete Feature List

Grouped by module. **[M]** = MVP (Phases 1–2), **[F]** = fast-follow (Phases 3–4), **[L]** = later (Phase 5+).

### 6.1 Markets & Discovery
- [M] User-defined search areas: draw polygon on map, or select city/ZIP/county; multiple areas per user (tier-limited)
- [M] Continuous listing ingestion for defined areas (licensed feeds), status/price change tracking
- [M] **Top 25 Deals dashboard** per market × strategy, refreshed continuously
- [M] Map-first market explorer: score-colored pins, clustering, filter overlay
- [M] Full filtering: price, beds/baths, sqft, lot, year, property type, DOM, score ranges, strategy scores, status
- [M] Buy box: saved named filter+strategy+assumption sets that drive alerts
- [F] Off-market signals: absentee owner, long-hold, tax delinquency, pre-foreclosure (public-records driven)
- [F] Bulk screening: CSV/address-list import → batch analysis with ranked results and export (P5 volume workflow; Team tier)
- [F] Sold/leased comp browser as a first-class research surface
- [L] Nationwide market screener ("which metro fits my strategy") — see §29

### 6.2 Property Analysis
- [M] Property detail page: all listing facts, photos, price & listing history, taxes, HOA, ownership summary
- [M] Deterministic financial engine: ARV, rehab, rent, cash flow, cap rate, CoC, ROI, DSCR, holding & closing costs, financing scenarios (full spec in 03 §26)
- [M] Comparable sales & comparable rentals with adjustment table; user can pin/exclude comps and the model recomputes
- [M] Interactive Deal Analyzer: every assumption editable (purchase price, rate, down %, rehab, rent, expenses); live recompute; side-by-side scenario compare (up to 4)
- [M] Strategy switcher: same property re-underwritten instantly as flip / BRRRR / LTR / house-hack
- [F] STR underwriting (ADR/occupancy), multifamily (unit-mix, T12-style modeling), land, commercial (NOI/cap-driven), wholesale (spread/assignment)
- [F] Offer price solver: "what purchase price hits my target CoC / flip margin?"
- [F] **Analyze Any Address**: on-demand underwriting of any US address, on- or off-market — public records + valuation model + last-listing data where licensed; explicitly lower confidence with an "off-market — no interior data" state. (The investor who drives past a house must be able to punch in the address; competitors have this and it's a top-of-funnel hook.)
- [L] Sensitivity analysis (tornado charts), Monte Carlo ranges on key outputs

### 6.3 AI Analysis
- [M] Photo pipeline on every listing: room classification; condition grades (kitchen, baths, flooring, exterior, roof, landscaping); renovation difficulty; foundation/water-damage warning flags; cosmetic vs. major repair estimates; per-item and overall confidence (full rubric in 03 §27)
- [M] AI Investment Report per property: why it ranked, renovation estimate narrative, risks, comps discussion, exit strategies, expected ROI, strengths/weaknesses, recommendation — every claim grounded in engine outputs
- [M] Listing-description NLP: motivated-seller signals ("as-is", "TLC", "estate sale"), feature extraction, contradiction flags vs. photos
- [F] "Ask the Analyst" chat on any property (grounded, cites its sources, refuses ungrounded speculation)
- [F] User feedback loop: correct a condition grade or actual rehab cost → logged as labeled data for eval/calibration
- [L] Photo-over-time diffing (same property relisted: what changed?)

### 6.4 Scoring & Ranking
- [M] Overall Investment Score 0–100 + Letter Grade (A+…F)
- [M] Per-strategy scores: Flip, Rental (LTR), BRRRR — [F] STR, Multifamily, Commercial, Land, Wholesale, House-hack, Value-add
- [M] Risk Score and Confidence Score, each decomposed
- [M] Full explainability panel: every factor's value, percentile vs. market, weight, and point contribution (framework in 03 §25)
- [M] AI Investment Recommendation (Strong Buy / Buy / Hold / Pass, with rationale)
- [F] User-tunable weights within guardrails ("I care more about cash flow than appreciation")

### 6.5 Alerts & Monitoring
- [M] Buy-box alerts: new/changed listing matches → in-app + email; [F] SMS/push; tier-based latency (15 min Pro / hourly Standard / daily digest Basic)
- [M] Watchlist per property: price cuts, status changes, DOM milestones, score changes
- [M] Daily/weekly market digest email (new top deals, market movement)
- [F] Saved-search re-run on assumption changes ("rates dropped 50bps — 12 properties now clear your DSCR floor")

### 6.6 Reports & Sharing
- [M] Investment report as web view + branded PDF export
- [M] Share link (view-only, no PII, MLS-display-compliant)
- [F] Wholesaler/agent white-label branding on shared reports (team tier)
- [F] Excel/CSV export of engine outputs and comp tables
- [L] IC-memo template pack (fund tier)

### 6.7 Portfolio & Pipeline
- [F] Deal pipeline: stages (Watching → Analyzing → Offer → Under Contract → Closed/Dead), notes, tasks
- [F] Owned-portfolio tracking: actuals vs. underwrite, equity/valuation refresh
- [L] Performance analytics across portfolio; refi/sell recommendations

### 6.8 Accounts, Teams, Billing
- [M] Auth (email+OAuth), profile, strategy preferences, default assumption set
- [M] Subscription billing (Stripe): Basic / Pro tiers; market-count and alert-speed entitlements; trial
- [F] Team workspaces: shared markets, buy boxes, pipeline; roles (owner/analyst/viewer); locked house assumptions
- [L] Public API + Zapier (fund/enterprise tier)

### 6.9 Admin & Ops (internal)
- [M] Admin console: user/subscription management, market onboarding (feed config, boundaries), ingestion health dashboards, AI cost dashboards, feature flags
- [M] Data-quality tooling: anomaly review queue (impossible sqft, $1 listings), manual re-analyze trigger
- [F] Eval console: accuracy dashboards (§4.3), photo-label audit workflow, prompt/model version management

---

## 7. Functional Requirements

Format: **FR-### (Module) [Phase]** — requirement. "System" = platform backend unless noted.
Acceptance criteria live in tickets; requirements here define scope and behavior.

### Markets & Ingestion
- **FR-001 (Markets) [1]** Users can define a search area by drawing a polygon, selecting ZIP codes, a city, or a county. Areas are named, editable, and deletable. Tier limits enforced (Basic: 1 area, Pro: 5, Team: 25).
- **FR-002 (Ingestion) [1]** System ingests all residential listing records (active, pending, sold ≥ 24 mo back) for supported markets from licensed sources, normalized to the canonical property/listing model (02 §11).
- **FR-003 (Ingestion) [1]** System detects and records every listing change event (price, status, remarks, photos, withdrawal) with timestamp; full price/listing history is queryable per property.
- **FR-004 (Ingestion) [1]** New/changed listings are fully analyzed (enrichment, photos, engine, scores) within the latency SLOs of §4.4.
- **FR-005 (Ingestion) [1]** System enriches properties with public-records data: assessor (taxes, lot, year built), deeds/ownership (hold time, absentee flag), and geo layers (flood zone, school assignment).
- **FR-006 (Ingestion) [2]** Duplicate/relisted properties are resolved to a single canonical property via address+APN entity resolution; listing histories chain across relistings.
- **FR-007 (Markets) [1]** Market onboarding is config-driven: adding a metro requires feed credentials + boundary config + baseline stats calibration, no code changes.

### Analysis Engine
- **FR-010 (Engine) [1]** For every analyzed property, system computes the full financial output set defined in 03 §26 (ARV, rehab, rent, NOI, cash flow, cap rate, CoC, ROI, DSCR, holding costs, closing costs, ≥ 3 financing scenarios) for each enabled strategy.
- **FR-011 (Engine) [1]** All calculations are deterministic and versioned: same inputs + same engine version ⇒ identical outputs; every stored result records engine version and full input snapshot.
- **FR-012 (Engine) [1]** ARV and rent estimates are produced from comp-based models with the comp set stored alongside the estimate; each estimate carries a confidence interval.
- **FR-013 (Engine) [1]** Users can override any assumption at three levels — global defaults, per-buy-box, per-property — with clear precedence (property > buy box > user > system) and one-click reset.
- **FR-014 (Engine) [1]** Editing assumptions recomputes all outputs and scores in ≤ 1 s (client-visible), without mutating the canonical system-assumption analysis.
- **FR-015 (Comps) [1]** Comp selection is inspectable: distance, recency, similarity score, and adjustment line items shown per comp; user pin/exclude triggers re-estimate (marked "user-adjusted").
- **FR-016 (Engine) [3]** Offer solver: given a target metric (CoC, flip margin, DSCR), system solves for maximum purchase price and displays it as "Max Allowable Offer."
- **FR-017 (Engine) [3]** Analyze Any Address: user submits an arbitrary US address; system resolves it via public records, runs the full engine with a records-derived condition prior (no photos ⇒ widened intervals and an explicit "off-market — no interior data" confidence state); usage metered per tier (§33).
- **FR-018 (Engine) [3]** Bulk screening: CSV of addresses (Team tier) → batch FR-017 analysis producing a ranked, exportable results table; size and rate limits per tier; per-row failure reporting (unresolvable address ≠ silent drop).

### AI Pipeline
- **FR-020 (AI) [1]** Every listing photo is classified (room/scene type) and condition-graded per the rubric in 03 §27, producing structured JSON with per-field confidence; failures degrade gracefully (property remains scored with "photo analysis unavailable" flag and widened confidence intervals).
- **FR-021 (AI) [1]** Photo-level outputs aggregate to property-level condition summary, renovation difficulty (1–5), cosmetic and major repair estimates (ranges), and red-flag list (foundation, water, roof indicators) — each with confidence.
- **FR-022 (AI) [1]** Rehab cost estimate combines photo conditions, property size/age, and market cost tables into line-item ranges (03 §27.4); line items visible and user-editable in the analyzer.
- **FR-023 (AI) [1]** System generates the AI Investment Report (§6.3) from engine outputs + structured AI findings only; the generator cannot introduce numbers not present in its grounded context (enforced by template + post-check).
- **FR-024 (AI) [1]** Listing remarks are parsed for motivated-seller and condition signals; extracted signals feed scoring and are shown with source phrase.
- **FR-025 (AI) [2]** All AI outputs record model ID, prompt version, token cost; admin can re-run any property's pipeline against a new prompt/model version and diff results.
- **FR-026 (AI) [3]** "Ask the Analyst": property-scoped chat answering only from the property's grounded context; cites which data it used; explicit "insufficient data" behavior.
- **FR-027 (AI) [3]** Users can submit corrections (condition grade, actual rehab cost, actual rent); corrections are stored as labeled evaluation data and never silently alter live estimates.

### Scoring
- **FR-030 (Scoring) [1]** Every analyzed property receives: overall score 0–100, letter grade, flip score, rental score, BRRRR score, risk score, confidence score, and a recommendation label — computed per 03 §25.
- **FR-031 (Scoring) [1]** Score explanation endpoint/UI shows each factor: raw value, market percentile, weight, contribution (± points), and one-line rationale.
- **FR-032 (Scoring) [1]** Scores recompute on any material input change (price cut, new photos, comp updates, market stat refresh); score history is stored and chartable.
- **FR-033 (Scoring) [1]** Top 25 Deals list per (market, strategy) ranks by strategy score with tie-break on confidence; list updates within 5 minutes of any member score change.
- **FR-034 (Scoring) [3]** Users adjust factor-group weights within guardrails (no group to zero, normalized to 100%); personalized scores are labeled as such and don't affect the market-wide default ranking.

### Alerts & Watchlists
- **FR-040 (Alerts) [1]** Buy-box match → notification per user's channel/latency entitlement; alert payload includes score, grade, headline metrics, thumbnail, deep link.
- **FR-041 (Alerts) [1]** Watchlisted property events (price/status/score/DOM milestones) notify watchers; per-event-type opt-out.
- **FR-042 (Alerts) [1]** Digest emails (daily/weekly) summarize new top deals, price cuts on watched properties, and market stat movement; unsubscribe granular per digest type.
- **FR-043 (Alerts) [2]** Alert deduplication: one property matching multiple buy boxes generates one notification listing all matched boxes; per-user rate cap with overflow into digest.

### Users, Teams, Billing
- **FR-050 (Auth) [1]** Signup/login via email-password and Google OAuth; email verification; password reset; session management per 02 §16.
- **FR-051 (Billing) [1]** Stripe-backed subscriptions: trial, upgrade/downgrade with proration, cancellation, dunning; entitlements (markets, alert speed, seats, report exports) enforced server-side.
- **FR-052 (Teams) [3]** Team workspaces: invite by email, roles owner/admin/analyst/viewer; shared markets, buy boxes, pipeline; owner can lock assumption sets workspace-wide.
- **FR-053 (Account) [1]** Users manage profile, strategy preferences, default assumptions, notification channels; account deletion performs data erasure per privacy policy (30-day grace).

### Reports & Sharing
- **FR-060 (Reports) [1]** Any property report exports to PDF (branded, paginated, disclaimer-bearing) in ≤ 15 s async with notification on ready.
- **FR-061 (Reports) [1]** Share links: revocable, view-only, no requester PII, watermarked, compliant with MLS display rules for that market (e.g., required attribution, no sold-price display where prohibited).
- **FR-062 (Reports) [3]** Team tier: custom logo/colors on exports and share pages.

### Admin
- **FR-070 (Admin) [1]** Internal console: user/subscription lookup and admin actions, market feed health (lag, error rates, coverage), AI spend by pipeline stage, feature flags, manual property re-analyze.
- **FR-071 (Admin) [1]** Data-quality queue: statistically anomalous records held for review with approve/fix/suppress actions; suppressed records excluded from user surfaces but retained.
- **FR-072 (Admin) [2]** Eval dashboards: metrics of §4.3 over time, segmented by market and model version.

---

## 8. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | Read API p95 ≤ 400 ms; dashboard TTI ≤ 2.5 s on mid-tier hardware; map interactions ≥ 30 fps with 5k pins (clustering/tiling required); analyzer recompute ≤ 1 s. |
| NFR-02 | Freshness | Pipeline latency per §4.4 (p50 ≤ 15 min feed-event → scored). Listing status accuracy ≥ 99% at any time. |
| NFR-03 | Scalability | No architectural rewrite required to reach: 50 markets, 1M active + 10M historical properties, 100M+ listing events, 5k concurrent users, 500k photos/day through AI pipeline. Scale-out path documented per component (02 §9/§18). |
| NFR-04 | Availability | 99.5% MVP → 99.9% (Phase 4) for user-facing surfaces. Ingestion may degrade independently; user surfaces serve last-known-good data with staleness indicators. |
| NFR-05 | Durability | RPO ≤ 15 min, RTO ≤ 4 h. Point-in-time DB recovery; raw ingestion payloads retained (replayable pipeline). |
| NFR-06 | Security | Per 02 §15: TLS everywhere, encryption at rest, least-privilege IAM, secrets in manager (never in code), OWASP ASVS L2 alignment, dependency and container scanning in CI, pen test before Phase-4 GA; SOC 2 Type II program from Phase 5 (fund/team-tier procurement will require it). |
| NFR-07 | Privacy & compliance | CCPA-ready (access/deletion); minimal PII collected; MLS/provider display and retention rules encoded as per-market policy config enforced at API layer; Fair-Housing review: no protected-class proxies exposed as user-facing decision factors (see 03 §28.5). |
| NFR-08 | Auditability | Every score/estimate reproducible: input snapshot + engine/model/prompt versions stored. User-visible "as of" timestamps on all analyses. |
| NFR-09 | Cost | COGS per §4.4/§4.5 budgets; AI spend capped per property and per day with automatic degradation ladder (02 §13.6); per-market data cost tracked against revenue. |
| NFR-10 | Observability | Structured logs, metrics, traces (OpenTelemetry) across web/API/workers; SLO dashboards + paging alerts for §4.4 SLOs; AI pipeline step-level success/cost/latency metrics. |
| NFR-11 | Maintainability | Modular-monolith boundaries enforced by lint rules; ≥ 80% coverage on financial engine and scoring (property-based tests), ≥ 60% overall; CI < 10 min; any engineer can run full stack locally with seeded data in < 15 min. |
| NFR-12 | Extensibility | New strategy = new scoring profile + engine module behind interfaces (no core changes). New data source = new adapter conforming to ingestion contract. New market = configuration. |
| NFR-13 | Accessibility | WCAG 2.1 AA on all user surfaces; full keyboard navigation of dashboard/table views; color-independent score encoding (grade text + icon, not color alone). |
| NFR-14 | Browser/device | Latest 2 versions Chrome/Safari/Edge/Firefox; responsive down to 375 px (analysis surfaces optimized for ≥ 768 px; phone experience prioritizes alerts, top deals, report viewing). |
| NFR-15 | Internationalization | US-only launch; all copy externalized and currency/units centralized so i18n is not a rewrite. |
| NFR-16 | Data lifecycle | Per-source retention and termination obligations encoded in `license_policy` config; on feed termination, the market-offboarding runbook (02 §14.5) purges/retains data exactly per contract within the contractual window, with audit evidence. Offboarding rehearsed in staging before the first market launch. |

---

## 21. UI/UX Design Philosophy

*(Numbering follows the master outline; sections 9–20 are in 02-TECHNICAL-DESIGN.md.)*

**Positioning: a professional terminal, not a consumer portal.** Zillow is browsing;
DealLens is deciding. The UI should feel like a precision instrument — dense, fast, calm.

Principles:

1. **The number and the why, always together.** No score, grade, or dollar figure appears
   without an affordance to decompose it (hover → contribution summary; click → full
   breakdown). Trust is the product.
2. **Ranked by profit, not price.** Default sort everywhere is strategy score. Price is an
   attribute, never the organizing principle.
3. **Density with hierarchy.** Investors want many numbers per screen — deliver them with
   strict typographic hierarchy (tabular numerals, aligned decimal columns, muted labels /
   strong values), not white-space-starved clutter. Bloomberg discipline, modern skin.
4. **Speed is a feature.** Optimistic UI, skeletons < 100 ms, prefetch on hover of any
   property row, recompute-on-keystroke in the analyzer. Perceived latency budget: nothing
   the user does should feel slower than a spreadsheet.
5. **Confidence made visible.** Every estimate renders its uncertainty: interval bands on
   dollar figures, confidence chips (High/Med/Low) with tooltip reasons, and visual
   dimming of low-confidence factors. Never false precision ("$237,412" → "$230k–$245k").
6. **Dark-first, data-forward aesthetic.** Default dark theme (long analysis sessions,
   terminal feel) with full light theme. Restrained palette: neutral surfaces; one accent;
   semantic green/amber/red reserved exclusively for deal quality and risk — never
   decorative. Score grades get a consistent color+letter system used identically
   everywhere (map pins, tables, chips, PDFs).
7. **Map and table are peers.** Split-view market explorer; selections/filters/hover states
   stay synchronized bidirectionally.
8. **Progressive depth.** Card → detail → analyzer → full report: each level reveals more
   without dead ends; novices stop early, pros go deep. Educational tooltips (P1) are
   present but dismissible forever.
9. **Empty/degraded states are designed.** "Photos not yet analyzed," "2 comps only — low
   confidence," "feed delayed 3h" are explicit, honest states — not blanks.
10. **Disclaim without nagging.** Estimates labeled as estimates once per surface,
    footered, legally sufficient, visually quiet.

Design system: Tailwind + shadcn/ui base, extended into a DealLens token set (spacing,
type scale, semantic colors, elevation) documented in Storybook from Phase 1. Charts follow
a single viz spec (consistent axes, tabular tooltips, colorblind-safe ramps).

---

## 22–23. Complete Screen List & Each Page's Purpose

**Legend:** phase in brackets. All screens responsive per NFR-14.

### Public / Marketing
| # | Screen | Purpose |
|---|---|---|
| S01 | Landing page [1] | Convert: value prop ("the Top 25 deals in your market, underwritten by AI"), live sample report, pricing, social proof. |
| S02 | Pricing [1] | Tier comparison (markets, alert speed, seats, exports); trial CTA; FAQ. |
| S03 | Sample report (public) [1] | Full anonymized investment report as the marketing artifact — the product sells itself. |
| S04 | Legal (ToS, privacy, disclosures) [1] | Compliance; estimate-disclaimer language; data attribution as required by MLS agreements. |

### Auth & Onboarding
| # | Screen | Purpose |
|---|---|---|
| S05 | Sign up / Log in [1] | Email+password, Google OAuth, verification, reset. Minimal friction. |
| S06 | Onboarding wizard [1] | 4 steps: (1) investor profile & experience, (2) strategy selection, (3) first market area (map draw/ZIP picker with live listing-count preview), (4) buy-box basics + default assumptions (pre-filled by strategy). Ends on Dashboard with data already loading. |

### Core App
| # | Screen | Purpose |
|---|---|---|
| S10 | **Dashboard / Top Deals** [1] | Home. Top 25 Deals for selected market × strategy: ranked cards (photo, score+grade, headline metrics, movement badges "new/price cut/score ↑"). Market pulse strip (median price, DOM, inventory trend). Alert inbox summary. |
| S11 | **Market Explorer** [1] | Split map+table. Score-colored pins, cluster/heat modes, polygon draw, full filter panel, saved-view management. Hover syncs both panes. Bulk-select → compare. |
| S12 | **Property Detail** [1] | Single-property command center: photo gallery with AI condition overlays, key facts, score panel with decomposition, financial summary per strategy tab, price/listing history timeline, taxes/HOA, map context (flood, schools), comps preview, report + analyzer CTAs, watch button. |
| S13 | **Deal Analyzer** [1] | Interactive underwriting: assumptions rail (grouped, reset-able, precedence-labeled), live outputs (cash flow waterfall, returns table, amortization), strategy switcher, scenario compare (A/B/C/D columns), offer-solver [3]. Save scenario to property. |
| S14 | **Investment Report** [1] | The full AI-generated report (§6.3 contents), web-native with PDF export & share link. Every number links back to its source panel. |
| S15 | Comps Workbench [1-lite, 2-full] | Sales & rental comps: map + adjustment grid, pin/exclude with live re-estimate, similarity rationale per comp. Lite version embedded in S12; standalone in Phase 2. |
| S16 | Compare view [2] | Up to 4 properties side-by-side: scores, metrics, conditions, verdicts. |
| S17 | Watchlist [1] | Watched properties with event feed (price cuts, status, score changes) and quick metrics; bulk unwatch/notes. |
| S18 | Buy Boxes [1] | Create/edit named buy boxes (filters + strategy + assumptions + alert channel/speed); match-count preview; per-box performance ("12 matches, 3 viewed, 1 offer"). |
| S19 | Alerts inbox [1] | All notifications with read/unread, filters by box/market/type; deep links. |
| S20 | Deal Pipeline [3] | Kanban: Watching → Analyzing → Offer → Under Contract → Closed/Dead; cards carry key metrics; notes/tasks; team-visible. |
| S21 | Portfolio [4] | Owned properties: underwrite vs. actuals, equity tracking, valuation refresh, refi/sell flags. |

### Account & Team
| # | Screen | Purpose |
|---|---|---|
| S30 | Profile & preferences [1] | Identity, strategy defaults, assumption defaults (rates, expense ratios, mgmt %…), notification channels, theme. |
| S31 | Billing [1] | Plan, usage vs. entitlements, payment method, invoices, upgrade/downgrade/cancel. |
| S32 | Team management [3] | Members, roles, invites; shared assets (markets, boxes, assumption locks). |
| S33 | Markets manager [1] | User's search areas: map boundaries, listing counts, tier slots, add/edit/remove. |

### Internal Admin (staff only, separate access)
| # | Screen | Purpose |
|---|---|---|
| S40 | Ops dashboard [1] | Ingestion health per market (lag, errors, coverage), pipeline queue depths, AI spend, SLO status. |
| S41 | User/subscription admin [1] | Lookup, entitlement overrides, refunds, impersonate-for-support (audited). |
| S42 | Data quality queue [1] | Anomaly review: approve/fix/suppress with audit trail. |
| S43 | Market onboarding [1] | Feed credentials, boundary config, cost tables, MLS display-policy flags, baseline calibration runs. |
| S44 | AI eval console [2] | Accuracy dashboards, label audit workflow, prompt/model version management and diff-testing. |

---

## 24. User Flows

Notation: → step, ⇢ system action. Failure paths noted where they materially matter.

**UF-1 · Signup → first deal (activation, P1–P3)**
Landing → trial signup (S05) → verify email → onboarding (S06): profile → strategy →
draw market (⇢ live count: "1,204 active listings") → buy-box basics → Dashboard (S10)
⇢ Top 25 for their market renders from pre-analyzed market data (no wait — market was
pre-onboarded) → user opens #1 deal → Property Detail (S12) → views report (S14).
*Target: ≤ 8 minutes signup-to-first-report. Failure path: requested market unsupported →
waitlist capture + suggest nearest supported metro.*

**UF-2 · Daily deal check (core habit, P2/P3)**
Alert email/push → Alerts inbox (S19) or direct deep link → Property Detail → scan score
decomposition → open Analyzer (S13) → adjust rehab +$15k, rate to today's quote ⇢ live
recompute → still clears floor → Watch + note → (P2) export PDF for lender → offer offline
→ moves card to "Offer" in Pipeline (S20) [3].

**UF-3 · Market exploration (research mode)**
Dashboard → Market Explorer (S11) → filter (SFR, 3+bd, ≤$400k, Flip ≥ 75) → map pins
update ⇢ table syncs → hover pins to preview → select 3 → Compare (S16) → open winner →
Detail → Comps Workbench (S15): exclude an outlier comp ⇢ ARV re-estimates, score updates
(labeled "user-adjusted") → save scenario.

**UF-4 · Buy-box creation & alert lifecycle**
Buy Boxes (S18) → New → name "Eastside BRRRR" → filters + strategy=BRRRR + assumption
overrides (mgmt 8%, rate 7.1%) → preview: "matches 14 current" → set alert: instant/push →
save ⇢ backend registers matcher → days later: new listing scores 82 ⇢ matcher fires ≤ 15
min post-ingest → push → UF-2. *Rate-cap path: 6th alert in an hour folds into digest with
"4 more matches" summary (FR-043).*

**UF-5 · Deep underwriting → shareable case (P2, P5)**
Property Detail → Analyzer → Scenario A (system), B (aggressive rehab), C (refi at 6.5%)
→ compare columns → pick B → generate report ⇢ async, ready toast ≤ 15 s → share link
(revocable) to lender/buyer → recipient sees view-only branded report (no login) ⇢ view
tracked → owner sees "viewed 3×" on pipeline card.

**UF-6 · Watchlist price-cut response**
Watched property price cut ⇢ event pipeline recomputes: flip spread now positive, score
71→84 ⇢ watchers + matching boxes notified with delta framing ("cut $24k — now grades A-")
→ user opens analyzer pre-loaded at new price.

**UF-7 · Subscription upgrade (monetization)**
User adds 2nd market on Basic → soft gate: "Basic includes 1 market" → S31 → Pro →
Stripe checkout → immediate entitlement (grace-refund allowed) → 2nd market activates ⇢
if metro not yet analyzed: transparent progress state, email when ready. *Downgrade path:
choose which market to keep; others pause (not delete).*

**UF-8 · Team workflow (P6) [3]**
Owner creates workspace → invites analysts → locks house assumptions → shared buy boxes →
analyst triages alerts, moves pipeline cards, @-notes → owner reviews Offer column with
uniform assumptions → exports IC packet.

**UF-9 · Feedback loop (accuracy flywheel) [3]**
User closed a flip → prompts: "actual rehab? actual sale?" → simple form ⇢ stored as
labeled outcome, joins eval sets (§4.3) → periodic model/weight recalibration → user sees
"your feedback improved local rehab estimates" (reciprocity, retention).

**UF-10 · Degraded-data behavior (trust preservation)**
Feed outage in market ⇢ status banner "Listings delayed since 9:14 AM" on affected
surfaces; scores/timestamps show "as of"; alerts pause rather than fire on stale data;
auto-recovery clears banner and backfills events in order.

---

## 29. Future Expansion Opportunities

Sequenced options, not commitments; each with the strategic reason it's later.

1. **STR revenue intelligence** — licensed ADR/occupancy data; unlocks P4 fully. *Later
   because the data licensing is expensive; validate demand via STR-flagged waitlist.*
2. **Off-market & pre-market sourcing** — probate, tax-delinquency, absentee, pre-foreclosure
   scoring; skip-trace integrations for wholesalers. *Powerful but a distinct data/compliance
   footprint (contact-data laws).*
3. **Nationwide Market Screener** — invert the funnel: "which metro should I invest in for
   BRRRR at 8% CoC?" using our market-analysis layer (03 §28) across all metros.
4. **Offer & transaction assist** — LOI/offer doc generation, e-sign, agent hand-off
   marketplace; referral-fee revenue.
5. **Financing marketplace** — investor-loan rate quotes (DSCR/hard-money) inside the
   analyzer; lead-gen revenue; makes financing scenarios live-priced.
6. **Portfolio intelligence** — continuous valuation of owned assets, refi/sell/1031
   triggers; expands from acquisition tool to lifecycle platform (retention moat).
7. **Public API & data products** — underwriting-as-a-service for funds and proptechs;
   anonymized market indices.
8. **Commercial expansion** — office/retail/industrial with T12/rent-roll ingestion and
   DCF; different data vendors (CoStar-class) and buyer persona; separate product line.
9. **Predictive listing models** — "likely to list in 90 days" propensity scoring from
   public-records signals; ethically reviewed before build.
10. **International markets** — Canada/UK first; requires per-country data-source and legal
    rework; only after US model is proven.
11. **Mobile native apps** — after PWA/push proves the alert loop; native adds camera
    (on-site photo → instant rehab check) which is a genuinely new capability, not a port.
12. **Agent/brokerage white-label** — DealLens engine inside brokerage tools; enterprise
    ARR channel.

---

## 31. Assumptions & Constraints Register

*(Added v2.0 — the master outline extends 31–35.)* Binding assumptions the whole design
depends on. Each has a validation checkpoint and a fallback; TDD citations (02 §13.3,
§14.1) reference these IDs.

| ID | Assumption | Validation | Fallback if false |
|---|---|---|---|
| A1 | **Licensed data only.** MLS agreements are obtainable for target metros for an analytics (non-IDX) use case; no scraping, ever. | Phase-0 licensing workstream (04); metro selection weighted by licensing ease. | Broker-partnership / vendor-sponsorship route; different metros; public-records-led interim product. Kill trigger per 04 P0. |
| A2 | **LLMs never compute — they perceive and narrate.** All financial math is deterministic, versioned code; vision output is structured perception with confidence. | Grounding validator (02 §13.3); engine property tests. | None — architectural invariant, not a bet. |
| A3 | Claude-class vision meets §4.3 photo-agreement targets at ≤ $0.10/property. | Phase-0 benchmark on the 500-photo labeled set (go/no-go gate). | Alternative vision models; reduced photo scope; records-derived condition prior with wider intervals. |
| A4 | **AI processing of listing photos is contractually permitted** — each MLS license allows transmitting photos to the Anthropic API for transient inference, and Anthropic terms (no-training, DPA) satisfy the MLS's derivative-use clauses. *This is a distinct question from "can we display the photos" and must be verified per source in writing.* | Counsel-reviewed AI-use clause checklist per source, completed before any photo enters the pipeline (S43 onboarding gate); Anthropic DPA executed Phase 0. | Transient-only processing without caching; exclude non-permitting markets; self-hosted vision model [L]. |
| A5 | A two-person (Claude-assisted) team can ship the P1 scope in 10–12 weeks. | M1.x milestone tracking; scope-shed order pre-agreed in 04. | Cut breadth (S15-lite, secondary screens) — never accuracy gates or the tenant-isolation suite. |
| A6 | Public-records coverage (ATTOM-class) is sufficient for enrichment in launch metros. | Phase-0 trial-data audit: APN match rate ≥ 90% in candidate metros. | County-direct adapters; serve with degraded confidence flags. |
| A7 | Investors pay $39–$99/mo for ranked, explainable deal flow. | Phase-1 pricing interviews (≥ 10 would-pay); Phase-3 trial→paid ≥ 8%. | Reprice/repackage (§33); shift wedge toward P5/P6 (deal-flow-as-product personas). |

**Constraints:** US-only launch (NFR-15); no brokerage/lending/advisory activity (§1);
all outputs are decision support — never appraisals or investment advice (disclaimers per
§35 R8); solo-team operational simplicity dominates architecture choices (02 §9.1).

---

## 32. Competitive Landscape

| Competitor | What it is | Where DealLens wins | Threat |
|---|---|---|---|
| **Privy** | Investor-oriented MLS search with deal heuristics | Real underwriting + photo-derived rehab + explainable scores vs. comp-ratio heuristics | **High — closest analog; watch closely** |
| Zillow (investor filters) | Consumer portal; unmatched traffic and data | Strategy-native profit ranking and rehab/ARV depth conflict with Zillow's consumer/broker model; terminal UX for professionals | High *if* they enter; structurally unlikely to go deep |
| PropStream / BatchLeads | Off-market lead-gen + public-records lists | Underwriting depth and explainability vs. raw list-building | Medium — owns P5 wholesaler mindshare |
| DealCheck / Zilculator | Manual per-property calculators | We analyze the whole market continuously; they analyze one address you already found | Low — feature, not platform |
| Mashvisor / AirDNA | Market analytics; STR revenue data | Property-level underwriting + the speed loop; AirDNA is a data *vendor* to us [F] | Low / partner |
| Roofstock-class marketplaces | Curated transaction inventory | Decision support across the entire MLS, not a curated shelf | Low |
| **Spreadsheet + VA + gut** | The real incumbent | 15-minute alerts, consistency, photo analysis at scale | The actual bar to clear |

**Moat thesis, in order:** (1) the accuracy flywheel — user-corrected rehab/ARV/rent
actuals (FR-027, UF-9) are proprietary calibration data no competitor gets; (2) per-market
calibrated cost tables and score curves — operational grind competitors under-invest in;
(3) trust via explainability — investors defend deals to lenders with our ledger, making
switching psychological, not just practical; (4) breadth of licensed data relationships.
**Not moats:** UI polish, prompt engineering, model choice — all replicable in a quarter.

---

## 33. Pricing & Packaging (v1 hypothesis — validated in Phase 3)

| | **Basic $39/mo** | **Pro $99/mo** | **Team $299/mo** |
|---|---|---|---|
| Markets | 1 | 5 | 25 |
| Alert latency | Daily digest | Instant (≤ 15 min) | Instant |
| Alert channels | Email | Email + push + SMS | All |
| Seats | 1 | 1 | 5 (+$39/seat) |
| Reports | Web + 10 PDF/mo | Unlimited PDF + share links | + white-label |
| Analyze Any Address [F] | 10/mo | 100/mo | 500/mo |
| Bulk screening [F] | — | — | ✓ |
| Excel/CSV export | — | ✓ | ✓ |
| Pipeline board | — | ✓ | ✓ shared + locked assumptions |
| Annual billing | −20% | −20% | −20% |

Rules:
- 14-day trial, card up front (quality filter; test a card-free variant if activation
  misses §4.2 targets).
- **Price on value metrics — markets, speed, seats, volume — never on analysis quality.**
  Every tier gets full-fidelity underwriting; a "worse-math cheap tier" poisons the trust
  brand that is the product.
- Entitlements enforced server-side (FR-051); usage caps above are derived from the COGS
  model (02 §18.1) so Basic clears ≥ 60% gross margin at P95 usage.
- Grandfathering policy decided before first price change, not during it.

---

## 34. Go-to-Market Plan

The funnel math behind §4.5, stated plainly: 100 subscribers at 8% trial→paid requires
~1,250 trials in 90 days ≈ **14 trials/day by GA** — demand that must be built during
alpha/beta, not switched on afterward. Sequenced with the roadmap:

1. **Alpha (P1):** 20–50 hand-recruited investors from launch-metro REIA meetups and
   BiggerPockets outreach. Goal: testimonials + §4.3 accuracy proof points, not revenue.
2. **Waitlist engine (from P1):** public sample report (S03) + a "your market's Top 25"
   teaser page per candidate metro; waitlist volume becomes the market-expansion
   prioritization signal (which metro next is a data question, not a guess).
3. **Content wedge (P2–P3):** publish accuracy stats and market-level underwriting data
   ("we underwrote all 1,204 Austin actives — here's the distribution"); partnerships
   with investor educators (P1 pipeline; affiliate 20% year-1).
4. **Community & lender loop (P3):** REIA sponsorships in live metros; hard-money-lender
   co-marketing — every shared report (UF-5) is a product demo delivered to exactly the
   right audience; the share link is the viral loop.
5. **Paid acquisition (P4+):** only after organic CAC/payback baselines exist; intent
   terms ("BRRRR calculator," "ARV estimator") land on a free single-use analyzer teaser.
6. **Compliance guardrail:** public/SEO surfaces use aggregate stats and anonymized sample
   reports only — never raw listing dumps (NFR-07; MLS display rules apply to marketing
   pages too).

Ownership: Carson (community/content). Engineering time budgeted to GTM assets ≤ 10% per
phase — the product is the growth engine; GTM assets mostly reuse product surfaces.

---

## 35. Risk Register

Scored likelihood × impact (1–5 each); reviewed monthly (04 rule 5). Top block is
existential; bottom block is operational.

| # | Risk | L×I | Mitigation / trigger |
|---|---|---|---|
| R1 | **MLS licensing blocked, or AI photo processing prohibited**, in target metros | 4×5 | Phase-0 gate + kill trigger (04 P0); metro choice weighted by licensing ease; broker-partnership fallback scoped *in parallel*, not after failure (A1/A4); counsel-reviewed AI-use clause per source. |
| R2 | **Feed revocation post-launch** (MLS policy change, aggregator loss) | 2×5 | Termination runbook + purge obligations (02 §14.5, NFR-16); ≥ 2 feed relationships per metro where economics allow; contractual notice periods negotiated up front. |
| R3 | **Winner's curse in rankings** — top-ranked deals systematically over-estimated (selection on estimation error) | 4×4 | Conservative-quantile ranking (03 §25.8); confidence-gated alerts; §4.3 score→outcome panel segmented by rank position from day one. |
| R4 | Vision accuracy below bar → rehab estimates wrong → trust collapse | 3×5 | Phase-0 go/no-go benchmark (A3); §4.3 metrics as release gates; ranges not points; inspection-contingent labeling (03 §27.4). |
| R5 | Privy/Zillow/CoStar ships explainable investor scoring | 2×4 | Speed to the accuracy flywheel (§32 moats); publish accuracy stats as public proof; strategy-engine depth incumbents won't prioritize. |
| R6 | Unit economics: AI + data COGS eats Basic-tier margin | 3×3 | COGS model (02 §18.1) reviewed monthly from first alpha week (04 rule 4); degradation ladder (02 §13.6); §33 usage caps. |
| R7 | Two-person team burnout / bus factor | 3×4 | Pre-agreed scope-shed order (A5); boring-tech stack; runbooks from P1; phase gates permit pausing without half-built systems. |
| R8 | Legal: outputs construed as appraisal or investment advice; fair-housing claim | 2×5 | "Not an appraisal / not advice" disclaimers (S04) on every report surface and PDF; E&O insurance from Phase 0; fair-housing CI guardrail (03 §28.5); counsel review at GA. |
| R9 | Rate/market regime shift changes what "a deal" is | 3×3 | Market-relative normalization adapts by construction (03 §25.2); daily rate ingestion (03 §26.3); positioning: DealLens finds the best of *this* market. |
| R10 | Alert fatigue → churn (too many mediocre "deals") | 3×4 | Alert-precision counter-metric (§4.2); confidence gating (03 §25.1); rate caps + digest folding (FR-043). |
| R11 | Breach / licensed-data exfiltration (our data *is* the product) | 2×5 | 02 §15 control set; tile-endpoint hardening (02 §12.2); bulk-read anomaly detection; incident-response runbook from P1. |
| R12 | Email deliverability failure silently kills the alert loop | 2×4 | Dedicated sending subdomain, SPF/DKIM/DMARC from day 1, warm-up plan, seed-list + bounce monitoring paged like an SLO (02 §10). |

---

*End of PRD. Technical sections 9–20 → [02-TECHNICAL-DESIGN.md](02-TECHNICAL-DESIGN.md);
analytical frameworks 25–28 → [03-ANALYSIS-FRAMEWORKS.md](03-ANALYSIS-FRAMEWORKS.md);
roadmap 30 → [04-ROADMAP.md](04-ROADMAP.md).*
