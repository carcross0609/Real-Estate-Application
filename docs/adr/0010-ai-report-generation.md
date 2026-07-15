# ADR 0010: AI report generation system (`reports` module, no schema change)

## Context

The prompt asks that every analyzed property automatically receive a comprehensive investment
report — opportunity, supporting calculations, comparables, risks, renovation estimates, financial
projections, recommended strategies, and exit scenarios — exportable and professionally formatted.
This is §11.3 / FR-060, grounded per §13.3.

Like ADRs 0005–0009 it adds **no table**: `reports` / `share_links` shipped in migration 0005
(USER-WORK schema, commit `51c81e6`), with `context_pack` (grounded inputs) and `narrative`
(section → prose) columns already designed for exactly this. What this ADR records is how the
report is grounded, generated, and rendered safely.

## Decision

Add the `reports` logic as a pure-core / impure-seam split:

- **`schemas.py`** — `ReportContextPack`: the **grounded** input bundle (§13.3), a composition of
  the other engines' typed outputs (valuation, per-strategy analyses, condition, score, market).
  Stored verbatim in `reports.context_pack` so a report re-renders identically and an auditor sees
  the exact numbers the prose was grounded in.
- **`builder.py` (pure, §13.3)** — the narrative generator: `(pack) -> list[ReportSection]`. Every
  figure in the prose is read straight from the pack, so a number can never be hallucinated and the
  report reconciles with the analyzer by construction. Eight sections (the prompt's list); a
  section with no inputs is **skipped**, never fabricated. The deterministic template generator is
  the shipped default; an LLM generator plugs in behind the same shape (like the vision provider),
  still constrained to the pack's numbers and post-validated.
- **`render.py` (pure, §15)** — Markdown + a self-contained, professionally-formatted HTML document
  (inline CSS, no external assets, PDF-ready). The HTML renderer **escapes all content first**,
  then applies the fixed markdown subset — so user-authored text (notes, remarks) can never inject
  markup. This closes the §15 XSS/SSRF-via-PDF vector by construction, not by trusting input.
- **`service.py` (impure seam)** — assemble the pack from the engine services, compose, render, and
  persist a `reports` row (org-scoped, RLS). **Router**: `POST /v1/properties/{id}/report`,
  `GET /v1/reports/{id}`, `GET /v1/reports/{id}/export?format=html|markdown`.

**Tenancy.** Reports are org-scoped, RLS, soft-deleted (USER-WORK, §11.2), so generation runs on
the actor's request-scoped session — it *reads* shared engine data and *writes* the tenant's
report. A test asserts org B cannot read org A's report (RLS → not-found).

**Grounded, reproducible, safe** — the three §13.3/§15 commitments, each enforced structurally:
the pack is the single source of every number (grounding), stored so re-render is identical
(reproducibility), and escaped before formatting (safety).

## Consequences

- Every analyzed property can produce a comprehensive, professionally-formatted, exportable report
  (Markdown + PDF-ready HTML) grounded in the real engine outputs — the S12 report surface and the
  S15 export/share surfaces have their backend. This is the capstone over all six analysis engines
  (comps, financial, vision, scoring, market → report).
- **No migration, no metadata-test drift** — schema was already in place.
- **Deliberately deferred (tracked, not done here):**
  - **The LLM narrative generator (§13.3).** The deterministic generator ships today (no API key
    needed); an LLM generator registers behind the same `(pack) -> sections` interface to add prose
    polish, constrained to the pack and post-validated — the same provider seam as vision (ADR
    0007) and the market metric feeds (ADR 0009).
  - **PDF materialization + `pdf_s3_key` cache (§9.4).** The HTML is PDF-ready; the network-isolated
    headless-Chromium render + S3 cache (§15) is infra wiring, like the ingestion outbox.
  - **Public share pages via `share_links`** (the by-token `system_session` render path, §12.3
    display-policy stripping) — the model exists; the anonymous render surface is a follow-on.
  - **Automatic generation on `ScoreUpdated`** — "every analyzed property receives a report" is
    wired when the event outbox lands (deferred since ADR 0003); today generation is on-demand via
    the endpoint (and lazy-on-first-view per §9.4).
