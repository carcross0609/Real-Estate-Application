# @deallens/design-tokens

Canonical DealLens design tokens (PRD §21). The **source of truth is
`apps/web/app/globals.css`** — edit tokens there; `tokens.json` here is the exported
artifact consumed by non-web renderers (PDF reports, email templates).

Rules encoded in the tokens:

- Dark-first; light theme is full-parity, same information density.
- One accent ("signal blue"). Green/amber/red are **reserved for deal quality & risk** —
  never decorative.
- Grade color + letter always travel together (map pins, tables, chips, PDFs).
- Mono speaks data (tabular numerals, labels); sans speaks prose.
