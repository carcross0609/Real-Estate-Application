# DealLens — AI-Powered Real Estate Investment Platform

> **Working codename:** DealLens (placeholder — final brand TBD)
> **Status:** Blueprint phase. No production code exists yet, by design.
> **Last updated:** 2026-07-08

DealLens continuously scans real estate listings in user-defined markets, analyzes every
property across dozens of financial, physical, and market dimensions — including AI vision
analysis of listing photos — and surfaces the most profitable investment opportunities for
each investor's strategy before other buyers find them.

## Document Set

| Doc | Contents |
|---|---|
| [docs/01-PRD.md](docs/01-PRD.md) | Product Requirements: executive summary, vision, goals, success metrics, personas, feature list, functional & non-functional requirements, UI/UX philosophy, complete screen list, user flows, future expansion |
| [docs/02-TECHNICAL-DESIGN.md](docs/02-TECHNICAL-DESIGN.md) | Technical Design: architecture, tech stack with justification, database design, API architecture, AI pipeline, data collection, security, auth, deployment, infrastructure, folder structure, coding standards |
| [docs/03-ANALYSIS-FRAMEWORKS.md](docs/03-ANALYSIS-FRAMEWORKS.md) | Analytical core: investment scoring framework, financial calculation framework, AI property analysis framework, market analysis framework |
| [docs/04-ROADMAP.md](docs/04-ROADMAP.md) | Phased development roadmap with milestones and exit criteria, from foundation to production SaaS |

## Governing Assumptions (read first)

These assumptions shape every decision in the documents. If any is wrong, flag it before build begins.

1. **Data is licensed, not scraped.** We acquire listing data through legal channels (RESO Web
   API via MLS Grid / Trestle / Bridge Interactive, ATTOM, county public records). Scraping
   Zillow/Redfin violates their ToS and is an existential legal risk for a funded SaaS. This
   means real data costs (~$500–$3,000/mo at MVP) and per-MLS licensing effort. It is the
   single largest external dependency and is treated as such in the architecture.
2. **AI perceives and explains; deterministic code calculates.** LLMs never do financial
   arithmetic. All dollar figures come from an auditable calculation engine; AI provides
   inputs (photo condition scores, rehab line items) and narrative (report prose), always
   with confidence levels.
3. **Team of two, capital-efficient.** Architecture is a modular monolith with worker
   processes — service boundaries are drawn in code so we can extract microservices later,
   but we do not pay the distributed-systems tax on day one.
4. **US market first.** Single country, launch in 1–3 metro markets, expand market-by-market.
5. **We are decision support, not advice.** Every output is an estimate with stated
   confidence. Legal disclaimers are a product requirement, not boilerplate.

## How to Use This Blueprint

- Product/design decisions → 01. Engineering decisions → 02. "How is a score computed" → 03.
- Every requirement has an ID (`FR-###`, `NFR-###`). Reference IDs in tickets and PRs.
- The roadmap (04) sequences everything; nothing in 01–03 should be built out of phase order
  without a written reason.
