# DealLens — Phased Development Roadmap

Version 1.0 · 2026-07-08 · Section 30 of the master outline.

Durations assume two builders (Carson + Claude-assisted development) working near
full-time, and are calendar estimates, not commitments. **Each phase has binding exit
criteria — we do not start the next phase on vibes.** Anything not listed in a phase is
explicitly out of that phase's scope.

```
P0 Foundations ─▶ P1 Analyst MVP ─▶ P2 AI Depth ─▶ P3 Strategies+Alerts ─▶ P4 SaaS GA ─▶ P5 Scale
   (4–6 wk)         (10–12 wk)        (6–8 wk)         (8–10 wk)             (8–10 wk)     (ongoing)
                         └────────── private alpha ──────────┘  └── paid beta ──┘  └ GA ┘
```

---

## Phase 0 — Foundations & De-risking (4–6 weeks)

Goal: kill the three existential risks — **data access, vision quality, unit economics** —
before writing product code.

| Workstream | Deliverables |
|---|---|
| Data licensing | Applications/agreements with MLS Grid/Trestle/Bridge for 2–3 candidate launch metros (choose metros partly by licensing ease + investor activity); ATTOM + RentCast trials signed; per-source license-policy matrix documented (display rules, photo caching, sold-price rules). |
| Vision de-risking | Build the photo eval set (≥500 labeled photos); benchmark Haiku vs. Sonnet on the 27.2 rubric; measure agreement + cost per property; go/no-go vs. PRD §4.3 targets. |
| Engine spike | Implement 26.5–26.7 core formulas + golden fixtures reviewed against hand underwriting of 20 real properties. |
| Skeleton | Monorepo scaffold (02 §19), CI, docker-compose dev env, Terraform staging env, ADR process started. |
| Legal | Entity, ToS/privacy drafts, estimate-disclaimer language, fair-housing review checklist (28.5). |

**Exit criteria:** ≥1 MLS feed agreement signed (sandbox flowing) · vision benchmark meets
80%-within-one-grade on eval set at ≤ $0.10/property · engine fixtures match human
underwriting within tolerance · `make dev` gives any machine a running stack in <15 min.

**Kill/pivot trigger:** if no viable MLS access in any target metro within 6 weeks →
revisit data strategy (different metros, direct-MLS applications, or Tier-B-led interim
product) before proceeding. Do not build the product on data hope.

---

## Phase 1 — Analyst MVP, single market, private alpha (10–12 weeks)

Goal: one metro, end-to-end value loop live for 20–50 hand-recruited alpha users (free):
**listing in feed → analyzed, scored, explained → Top 25 → report → alert.**

Scope (IDs from PRD): ingestion + enrichment for market #1 (FR-001–005, 007) · vision
pipeline v1 (FR-020–024) · engine for **Flip, LTR, BRRRR** (FR-010–015) · scoring +
explanations + Top 25 (FR-030–033) · screens S05–S06, S10–S14, S15-lite, S17–S19, S30,
S33 · buy-box alerts email/in-app (FR-040–042) · reports + PDF + share (FR-060–061) ·
admin S40–S43 (FR-070–071) · security baseline + tenant isolation suite · observability
SLO dashboards.

Explicitly **out**: payments, teams, STR/MF/land/commercial, pipeline board, compare,
user-tunable weights, SMS/push.

Milestones (cumulative): **M1.1** (wk 3) live feed → normalized DB, coverage watchdog
green · **M1.2** (wk 5) engine+scores on full market, Top-25 API · **M1.3** (wk 7) vision
pipeline on all active listings within budget · **M1.4** (wk 9) web app usable end-to-end
(UF-1, UF-2, UF-3) · **M1.5** (wk 11) alerts live, alpha cohort onboarded.

**Exit criteria:** pipeline SLOs met for 2 consecutive weeks (p50 ≤ 15 min, coverage ≥ 98%)
· ≥ 60% of alpha users return in week 2 · ≥ 10 users say they'd pay (pricing interviews)
· ARV median error ≤ 12% on trailing sold validation · zero cross-tenant findings.

---

## Phase 2 — AI depth & accuracy (6–8 weeks, overlaps alpha feedback)

Goal: make the analysis defensibly better than a competent human's first pass.

Scope: comps workbench full (S15, FR-015 interactive) · entity resolution + relist chains
(FR-006) · rehab line-item model v2 with market cost tables + contingency logic (27.4) ·
confidence calibration v1 (reliability curves live on eval console S44, FR-072) · report
quality pass (grounding validator, exit-strategy comparison section) · compare view (S16)
· score history + movement badges · eval console + prompt/model version workflow (FR-025)
· market #2 onboarded **via config only** (proves FR-007/NFR-12; fix whatever breaks).

**Exit criteria:** PRD §4.3 MVP accuracy targets hit on rolling windows · market #2 onboard
took ≤ 2 weeks and zero code changes to core · report fact-check suite: 100% grounded
numerals · alpha NPS ≥ 30.

---

## Phase 3 — Strategy breadth + monetizable alerts, paid beta (8–10 weeks)

Goal: the product people pay for — full strategy coverage for launch personas + the speed
loop + billing.

Scope: **Stripe billing, tiers, entitlements** (FR-051, UF-7) · strategy engines: STR
(with regulation flags; licensed STR data if Phase-0 economics allowed, else LTR-fallback
mode), house-hack, wholesale, multifamily 2–4 (26.8) · off-market signal pack v1 (absentee,
long-hold, price-cut velocity) · alert upgrades: push/SMS, instant tier, dedupe/rate caps
(FR-043) · offer solver (FR-016) · "Ask the Analyst" chat (FR-026) · user-tunable weights
(FR-034) · feedback capture (FR-027, UF-9) · pipeline board (S20) · markets #3–5.

Beta pricing hypothesis to test: Basic $39 (1 market, daily digest) · Pro $99 (5 markets,
instant alerts, exports) · annual −20%. Validate against P2/P3 willingness-to-pay.

**Exit criteria:** ≥ 100 paying subscribers · trial→paid ≥ 8% · gross churn ≤ 7% ·
alert-instant tier p95 ≤ 15 min sustained · support load ≤ 15 tickets/wk/100 users
(product-quality proxy).

---

## Phase 4 — SaaS hardening & GA (8–10 weeks)

Goal: production-grade for the public: reliability, security, teams, self-serve growth.

Scope: teams/workspaces + roles + locked assumptions (FR-052, UF-8) · white-label share
(FR-062) · Excel/CSV export · 99.9% availability work (blue-green matured, load tests to
5k concurrent, read replica if triggered) · pen test + remediation (NFR-06) · CCPA
data-access/deletion self-serve (FR-053) · billing maturity (dunning, proration edge
cases) · onboarding conversion optimization (UF-1 funnel instrumented to §4.2 targets) ·
marketing site + sample-report SEO (S01–S03) · markets #6–10 · docs/status page.

**Exit criteria:** GA launch · uptime ≥ 99.9% over 30 days · pen-test criticals = 0 ·
activation ≥ 50% · MRR ≥ $15k or clear path (growth ≥ 15%/mo).

---

## Phase 5 — Scale & expansion (ongoing)

Sequenced by the option list in PRD §29; standing tracks:

- **Market expansion engine:** target 25–50 metros; onboarding time driven < 1 week/market;
  per-market P&L (data cost vs. attributable MRR) reviewed monthly.
- **Accuracy flywheel:** feedback-label volume → quarterly recalibration; publish accuracy
  stats (marketing asset: "our ARVs are within 7%").
- **Platform economics:** AI cost/property → ≤ $0.06 (routing, caching, batch); COGS ≥ 75%
  gross margin.
- **Product options (pull forward on demand signal):** portfolio intelligence → nationwide
  screener → API/data products → financing marketplace → native mobile → commercial line.
- **Architecture:** extraction triggers from 02 §9.1 reviewed quarterly; Temporal decision;
  read-replica/partitioning as volume dictates.

---

## Cross-phase operating rules

1. **Accuracy metrics (PRD §4.3) are release gates from Phase 1 on** — a feature that
   degrades calibration doesn't ship, whatever it demos like.
2. **Every phase ships behind flags continuously** — "phase end" = flags on for everyone,
   not a big-bang merge.
3. **Data-license compliance review at every market launch** (display policies tested in
   staging against that MLS's rules).
4. **Budget honesty:** data + AI + infra COGS tracked monthly against §4.5 margin targets
   from the first alpha week — unit economics are a Phase-1 concern, not a Phase-5 surprise.
5. **This document set is living:** material deviations require a PR to `docs/` with an
   ADR — the blueprint stays true or gets formally amended.
