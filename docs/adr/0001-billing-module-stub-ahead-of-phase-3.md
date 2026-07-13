# ADR 0001: Billing module created as a stub in Phase 0, ahead of its Phase 3 slot

## Context

04-ROADMAP.md scopes real Stripe billing (checkout, portal, subscription webhooks) to
Phase 3 ("paid beta") and explicitly lists payments as out of scope for Phase 1. We're
still in Phase 0. However, 02-TECHNICAL-DESIGN.md §9.3 already assigns "plans,
entitlement computation" to a `billing` module, and the identity module's Phase-0
scaffold (`identity.service.create_org`, `identity.webhooks._upsert_org`) already needed
a plan → `Entitlement` computation to bootstrap new orgs onto Basic — that logic had been
built inline in `identity.service` (`PLAN_ENTITLEMENTS`, `recompute_entitlements`),
contradicting §9.3's ownership split.

## Decision

- Created `services/platform/src/deallens/modules/billing/` now, but as a stub only: no
  `stripe` dependency, no `/billing/*` routes, no webhook handler. It exists solely so
  `recompute_entitlements` and the plan→entitlement mapping sit behind the correct module
  boundary from day one.
- Moved `PLAN_ENTITLEMENTS` and `recompute_entitlements` from `identity.service` to
  `billing.service`, correcting three values that had drifted from PRD §33 in the
  original placeholder (Pro `alert_latency`: hourly → instant; Pro `pipeline_enabled`:
  false → true; Team `seats`: 8 → 5 base seats).
- `identity.service` and `identity.webhooks` import `recompute_entitlements` from
  `billing.service` directly — a deliberate, narrow identity → billing dependency for the
  org-bootstrap call site (billing does not import identity.service, so no cycle).
  `identity.service.check_entitlement` (the consumption/check side) stays in identity,
  matching §9.3's "identity consumes computed entitlements."
- `Subscription`/`Entitlement` ORM models stay defined in `identity.models` (colocated
  with `Org` for the existing migration/FKs) rather than moving to `billing.models` —
  moving table definitions across modules is a bigger, riskier change than this pass
  warranted. `billing.models` is a placeholder docstring for now.

## Consequences

- When Phase 3 Stripe work starts, `billing.service.recompute_entitlements` and
  `PLAN_ENTITLEMENTS` are already in the right place; only checkout/portal/webhook
  plumbing needs to be added (a `billing/webhooks.py` translating Stripe events into
  calls to the existing function).
- `identity` has one intentional compile-time dependency on `billing` (the org-bootstrap
  call). If this pattern repeats for other modules needing entitlement defaults on
  creation, revisit via a domain event (`OrgCreated` → billing listener) instead of
  further direct imports, per §9.4's event-driven convention.
- No functional/product change: entitlement values now match PRD §33 exactly instead of
  the earlier placeholder approximation.
