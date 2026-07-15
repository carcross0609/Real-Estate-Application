# DealLens Web (apps/web)

Next.js 15 (App Router) terminal UI for DealLens — implements the PRD's screen list
(S10–S33) with the §21 design philosophy: dark-first, dense-with-hierarchy, one accent,
semantic color reserved for deal quality and risk.

## Run

```bash
npm install
npm run dev        # http://localhost:3000
npm run build      # production build (stop the dev server first — they share .next/)
```

## Architecture

- `app/(app)/…` — all core screens: dashboard (Top 25), explorer (map+table), property
  detail, analyzer, AI report, watchlist, alerts, portfolio, markets, settings.
- `components/ui/` — shadcn-style primitives on Radix (buttons, tables, dialogs, …).
- `components/deal/` — domain components: **GradeRing** (the signature score gauge),
  score explainer popover, chips, stat tiles, sparklines, generative property art.
- `lib/api.ts` — the single data seam. Currently serves a deterministic demo dataset
  (`lib/mock/`) whose shapes mirror the platform's Pydantic contracts field-for-field;
  swap in the generated OpenAPI client (packages/api-client) without touching pages.
- `lib/finance.ts` — pure client-side underwriting math for the analyzer's live recompute.
- Design tokens live in `app/globals.css` (canonical) and are exported as
  `packages/design-tokens/tokens.json` for the PDF renderer / email templates.

## Conventions

- Every numeral renders in Geist Mono with tabular figures (`.figure` / `.tnum`).
- Labels/eyebrows are uppercase letterspaced mono (`.eyebrow`).
- No score or dollar figure appears without a "why" affordance (tooltip, popover, or
  ledger) — PRD §21 #1.
- Estimates render as intervals or compact figures, never false precision (§21 #5).
